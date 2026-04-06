"""Tests for cross-practice analytics engine and API routes."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.analytics.engine import AnalyticsEngine, AnalyticsSnapshot, K_ANONYMITY_THRESHOLD
from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-analytics"
    cfg.jwt_secret = "test-jwt-secret-analytics"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _register_practice(client, name, email):
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": name, "email": email, "password": "SecurePass123",
    })
    assert resp.status_code == 201
    return resp.json()


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_practices(client, count=4):
    """Register multiple practices and return their tokens."""
    practices = []
    for i in range(count):
        reg = _register_practice(client, f"Practice {i}", f"p{i}@test.com")
        practices.append(reg)
    return practices


def _seed_files(db, practice_id, count=5, domain="demographics"):
    """Create fake processed file records."""
    for i in range(count):
        file_id = str(uuid.uuid4())
        db.create_file_record(
            id=file_id,
            practice_id=practice_id,
            original_filename=f"file_{i}.pdf",
            source_type="pdf",
            source_hash=f"hash_{uuid.uuid4().hex[:8]}",
            file_size_bytes=1024,
            data_domain=domain,
        )
        db.update_file_record(file_id, status="completed", token_count=10)


# ---------------------------------------------------------------------------
# TestAnalyticsEngine
# ---------------------------------------------------------------------------

class TestAnalyticsEngine:
    def test_snapshot_below_k_threshold(self, client):
        """With fewer than K practices, snapshot is suppressed."""
        reg = _register_practice(client, "Solo Practice", "solo@test.com")
        db = client.app.state.db

        engine = AnalyticsEngine(db)
        snapshot = engine.generate_snapshot()
        assert snapshot.participating_practices < K_ANONYMITY_THRESHOLD
        assert "insufficient" in snapshot.suppressed[0].lower()

    def test_snapshot_with_enough_practices(self, client):
        """With K+ practices, snapshot returns real data."""
        practices = _seed_practices(client, count=4)
        db = client.app.state.db

        # Seed some files
        for p in practices:
            _seed_files(db, p["practice_id"], count=3, domain="dental")

        engine = AnalyticsEngine(db)
        snapshot = engine.generate_snapshot()
        assert snapshot.participating_practices >= K_ANONYMITY_THRESHOLD
        assert snapshot.total_files_processed == 12  # 4 practices * 3 files
        assert snapshot.total_tokens_generated == 120  # 12 files * 10 tokens
        assert "dental" in snapshot.domain_distribution

    def test_domain_distribution_percentages(self, client):
        practices = _seed_practices(client, count=3)
        db = client.app.state.db

        _seed_files(db, practices[0]["practice_id"], count=3, domain="dental")
        _seed_files(db, practices[1]["practice_id"], count=3, domain="demographics")
        _seed_files(db, practices[2]["practice_id"], count=4, domain="dental")

        engine = AnalyticsEngine(db)
        snapshot = engine.generate_snapshot()
        # 7 dental, 3 demographics = 70%, 30%
        assert snapshot.domain_distribution["dental"] == 70.0
        assert snapshot.domain_distribution["demographics"] == 30.0

    def test_practice_benchmarks(self, client):
        practices = _seed_practices(client, count=4)
        db = client.app.state.db

        # Give one practice more files than others
        _seed_files(db, practices[0]["practice_id"], count=20, domain="dental")
        for p in practices[1:]:
            _seed_files(db, p["practice_id"], count=5)

        engine = AnalyticsEngine(db)
        benchmarks = engine.get_practice_benchmarks(practices[0]["practice_id"])
        assert benchmarks["files_processed"] == 20
        assert benchmarks["network_avg_files"] > 5
        assert benchmarks["files_percentile"] > 50  # top practice

    def test_benchmarks_below_threshold(self, client):
        reg = _register_practice(client, "Alone", "alone@test.com")
        db = client.app.state.db

        engine = AnalyticsEngine(db)
        benchmarks = engine.get_practice_benchmarks(reg["practice_id"])
        assert "error" in benchmarks

    def test_practice_size_buckets(self, client):
        practices = _seed_practices(client, count=4)
        db = client.app.state.db

        for p in practices:
            _seed_files(db, p["practice_id"], count=5)

        engine = AnalyticsEngine(db)
        snapshot = engine.generate_snapshot()
        # All practices are small (5 files each)
        assert snapshot.practice_size_buckets.get("small (0-100)") == 4

    def test_snapshot_to_dict(self, client):
        practices = _seed_practices(client, count=3)
        db = client.app.state.db

        engine = AnalyticsEngine(db)
        snapshot = engine.generate_snapshot()
        d = snapshot.to_dict()
        assert "privacy" in d
        assert d["privacy"]["phi_exposed"] is False
        assert d["privacy"]["k_anonymity_threshold"] == K_ANONYMITY_THRESHOLD


# ---------------------------------------------------------------------------
# TestAnalyticsAPI
# ---------------------------------------------------------------------------

class TestAnalyticsAPI:
    def test_snapshot_endpoint(self, client):
        practices = _seed_practices(client, count=4)
        headers = _headers(practices[0]["token"])

        resp = client.get("/api/v1/analytics/snapshot", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "participating_practices" in data
        assert "privacy" in data

    def test_benchmarks_endpoint(self, client):
        practices = _seed_practices(client, count=4)
        db = client.app.state.db
        for p in practices:
            _seed_files(db, p["practice_id"], count=3)

        headers = _headers(practices[0]["token"])
        resp = client.get("/api/v1/analytics/benchmarks", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "files_processed" in data
        assert "network_avg_files" in data

    def test_trends_endpoint(self, client):
        practices = _seed_practices(client, count=4)
        headers = _headers(practices[0]["token"])

        resp = client.get("/api/v1/analytics/trends", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "monthly_volume" in data
        assert "network_growth" in data

    def test_trends_invalid_months(self, client):
        reg = _register_practice(client, "Test", "trend@test.com")
        headers = _headers(reg["token"])

        resp = client.get("/api/v1/analytics/trends?months=0", headers=headers)
        assert resp.status_code == 400

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/analytics/snapshot")
        assert resp.status_code == 401
