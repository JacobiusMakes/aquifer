"""Integration tests for patient-facing app routes.

Tests /patient/my-data, /patient/fill-form, /patient/share-email,
/patient/import/*, and /patient/health-records.

These routes use share-key auth (not JWT), so they bypass the Strata
auth middleware. A verified patient is required for all endpoints.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_state():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-patient-app"
    cfg.jwt_secret = "test-jwt-secret-patient-app"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as client:
        yield client, app
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


@pytest.fixture
def client(app_state):
    return app_state[0]


def _setup_verified_patient(client) -> tuple[str, str]:
    """Register a practice, register a patient, verify via OTP, return (patient_id, share_key)."""
    # Register a practice (needed to call patient registration endpoints)
    reg = client.post("/api/v1/auth/register", json={
        "practice_name": "Test Dental",
        "email": "admin@test.com",
        "password": "SecurePass123",
    })
    assert reg.status_code == 201
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Register patient
    resp = client.post("/api/v1/patients/register", json={
        "email": "patient@example.com",
    }, headers=headers)
    assert resp.status_code == 201
    patient_id = resp.json()["patient_id"]
    share_key = resp.json().get("share_key")

    # Generate and verify OTP
    otp_resp = client.post(f"/api/v1/patients/{patient_id}/otp", headers=headers)
    assert otp_resp.status_code == 200
    otp = otp_resp.json()["otp"]

    verify_resp = client.post("/api/v1/patients/verify", json={
        "patient_id": patient_id,
        "otp": otp,
    }, headers=headers)
    assert verify_resp.status_code == 200
    assert verify_resp.json()["verified"] is True

    if not share_key:
        share_key = verify_resp.json().get("share_key")

    return patient_id, share_key


# ---------------------------------------------------------------------------
# TestMyData
# ---------------------------------------------------------------------------

class TestMyData:
    def test_my_data_without_otp_returns_masked(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/my-data", json={
            "share_key": share_key,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["patient_id"] == patient_id
        assert data["otp_verified"] is False
        assert "email_masked" in data

    def test_my_data_invalid_share_key(self, client):
        resp = client.post("/api/v1/patient/my-data", json={
            "share_key": "AQ-XXXX-XXXX",
        })
        assert resp.status_code == 401

    def test_my_data_unverified_patient(self, client):
        # Register practice and patient but don't verify
        reg = client.post("/api/v1/auth/register", json={
            "practice_name": "Unverified Practice",
            "email": "unver@test.com",
            "password": "SecurePass123",
        })
        token = reg.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post("/api/v1/patients/register", json={
            "email": "unverified@example.com",
        }, headers=headers)
        share_key = resp.json().get("share_key")

        # share_key auth should fail because email_verified is false
        resp = client.post("/api/v1/patient/my-data", json={
            "share_key": share_key,
        })
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TestFillForm
# ---------------------------------------------------------------------------

class TestFillForm:
    def test_fill_form_returns_filled_text(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/fill-form", json={
            "share_key": share_key,
            "form_text": "Name: ___________\nDOB: ___________\n",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "filled_text" in data
        assert "summary" in data

    def test_fill_form_invalid_share_key(self, client):
        resp = client.post("/api/v1/patient/fill-form", json={
            "share_key": "AQ-XXXX-XXXX",
            "form_text": "Name: ___",
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestShareEmail
# ---------------------------------------------------------------------------

class TestShareEmail:
    def test_share_email_no_smtp_returns_summary(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/share-email", json={
            "share_key": share_key,
            "practice_email": "office@dental.com",
        })
        assert resp.status_code == 200
        data = resp.json()
        # SMTP not configured, should return sent=False with copy instructions
        assert data["sent"] is False
        assert "Copy the summary" in data["message"] or "not configured" in data["message"]


# ---------------------------------------------------------------------------
# TestImportManual
# ---------------------------------------------------------------------------

class TestImportManual:
    def test_import_manual_entry(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/import/manual", json={
            "share_key": share_key,
            "data": {
                "name": "Maria Garcia",
                "dob": "1985-07-22",
                "phone": "(512) 555-0147",
                "allergies": ["Penicillin"],
                "medications": ["Metformin 500mg"],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["records_imported"] >= 5
        assert data["source"] == "manual"

    def test_import_manual_empty_data(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/import/manual", json={
            "share_key": share_key,
            "data": {},
        })
        assert resp.status_code == 422

    def test_import_manual_invalid_share_key(self, client):
        resp = client.post("/api/v1/patient/import/manual", json={
            "share_key": "AQ-XXXX-XXXX",
            "data": {"name": "Test"},
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestImportFHIR
# ---------------------------------------------------------------------------

class TestImportFHIR:
    def test_import_fhir_bundle(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        fhir_bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Condition",
                        "code": {"text": "Hypertension"},
                        "onsetDateTime": "2020-01-15",
                    }
                },
                {
                    "resource": {
                        "resourceType": "AllergyIntolerance",
                        "code": {"text": "Penicillin"},
                    }
                },
            ],
        }

        resp = client.post(
            "/api/v1/patient/import/fhir",
            files={"file": ("bundle.json", json.dumps(fhir_bundle).encode(), "application/json")},
            headers={"X-Share-Key": share_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["records_imported"] == 2
        assert data["source"] == "fhir"

    def test_import_fhir_empty_bundle(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        empty_bundle = {"resourceType": "Bundle", "entry": []}
        resp = client.post(
            "/api/v1/patient/import/fhir",
            files={"file": ("empty.json", json.dumps(empty_bundle).encode(), "application/json")},
            headers={"X-Share-Key": share_key},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestImportAppleHealth
# ---------------------------------------------------------------------------

class TestImportAppleHealth:
    def test_import_apple_health_xml(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierBodyMass" value="72" unit="kg"
          startDate="2025-01-15T08:00:00Z" />
  <Record type="HKQuantityTypeIdentifierHeartRate" value="68" unit="count/min"
          startDate="2025-01-15T09:00:00Z" />
</HealthData>
"""
        resp = client.post(
            "/api/v1/patient/import/apple-health",
            files={"file": ("export.xml", xml.encode(), "application/xml")},
            headers={"X-Share-Key": share_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["records_imported"] == 2
        assert data["source"] == "apple_health"

    def test_import_apple_health_no_share_key(self, client):
        xml = "<HealthData></HealthData>"
        resp = client.post(
            "/api/v1/patient/import/apple-health",
            files={"file": ("export.xml", xml.encode(), "application/xml")},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TestHealthRecords
# ---------------------------------------------------------------------------

class TestHealthRecords:
    def test_get_health_records_after_import(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        # Import some data first
        client.post("/api/v1/patient/import/manual", json={
            "share_key": share_key,
            "data": {
                "name": "Test Patient",
                "allergies": ["Penicillin"],
            },
        })

        resp = client.post("/api/v1/patient/health-records", json={
            "share_key": share_key,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2
        assert data["patient_id"] == patient_id

    def test_health_records_values_masked_without_otp(self, client):
        patient_id, share_key = _setup_verified_patient(client)

        client.post("/api/v1/patient/import/manual", json={
            "share_key": share_key,
            "data": {"name": "Maria Garcia"},
        })

        resp = client.post("/api/v1/patient/health-records", json={
            "share_key": share_key,
        })
        assert resp.status_code == 200
        records = resp.json()["records"]
        # Values should be masked (contain asterisks)
        for r in records:
            if "value" in r and len(r["value"]) > 2:
                assert "*" in r["value"]
