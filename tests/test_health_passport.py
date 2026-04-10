"""Tests for the Patient Health Passport."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.patient_app.health_passport import (
    generate_passport, passport_to_text, passport_to_html, verify_passport,
)
from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Unit tests for passport generation
# ---------------------------------------------------------------------------

SAMPLE_DATA = {
    "NAME": "Maria Garcia",
    "DATE": "07/22/1985",
    "PHONE": "(512) 555-0147",
    "EMAIL": "maria@example.com",
    "ADDRESS": "123 Main St, Austin, TX",
    "ACCOUNT": "BlueCross PPO",
}

SAMPLE_RECORDS = [
    {"domain": "medications", "field_type": "medication", "label": "Metformin 500mg", "value": "Metformin 500mg"},
    {"domain": "allergies", "field_type": "allergy", "label": "Penicillin", "value": "Penicillin"},
    {"domain": "medical_history", "field_type": "condition", "label": "Type 2 Diabetes", "value": "Type 2 Diabetes", "recorded_date": "2020-01-15"},
    {"domain": "medical_history", "field_type": "vital", "label": "Blood Pressure", "value": "120/80 mmHg", "recorded_date": "2025-01-15"},
]


class TestGeneratePassport:
    def test_basic_generation(self):
        passport = generate_passport(
            patient_id="p1", patient_email="maria@example.com",
            share_key="AQ-ABCD-EFGH", patient_data=SAMPLE_DATA,
            health_records=SAMPLE_RECORDS, signing_key="test-key",
        )
        data = passport["aquifer_health_passport"]
        assert data["version"] == "1.0"
        assert data["patient"]["share_key"] == "AQ-ABCD-EFGH"
        assert data["demographics"]["Name"] == "Maria Garcia"
        assert len(data["medications"]) == 1
        assert len(data["allergies"]) == 1
        assert len(data["conditions"]) == 1
        assert len(data["vitals"]) == 1
        assert "signature" in passport

    def test_email_masked(self):
        passport = generate_passport(
            patient_id="p1", patient_email="maria@example.com",
            share_key="AQ-XXXX-XXXX", patient_data={},
            health_records=[], signing_key="key",
        )
        email = passport["aquifer_health_passport"]["patient"]["email_masked"]
        assert email.startswith("ma")
        assert "*" in email
        assert "@example.com" in email

    def test_empty_records(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-XXXX-XXXX", patient_data={},
            health_records=[], signing_key="key",
        )
        assert passport["aquifer_health_passport"]["medications"] == []
        assert passport["aquifer_health_passport"]["conditions"] == []


class TestVerifyPassport:
    def test_valid_signature(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-XXXX-XXXX", patient_data=SAMPLE_DATA,
            health_records=SAMPLE_RECORDS, signing_key="my-secret",
        )
        assert verify_passport(passport, "my-secret") is True

    def test_invalid_signature(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-XXXX-XXXX", patient_data=SAMPLE_DATA,
            health_records=SAMPLE_RECORDS, signing_key="my-secret",
        )
        assert verify_passport(passport, "wrong-key") is False

    def test_tampered_passport(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-XXXX-XXXX", patient_data=SAMPLE_DATA,
            health_records=[], signing_key="key",
        )
        passport["aquifer_health_passport"]["patient"]["id"] = "tampered"
        assert verify_passport(passport, "key") is False


class TestPassportFormats:
    def test_to_text(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-ABCD-EFGH", patient_data=SAMPLE_DATA,
            health_records=SAMPLE_RECORDS, signing_key="key",
        )
        text = passport_to_text(passport)
        assert "AQUIFER HEALTH PASSPORT" in text
        assert "Maria Garcia" in text
        assert "Metformin" in text
        assert "Penicillin" in text
        assert "AQ-ABCD-EFGH" in text

    def test_to_html(self):
        passport = generate_passport(
            patient_id="p1", patient_email="a@b.com",
            share_key="AQ-ABCD-EFGH", patient_data=SAMPLE_DATA,
            health_records=SAMPLE_RECORDS, signing_key="key",
        )
        html = passport_to_html(passport)
        assert "<html>" in html
        assert "Maria Garcia" in html
        assert "Metformin" in html
        assert "aquifer.health" in html


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-passport"
    cfg.jwt_secret = "test-jwt-secret-passport"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _setup_verified_patient(client):
    reg = client.post("/api/v1/auth/register", json={
        "practice_name": "Passport Test", "email": "admin@test.com", "password": "SecurePass123",
    })
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/v1/patients/register", json={"email": "patient@test.com"}, headers=headers)
    patient_id = resp.json()["patient_id"]
    share_key = resp.json().get("share_key")

    otp = client.post(f"/api/v1/patients/{patient_id}/otp", headers=headers).json()["otp"]
    verify = client.post("/api/v1/patients/verify", json={
        "patient_id": patient_id, "otp": otp,
    }, headers=headers).json()

    if not share_key:
        share_key = verify.get("share_key")

    # Generate a fresh OTP for passport (previous one was consumed by verify)
    otp2 = client.post(f"/api/v1/patients/{patient_id}/otp", headers=headers).json()["otp"]

    return patient_id, share_key, otp2


class TestPassportAPI:
    def test_generate_passport_json(self, client):
        patient_id, share_key, otp = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/passport", json={
            "share_key": share_key, "otp": otp, "format": "json",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "aquifer_health_passport" in data
        assert "signature" in data

    def test_generate_passport_text(self, client):
        patient_id, share_key, otp = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/passport", json={
            "share_key": share_key, "otp": otp, "format": "text",
        })
        assert resp.status_code == 200
        assert "passport_text" in resp.json()
        assert "AQUIFER HEALTH PASSPORT" in resp.json()["passport_text"]

    def test_generate_passport_html(self, client):
        patient_id, share_key, otp = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/passport", json={
            "share_key": share_key, "otp": otp, "format": "html",
        })
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Aquifer Health Passport" in resp.text

    def test_passport_requires_otp(self, client):
        patient_id, share_key, otp = _setup_verified_patient(client)

        resp = client.post("/api/v1/patient/passport", json={
            "share_key": share_key, "otp": "000000", "format": "json",
        })
        assert resp.status_code == 403

    def test_verify_passport_endpoint(self, client):
        patient_id, share_key, otp = _setup_verified_patient(client)

        # Generate passport
        resp = client.post("/api/v1/patient/passport", json={
            "share_key": share_key, "otp": otp, "format": "json",
        })
        passport = resp.json()

        # Verify it
        resp = client.post("/api/v1/patient/passport/verify", json=passport)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
