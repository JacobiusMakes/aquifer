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

import shutil
from pathlib import Path
from typing import Optional

from aquifer.strata.auth import decrypt_vault_key
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

    def open_vault(
        self, practice_id: str, encrypted_vault_key: str,
    ) -> TokenVault:
        """Open a practice's vault using the encrypted vault key.

        Decrypts the vault key using the server master key, then opens the vault.
        Caches open vaults for the lifetime of this manager.
        """
        if practice_id in self._vault_cache:
            return self._vault_cache[practice_id]

        vault_password = decrypt_vault_key(encrypted_vault_key, self.config.master_key)
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
