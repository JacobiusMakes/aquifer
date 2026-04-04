"""Integration tests for the patient portability API endpoints.

Uses FastAPI TestClient to test the full request/response cycle for
patient registration, OTP, consent, and transfer endpoints.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-for-patient-tests"
    cfg.jwt_secret = "test-jwt-secret-for-patient-tests"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers — same pattern as test_strata.py
# ---------------------------------------------------------------------------

def register_and_login(
    client,
    practice_name="Test Dental",
    email="admin@test.com",
    password="SecurePass123",
):
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": practice_name,
        "email": email,
        "password": password,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_patient(client, headers, email="patient@example.com", phone=None):
    """Register a patient and return the response JSON."""
    body = {"email": email}
    if phone:
        body["phone"] = phone
    resp = client.post("/api/v1/patients/register", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _generate_otp(client, headers, patient_id):
    resp = client.post(f"/api/v1/patients/{patient_id}/otp", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["otp"]


def _verify_patient(client, headers, patient_id, otp):
    resp = client.post("/api/v1/patients/verify", json={
        "patient_id": patient_id,
        "otp": otp,
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_consent(client, headers, patient_id, source_id, target_id, scope="all", practice_type=None):
    body = {
        "source_practice_id": source_id,
        "target_practice_id": target_id,
        "scope": scope,
    }
    if practice_type:
        body["practice_type"] = practice_type
    resp = client.post(f"/api/v1/patients/{patient_id}/consent", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# TestPatientAPI
# ---------------------------------------------------------------------------

class TestPatientAPI:
    def test_register_patient_endpoint(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post("/api/v1/patients/register", json={
            "email": "newpatient@example.com"
        }, headers=headers)

        assert resp.status_code == 201
        data = resp.json()
        assert "patient_id" in data
        assert data["email"] == "newpatient@example.com"

    def test_register_duplicate_email_endpoint(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        client.post("/api/v1/patients/register", json={
            "email": "dup@example.com"
        }, headers=headers)

        resp = client.post("/api/v1/patients/register", json={
            "email": "dup@example.com"
        }, headers=headers)

        assert resp.status_code == 409

    def test_generate_otp_endpoint(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        patient = _register_patient(client, headers, email="otp-endpoint@example.com")
        resp = client.post(f"/api/v1/patients/{patient['patient_id']}/otp", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "otp" in data
        assert data["ttl_minutes"] == 15
        assert len(data["otp"]) == 6

    def test_verify_patient_endpoint(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        patient = _register_patient(client, headers, email="verify-endpoint@example.com")
        otp = _generate_otp(client, headers, patient["patient_id"])

        resp = client.post("/api/v1/patients/verify", json={
            "patient_id": patient["patient_id"],
            "otp": otp,
        }, headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["verified"] is True

    def test_create_consent_endpoint(self, client):
        reg_a = register_and_login(client, practice_name="Source Practice", email="source@example.com")
        reg_b = register_and_login(client, practice_name="Target Practice", email="target@example.com")
        headers = auth_headers(reg_a["token"])

        patient = _register_patient(client, headers, email="consent-endpoint@example.com")
        resp = client.post(f"/api/v1/patients/{patient['patient_id']}/consent", json={
            "source_practice_id": reg_a["practice_id"],
            "target_practice_id": reg_b["practice_id"],
            "scope": "all",
        }, headers=headers)

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["scope"] == "all"
        assert data["source_practice_id"] == reg_a["practice_id"]
        assert data["target_practice_id"] == reg_b["practice_id"]

    def test_create_consent_with_practice_type(self, client):
        reg_a = register_and_login(client, practice_name="Dental A", email="dental-a@example.com")
        reg_b = register_and_login(client, practice_name="Dental B", email="dental-b@example.com")
        headers = auth_headers(reg_a["token"])

        patient = _register_patient(client, headers, email="ptype-consent@example.com")
        resp = client.post(f"/api/v1/patients/{patient['patient_id']}/consent", json={
            "source_practice_id": reg_a["practice_id"],
            "target_practice_id": reg_b["practice_id"],
            "scope": "all",
            "practice_type": "dental",
        }, headers=headers)

        assert resp.status_code == 201
        data = resp.json()
        # The scope should have been expanded from "all" to the dental defaults
        assert data["scope"] != "all"
        assert "dental" in data["scope"]
        assert "demographics" in data["scope"]

    def test_authorize_consent_endpoint(self, client):
        reg_a = register_and_login(client, practice_name="Auth Source", email="auth-src@example.com")
        reg_b = register_and_login(client, practice_name="Auth Target", email="auth-tgt@example.com")
        headers = auth_headers(reg_a["token"])

        patient = _register_patient(client, headers, email="authorize-consent@example.com")
        consent = _create_consent(
            client, headers,
            patient["patient_id"],
            reg_a["practice_id"],
            reg_b["practice_id"],
        )

        resp = client.post(
            f"/api/v1/patients/{patient['patient_id']}/consent/{consent['consent_id']}/authorize",
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "authorized"
        assert data["authorized_at"] is not None

    def test_revoke_consent_endpoint(self, client):
        reg_a = register_and_login(client, practice_name="Revoke Source", email="rev-src@example.com")
        reg_b = register_and_login(client, practice_name="Revoke Target", email="rev-tgt@example.com")
        headers = auth_headers(reg_a["token"])

        patient = _register_patient(client, headers, email="revoke-consent@example.com")
        consent = _create_consent(
            client, headers,
            patient["patient_id"],
            reg_a["practice_id"],
            reg_b["practice_id"],
        )

        resp = client.delete(
            f"/api/v1/patients/{patient['patient_id']}/consent/{consent['consent_id']}",
            headers=headers,
        )
        assert resp.status_code == 204

        # Listing consents should show revoked status
        list_resp = client.get(
            f"/api/v1/patients/{patient['patient_id']}/consents",
            headers=headers,
        )
        assert list_resp.status_code == 200
        consents = list_resp.json()
        assert any(c["status"] == "revoked" for c in consents)

    def test_list_practices_endpoint(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        patient = _register_patient(client, headers, email="list-practices@example.com")

        # Link the patient to this practice via the link endpoint
        resp = client.post(
            f"/api/v1/patients/{patient['patient_id']}/link",
            json={"source_file_hashes": ""},
            headers=headers,
        )
        assert resp.status_code == 204

        resp = client.get(
            f"/api/v1/patients/{patient['patient_id']}/practices",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["practice_id"] == reg["practice_id"]

    def test_list_consents_endpoint(self, client):
        reg_a = register_and_login(client, practice_name="Consents Source", email="cs-src@example.com")
        reg_b = register_and_login(client, practice_name="Consents Target", email="cs-tgt@example.com")
        headers = auth_headers(reg_a["token"])

        patient = _register_patient(client, headers, email="list-consents@example.com")
        _create_consent(
            client, headers,
            patient["patient_id"],
            reg_a["practice_id"],
            reg_b["practice_id"],
        )

        resp = client.get(
            f"/api/v1/patients/{patient['patient_id']}/consents",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["patient_id"] == patient["patient_id"]
        assert data[0]["status"] == "pending"
