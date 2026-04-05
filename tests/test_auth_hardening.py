"""Tests for email verification, password change, and password reset flows."""

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
    cfg.master_key = "test-master-key-auth-hardening"
    cfg.jwt_secret = "test-jwt-secret-auth-hardening"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _register(client, email="admin@test.com", password="SecurePass123"):
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": "Test Practice",
        "email": email,
        "password": password,
    })
    assert resp.status_code == 201
    return resp.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# TestEmailVerification
# ---------------------------------------------------------------------------

class TestEmailVerification:
    def test_register_creates_unverified_user(self, client):
        reg = _register(client)
        # User should exist but email not verified
        headers = _headers(reg["token"])
        # The user can still use the API (verification is advisory for now)
        resp = client.get("/api/v1/practice", headers=headers)
        assert resp.status_code == 200

    def test_verify_email_with_token(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        # Get the verification token by resending
        resp = client.post("/api/v1/auth/resend-verification", headers=headers)
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Verify the email
        resp = client.get(f"/api/v1/auth/verify-email?token={token}")
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    def test_verify_email_invalid_token(self, client):
        resp = client.get("/api/v1/auth/verify-email?token=bogus-token")
        assert resp.status_code == 400

    def test_resend_verification_already_verified(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        # Verify first
        resp = client.post("/api/v1/auth/resend-verification", headers=headers)
        token = resp.json()["token"]
        client.get(f"/api/v1/auth/verify-email?token={token}")

        # Resend should say already verified
        resp = client.post("/api/v1/auth/resend-verification", headers=headers)
        assert resp.status_code == 200
        assert "already verified" in resp.json()["message"]


# ---------------------------------------------------------------------------
# TestChangePassword
# ---------------------------------------------------------------------------

class TestChangePassword:
    def test_change_password_success(self, client):
        reg = _register(client, password="OldPassword123")
        headers = _headers(reg["token"])

        resp = client.post("/api/v1/auth/change-password", json={
            "current_password": "OldPassword123",
            "new_password": "NewPassword456",
        }, headers=headers)
        assert resp.status_code == 200
        assert "changed" in resp.json()["message"]

        # Login with new password
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com",
            "password": "NewPassword456",
        })
        assert resp.status_code == 200

    def test_change_password_wrong_current(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        resp = client.post("/api/v1/auth/change-password", json={
            "current_password": "WrongPassword99",
            "new_password": "NewPassword456",
        }, headers=headers)
        assert resp.status_code == 401

    def test_change_password_same_as_current(self, client):
        reg = _register(client, password="SamePassword123")
        headers = _headers(reg["token"])

        resp = client.post("/api/v1/auth/change-password", json={
            "current_password": "SamePassword123",
            "new_password": "SamePassword123",
        }, headers=headers)
        assert resp.status_code == 400

    def test_change_password_weak_new_password(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        resp = client.post("/api/v1/auth/change-password", json={
            "current_password": "SecurePass123",
            "new_password": "weak",
        }, headers=headers)
        assert resp.status_code == 422  # Pydantic validation


# ---------------------------------------------------------------------------
# TestPasswordReset
# ---------------------------------------------------------------------------

class TestPasswordReset:
    def test_request_reset_existing_email(self, client):
        _register(client, email="reset@test.com")

        resp = client.post("/api/v1/auth/request-reset", json={
            "email": "reset@test.com",
        })
        assert resp.status_code == 200
        # Always returns success (no email enumeration)
        assert "sent" in resp.json()["message"]

    def test_request_reset_nonexistent_email(self, client):
        resp = client.post("/api/v1/auth/request-reset", json={
            "email": "nobody@test.com",
        })
        # Still returns 200 (anti-enumeration)
        assert resp.status_code == 200

    def test_reset_password_with_token(self, client):
        _register(client, email="resetme@test.com", password="OldPassword123")

        # Request reset
        client.post("/api/v1/auth/request-reset", json={"email": "resetme@test.com"})

        # Get the token from the DB directly (in real flow it would be emailed)
        from aquifer.strata.database import StrataDB
        db = client.app.state.db
        user = db.get_user_by_email("resetme@test.com")
        reset_token = user["password_reset_token"]
        assert reset_token is not None

        # Reset password
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": reset_token,
            "new_password": "ResetPassword789",
        })
        assert resp.status_code == 200

        # Login with new password
        resp = client.post("/api/v1/auth/login", json={
            "email": "resetme@test.com",
            "password": "ResetPassword789",
        })
        assert resp.status_code == 200

        # Old password should no longer work
        resp = client.post("/api/v1/auth/login", json={
            "email": "resetme@test.com",
            "password": "OldPassword123",
        })
        assert resp.status_code == 401

    def test_reset_password_invalid_token(self, client):
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": "bogus-token",
            "new_password": "NewPassword123",
        })
        assert resp.status_code == 400

    def test_reset_token_single_use(self, client):
        _register(client, email="oneuse@test.com", password="OldPassword123")
        client.post("/api/v1/auth/request-reset", json={"email": "oneuse@test.com"})

        db = client.app.state.db
        user = db.get_user_by_email("oneuse@test.com")
        reset_token = user["password_reset_token"]

        # First reset succeeds
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": reset_token,
            "new_password": "FirstReset123",
        })
        assert resp.status_code == 200

        # Second use of same token fails
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": reset_token,
            "new_password": "SecondReset456",
        })
        assert resp.status_code == 400
