"""Tests for the patient mobile PWA."""

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
    cfg.master_key = "test-master-key-pwa"
    cfg.jwt_secret = "test-jwt-secret-pwa"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


class TestPWA:
    def test_app_serves_html(self, client):
        resp = client.get("/app")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Aquifer" in resp.text
        assert "serviceWorker" in resp.text

    def test_manifest_json(self, client):
        resp = client.get("/app/manifest.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Aquifer"
        assert data["display"] == "standalone"

    def test_service_worker(self, client):
        resp = client.get("/app/sw.js")
        assert resp.status_code == 200
        assert "aquifer-v1" in resp.text

    def test_icons(self, client):
        resp = client.get("/app/icon-192.svg")
        assert resp.status_code == 200
        assert "svg" in resp.headers["content-type"]

        resp = client.get("/app/icon-512.svg")
        assert resp.status_code == 200

    def test_no_auth_required(self, client):
        # PWA pages should be accessible without authentication
        resp = client.get("/app")
        assert resp.status_code == 200

    def test_has_all_pages(self, client):
        resp = client.get("/app")
        text = resp.text
        assert "page-home" in text
        assert "page-checkin" in text
        assert "page-mydata" in text
        assert "page-share" in text
        assert "page-scan" in text

    def test_has_navigation(self, client):
        resp = client.get("/app")
        assert "nav-home" in resp.text
        assert "nav-checkin" in resp.text
        assert "nav-mydata" in resp.text
        assert "nav-share" in resp.text
