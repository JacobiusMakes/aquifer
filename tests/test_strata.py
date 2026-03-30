"""Tests for the Aquifer Strata API server.

Uses httpx TestClient to test the full API lifecycle:
  register → login → API key → upload → deid → inspect → rehydrate
"""

from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


@pytest.fixture
def strata_config(tmp_path):
    """Create a test config pointing at a temp directory."""
    cfg = StrataConfig(
        debug=True,
        data_dir=tmp_path / "strata_data",
        db_path=tmp_path / "strata_data" / "strata.db",
        master_key="test-master-key-not-for-production",
        jwt_secret="test-jwt-secret-not-for-production",
        use_ner=False,  # Faster tests
    )
    return cfg


@pytest.fixture
def app(strata_config):
    return create_app(strata_config)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# --- Helper ---

def register_and_login(client, practice_name="Test Dental", email="admin@test.com", password="securepass123"):
    """Register a practice and return the auth token."""
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": practice_name,
        "email": email,
        "password": password,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- Auth Tests ---

class TestAuth:
    def test_register(self, client):
        resp = register_and_login(client)
        assert resp["practice_id"]
        assert resp["user_id"]
        assert resp["email"] == "admin@test.com"
        assert resp["token"]

    def test_register_duplicate_email(self, client):
        register_and_login(client)
        resp = client.post("/api/v1/auth/register", json={
            "practice_name": "Another Practice",
            "email": "admin@test.com",
            "password": "securepass123",
        })
        assert resp.status_code == 409

    def test_register_short_password(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "practice_name": "Test", "email": "a@b.com", "password": "short",
        })
        assert resp.status_code == 422

    def test_login(self, client):
        register_and_login(client)
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "securepass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"]
        assert data["tier"] == "community"

    def test_login_wrong_password(self, client):
        register_and_login(client)
        resp = client.post("/api/v1/auth/login", json={
            "email": "admin@test.com", "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_api_key_lifecycle(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Create key
        resp = client.post("/api/v1/auth/api-keys", json={
            "name": "CI/CD Key", "scopes": "deid,files",
        }, headers=headers)
        assert resp.status_code == 201
        key_data = resp.json()
        assert key_data["key"].startswith("aq_")
        assert key_data["key_prefix"] == key_data["key"][:11]

        # List keys
        resp = client.get("/api/v1/auth/api-keys", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # Use API key for auth
        api_headers = {"Authorization": f"Bearer {key_data['key']}"}
        resp = client.get("/api/v1/practice", headers=api_headers)
        assert resp.status_code == 200

        # Revoke key
        resp = client.delete(f"/api/v1/auth/api-keys/{key_data['id']}", headers=headers)
        assert resp.status_code == 204

        # Key no longer works
        resp = client.get("/api/v1/practice", headers=api_headers)
        assert resp.status_code == 401

    def test_api_key_cannot_manage_keys_without_admin_scope(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post("/api/v1/auth/api-keys", json={
            "name": "Scoped Key", "scopes": "deid,files",
        }, headers=headers)
        key_data = resp.json()

        api_headers = {"Authorization": f"Bearer {key_data['key']}"}
        resp = client.get("/api/v1/auth/api-keys", headers=api_headers)
        assert resp.status_code == 403


# --- Health ---

class TestHealth:
    def test_health_no_auth(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aquifer-strata"

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/v1/files")
        assert resp.status_code == 401


# --- Practice ---

class TestPractice:
    def test_get_practice(self, client):
        reg = register_and_login(client)
        resp = client.get("/api/v1/practice", headers=auth_headers(reg["token"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Dental"
        assert data["tier"] == "community"
        assert "deid" in data["features"]

    def test_get_usage(self, client):
        reg = register_and_login(client)
        resp = client.get("/api/v1/practice/usage", headers=auth_headers(reg["token"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_days"] == 30
        assert isinstance(data["total_actions"], int)


# --- De-identification ---

SAMPLE_CLINICAL_NOTE = """
Patient: John Smith
DOB: 01/15/1982
SSN: 123-45-6789
Phone: (555) 123-4567

Chief Complaint: Patient presents with pain in tooth #19.
Treatment plan: Root canal therapy (D3330) followed by PFM crown (D2750).
Referring provider: Dr. Sarah Johnson, NPI: 1234567890
"""


class TestDeid:
    def test_deid_single_file(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post(
            "/api/v1/deid",
            files={"file": ("clinical_note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "completed"
        assert data["token_count"] > 0
        assert data["file_id"]
        assert data["aqf_hash"]

    def test_deid_unsupported_type(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post(
            "/api/v1/deid",
            files={"file": ("bad.exe", b"MZ\x00", "application/octet-stream")},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_deid_and_list_files(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Upload a file
        client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )

        # List files
        resp = client.get("/api/v1/files", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["files"]) >= 1

    def test_deid_inspect(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        deid_resp = client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )
        file_id = deid_resp.json()["file_id"]

        resp = client.get(f"/api/v1/files/{file_id}/inspect", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_count"] > 0
        assert data["integrity_valid"] is True
        # Tokens should have type/confidence but NOT phi_value
        for t in data["tokens"]:
            assert "token_id" in t
            assert "phi_type" in t
            assert "phi_value" not in t

    def test_deid_download(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        deid_resp = client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )
        file_id = deid_resp.json()["file_id"]

        resp = client.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert resp.status_code == 200
        assert len(resp.content) > 0
        # Should be a valid ZIP (AQF format)
        assert resp.content[:2] == b"PK"

    def test_deid_rehydrate(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        deid_resp = client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )
        file_id = deid_resp.json()["file_id"]

        resp = client.post(f"/api/v1/files/{file_id}/rehydrate", headers=headers)
        assert resp.status_code == 200
        text = resp.text
        # The rehydrated text should contain original PHI
        assert "John Smith" in text or "123-45-6789" in text

    def test_api_key_without_vault_scope_cannot_rehydrate(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post("/api/v1/auth/api-keys", json={
            "name": "Upload Key", "scopes": "deid,files",
        }, headers=headers)
        key_data = resp.json()
        api_headers = {"Authorization": f"Bearer {key_data['key']}"}

        deid_resp = client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=api_headers,
        )
        assert deid_resp.status_code == 201
        file_id = deid_resp.json()["file_id"]

        resp = client.post(f"/api/v1/files/{file_id}/rehydrate", headers=api_headers)
        assert resp.status_code == 403

    def test_deid_batch(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        files = [
            ("files", ("note1.txt", b"Patient: Jane Doe SSN: 999-88-7777", "text/plain")),
            ("files", ("note2.txt", b"Dr. Smith called (555) 111-2222", "text/plain")),
        ]
        resp = client.post("/api/v1/deid/batch", files=files, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["total"] == 2
        assert data["succeeded"] >= 1


# --- Vault ---

class TestVault:
    def test_vault_stats(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Upload a file first
        client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )

        resp = client.get("/api/v1/vault/stats", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] > 0
        assert data["total_files"] >= 1

    def test_vault_stats_require_vault_scope_for_api_keys(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.post("/api/v1/auth/api-keys", json={
            "name": "Files Key", "scopes": "files",
        }, headers=headers)
        key_data = resp.json()
        api_headers = {"Authorization": f"Bearer {key_data['key']}"}

        resp = client.get("/api/v1/vault/stats", headers=api_headers)
        assert resp.status_code == 403

    def test_vault_sync_status(self, client):
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.get("/api/v1/vault/sync/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_tokens" in data


# --- Multi-tenant isolation ---

class TestMultiTenant:
    def test_practices_isolated(self, client):
        """Files from one practice are not visible to another."""
        reg1 = register_and_login(client, "Practice A", "admin@a.com", "securepass1")
        reg2 = register_and_login(client, "Practice B", "admin@b.com", "securepass2")

        # Practice A uploads a file
        client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=auth_headers(reg1["token"]),
        )

        # Practice B should see 0 files
        resp = client.get("/api/v1/files", headers=auth_headers(reg2["token"]))
        assert resp.json()["total"] == 0

        # Practice A should see 1 file
        resp = client.get("/api/v1/files", headers=auth_headers(reg1["token"]))
        assert resp.json()["total"] == 1

    def test_cross_practice_file_access_blocked(self, client):
        """Cannot access another practice's files by ID."""
        reg1 = register_and_login(client, "Practice A", "admin@a.com", "securepass1")
        reg2 = register_and_login(client, "Practice B", "admin@b.com", "securepass2")

        deid_resp = client.post(
            "/api/v1/deid",
            files={"file": ("note.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=auth_headers(reg1["token"]),
        )
        file_id = deid_resp.json()["file_id"]

        # Practice B tries to access Practice A's file
        resp = client.get(f"/api/v1/files/{file_id}", headers=auth_headers(reg2["token"]))
        assert resp.status_code == 404


# --- Full lifecycle ---

class TestFullLifecycle:
    def test_end_to_end(self, client):
        """Full workflow: register → deid → inspect → download → rehydrate."""
        # 1. Register
        reg = register_and_login(client, "Smile Dental Group", "dr@smile.com", "secure123!!")
        headers = auth_headers(reg["token"])

        # 2. Check practice
        resp = client.get("/api/v1/practice", headers=headers)
        assert resp.json()["name"] == "Smile Dental Group"

        # 3. De-identify
        resp = client.post(
            "/api/v1/deid",
            files={"file": ("patient_record.txt", SAMPLE_CLINICAL_NOTE.encode(), "text/plain")},
            headers=headers,
        )
        assert resp.status_code == 201
        file_id = resp.json()["file_id"]
        assert resp.json()["token_count"] > 0

        # 4. Inspect (no PHI visible)
        resp = client.get(f"/api/v1/files/{file_id}/inspect", headers=headers)
        assert resp.status_code == 200
        inspect_data = resp.json()
        assert inspect_data["integrity_valid"]

        # 5. Download .aqf
        resp = client.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert resp.status_code == 200
        assert resp.content[:2] == b"PK"

        # 6. Rehydrate
        resp = client.post(f"/api/v1/files/{file_id}/rehydrate", headers=headers)
        assert resp.status_code == 200
        assert "John Smith" in resp.text or "123-45-6789" in resp.text

        # 7. Vault stats
        resp = client.get("/api/v1/vault/stats", headers=headers)
        assert resp.json()["total_tokens"] > 0

        # 8. Usage
        resp = client.get("/api/v1/practice/usage", headers=headers)
        assert resp.json()["total_actions"] > 0
