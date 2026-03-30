"""Server-side SQLite database for Strata metadata.

Stores: practices, users, API keys, processed files, usage logs.
Vault token data stays in per-practice vault files — this DB only holds
server orchestration metadata.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS practices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    tier TEXT NOT NULL DEFAULT 'community',
    license_key TEXT,
    vault_key_encrypted TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    name TEXT,
    scopes TEXT NOT NULL DEFAULT 'deid,files',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processed_files (
    id TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    original_filename TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    aqf_hash TEXT,
    aqf_storage_path TEXT,
    token_count INTEGER DEFAULT 0,
    file_size_bytes INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    practice_id TEXT NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    file_id TEXT,
    bytes_processed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_practice ON users(practice_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_api_keys_practice ON api_keys(practice_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_files_practice ON processed_files(practice_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON processed_files(status);
CREATE INDEX IF NOT EXISTS idx_usage_practice ON usage_log(practice_id);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);
"""


class StrataDB:
    """Server metadata database."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # --- Practices ---

    def create_practice(
        self, id: str, name: str, slug: str, vault_key_encrypted: str,
        tier: str = "community", license_key: str | None = None,
    ) -> dict:
        self.conn.execute(
            """INSERT INTO practices (id, name, slug, vault_key_encrypted, tier, license_key)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (id, name, slug, vault_key_encrypted, tier, license_key),
        )
        self.conn.commit()
        return self.get_practice(id)

    def get_practice(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM practices WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def get_practice_by_slug(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM practices WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    # --- Users ---

    def create_user(
        self, id: str, practice_id: str, email: str, password_hash: str,
        role: str = "user",
    ) -> dict:
        self.conn.execute(
            """INSERT INTO users (id, practice_id, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)""",
            (id, practice_id, email, password_hash, role),
        )
        self.conn.commit()
        return self.get_user(id)

    def get_user(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    # --- API Keys ---

    def create_api_key(
        self, id: str, practice_id: str, user_id: str,
        key_hash: str, key_prefix: str, name: str | None = None,
        scopes: str = "deid,files",
    ) -> dict:
        self.conn.execute(
            """INSERT INTO api_keys (id, practice_id, user_id, key_hash, key_prefix, name, scopes)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id, practice_id, user_id, key_hash, key_prefix, name, scopes),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM api_keys WHERE id = ?", (id,)).fetchone())

    def get_api_key_by_hash(self, key_hash: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1", (key_hash,)
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (dict(row)["id"],),
            )
            self.conn.commit()
        return dict(row) if row else None

    def list_api_keys(self, practice_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, key_prefix, name, scopes, is_active, last_used_at, created_at "
            "FROM api_keys WHERE practice_id = ? ORDER BY created_at DESC",
            (practice_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, practice_id: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND practice_id = ?",
            (key_id, practice_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # --- Processed Files ---

    def create_file_record(
        self, id: str, practice_id: str, original_filename: str,
        source_type: str, source_hash: str, file_size_bytes: int,
    ) -> dict:
        self.conn.execute(
            """INSERT INTO processed_files
            (id, practice_id, original_filename, source_type, source_hash, file_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (id, practice_id, original_filename, source_type, source_hash, file_size_bytes),
        )
        self.conn.commit()
        return self.get_file_record(id)

    def update_file_record(
        self, id: str, *, status: str | None = None, aqf_hash: str | None = None,
        aqf_storage_path: str | None = None, token_count: int | None = None,
        error_message: str | None = None,
    ) -> dict | None:
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "completed":
                updates.append("completed_at = CURRENT_TIMESTAMP")
        if aqf_hash is not None:
            updates.append("aqf_hash = ?")
            params.append(aqf_hash)
        if aqf_storage_path is not None:
            updates.append("aqf_storage_path = ?")
            params.append(aqf_storage_path)
        if token_count is not None:
            updates.append("token_count = ?")
            params.append(token_count)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            return self.get_file_record(id)
        params.append(id)
        self.conn.execute(
            f"UPDATE processed_files SET {', '.join(updates)} WHERE id = ?", params,
        )
        self.conn.commit()
        return self.get_file_record(id)

    def get_file_record(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM processed_files WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def list_files(self, practice_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, original_filename, source_type, source_hash, aqf_hash,
                      token_count, file_size_bytes, status, created_at, completed_at
            FROM processed_files WHERE practice_id = ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (practice_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_files(self, practice_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM processed_files WHERE practice_id = ?", (practice_id,),
        ).fetchone()[0]

    # --- Usage Logging ---

    def log_usage(
        self, practice_id: str, action: str,
        user_id: str | None = None, file_id: str | None = None,
        bytes_processed: int = 0,
    ) -> None:
        self.conn.execute(
            """INSERT INTO usage_log (practice_id, user_id, action, file_id, bytes_processed)
            VALUES (?, ?, ?, ?, ?)""",
            (practice_id, user_id, action, file_id, bytes_processed),
        )
        self.conn.commit()

    def get_usage_stats(self, practice_id: str, days: int = 30) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) as total_actions,
                      SUM(bytes_processed) as total_bytes,
                      COUNT(DISTINCT file_id) as unique_files
            FROM usage_log
            WHERE practice_id = ?
              AND created_at >= datetime('now', ?)""",
            (practice_id, f"-{days} days"),
        ).fetchone()
        by_action = self.conn.execute(
            """SELECT action, COUNT(*) as count
            FROM usage_log
            WHERE practice_id = ? AND created_at >= datetime('now', ?)
            GROUP BY action""",
            (practice_id, f"-{days} days"),
        ).fetchall()
        return {
            "period_days": days,
            "total_actions": row["total_actions"] or 0,
            "total_bytes": row["total_bytes"] or 0,
            "unique_files": row["unique_files"] or 0,
            "by_action": {r["action"]: r["count"] for r in by_action},
        }
