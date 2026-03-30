"""Server-side sync engine for vault synchronization.

Handles bidirectional sync between local CLI vaults and cloud Strata vaults.
Token PHI values are transferred encrypted and re-encrypted for the destination vault.
Conflict resolution uses last-write-wins based on updated_at timestamps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from aquifer.vault.encryption import encrypt_value, decrypt_value
from aquifer.vault.store import TokenVault

logger = logging.getLogger(__name__)


@dataclass
class SyncDiff:
    """Result of comparing local and cloud manifests."""
    # Token IDs that the local side needs to push to the cloud
    push_token_ids: list[str] = field(default_factory=list)
    # Token IDs that the local side needs to pull from the cloud
    pull_token_ids: list[str] = field(default_factory=list)
    # Conflicts: token exists on both sides with different updated_at
    conflicts: list[dict] = field(default_factory=list)
    # Summary counts
    local_only_count: int = 0
    cloud_only_count: int = 0
    conflict_count: int = 0
    in_sync_count: int = 0


@dataclass
class SyncResult:
    """Result of a sync operation."""
    pushed: int = 0
    pulled: int = 0
    conflicts: int = 0
    conflict_log: list[dict] = field(default_factory=list)
    status: str = "completed"
    error: str | None = None


class SyncManager:
    """Server-side sync engine that processes sync requests.

    Works with a cloud vault (opened by CloudVaultManager) and incoming
    manifests/tokens from a local CLI vault.
    """

    def __init__(self, cloud_vault: TokenVault):
        self.cloud_vault = cloud_vault
        # Ensure cloud vault has sync schema
        self.cloud_vault.ensure_sync_schema()

    def get_cloud_manifest(self) -> list[dict]:
        """Generate the cloud vault's manifest (no PHI values)."""
        return self.cloud_vault.get_manifest()

    def compute_diff(
        self,
        local_manifest: list[dict],
        cloud_manifest: list[dict] | None = None,
    ) -> SyncDiff:
        """Determine what tokens need to sync in each direction.

        Args:
            local_manifest: List of {token_id, phi_type, source_file_hash, updated_at}
                           from the local vault.
            cloud_manifest: Optional pre-computed cloud manifest.
                           If None, fetched from cloud vault.

        Returns:
            SyncDiff with lists of token_ids to push, pull, and conflicts.
        """
        if cloud_manifest is None:
            cloud_manifest = self.get_cloud_manifest()

        # Index manifests by token_id
        local_index: dict[str, dict] = {t["token_id"]: t for t in local_manifest}
        cloud_index: dict[str, dict] = {t["token_id"]: t for t in cloud_manifest}

        local_ids = set(local_index.keys())
        cloud_ids = set(cloud_index.keys())

        diff = SyncDiff()

        # Tokens only on local side -> need to push
        local_only = local_ids - cloud_ids
        diff.push_token_ids = list(local_only)
        diff.local_only_count = len(local_only)

        # Tokens only on cloud side -> need to pull
        cloud_only = cloud_ids - local_ids
        diff.pull_token_ids = list(cloud_only)
        diff.cloud_only_count = len(cloud_only)

        # Tokens on both sides -> check for conflicts
        common = local_ids & cloud_ids
        for token_id in common:
            local_updated = local_index[token_id].get("updated_at") or ""
            cloud_updated = cloud_index[token_id].get("updated_at") or ""

            if local_updated == cloud_updated:
                # In sync, nothing to do
                diff.in_sync_count += 1
            else:
                # Conflict — last write wins
                local_ts = _parse_timestamp(local_updated)
                cloud_ts = _parse_timestamp(cloud_updated)

                conflict_entry = {
                    "token_id": token_id,
                    "local_updated_at": local_updated,
                    "cloud_updated_at": cloud_updated,
                }

                if local_ts and cloud_ts:
                    if local_ts > cloud_ts:
                        # Local is newer -> push to cloud
                        conflict_entry["resolution"] = "local_wins"
                        diff.push_token_ids.append(token_id)
                    else:
                        # Cloud is newer -> pull to local
                        conflict_entry["resolution"] = "cloud_wins"
                        diff.pull_token_ids.append(token_id)
                elif local_ts:
                    conflict_entry["resolution"] = "local_wins"
                    diff.push_token_ids.append(token_id)
                elif cloud_ts:
                    conflict_entry["resolution"] = "cloud_wins"
                    diff.pull_token_ids.append(token_id)
                else:
                    # Both timestamps missing — treat as conflict, local wins
                    conflict_entry["resolution"] = "local_wins"
                    diff.push_token_ids.append(token_id)

                diff.conflicts.append(conflict_entry)
                diff.conflict_count += 1

        return diff

    def receive_tokens(
        self,
        tokens: list[dict],
        local_vault_key: bytes,
    ) -> int:
        """Receive tokens from local vault and store in cloud vault.

        Tokens arrive encrypted with the local vault's key.
        They are decrypted and re-encrypted with the cloud vault's key.

        Args:
            tokens: List of token dicts with phi_value_encrypted (local key).
            local_vault_key: The Fernet key used by the local vault.

        Returns:
            Number of tokens stored.
        """
        cloud_key = self.cloud_vault.encryption_key
        count = 0
        for token in tokens:
            # Decrypt with local key, re-encrypt with cloud key
            try:
                phi_value = decrypt_value(token["phi_value_encrypted"], local_vault_key)
                cloud_encrypted = encrypt_value(phi_value, cloud_key)
            except Exception as e:
                logger.error(f"Failed to re-encrypt token {token['token_id']}: {e}")
                continue

            self.cloud_vault.import_token_raw(
                token_id=token["token_id"],
                phi_type=token["phi_type"],
                phi_value_encrypted=cloud_encrypted,
                source_file_hash=token["source_file_hash"],
                aqf_file_hash=token.get("aqf_file_hash"),
                confidence=token.get("confidence", 1.0),
                updated_at=token.get("updated_at"),
            )
            count += 1

        return count

    def export_tokens_for_pull(
        self,
        token_ids: list[str],
        local_vault_key: bytes,
    ) -> list[dict]:
        """Export cloud tokens re-encrypted for the local vault.

        Tokens are decrypted with cloud key and re-encrypted with local key
        so the local vault can import them directly.

        Args:
            token_ids: Token IDs to export.
            local_vault_key: The Fernet key used by the local vault.

        Returns:
            List of token dicts with phi_value_encrypted (local key).
        """
        cloud_key = self.cloud_vault.encryption_key
        cloud_tokens = self.cloud_vault.export_tokens_encrypted(token_ids)

        result = []
        for token in cloud_tokens:
            try:
                phi_value = decrypt_value(token["phi_value_encrypted"], cloud_key)
                local_encrypted = encrypt_value(phi_value, local_vault_key)
            except Exception as e:
                logger.error(f"Failed to re-encrypt token {token['token_id']}: {e}")
                continue

            result.append({
                "token_id": token["token_id"],
                "phi_type": token["phi_type"],
                "phi_value_encrypted": local_encrypted,
                "source_file_hash": token["source_file_hash"],
                "aqf_file_hash": token.get("aqf_file_hash"),
                "confidence": token.get("confidence", 1.0),
                "created_at": token.get("created_at"),
                "updated_at": token.get("updated_at"),
            })

        return result

    def get_sync_status(self) -> dict:
        """Get sync status for the cloud vault."""
        stats = self.cloud_vault.get_stats()
        last_sync = self.cloud_vault.get_last_sync()
        history = self.cloud_vault.get_sync_history(limit=5)

        return {
            "total_tokens": stats["total_tokens"],
            "total_files": stats["total_files"],
            "last_sync": {
                "direction": last_sync["direction"],
                "token_count": last_sync["token_count"],
                "conflict_count": last_sync["conflict_count"],
                "status": last_sync["status"],
                "completed_at": last_sync["completed_at"],
            } if last_sync else None,
            "recent_syncs": [
                {
                    "direction": s["direction"],
                    "token_count": s["token_count"],
                    "conflict_count": s["conflict_count"],
                    "status": s["status"],
                    "completed_at": s["completed_at"],
                }
                for s in history
            ],
        }


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse a SQLite timestamp string to datetime."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None
