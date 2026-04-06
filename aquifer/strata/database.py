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
    email_verified INTEGER NOT NULL DEFAULT 0,
    verification_token TEXT,
    verification_token_expires TIMESTAMP,
    password_reset_token TEXT,
    password_reset_expires TIMESTAMP,
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
    data_domain TEXT,
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
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_files_practice ON processed_files(practice_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON processed_files(status);
CREATE INDEX IF NOT EXISTS idx_files_source_hash ON processed_files(source_hash);
CREATE INDEX IF NOT EXISTS idx_usage_practice ON usage_log(practice_id);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_action ON usage_log(action);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    practice_id TEXT NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    detail TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_practice ON audit_log(practice_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0,
    otp_hash TEXT,
    otp_expires_at TIMESTAMP,
    share_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_practice_links (
    patient_id TEXT NOT NULL REFERENCES patients(id),
    practice_id TEXT NOT NULL REFERENCES practices(id),
    source_file_hashes TEXT,
    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (patient_id, practice_id)
);

CREATE TABLE IF NOT EXISTS consent_records (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    source_practice_id TEXT NOT NULL REFERENCES practices(id),
    target_practice_id TEXT NOT NULL REFERENCES practices(id),
    scope TEXT NOT NULL DEFAULT 'all',
    status TEXT NOT NULL DEFAULT 'pending',
    authorized_at TIMESTAMP,
    expires_at TIMESTAMP,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transfer_log (
    id TEXT PRIMARY KEY,
    consent_id TEXT NOT NULL REFERENCES consent_records(id),
    source_practice_id TEXT NOT NULL,
    target_practice_id TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_patients_email ON patients(email);
CREATE INDEX IF NOT EXISTS idx_patients_share_key ON patients(share_key);
CREATE INDEX IF NOT EXISTS idx_patient_links_patient ON patient_practice_links(patient_id);
CREATE INDEX IF NOT EXISTS idx_patient_links_practice ON patient_practice_links(practice_id);
CREATE INDEX IF NOT EXISTS idx_consent_patient ON consent_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_consent_source ON consent_records(source_practice_id);
CREATE INDEX IF NOT EXISTS idx_consent_target ON consent_records(target_practice_id);
CREATE INDEX IF NOT EXISTS idx_consent_status ON consent_records(status);
CREATE INDEX IF NOT EXISTS idx_transfer_consent ON transfer_log(consent_id);

CREATE TABLE IF NOT EXISTS patient_health_data (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    domain TEXT NOT NULL,
    field_type TEXT NOT NULL,
    label TEXT NOT NULL,
    value_encrypted TEXT NOT NULL,
    recorded_date TEXT,
    source TEXT NOT NULL,
    source_system TEXT NOT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_health_data_patient ON patient_health_data(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_data_domain ON patient_health_data(domain);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    user_id TEXT NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'batch_deid',
    status TEXT NOT NULL DEFAULT 'pending',
    total_files INTEGER NOT NULL DEFAULT 0,
    completed_files INTEGER NOT NULL DEFAULT 0,
    failed_files INTEGER NOT NULL DEFAULT 0,
    current_file TEXT,
    error_message TEXT,
    result_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_practice ON jobs(practice_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Migrations for existing databases
CREATE INDEX IF NOT EXISTS idx_files_data_domain ON processed_files(data_domain);
"""

# Run after schema to add columns that may not exist in older databases.
_MIGRATIONS = [
    "ALTER TABLE processed_files ADD COLUMN data_domain TEXT",
    "ALTER TABLE patients ADD COLUMN share_key TEXT",
]


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
        # Apply additive migrations — ignore errors for columns that already exist
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
                self._conn.commit()
            except Exception:
                pass

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
        data_domain: str | None = None,
    ) -> dict:
        self.conn.execute(
            """INSERT INTO processed_files
            (id, practice_id, original_filename, source_type, source_hash, file_size_bytes, data_domain)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id, practice_id, original_filename, source_type, source_hash, file_size_bytes, data_domain),
        )
        self.conn.commit()
        return self.get_file_record(id)

    def update_file_record(
        self, id: str, *, status: str | None = None, aqf_hash: str | None = None,
        aqf_storage_path: str | None = None, token_count: int | None = None,
        error_message: str | None = None, data_domain: str | None = None,
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
        if data_domain is not None:
            updates.append("data_domain = ?")
            params.append(data_domain)
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

    def get_file_record_by_hash(self, practice_id: str, source_hash: str) -> dict | None:
        """Return the processed_files row for a given practice + source file hash."""
        row = self.conn.execute(
            "SELECT * FROM processed_files WHERE practice_id = ? AND source_hash = ? LIMIT 1",
            (practice_id, source_hash),
        ).fetchone()
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

    def delete_file_record(self, id: str, practice_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM processed_files WHERE id = ? AND practice_id = ?",
            (id, practice_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_user_password(self, id: str, password_hash: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_verification_token(self, user_id: str, token: str, expires_at: str) -> None:
        self.conn.execute(
            "UPDATE users SET verification_token = ?, verification_token_expires = ? WHERE id = ?",
            (token, expires_at, user_id),
        )
        self.conn.commit()

    def verify_user_email(self, user_id: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE users SET email_verified = 1, verification_token = NULL, "
            "verification_token_expires = NULL WHERE id = ?",
            (user_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_user_by_verification_token(self, token: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE verification_token = ?", (token,),
        ).fetchone()
        return dict(row) if row else None

    def set_password_reset_token(self, user_id: str, token: str, expires_at: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_reset_token = ?, password_reset_expires = ? WHERE id = ?",
            (token, expires_at, user_id),
        )
        self.conn.commit()

    def get_user_by_reset_token(self, token: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE password_reset_token = ?", (token,),
        ).fetchone()
        return dict(row) if row else None

    def clear_reset_token(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_reset_token = NULL, password_reset_expires = NULL WHERE id = ?",
            (user_id,),
        )
        self.conn.commit()

    # --- Jobs ---

    def create_job(self, id: str, practice_id: str, user_id: str,
                   job_type: str, total_files: int) -> dict:
        self.conn.execute(
            """INSERT INTO jobs (id, practice_id, user_id, job_type, total_files)
            VALUES (?, ?, ?, ?, ?)""",
            (id, practice_id, user_id, job_type, total_files),
        )
        self.conn.commit()
        return self.get_job(id)

    def get_job(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def update_job_progress(self, id: str, completed_files: int = None,
                            failed_files: int = None, current_file: str = None,
                            status: str = None, error_message: str = None,
                            result_json: str = None) -> None:
        updates = []
        params = []
        if completed_files is not None:
            updates.append("completed_files = ?")
            params.append(completed_files)
        if failed_files is not None:
            updates.append("failed_files = ?")
            params.append(failed_files)
        if current_file is not None:
            updates.append("current_file = ?")
            params.append(current_file)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "processing":
                updates.append("started_at = CURRENT_TIMESTAMP")
            elif status in ("completed", "failed"):
                updates.append("completed_at = CURRENT_TIMESTAMP")
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if result_json is not None:
            updates.append("result_json = ?")
            params.append(result_json)
        if not updates:
            return
        params.append(id)
        self.conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()

    def list_jobs(self, practice_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE practice_id = ? ORDER BY created_at DESC LIMIT ?",
            (practice_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_practice_vault_key(self, practice_id: str, vault_key_encrypted: str) -> None:
        self.conn.execute(
            "UPDATE practices SET vault_key_encrypted = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (vault_key_encrypted, practice_id),
        )
        self.conn.commit()

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

    # --- Audit Logging ---

    def log_audit(
        self, practice_id: str, action: str, resource_type: str,
        user_id: str | None = None, resource_id: str | None = None,
        detail: str | None = None, ip_address: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_log
            (practice_id, user_id, action, resource_type, resource_id, detail, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (practice_id, user_id, action, resource_type, resource_id, detail, ip_address),
        )
        self.conn.commit()

    def get_audit_log(self, practice_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, practice_id, user_id, action, resource_type, resource_id,
                      detail, ip_address, created_at
            FROM audit_log WHERE practice_id = ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (practice_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Patients ---

    def create_patient(self, id: str, email: str, phone: str | None = None) -> dict:
        self.conn.execute(
            "INSERT INTO patients (id, email, phone) VALUES (?, ?, ?)",
            (id, email, phone),
        )
        self.conn.commit()
        return self.get_patient(id)

    def get_patient(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM patients WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def get_patient_by_email(self, email: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM patients WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None

    def update_patient_otp(self, patient_id: str, otp_hash: str, otp_expires_at: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE patients SET otp_hash = ?, otp_expires_at = ? WHERE id = ?",
            (otp_hash, otp_expires_at, patient_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def verify_patient_email(self, patient_id: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE patients SET email_verified = 1, otp_hash = NULL, otp_expires_at = NULL "
            "WHERE id = ?",
            (patient_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_patient_share_key(self, patient_id: str, share_key: str) -> None:
        self.conn.execute(
            "UPDATE patients SET share_key = ? WHERE id = ?",
            (share_key, patient_id),
        )
        self.conn.commit()

    def get_patient_by_share_key(self, share_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM patients WHERE share_key = ?", (share_key,)
        ).fetchone()
        return dict(row) if row else None

    def link_patient_to_practice(
        self, patient_id: str, practice_id: str, source_file_hashes: str = ""
    ) -> None:
        self.conn.execute(
            """INSERT INTO patient_practice_links (patient_id, practice_id, source_file_hashes)
            VALUES (?, ?, ?)
            ON CONFLICT(patient_id, practice_id) DO UPDATE SET
                source_file_hashes = excluded.source_file_hashes,
                linked_at = CURRENT_TIMESTAMP""",
            (patient_id, practice_id, source_file_hashes),
        )
        self.conn.commit()

    def get_patient_practices(self, patient_id: str) -> list[dict]:
        """Return all practices this patient is linked to."""
        rows = self.conn.execute(
            "SELECT * FROM patient_practice_links WHERE patient_id = ? ORDER BY linked_at DESC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_practice_patients(self, practice_id: str) -> list[dict]:
        """Return all patients linked to this practice."""
        rows = self.conn.execute(
            "SELECT * FROM patient_practice_links WHERE practice_id = ? ORDER BY linked_at DESC",
            (practice_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Consent Records ---

    def create_consent(
        self,
        id: str,
        patient_id: str,
        source_practice_id: str,
        target_practice_id: str,
        scope: str,
        expires_at: str,
    ) -> dict:
        self.conn.execute(
            """INSERT INTO consent_records
            (id, patient_id, source_practice_id, target_practice_id, scope, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (id, patient_id, source_practice_id, target_practice_id, scope, expires_at),
        )
        self.conn.commit()
        return self.get_consent(id)

    def get_consent(self, id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM consent_records WHERE id = ?", (id,)
        ).fetchone()
        return dict(row) if row else None

    def update_consent_status(
        self,
        id: str,
        status: str,
        authorized_at: str | None = None,
        expires_at: str | None = None,
    ) -> bool:
        updates = ["status = ?"]
        params: list = [status]
        if status == "revoked":
            updates.append("revoked_at = CURRENT_TIMESTAMP")
        if authorized_at is not None:
            updates.append("authorized_at = ?")
            params.append(authorized_at)
        if expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)
        params.append(id)
        cursor = self.conn.execute(
            f"UPDATE consent_records SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_consents_for_patient(self, patient_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM consent_records WHERE patient_id = ? ORDER BY created_at DESC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Transfer Log ---

    def log_transfer(
        self,
        id: str,
        consent_id: str,
        source_practice_id: str,
        target_practice_id: str,
        token_count: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO transfer_log
            (id, consent_id, source_practice_id, target_practice_id,
             token_count, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id, consent_id, source_practice_id, target_practice_id,
             token_count, status, error_message),
        )
        self.conn.commit()

    def get_transfers_for_consent(self, consent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM transfer_log WHERE consent_id = ? ORDER BY created_at DESC",
            (consent_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_consents_for_practice(self, practice_id: str) -> list[dict]:
        """Return all consent records where this practice is source or target."""
        rows = self.conn.execute(
            """SELECT * FROM consent_records
            WHERE source_practice_id = ? OR target_practice_id = ?
            ORDER BY created_at DESC""",
            (practice_id, practice_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_transfers_for_practice(self, practice_id: str) -> list[dict]:
        """Return all transfer log entries where this practice is source or target."""
        rows = self.conn.execute(
            """SELECT * FROM transfer_log
            WHERE source_practice_id = ? OR target_practice_id = ?
            ORDER BY created_at DESC""",
            (practice_id, practice_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_patient_files(self, practice_id: str, patient_id: str) -> int:
        """Return how many processed files are linked to a patient at this practice."""
        link = self.conn.execute(
            "SELECT source_file_hashes FROM patient_practice_links WHERE patient_id = ? AND practice_id = ?",
            (patient_id, practice_id),
        ).fetchone()
        if not link or not link["source_file_hashes"]:
            return 0
        hashes = [h.strip() for h in link["source_file_hashes"].split(",") if h.strip()]
        return len(hashes)

    # --- Patient Health Data ---

    def store_health_record(
        self,
        id: str,
        patient_id: str,
        domain: str,
        field_type: str,
        label: str,
        value_encrypted: str,
        recorded_date: str | None,
        source: str,
        source_system: str,
    ) -> None:
        self.conn.execute(
            """INSERT INTO patient_health_data
            (id, patient_id, domain, field_type, label, value_encrypted,
             recorded_date, source, source_system)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, patient_id, domain, field_type, label, value_encrypted,
             recorded_date, source, source_system),
        )
        self.conn.commit()

    def get_patient_health_records(
        self, patient_id: str, domain: str | None = None
    ) -> list[dict]:
        if domain is not None:
            rows = self.conn.execute(
                """SELECT id, patient_id, domain, field_type, label, value_encrypted,
                          recorded_date, source, source_system, imported_at
                FROM patient_health_data
                WHERE patient_id = ? AND domain = ?
                ORDER BY imported_at DESC""",
                (patient_id, domain),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT id, patient_id, domain, field_type, label, value_encrypted,
                          recorded_date, source, source_system, imported_at
                FROM patient_health_data
                WHERE patient_id = ?
                ORDER BY imported_at DESC""",
                (patient_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_patient_health_records(self, patient_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM patient_health_data WHERE patient_id = ?",
            (patient_id,),
        )
        self.conn.commit()
        return cursor.rowcount
