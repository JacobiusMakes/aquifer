"""Auto-sync service for offline-first vault operation.

Monitors connectivity and automatically syncs when the server becomes
reachable. Runs as a background daemon thread in the CLI or can be
invoked standalone.

Usage:
    service = AutoSyncService(vault, api_url, api_key)
    service.start()  # Non-blocking, starts daemon thread
    ...
    service.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from aquifer.vault.store import TokenVault
from aquifer.vault.sync_client import SyncResult, VaultSyncClient

logger = logging.getLogger(__name__)


@dataclass
class AutoSyncConfig:
    """Configuration for the auto-sync service."""
    # How often to attempt sync (seconds)
    interval: float = 300.0  # 5 minutes
    # How often to check connectivity when offline (seconds)
    retry_interval: float = 30.0
    # Maximum consecutive failures before backing off
    max_retries: int = 5
    # Backoff multiplier for consecutive failures
    backoff_factor: float = 2.0
    # Maximum backoff interval (seconds)
    max_backoff: float = 3600.0  # 1 hour


class AutoSyncService:
    """Background service that automatically syncs vault with cloud."""

    def __init__(
        self,
        vault: TokenVault,
        api_url: str,
        api_key: str,
        config: AutoSyncConfig | None = None,
        on_sync_complete: callable | None = None,
        on_error: callable | None = None,
    ):
        self.vault = vault
        self.sync_client = VaultSyncClient(api_url, api_key)
        self.config = config or AutoSyncConfig()
        self.on_sync_complete = on_sync_complete
        self.on_error = on_error

        self._running = False
        self._thread: threading.Thread | None = None
        self._consecutive_failures = 0
        self._last_sync: datetime | None = None
        self._last_result: SyncResult | None = None
        self._online = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_online(self) -> bool:
        return self._online

    @property
    def last_sync(self) -> datetime | None:
        return self._last_sync

    @property
    def last_result(self) -> SyncResult | None:
        return self._last_result

    def start(self) -> None:
        """Start the auto-sync daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Auto-sync service started (interval=%ss)", self.config.interval)

    def stop(self) -> None:
        """Stop the auto-sync service."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Auto-sync service stopped")

    def sync_now(self) -> SyncResult:
        """Trigger an immediate sync (can be called from any thread)."""
        return self._do_sync()

    def check_connectivity(self) -> bool:
        """Check if the Strata server is reachable."""
        try:
            status = self.sync_client.get_status()
            self._online = True
            return True
        except Exception:
            self._online = False
            return False

    def _run_loop(self) -> None:
        """Main loop: check connectivity, sync when online."""
        while self._running:
            try:
                if self.check_connectivity():
                    self._do_sync()
                    self._consecutive_failures = 0
                    self._sleep(self.config.interval)
                else:
                    logger.debug("Server unreachable, will retry in %ss",
                                 self._current_retry_interval())
                    self._sleep(self._current_retry_interval())
            except Exception as e:
                logger.error("Auto-sync error: %s", e)
                self._consecutive_failures += 1
                if self.on_error:
                    self.on_error(e)
                self._sleep(self._current_retry_interval())

    def _do_sync(self) -> SyncResult:
        """Execute a bidirectional sync."""
        logger.info("Auto-sync: starting bidirectional sync")
        result = self.sync_client.sync(self.vault)

        self._last_sync = datetime.now(timezone.utc)
        self._last_result = result

        if result.status == "completed":
            self._consecutive_failures = 0
            logger.info(
                "Auto-sync: completed (pushed=%d, pulled=%d, conflicts=%d)",
                result.pushed, result.pulled, result.conflicts,
            )
        else:
            self._consecutive_failures += 1
            logger.warning("Auto-sync: failed — %s", result.error)

        if self.on_sync_complete:
            self.on_sync_complete(result)

        return result

    def _current_retry_interval(self) -> float:
        """Calculate current retry interval with exponential backoff."""
        if self._consecutive_failures == 0:
            return self.config.retry_interval
        backoff = self.config.retry_interval * (
            self.config.backoff_factor ** min(self._consecutive_failures, self.config.max_retries)
        )
        return min(backoff, self.config.max_backoff)

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep — checks _running flag every second."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    def get_status(self) -> dict:
        """Get current auto-sync status."""
        return {
            "running": self._running,
            "online": self._online,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "last_result": {
                "pushed": self._last_result.pushed,
                "pulled": self._last_result.pulled,
                "conflicts": self._last_result.conflicts,
                "status": self._last_result.status,
            } if self._last_result else None,
            "consecutive_failures": self._consecutive_failures,
            "next_retry_interval": self._current_retry_interval(),
        }
