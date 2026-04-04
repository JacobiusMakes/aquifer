"""Cloud vault management — per-practice isolated vaults.

Each practice gets its own directory:
    strata_data/practices/{practice_id}/
        vault.aqv       — SQLite token vault (Fernet-encrypted values)
        aqf/            — Stored .aqf output files
        uploads/        — Temporary upload staging

The vault encryption password for each practice is a server-managed Fernet key,
encrypted at rest with the server master key. The practice's users never see it.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from aquifer.strata.auth import decrypt_vault_key, encrypt_vault_key
from aquifer.strata.config import StrataConfig
from aquifer.vault.store import TokenVault


class CloudVaultManager:
    """Manages per-practice vault lifecycle."""

    def __init__(self, config: StrataConfig):
        self.config = config
        self.practices_dir = config.data_dir / "practices"
        self._vault_cache: dict[str, TokenVault] = {}

    def practice_dir(self, practice_id: str) -> Path:
        return self.practices_dir / practice_id

    def vault_path(self, practice_id: str) -> Path:
        return self.practice_dir(practice_id) / "vault.aqv"

    def aqf_dir(self, practice_id: str) -> Path:
        return self.practice_dir(practice_id) / "aqf"

    def upload_dir(self, practice_id: str) -> Path:
        return self.practice_dir(practice_id) / "uploads"

    def init_practice(self, practice_id: str, vault_password: str) -> None:
        """Initialize storage for a new practice."""
        pdir = self.practice_dir(practice_id)
        pdir.mkdir(parents=True, exist_ok=True)
        self.aqf_dir(practice_id).mkdir(exist_ok=True)
        self.upload_dir(practice_id).mkdir(exist_ok=True)

        # Create vault
        vault = TokenVault(self.vault_path(practice_id), vault_password)
        vault.init()
        vault.close()

    def _decrypt_with_rotation(
        self, encrypted_vault_key: str, practice_id: str, db=None,
    ) -> str:
        """Decrypt a vault key, falling back to previous_master_key on failure.

        If the previous key succeeds, re-encrypts with the current key and
        updates the DB so the rotation is transparent on the next open.
        """
        try:
            return decrypt_vault_key(encrypted_vault_key, self.config.master_key)
        except Exception:
            if not self.config.previous_master_key:
                raise
            logger.info("Current master key failed for practice %s — trying previous key", practice_id)
            vault_key = decrypt_vault_key(encrypted_vault_key, self.config.previous_master_key)
            if db is not None:
                new_encrypted = encrypt_vault_key(vault_key, self.config.master_key)
                db.update_practice_vault_key(practice_id, new_encrypted)
                logger.info("Re-encrypted vault key for practice %s with new master key", practice_id)
            return vault_key

    def open_vault(
        self, practice_id: str, encrypted_vault_key: str, db=None,
    ) -> TokenVault:
        """Open a practice's vault using the encrypted vault key.

        Decrypts the vault key using the server master key, then opens the vault.
        Falls back to previous_master_key if configured (key rotation support).
        Caches open vaults for the lifetime of this manager.
        """
        if practice_id in self._vault_cache:
            return self._vault_cache[practice_id]

        vault_password = self._decrypt_with_rotation(encrypted_vault_key, practice_id, db=db)
        vault = TokenVault(self.vault_path(practice_id), vault_password)
        vault.open()
        self._vault_cache[practice_id] = vault
        return vault

    def close_vault(self, practice_id: str) -> None:
        """Close a cached vault."""
        vault = self._vault_cache.pop(practice_id, None)
        if vault:
            vault.close()

    def close_all(self) -> None:
        """Close all cached vaults."""
        for vault in self._vault_cache.values():
            vault.close()
        self._vault_cache.clear()

    def delete_practice(self, practice_id: str) -> None:
        """Delete all data for a practice. IRREVERSIBLE."""
        self.close_vault(practice_id)
        pdir = self.practice_dir(practice_id)
        if pdir.exists():
            shutil.rmtree(pdir)

    def get_practice_stats(self, practice_id: str) -> dict:
        """Get storage stats for a practice."""
        pdir = self.practice_dir(practice_id)
        if not pdir.exists():
            return {"exists": False}

        aqf_files = list(self.aqf_dir(practice_id).glob("*.aqf"))
        total_size = sum(f.stat().st_size for f in aqf_files)

        vault_size = 0
        vp = self.vault_path(practice_id)
        if vp.exists():
            vault_size = vp.stat().st_size

        return {
            "exists": True,
            "aqf_file_count": len(aqf_files),
            "aqf_total_bytes": total_size,
            "vault_size_bytes": vault_size,
        }
