"""Token vault CRUD operations.

Stores token-to-PHI mappings with encrypted PHI values.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aquifer.core import VaultError
from aquifer.vault.encryption import derive_key, encrypt_value, decrypt_value
from aquifer.vault.models import get_connection, init_db, get_salt, ensure_schema_v2


@dataclass
class VaultToken:
    token_id: str
    phi_type: str
    phi_value: str  # Decrypted
    source_file_hash: str
    aqf_file_hash: Optional[str]
    confidence: float
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TokenVault:
    """Encrypted token vault backed by SQLite."""

    def __init__(self, db_path: Path, password: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._key: Optional[bytes] = None
        self._password = password

    def init(self) -> None:
        """Initialize a new vault database."""
        key, salt = derive_key(self._password)
        self._key = key
        self._conn = init_db(self.db_path, salt)

    def open(self) -> None:
        """Open an existing vault database.

        Validates schema integrity on open. Raises ValueError if the vault
        file is corrupt or missing required tables.
        """
        try:
            self._conn = get_connection(self.db_path)
        except sqlite3.DatabaseError as e:
            raise VaultError(
                f"Vault file is corrupt or not a valid database: {self.db_path}"
            ) from e

        # Validate required tables exist
        try:
            tables = {
                row[0] for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as e:
            self._conn.close()
            self._conn = None
            raise VaultError(f"Vault file is corrupt: {e}") from e

        required = {"vault_meta", "tokens", "files"}
        missing = required - tables
        if missing:
            self._conn.close()
            self._conn = None
            raise VaultError(
                f"Vault is invalid — missing required tables: {', '.join(sorted(missing))}. "
                f"The file may be corrupt or not an Aquifer vault."
            )

        salt = get_salt(self._conn)
        self._key, _ = derive_key(self._password, salt)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        if not self.db_path.exists():
            self.init()
        else:
            self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def _ensure_open(self):
        if self._conn is None or self._key is None:
            raise RuntimeError("Vault is not open. Call init() or open() first.")

    def store_token(
        self,
        token_id: str,
        phi_type: str,
        phi_value: str,
        source_file_hash: str,
        aqf_file_hash: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        """Store a token-to-PHI mapping in the vault."""
        self._ensure_open()
        encrypted = encrypt_value(phi_value, self._key)
        self._conn.execute(
            """INSERT OR REPLACE INTO tokens
            (token_id, phi_type, phi_value_encrypted, source_file_hash,
             aqf_file_hash, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (token_id, phi_type, encrypted, source_file_hash,
             aqf_file_hash, confidence),
        )
        self._conn.commit()

    def store_tokens_batch(
        self,
        tokens: list[tuple[str, str, str, str, str | None, float]],
    ) -> None:
        """Batch store multiple token mappings.

        All-or-nothing: if any token fails to encrypt or store,
        the entire batch is rolled back.
        """
        self._ensure_open()
        try:
            encrypted_rows = [
                (tid, ptype, encrypt_value(pval, self._key), fhash, ahash, conf)
                for tid, ptype, pval, fhash, ahash, conf in tokens
            ]
            self._conn.executemany(
                """INSERT OR REPLACE INTO tokens
                (token_id, phi_type, phi_value_encrypted, source_file_hash,
                 aqf_file_hash, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                encrypted_rows,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get_token(self, token_id: str) -> VaultToken | None:
        """Retrieve a single token by ID."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT * FROM tokens WHERE token_id = ?", (token_id,)
        ).fetchone()
        if row is None:
            return None
        return VaultToken(
            token_id=row["token_id"],
            phi_type=row["phi_type"],
            phi_value=decrypt_value(row["phi_value_encrypted"], self._key),
            source_file_hash=row["source_file_hash"],
            aqf_file_hash=row["aqf_file_hash"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"] if "updated_at" in row.keys() else None,
        )

    def get_tokens_for_file(self, source_file_hash: str) -> list[VaultToken]:
        """Retrieve all tokens for a given source file."""
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT * FROM tokens WHERE source_file_hash = ?",
            (source_file_hash,),
        ).fetchall()
        return [
            VaultToken(
                token_id=r["token_id"],
                phi_type=r["phi_type"],
                phi_value=decrypt_value(r["phi_value_encrypted"], self._key),
                source_file_hash=r["source_file_hash"],
                aqf_file_hash=r["aqf_file_hash"],
                confidence=r["confidence"],
                created_at=r["created_at"],
                updated_at=r["updated_at"] if "updated_at" in r.keys() else None,
            )
            for r in rows
        ]

    def delete_tokens_for_file(self, source_file_hash: str) -> int:
        """Delete all tokens for a given source file. Returns count deleted."""
        self._ensure_open()
        cursor = self._conn.execute(
            "DELETE FROM tokens WHERE source_file_hash = ?",
            (source_file_hash,),
        )
        self._conn.commit()
        return cursor.rowcount

    def store_file_record(
        self,
        file_hash: str,
        original_filename: str,
        source_type: str,
        aqf_hash: str | None = None,
        token_count: int = 0,
    ) -> None:
        """Store a processed file record."""
        self._ensure_open()
        self._conn.execute(
            """INSERT OR REPLACE INTO files
            (file_hash, original_filename, source_type, aqf_hash, token_count)
            VALUES (?, ?, ?, ?, ?)""",
            (file_hash, original_filename, source_type, aqf_hash, token_count),
        )
        self._conn.commit()

    def get_file_record(self, file_hash: str) -> dict | None:
        """Retrieve a file record by hash."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT * FROM files WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_files(self) -> list[dict]:
        """Get all processed file records."""
        self._ensure_open()
        rows = self._conn.execute("SELECT * FROM files ORDER BY processed_at DESC").fetchall()
        return [dict(r) for r in rows]

    # --- Sync support ---

    def ensure_sync_schema(self) -> None:
        """Ensure the vault has sync-related tables/columns (v2 schema)."""
        self._ensure_open()
        ensure_schema_v2(self._conn)

    def get_manifest(self) -> list[dict]:
        """Generate a sync manifest: metadata for all tokens (NO PHI values).

        Returns list of {token_id, phi_type, source_file_hash, updated_at}.
        """
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT token_id, phi_type, source_file_hash, updated_at FROM tokens"
        ).fetchall()
        return [
            {
                "token_id": r["token_id"],
                "phi_type": r["phi_type"],
                "source_file_hash": r["source_file_hash"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def export_tokens_encrypted(self, token_ids: list[str]) -> list[dict]:
        """Export tokens with encrypted PHI values for sync transfer.

        Returns list of {token_id, phi_type, phi_value_encrypted, source_file_hash,
        aqf_file_hash, confidence, created_at, updated_at}.
        PHI values remain encrypted with this vault's key.
        """
        self._ensure_open()
        if not token_ids:
            return []
        placeholders = ",".join("?" for _ in token_ids)
        rows = self._conn.execute(
            f"SELECT * FROM tokens WHERE token_id IN ({placeholders})",
            token_ids,
        ).fetchall()
        return [
            {
                "token_id": r["token_id"],
                "phi_type": r["phi_type"],
                "phi_value_encrypted": r["phi_value_encrypted"],
                "source_file_hash": r["source_file_hash"],
                "aqf_file_hash": r["aqf_file_hash"],
                "confidence": r["confidence"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"] if "updated_at" in r.keys() else None,
            }
            for r in rows
        ]

    def import_token_raw(
        self,
        token_id: str,
        phi_type: str,
        phi_value_encrypted: str,
        source_file_hash: str,
        aqf_file_hash: str | None = None,
        confidence: float = 1.0,
        updated_at: str | None = None,
    ) -> None:
        """Import a token with an already-encrypted PHI value (for sync).

        The encrypted value must be encrypted with THIS vault's key.
        """
        self._ensure_open()
        ts = updated_at or "CURRENT_TIMESTAMP"
        if updated_at:
            self._conn.execute(
                """INSERT OR REPLACE INTO tokens
                (token_id, phi_type, phi_value_encrypted, source_file_hash,
                 aqf_file_hash, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (token_id, phi_type, phi_value_encrypted, source_file_hash,
                 aqf_file_hash, confidence, updated_at),
            )
        else:
            self._conn.execute(
                """INSERT OR REPLACE INTO tokens
                (token_id, phi_type, phi_value_encrypted, source_file_hash,
                 aqf_file_hash, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (token_id, phi_type, phi_value_encrypted, source_file_hash,
                 aqf_file_hash, confidence),
            )
        self._conn.commit()

    def log_sync(
        self,
        direction: str,
        token_count: int,
        server_url: str,
        status: str = "completed",
        conflict_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Record a sync operation in the sync log."""
        self._ensure_open()
        self._conn.execute(
            """INSERT INTO sync_log
            (direction, token_count, conflict_count, server_url, status,
             error_message, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (direction, token_count, conflict_count, server_url, status, error_message),
        )
        self._conn.commit()

    def get_sync_history(self, limit: int = 20) -> list[dict]:
        """Get recent sync log entries."""
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT * FROM sync_log ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_sync(self, server_url: str | None = None) -> dict | None:
        """Get the most recent successful sync entry."""
        self._ensure_open()
        if server_url:
            row = self._conn.execute(
                "SELECT * FROM sync_log WHERE status = 'completed' AND server_url = ? "
                "ORDER BY completed_at DESC, id DESC LIMIT 1",
                (server_url,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM sync_log WHERE status = 'completed' "
                "ORDER BY completed_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def rekey(self, new_password: str) -> None:
        """Re-encrypt all vault tokens under a new password.

        Derives a fresh key+salt from new_password, decrypts every token with
        the current key, re-encrypts with the new key, and atomically commits
        the updated rows and new salt.  If anything fails the transaction is
        rolled back and the vault state is unchanged.

        Can be called via: ``aquifer vault rekey``
        """
        self._ensure_open()

        import base64

        # Derive new key with a fresh salt
        new_key, new_salt = derive_key(new_password)

        # Read all tokens (still encrypted with old key)
        rows = self._conn.execute(
            "SELECT token_id, phi_value_encrypted FROM tokens"
        ).fetchall()

        # Decrypt with old key, re-encrypt with new key
        reencrypted: list[tuple[str, str]] = []
        for row in rows:
            plaintext = decrypt_value(row["phi_value_encrypted"], self._key)
            reencrypted.append((encrypt_value(plaintext, new_key), row["token_id"]))

        # Commit everything atomically
        new_salt_b64 = base64.b64encode(new_salt).decode()
        try:
            with self._conn:
                self._conn.executemany(
                    "UPDATE tokens SET phi_value_encrypted = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE token_id = ?",
                    reencrypted,
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO vault_meta (key, value) VALUES (?, ?)",
                    ("salt", new_salt_b64),
                )
        except Exception:
            # _conn context manager already rolled back; re-raise unchanged
            raise

        # Update in-memory state only after successful commit
        self._key = new_key
        self._password = new_password

    @property
    def encryption_key(self) -> bytes | None:
        """Expose the vault encryption key (needed for sync re-encryption)."""
        return self._key

    def get_stats(self) -> dict:
        """Get vault statistics."""
        self._ensure_open()
        token_count = self._conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        file_count = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        type_counts = self._conn.execute(
            "SELECT phi_type, COUNT(*) as cnt FROM tokens GROUP BY phi_type"
        ).fetchall()
        return {
            "total_tokens": token_count,
            "total_files": file_count,
            "tokens_by_type": {r["phi_type"]: r["cnt"] for r in type_counts},
        }
