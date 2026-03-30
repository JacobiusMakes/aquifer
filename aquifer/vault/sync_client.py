"""Client-side sync logic for vault synchronization.

Talks to the Strata API to sync local vault tokens with the cloud.
PHI values are transferred encrypted — re-encryption happens server-side.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from aquifer.vault.store import TokenVault

logger = logging.getLogger(__name__)

# Type for progress callbacks: (step_name, current, total)
ProgressCallback = Callable[[str, int, int], None]


@dataclass
class SyncResult:
    """Result of a sync operation."""
    pushed: int = 0
    pulled: int = 0
    conflicts: int = 0
    conflict_details: list[dict] = field(default_factory=list)
    status: str = "completed"
    error: str | None = None


class VaultSyncClient:
    """Client that syncs a local TokenVault with the Strata cloud API."""

    # Maximum tokens per batch request
    BATCH_SIZE = 500

    def __init__(
        self,
        api_url: str,
        api_key: str,
        timeout: float = 60.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.api_url,
            headers=self._headers(),
            timeout=self.timeout,
        )

    def _vault_key_b64(self, vault: TokenVault) -> str:
        """Get the vault's Fernet key as base64 string for API transport."""
        key = vault.encryption_key
        if key is None:
            raise RuntimeError("Vault is not open — cannot access encryption key.")
        # The Fernet key is already base64-encoded bytes, just decode to str
        return key.decode() if isinstance(key, bytes) else key

    def push(
        self,
        vault: TokenVault,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Push local tokens to the cloud vault.

        1. Generate local manifest
        2. Send to server to compute diff
        3. Push tokens that the server needs

        Args:
            vault: Open local TokenVault.
            progress: Optional callback for progress reporting.

        Returns:
            SyncResult with push statistics.
        """
        vault.ensure_sync_schema()
        result = SyncResult()

        try:
            with self._client() as client:
                # Step 1: Generate local manifest
                if progress:
                    progress("generating_manifest", 0, 0)
                manifest = vault.get_manifest()

                # Step 2: Send manifest to server, get diff
                if progress:
                    progress("computing_diff", 0, len(manifest))
                resp = client.post(
                    "/api/v1/vault/sync/manifest",
                    json={
                        "manifest": manifest,
                        "vault_key": self._vault_key_b64(vault),
                        "direction": "push",
                    },
                )
                resp.raise_for_status()
                diff = resp.json()

                push_ids = diff.get("push_token_ids", [])
                result.conflicts = diff.get("conflict_count", 0)
                result.conflict_details = diff.get("conflicts", [])

                if not push_ids:
                    if progress:
                        progress("push_complete", 0, 0)
                    result.status = "completed"
                    vault.log_sync("push", 0, self.api_url, "completed",
                                   conflict_count=result.conflicts)
                    return result

                # Step 3: Push tokens in batches
                total = len(push_ids)
                pushed = 0
                for i in range(0, total, self.BATCH_SIZE):
                    batch_ids = push_ids[i:i + self.BATCH_SIZE]
                    tokens = vault.export_tokens_encrypted(batch_ids)

                    if progress:
                        progress("pushing", pushed, total)

                    resp = client.post(
                        "/api/v1/vault/sync/push",
                        json={
                            "tokens": tokens,
                            "vault_key": self._vault_key_b64(vault),
                        },
                    )
                    resp.raise_for_status()
                    pushed += resp.json().get("stored", len(batch_ids))

                result.pushed = pushed
                result.status = "completed"

                if progress:
                    progress("push_complete", pushed, total)

                vault.log_sync("push", pushed, self.api_url, "completed",
                               conflict_count=result.conflicts)

        except httpx.HTTPStatusError as e:
            result.status = "error"
            result.error = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Push failed: {result.error}")
            vault.log_sync("push", result.pushed, self.api_url, "error",
                           error_message=result.error)
        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.error(f"Push failed: {e}")
            vault.log_sync("push", result.pushed, self.api_url, "error",
                           error_message=result.error)

        return result

    def pull(
        self,
        vault: TokenVault,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Pull cloud tokens to the local vault.

        1. Generate local manifest
        2. Send to server to compute diff
        3. Pull tokens that local is missing

        Args:
            vault: Open local TokenVault.
            progress: Optional callback for progress reporting.

        Returns:
            SyncResult with pull statistics.
        """
        vault.ensure_sync_schema()
        result = SyncResult()

        try:
            with self._client() as client:
                # Step 1: Generate local manifest
                if progress:
                    progress("generating_manifest", 0, 0)
                manifest = vault.get_manifest()

                # Step 2: Send manifest to server, get diff
                if progress:
                    progress("computing_diff", 0, len(manifest))
                resp = client.post(
                    "/api/v1/vault/sync/manifest",
                    json={
                        "manifest": manifest,
                        "vault_key": self._vault_key_b64(vault),
                        "direction": "pull",
                    },
                )
                resp.raise_for_status()
                diff = resp.json()

                pull_ids = diff.get("pull_token_ids", [])
                result.conflicts = diff.get("conflict_count", 0)
                result.conflict_details = diff.get("conflicts", [])

                if not pull_ids:
                    if progress:
                        progress("pull_complete", 0, 0)
                    result.status = "completed"
                    vault.log_sync("pull", 0, self.api_url, "completed",
                                   conflict_count=result.conflicts)
                    return result

                # Step 3: Pull tokens in batches
                total = len(pull_ids)
                pulled = 0
                for i in range(0, total, self.BATCH_SIZE):
                    batch_ids = pull_ids[i:i + self.BATCH_SIZE]

                    if progress:
                        progress("pulling", pulled, total)

                    resp = client.post(
                        "/api/v1/vault/sync/pull",
                        json={
                            "token_ids": batch_ids,
                            "vault_key": self._vault_key_b64(vault),
                        },
                    )
                    resp.raise_for_status()
                    tokens = resp.json().get("tokens", [])

                    # Import tokens into local vault
                    for token in tokens:
                        vault.import_token_raw(
                            token_id=token["token_id"],
                            phi_type=token["phi_type"],
                            phi_value_encrypted=token["phi_value_encrypted"],
                            source_file_hash=token["source_file_hash"],
                            aqf_file_hash=token.get("aqf_file_hash"),
                            confidence=token.get("confidence", 1.0),
                            updated_at=token.get("updated_at"),
                        )
                    pulled += len(tokens)

                result.pulled = pulled
                result.status = "completed"

                if progress:
                    progress("pull_complete", pulled, total)

                vault.log_sync("pull", pulled, self.api_url, "completed",
                               conflict_count=result.conflicts)

        except httpx.HTTPStatusError as e:
            result.status = "error"
            result.error = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Pull failed: {result.error}")
            vault.log_sync("pull", result.pulled, self.api_url, "error",
                           error_message=result.error)
        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.error(f"Pull failed: {e}")
            vault.log_sync("pull", result.pulled, self.api_url, "error",
                           error_message=result.error)

        return result

    def sync(
        self,
        vault: TokenVault,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Bidirectional sync: push local changes, then pull cloud changes.

        1. Generate local manifest
        2. Send to server to compute full diff
        3. Push tokens cloud is missing
        4. Pull tokens local is missing
        5. Conflicts resolved by last-write-wins

        Args:
            vault: Open local TokenVault.
            progress: Optional callback for progress reporting.

        Returns:
            SyncResult with combined push/pull statistics.
        """
        vault.ensure_sync_schema()
        result = SyncResult()

        try:
            with self._client() as client:
                # Step 1: Generate local manifest
                if progress:
                    progress("generating_manifest", 0, 0)
                manifest = vault.get_manifest()

                # Step 2: Send manifest to server, get full diff
                if progress:
                    progress("computing_diff", 0, len(manifest))
                resp = client.post(
                    "/api/v1/vault/sync/manifest",
                    json={
                        "manifest": manifest,
                        "vault_key": self._vault_key_b64(vault),
                        "direction": "sync",
                    },
                )
                resp.raise_for_status()
                diff = resp.json()

                push_ids = diff.get("push_token_ids", [])
                pull_ids = diff.get("pull_token_ids", [])
                result.conflicts = diff.get("conflict_count", 0)
                result.conflict_details = diff.get("conflicts", [])

                # Step 3: Push tokens to cloud
                if push_ids:
                    total_push = len(push_ids)
                    pushed = 0
                    for i in range(0, total_push, self.BATCH_SIZE):
                        batch_ids = push_ids[i:i + self.BATCH_SIZE]
                        tokens = vault.export_tokens_encrypted(batch_ids)

                        if progress:
                            progress("pushing", pushed, total_push)

                        resp = client.post(
                            "/api/v1/vault/sync/push",
                            json={
                                "tokens": tokens,
                                "vault_key": self._vault_key_b64(vault),
                            },
                        )
                        resp.raise_for_status()
                        pushed += resp.json().get("stored", len(batch_ids))

                    result.pushed = pushed
                    if progress:
                        progress("push_complete", pushed, total_push)

                # Step 4: Pull tokens from cloud
                if pull_ids:
                    total_pull = len(pull_ids)
                    pulled = 0
                    for i in range(0, total_pull, self.BATCH_SIZE):
                        batch_ids = pull_ids[i:i + self.BATCH_SIZE]

                        if progress:
                            progress("pulling", pulled, total_pull)

                        resp = client.post(
                            "/api/v1/vault/sync/pull",
                            json={
                                "token_ids": batch_ids,
                                "vault_key": self._vault_key_b64(vault),
                            },
                        )
                        resp.raise_for_status()
                        tokens = resp.json().get("tokens", [])

                        for token in tokens:
                            vault.import_token_raw(
                                token_id=token["token_id"],
                                phi_type=token["phi_type"],
                                phi_value_encrypted=token["phi_value_encrypted"],
                                source_file_hash=token["source_file_hash"],
                                aqf_file_hash=token.get("aqf_file_hash"),
                                confidence=token.get("confidence", 1.0),
                                updated_at=token.get("updated_at"),
                            )
                        pulled += len(tokens)

                    result.pulled = pulled
                    if progress:
                        progress("pull_complete", pulled, total_pull)

                result.status = "completed"

                if progress:
                    progress("sync_complete", result.pushed + result.pulled, 0)

                vault.log_sync(
                    "sync",
                    result.pushed + result.pulled,
                    self.api_url,
                    "completed",
                    conflict_count=result.conflicts,
                )

        except httpx.HTTPStatusError as e:
            result.status = "error"
            result.error = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Sync failed: {result.error}")
            vault.log_sync(
                "sync", result.pushed + result.pulled, self.api_url,
                "error", error_message=result.error,
            )
        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.error(f"Sync failed: {e}")
            vault.log_sync(
                "sync", result.pushed + result.pulled, self.api_url,
                "error", error_message=result.error,
            )

        return result

    def get_status(self) -> dict:
        """Get sync status from the server.

        Returns:
            Dict with server-side sync status.
        """
        with self._client() as client:
            resp = client.get("/api/v1/vault/sync/status")
            resp.raise_for_status()
            return resp.json()
