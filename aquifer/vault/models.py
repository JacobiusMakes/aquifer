"""SQLite schema and connection management for the token vault."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vault_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token_id TEXT PRIMARY KEY,
    phi_type TEXT NOT NULL,
    phi_value_encrypted TEXT NOT NULL,
    source_file_hash TEXT NOT NULL,
    aqf_file_hash TEXT,
    confidence REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    file_hash TEXT PRIMARY KEY,
    original_filename TEXT,
    source_type TEXT,
    aqf_hash TEXT,
    token_count INTEGER,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tokens_source ON tokens(source_file_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_type ON tokens(phi_type);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a SQLite connection to the vault database."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path, salt: bytes) -> sqlite3.Connection:
    """Initialize a new vault database with schema and store the salt."""
    import base64

    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)

    # Store salt for key derivation
    salt_b64 = base64.b64encode(salt).decode()
    conn.execute(
        "INSERT OR REPLACE INTO vault_meta (key, value) VALUES (?, ?)",
        ("salt", salt_b64),
    )
    conn.execute(
        "INSERT OR REPLACE INTO vault_meta (key, value) VALUES (?, ?)",
        ("version", "1"),
    )
    conn.commit()
    return conn


def get_salt(conn: sqlite3.Connection) -> bytes:
    """Retrieve the salt from vault metadata."""
    import base64

    row = conn.execute(
        "SELECT value FROM vault_meta WHERE key = ?", ("salt",)
    ).fetchone()
    if row is None:
        raise ValueError("Vault is not initialized (no salt found)")
    return base64.b64decode(row["value"])
