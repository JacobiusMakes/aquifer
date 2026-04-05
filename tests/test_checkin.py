"""Tests for QR code check-in routes.

Tests the patient-facing self-service check-in flow:
- GET /checkin/{slug} — renders check-in page
- POST /checkin/{slug}/pull — executes record pull
- GET /api/v1/practice/qr-checkin — generates QR code (requires auth)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-checkin"
    cfg.jwt_secret = "test-jwt-secret-checkin"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _setup_practice(client, name="Test Dental", email="admin@test.com"):
    """Register a practice and return (token, practice_id, slug)."""
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": name, "email": email, "password": "SecurePass123",
    })
    assert resp.status_code == 201
    data = resp.json()
    return data["token"], data["practice_id"], data.get("slug", "")


def _setup_verified_patient(client, token):
    """Register and verify a patient, return (patient_id, share_key)."""
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/v1/patients/register", json={
        "email": "checkin-patient@example.com",
    }, headers=headers)
    assert resp.status_code == 201
    patient_id = resp.json()["patient_id"]
    share_key = resp.json().get("share_key")

    otp = client.post(f"/api/v1/patients/{patient_id}/otp", headers=headers).json()["otp"]
    verify = client.post("/api/v1/patients/verify", json={
        "patient_id": patient_id, "otp": otp,
    }, headers=headers).json()
    assert verify["verified"]

    if not share_key:
        share_key = verify.get("share_key")

    # Link patient to practice
    client.post(f"/api/v1/patients/{patient_id}/link",
                json={"source_file_hashes": ""}, headers=headers)

    return patient_id, share_key


class TestCheckinPage:
    def test_renders_checkin_page(self, client):
        token, pid, slug = _setup_practice(client)

        # Get slug from practice info
        headers = {"Authorization": f"Bearer {token}"}
        info = client.get("/api/v1/practice", headers=headers).json()
        slug = info["slug"]

        resp = client.get(f"/checkin/{slug}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Aquifer" in resp.text
        assert "share_key" in resp.text

    def test_404_for_unknown_slug(self, client):
        resp = client.get("/checkin/nonexistent-practice-slug")
        assert resp.status_code == 404


class TestCheckinPull:
    def test_pull_records_via_checkin(self, client):
        token, pid, _ = _setup_practice(client)
        headers = {"Authorization": f"Bearer {token}"}
        slug = client.get("/api/v1/practice", headers=headers).json()["slug"]

        patient_id, share_key = _setup_verified_patient(client, token)

        resp = client.post(f"/checkin/{slug}/pull", json={
            "share_key": share_key,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "patient_email_masked" in data
        assert "transfers" in data
        assert "total_tokens" in data
        assert data["total_tokens"] == 0  # No vault tokens stored in test

    def test_pull_invalid_share_key(self, client):
        token, _, _ = _setup_practice(client)
        headers = {"Authorization": f"Bearer {token}"}
        slug = client.get("/api/v1/practice", headers=headers).json()["slug"]

        resp = client.post(f"/checkin/{slug}/pull", json={
            "share_key": "AQ-ZZZZ-ZZZZ",
        })
        assert resp.status_code == 404

    def test_pull_bad_format(self, client):
        token, _, _ = _setup_practice(client)
        headers = {"Authorization": f"Bearer {token}"}
        slug = client.get("/api/v1/practice", headers=headers).json()["slug"]

        resp = client.post(f"/checkin/{slug}/pull", json={
            "share_key": "BADKEY",
        })
        assert resp.status_code == 422

    def test_pull_unknown_practice(self, client):
        resp = client.post("/checkin/nonexistent/pull", json={
            "share_key": "AQ-AAAA-BBBB",
        })
        assert resp.status_code == 404


class TestQRCodeGeneration:
    def test_qr_json_format(self, client):
        token, _, _ = _setup_practice(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/v1/practice/qr-checkin?format=json", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "checkin_url" in data
        assert "/checkin/" in data["checkin_url"]
        assert data["practice_name"]
        assert data["practice_slug"]

    def test_qr_svg_format(self, client):
        token, _, _ = _setup_practice(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/v1/practice/qr-checkin", headers=headers)
        assert resp.status_code == 200
        assert "svg" in resp.headers["content-type"]

    def test_qr_requires_auth(self, client):
        resp = client.get("/api/v1/practice/qr-checkin")
        assert resp.status_code == 401
