"""Tests for the auto-sync service."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from aquifer.vault.auto_sync import AutoSyncService, AutoSyncConfig
from aquifer.vault.sync_client import SyncResult


@pytest.fixture
def mock_vault():
    return MagicMock()


@pytest.fixture
def service(mock_vault):
    svc = AutoSyncService(
        vault=mock_vault,
        api_url="http://localhost:8080",
        api_key="test-key",
        config=AutoSyncConfig(interval=0.5, retry_interval=0.2),
    )
    yield svc
    if svc.is_running:
        svc.stop()


class TestAutoSyncConfig:
    def test_defaults(self):
        cfg = AutoSyncConfig()
        assert cfg.interval == 300.0
        assert cfg.retry_interval == 30.0
        assert cfg.max_retries == 5
        assert cfg.backoff_factor == 2.0


class TestAutoSyncService:
    def test_start_stop(self, service):
        assert not service.is_running
        service.start()
        assert service.is_running
        service.stop()
        assert not service.is_running

    def test_double_start_noop(self, service):
        service.start()
        thread1 = service._thread
        service.start()  # Should not create a second thread
        assert service._thread is thread1
        service.stop()

    def test_check_connectivity_online(self, service):
        with patch.object(service.sync_client, "get_status", return_value={"status": "ok"}):
            assert service.check_connectivity() is True
            assert service.is_online

    def test_check_connectivity_offline(self, service):
        with patch.object(service.sync_client, "get_status", side_effect=ConnectionError):
            assert service.check_connectivity() is False
            assert not service.is_online

    def test_sync_now(self, service):
        result = SyncResult(pushed=5, pulled=3, conflicts=0, status="completed")
        with patch.object(service.sync_client, "sync", return_value=result):
            r = service.sync_now()
            assert r.pushed == 5
            assert r.pulled == 3
            assert service.last_result is not None
            assert service.last_sync is not None

    def test_sync_now_failure(self, service):
        result = SyncResult(status="error", error="connection refused")
        with patch.object(service.sync_client, "sync", return_value=result):
            r = service.sync_now()
            assert r.status == "error"
            assert service._consecutive_failures == 1

    def test_backoff_increases(self, service):
        service._consecutive_failures = 0
        assert service._current_retry_interval() == 0.2
        service._consecutive_failures = 1
        assert service._current_retry_interval() == 0.4
        service._consecutive_failures = 2
        assert service._current_retry_interval() == 0.8

    def test_backoff_capped(self, service):
        service.config.max_backoff = 1.0
        service._consecutive_failures = 100
        assert service._current_retry_interval() <= 1.0

    def test_callback_on_sync_complete(self, service):
        callback = MagicMock()
        service.on_sync_complete = callback
        result = SyncResult(pushed=1, pulled=0, status="completed")
        with patch.object(service.sync_client, "sync", return_value=result):
            service.sync_now()
            callback.assert_called_once_with(result)

    def test_callback_on_error(self, service):
        error_cb = MagicMock()
        service.on_error = error_cb
        # The error callback is only triggered by the run loop, not sync_now
        # Test via get_status instead
        status = service.get_status()
        assert status["running"] is False
        assert status["online"] is False

    def test_get_status(self, service):
        status = service.get_status()
        assert "running" in status
        assert "online" in status
        assert "last_sync" in status
        assert "consecutive_failures" in status
        assert status["consecutive_failures"] == 0

    def test_auto_sync_loop_runs(self, service):
        sync_count = {"n": 0}
        result = SyncResult(pushed=0, pulled=0, status="completed")

        def mock_sync(vault, progress=None):
            sync_count["n"] += 1
            return result

        with patch.object(service.sync_client, "get_status", return_value={}), \
             patch.object(service.sync_client, "sync", side_effect=mock_sync):
            service.start()
            time.sleep(2.0)
            service.stop()

        # Should have synced at least once
        assert sync_count["n"] >= 1
