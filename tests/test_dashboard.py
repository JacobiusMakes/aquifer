"""Tests for the Strata hosted web dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


@pytest.fixture
def app(tmp_path):
    cfg = StrataConfig(
        debug=True,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "strata.db",
        master_key="test-key",
        jwt_secret="test-jwt",
        use_ner=False,
    )
    return create_app(cfg)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def register(client, practice="Test Dental", email="admin@test.com", password="securepass1"):
    return client.post("/dashboard/register", data={
        "practice_name": practice, "email": email, "password": password,
    }, follow_redirects=False)


class TestAuthPages:
    def test_login_page_loads(self, client):
        resp = client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Sign in" in resp.text

    def test_register_page_loads(self, client):
        resp = client.get("/dashboard/register")
        assert resp.status_code == 200
        assert "Create your account" in resp.text

    def test_register_flow(self, client):
        resp = register(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"
        assert "aq_session" in resp.cookies

    def test_login_flow(self, client):
        register(client)
        resp = client.post("/dashboard/login", data={
            "email": "admin@test.com", "password": "securepass1",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert "aq_session" in resp.cookies

    def test_login_wrong_password(self, client):
        register(client)
        resp = client.post("/dashboard/login", data={
            "email": "admin@test.com", "password": "wrong",
        })
        assert resp.status_code == 200
        assert "Invalid" in resp.text

    def test_logout(self, client):
        register(client)
        resp = client.get("/dashboard/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/login"

    def test_unauthenticated_redirect(self, client):
        resp = client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]


class TestDashboardPages:
    def _login(self, client):
        register(client)
        # The register response sets the cookie, and client persists cookies
        return client

    def test_home_page(self, client):
        self._login(client)
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "Test Dental" in resp.text

    def test_upload_page(self, client):
        self._login(client)
        resp = client.get("/dashboard/upload")
        assert resp.status_code == 200
        assert "Upload" in resp.text

    def test_files_page_empty(self, client):
        self._login(client)
        resp = client.get("/dashboard/files")
        assert resp.status_code == 200
        assert "No files" in resp.text or "0 files" in resp.text

    def test_settings_page(self, client):
        self._login(client)
        resp = client.get("/dashboard/settings")
        assert resp.status_code == 200
        assert "API Keys" in resp.text
        assert "Test Dental" in resp.text

    def test_upload_and_view_file(self, client):
        self._login(client)

        # Upload via dashboard
        note = b"Patient: John Smith DOB: 01/15/1982 SSN: 123-45-6789"
        resp = client.post("/dashboard/upload",
                           files={"file": ("note.txt", note, "text/plain")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_count"] > 0
        file_id = data["file_id"]

        # Files page should show it
        resp = client.get("/dashboard/files")
        assert "note.txt" in resp.text

        # File detail page
        resp = client.get(f"/dashboard/files/{file_id}")
        assert resp.status_code == 200
        assert "note.txt" in resp.text
        assert "VALID" in resp.text

    def test_download_aqf(self, client):
        self._login(client)
        note = b"Patient: Jane Doe SSN: 999-88-7777"
        resp = client.post("/dashboard/upload",
                           files={"file": ("test.txt", note, "text/plain")})
        file_id = resp.json()["file_id"]

        resp = client.get(f"/dashboard/files/{file_id}/download")
        assert resp.status_code == 200
        assert resp.content[:2] == b"PK"

    def test_rehydrate(self, client):
        self._login(client)
        note = b"Patient: John Smith SSN: 123-45-6789"
        resp = client.post("/dashboard/upload",
                           files={"file": ("test.txt", note, "text/plain")})
        file_id = resp.json()["file_id"]

        resp = client.post(f"/dashboard/files/{file_id}/rehydrate")
        assert resp.status_code == 200
        assert "John Smith" in resp.text or "123-45-6789" in resp.text

    def test_create_api_key(self, client):
        self._login(client)
        resp = client.post("/dashboard/settings/api-keys",
                           json={"name": "Test Key"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"].startswith("aq_")

    def test_revoke_api_key(self, client):
        self._login(client)
        resp = client.post("/dashboard/settings/api-keys",
                           json={"name": "Temp Key"})
        key_id = resp.json()["id"]

        resp = client.delete(f"/dashboard/settings/api-keys/{key_id}")
        assert resp.status_code == 200
