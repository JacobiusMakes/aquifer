"""Token vault CRUD operations.

Stores token-to-PHI mappings with encrypted PHI values.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aquifer.vault.encryption import derive_key, encrypt_value, decrypt_value
from aquifer.vault.models import get_connection, init_db, get_salt


@dataclass
class VaultToken:
    token_id: str
    phi_type: str
    phi_value: str  # Decrypted
    source_file_hash: str
    aqf_file_hash: Optional[str]
    confidence: float
    created_at: Optional[str] = None


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
        """Open an existing vault database."""
        self._conn = get_connection(self.db_path)
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
             aqf_file_hash, confidence)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (token_id, phi_type, encrypted, source_file_hash,
             aqf_file_hash, confidence),
        )
        self._conn.commit()

    def store_tokens_batch(
        self,
        tokens: list[tuple[str, str, str, str, str | None, float]],
    ) -> None:
        """Batch store multiple token mappings."""
        self._ensure_open()
        encrypted_rows = [
            (tid, ptype, encrypt_value(pval, self._key), fhash, ahash, conf)
            for tid, ptype, pval, fhash, ahash, conf in tokens
        ]
        self._conn.executemany(
            """INSERT OR REPLACE INTO tokens
            (token_id, phi_type, phi_value_encrypted, source_file_hash,
             aqf_file_hash, confidence)
            VALUES (?, ?, ?, ?, ?, ?)""",
            encrypted_rows,
        )
        self._conn.commit()

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
