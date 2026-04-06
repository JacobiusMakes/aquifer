"""Tests for the background job system — batch-async, job status, WebSocket progress."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.jobs import JobRunner, FileSpec, JobProgress
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-jobs"
    cfg.jwt_secret = "test-jwt-secret-jobs"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _register(client):
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": "Job Test Practice",
        "email": "jobs@test.com",
        "password": "SecurePass123",
    })
    assert resp.status_code == 201
    return resp.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# TestJobProgress
# ---------------------------------------------------------------------------

class TestJobProgress:
    def test_percent_calculation(self):
        p = JobProgress(job_id="j1", status="processing", total_files=10, completed_files=3, failed_files=2)
        assert p.percent == 50.0

    def test_percent_zero_files(self):
        p = JobProgress(job_id="j1", status="completed", total_files=0)
        assert p.percent == 100.0

    def test_to_dict(self):
        p = JobProgress(job_id="j1", status="processing", total_files=5, completed_files=2)
        d = p.to_dict()
        assert d["job_id"] == "j1"
        assert d["status"] == "processing"
        assert d["percent"] == 40.0


# ---------------------------------------------------------------------------
# TestJobDB
# ---------------------------------------------------------------------------

class TestJobDB:
    def test_create_and_get_job(self, client):
        reg = _register(client)
        db = client.app.state.db
        job_id = str(uuid.uuid4())
        job = db.create_job(
            id=job_id, practice_id=reg["practice_id"], user_id=reg["user_id"],
            job_type="batch_deid", total_files=5,
        )
        assert job["id"] == job_id
        assert job["status"] == "pending"
        assert job["total_files"] == 5

    def test_update_job_progress(self, client):
        reg = _register(client)
        db = client.app.state.db
        job_id = str(uuid.uuid4())
        db.create_job(id=job_id, practice_id=reg["practice_id"],
                       user_id=reg["user_id"], job_type="batch_deid", total_files=3)

        db.update_job_progress(job_id, status="processing", current_file="test.pdf")
        job = db.get_job(job_id)
        assert job["status"] == "processing"
        assert job["current_file"] == "test.pdf"
        assert job["started_at"] is not None

        db.update_job_progress(job_id, completed_files=2, failed_files=1, status="completed")
        job = db.get_job(job_id)
        assert job["completed_files"] == 2
        assert job["failed_files"] == 1
        assert job["completed_at"] is not None

    def test_list_jobs(self, client):
        reg = _register(client)
        db = client.app.state.db
        pid = reg["practice_id"]

        for i in range(3):
            db.create_job(id=str(uuid.uuid4()), practice_id=pid,
                          user_id=reg["user_id"], job_type="batch_deid", total_files=i+1)

        jobs = db.list_jobs(pid)
        assert len(jobs) == 3


# ---------------------------------------------------------------------------
# TestBatchAsyncEndpoint
# ---------------------------------------------------------------------------

class TestBatchAsyncEndpoint:
    def test_submit_batch_async_returns_202(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        files = [
            ("files", ("test1.txt", b"Patient Name: Jane Doe", "text/plain")),
            ("files", ("test2.txt", b"SSN: 123-45-6789", "text/plain")),
        ]
        resp = client.post("/api/v1/deid/batch-async", files=files, headers=headers)
        assert resp.status_code == 202
        data = resp.json()
        assert data["total_files"] == 2
        assert data["status"] == "pending"
        assert "job_id" in data
        assert "ws_url" in data
        assert "poll_url" in data

        # Job should exist in DB
        job = client.app.state.db.get_job(data["job_id"])
        assert job is not None
        assert job["total_files"] == 2

    def test_submit_unsupported_file_type(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        files = [("files", ("script.py", b"print('hello')", "text/plain"))]
        resp = client.post("/api/v1/deid/batch-async", files=files, headers=headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TestJobStatusEndpoint
# ---------------------------------------------------------------------------

class TestJobStatusEndpoint:
    def test_get_job_not_found(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        resp = client.get("/api/v1/deid/jobs/nonexistent-id", headers=headers)
        assert resp.status_code == 404

    def test_list_jobs_empty(self, client):
        reg = _register(client)
        headers = _headers(reg["token"])

        resp = client.get("/api/v1/deid/jobs", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []


# ---------------------------------------------------------------------------
# TestWebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_ws_nonexistent_job(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/jobs/nonexistent"):
                pass

    def test_ws_completed_job_sends_state_and_closes(self, client):
        db = client.app.state.db
        reg = _register(client)

        job_id = str(uuid.uuid4())
        db.create_job(id=job_id, practice_id=reg["practice_id"],
                      user_id=reg["user_id"], job_type="batch_deid", total_files=1)
        db.update_job_progress(job_id, status="completed", completed_files=1)

        with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
            msg = ws.receive_json()
            assert msg["status"] == "completed"
            assert msg["percent"] == 100.0
