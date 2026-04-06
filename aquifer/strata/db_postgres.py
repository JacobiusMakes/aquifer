"""PostgreSQL backend for the Strata metadata database.

Drop-in replacement for StrataDB (SQLite) that uses psycopg3.
Same method signatures, same return types. Switch via config:
    AQUIFER_DATABASE_URL=postgresql://user:pass@host/dbname

Connection pooling is built in via psycopg.pool.
"""

from __future__ import annotations

import logging
from typing import Optional

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# PostgreSQL schema — same structure as SQLite, with PG-specific types
_PG_SCHEMA = """
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
    file_size_bytes INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    data_domain TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_log (
    id SERIAL PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    action TEXT NOT NULL,
    user_id TEXT,
    file_id TEXT,
    bytes_processed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    practice_id TEXT NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    detail TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_files_practice ON processed_files(practice_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON processed_files(status);
CREATE INDEX IF NOT EXISTS idx_files_data_domain ON processed_files(data_domain);
CREATE INDEX IF NOT EXISTS idx_usage_practice ON usage_log(practice_id);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_practice ON audit_log(practice_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_patients_email ON patients(email);
CREATE INDEX IF NOT EXISTS idx_patients_share_key ON patients(share_key);
CREATE INDEX IF NOT EXISTS idx_patient_links_patient ON patient_practice_links(patient_id);
CREATE INDEX IF NOT EXISTS idx_patient_links_practice ON patient_practice_links(practice_id);
CREATE INDEX IF NOT EXISTS idx_consent_patient ON consent_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_consent_status ON consent_records(status);
CREATE INDEX IF NOT EXISTS idx_jobs_practice ON jobs(practice_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class PostgresDB:
    """PostgreSQL backend — same interface as StrataDB.

    Usage:
        db = PostgresDB("postgresql://user:pass@localhost/aquifer")
        db.connect()
        ...
        db.close()
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.db_path = database_url  # Compatibility with StrataDB interface
        self._conn: Optional[psycopg.Connection] = None

    def connect(self) -> None:
        self._conn = psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            autocommit=False,
        )
        # Create schema
        with self._conn.cursor() as cur:
            cur.execute(_PG_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def _execute(self, sql: str, params: tuple = ()) -> psycopg.Cursor:
        """Execute SQL with PostgreSQL %s placeholders.

        Accepts SQLite-style ? placeholders and converts them automatically.
        """
        pg_sql = sql.replace("?", "%s")
        cur = self.conn.cursor()
        cur.execute(pg_sql, params)
        return cur

    def _execute_commit(self, sql: str, params: tuple = ()) -> psycopg.Cursor:
        """Execute and commit."""
        cur = self._execute(sql, params)
        self.conn.commit()
        return cur

    def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cur = self._execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # --- Practices ---

    def create_practice(self, id: str, name: str, slug: str,
                        vault_key_encrypted: str, tier: str = "community",
                        license_key: str | None = None) -> dict:
        self._execute_commit(
            "INSERT INTO practices (id, name, slug, vault_key_encrypted, tier, license_key) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (id, name, slug, vault_key_encrypted, tier, license_key),
        )
        return self.get_practice(id)

    def get_practice(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM practices WHERE id = %s", (id,))

    def get_practice_by_slug(self, slug: str) -> dict | None:
        return self._fetchone("SELECT * FROM practices WHERE slug = %s", (slug,))

    # --- Users ---

    def create_user(self, id: str, practice_id: str, email: str,
                    password_hash: str, role: str = "user") -> dict:
        self._execute_commit(
            "INSERT INTO users (id, practice_id, email, password_hash, role) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id, practice_id, email, password_hash, role),
        )
        return self.get_user(id)

    def get_user(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM users WHERE id = %s", (id,))

    def get_user_by_email(self, email: str) -> dict | None:
        return self._fetchone("SELECT * FROM users WHERE email = %s", (email,))

    def update_user_password(self, id: str, password_hash: str) -> bool:
        cur = self._execute_commit(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, id),
        )
        return cur.rowcount > 0

    def set_verification_token(self, user_id: str, token: str, expires_at: str) -> None:
        self._execute_commit(
            "UPDATE users SET verification_token = %s, verification_token_expires = %s WHERE id = %s",
            (token, expires_at, user_id),
        )

    def verify_user_email(self, user_id: str) -> bool:
        cur = self._execute_commit(
            "UPDATE users SET email_verified = 1, verification_token = NULL, "
            "verification_token_expires = NULL WHERE id = %s",
            (user_id,),
        )
        return cur.rowcount > 0

    def get_user_by_verification_token(self, token: str) -> dict | None:
        return self._fetchone("SELECT * FROM users WHERE verification_token = %s", (token,))

    def set_password_reset_token(self, user_id: str, token: str, expires_at: str) -> None:
        self._execute_commit(
            "UPDATE users SET password_reset_token = %s, password_reset_expires = %s WHERE id = %s",
            (token, expires_at, user_id),
        )

    def get_user_by_reset_token(self, token: str) -> dict | None:
        return self._fetchone("SELECT * FROM users WHERE password_reset_token = %s", (token,))

    def clear_reset_token(self, user_id: str) -> None:
        self._execute_commit(
            "UPDATE users SET password_reset_token = NULL, password_reset_expires = NULL WHERE id = %s",
            (user_id,),
        )

    # --- API Keys ---

    def create_api_key(self, id: str, practice_id: str, user_id: str,
                       key_hash: str, key_prefix: str, name: str | None = None,
                       scopes: str = "deid,files") -> dict:
        self._execute_commit(
            "INSERT INTO api_keys (id, practice_id, user_id, key_hash, key_prefix, name, scopes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (id, practice_id, user_id, key_hash, key_prefix, name, scopes),
        )
        return self._fetchone("SELECT * FROM api_keys WHERE id = %s", (id,))

    def get_api_key_by_hash(self, key_hash: str) -> dict | None:
        return self._fetchone(
            "SELECT * FROM api_keys WHERE key_hash = %s AND is_active = 1", (key_hash,)
        )

    def list_api_keys(self, practice_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM api_keys WHERE practice_id = %s ORDER BY created_at DESC",
            (practice_id,),
        )

    def revoke_api_key(self, key_id: str, practice_id: str) -> bool:
        cur = self._execute_commit(
            "UPDATE api_keys SET is_active = 0 WHERE id = %s AND practice_id = %s",
            (key_id, practice_id),
        )
        return cur.rowcount > 0

    def update_api_key_last_used(self, key_id: str) -> None:
        self._execute_commit(
            "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = %s",
            (key_id,),
        )

    # --- Files ---

    def create_file_record(self, id: str, practice_id: str, original_filename: str,
                           source_type: str, source_hash: str, file_size_bytes: int = 0,
                           data_domain: str | None = None) -> dict:
        self._execute_commit(
            "INSERT INTO processed_files (id, practice_id, original_filename, source_type, "
            "source_hash, file_size_bytes, data_domain) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (id, practice_id, original_filename, source_type, source_hash, file_size_bytes, data_domain),
        )
        return self._fetchone("SELECT * FROM processed_files WHERE id = %s", (id,))

    def update_file_record(self, id: str, **kwargs) -> None:
        updates = []
        params = []
        for key, value in kwargs.items():
            updates.append(f"{key} = %s")
            params.append(value)
        if not updates:
            return
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(id)
        self._execute_commit(
            f"UPDATE processed_files SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )

    def get_file_record(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM processed_files WHERE id = %s", (id,))

    def get_file_record_by_hash(self, practice_id: str, source_hash: str) -> dict | None:
        return self._fetchone(
            "SELECT * FROM processed_files WHERE practice_id = %s AND source_hash = %s",
            (practice_id, source_hash),
        )

    def list_files(self, practice_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM processed_files WHERE practice_id = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (practice_id, limit, offset),
        )

    def count_files(self, practice_id: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) as count FROM processed_files WHERE practice_id = %s",
            (practice_id,),
        )
        return row["count"] if row else 0

    # --- Usage / Audit ---

    def log_usage(self, practice_id: str, action: str, user_id: str | None = None,
                  file_id: str | None = None, bytes_processed: int = 0) -> None:
        self._execute_commit(
            "INSERT INTO usage_log (practice_id, action, user_id, file_id, bytes_processed) "
            "VALUES (%s, %s, %s, %s, %s)",
            (practice_id, action, user_id, file_id, bytes_processed),
        )

    def log_audit(self, practice_id: str, action: str, resource_type: str,
                  resource_id: str | None = None, user_id: str | None = None,
                  detail: str | None = None, ip_address: str | None = None) -> None:
        self._execute_commit(
            "INSERT INTO audit_log (practice_id, user_id, action, resource_type, "
            "resource_id, detail, ip_address) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (practice_id, user_id, action, resource_type, resource_id, detail, ip_address),
        )

    def get_usage_stats(self, practice_id: str, days: int = 30) -> dict:
        rows = self._fetchall(
            "SELECT action, COUNT(*) as count, COALESCE(SUM(bytes_processed), 0) as total_bytes "
            "FROM usage_log WHERE practice_id = %s "
            "AND created_at >= CURRENT_TIMESTAMP - INTERVAL '%s days' "
            "GROUP BY action",
            (practice_id, days),
        )
        by_action = {r["action"]: r["count"] for r in rows}
        total_actions = sum(r["count"] for r in rows)
        total_bytes = sum(r["total_bytes"] for r in rows)
        unique_files = len({r["action"] for r in rows if r["action"] == "deid"})
        return {
            "period_days": days,
            "total_actions": total_actions,
            "total_bytes": total_bytes,
            "unique_files": unique_files,
            "by_action": by_action,
        }

    def get_audit_log(self, practice_id: str, limit: int = 50) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM audit_log WHERE practice_id = %s ORDER BY created_at DESC LIMIT %s",
            (practice_id, limit),
        )

    # --- Patients ---

    def create_patient(self, id: str, email: str, phone: str | None = None) -> dict:
        self._execute_commit(
            "INSERT INTO patients (id, email, phone) VALUES (%s, %s, %s)",
            (id, email, phone),
        )
        return self.get_patient(id)

    def get_patient(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM patients WHERE id = %s", (id,))

    def get_patient_by_email(self, email: str) -> dict | None:
        return self._fetchone("SELECT * FROM patients WHERE email = %s", (email,))

    def get_patient_by_share_key(self, share_key: str) -> dict | None:
        return self._fetchone("SELECT * FROM patients WHERE share_key = %s", (share_key,))

    def set_patient_share_key(self, patient_id: str, share_key: str) -> None:
        self._execute_commit(
            "UPDATE patients SET share_key = %s WHERE id = %s",
            (share_key, patient_id),
        )

    def update_patient_otp(self, patient_id: str, otp_hash: str, expires_at: str) -> None:
        self._execute_commit(
            "UPDATE patients SET otp_hash = %s, otp_expires_at = %s WHERE id = %s",
            (otp_hash, expires_at, patient_id),
        )

    def verify_patient_email(self, patient_id: str) -> None:
        self._execute_commit(
            "UPDATE patients SET email_verified = 1 WHERE id = %s",
            (patient_id,),
        )

    # --- Patient-Practice Links ---

    def link_patient_to_practice(self, patient_id: str, practice_id: str,
                                  source_file_hashes: str = "") -> None:
        self._execute_commit(
            "INSERT INTO patient_practice_links (patient_id, practice_id, source_file_hashes) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (patient_id, practice_id) DO UPDATE SET source_file_hashes = "
            "CASE WHEN patient_practice_links.source_file_hashes = '' THEN EXCLUDED.source_file_hashes "
            "ELSE patient_practice_links.source_file_hashes || ',' || EXCLUDED.source_file_hashes END",
            (patient_id, practice_id, source_file_hashes),
        )

    def get_patient_practices(self, patient_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM patient_practice_links WHERE patient_id = %s ORDER BY linked_at DESC",
            (patient_id,),
        )

    def get_practice_patients(self, practice_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM patient_practice_links WHERE practice_id = %s ORDER BY linked_at DESC",
            (practice_id,),
        )

    # --- Consent ---

    def create_consent(self, id: str, patient_id: str, source_practice_id: str,
                       target_practice_id: str, scope: str = "all",
                       expires_at: str | None = None) -> dict:
        self._execute_commit(
            "INSERT INTO consent_records (id, patient_id, source_practice_id, "
            "target_practice_id, scope, expires_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (id, patient_id, source_practice_id, target_practice_id, scope, expires_at),
        )
        return self.get_consent(id)

    def get_consent(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM consent_records WHERE id = %s", (id,))

    def update_consent_status(self, id: str, status: str, authorized_at: str | None = None,
                               expires_at: str | None = None) -> bool:
        updates = ["status = %s"]
        params = [status]
        if authorized_at is not None:
            updates.append("authorized_at = %s")
            params.append(authorized_at)
        if expires_at is not None:
            updates.append("expires_at = %s")
            params.append(expires_at)
        params.append(id)
        cur = self._execute_commit(
            f"UPDATE consent_records SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        return cur.rowcount > 0

    def list_consents_for_patient(self, patient_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM consent_records WHERE patient_id = %s ORDER BY created_at DESC",
            (patient_id,),
        )

    # --- Transfers ---

    def log_transfer(self, id: str, consent_id: str, source_practice_id: str,
                     target_practice_id: str, token_count: int, status: str = "completed",
                     error_message: str | None = None) -> None:
        self._execute_commit(
            "INSERT INTO transfer_log (id, consent_id, source_practice_id, target_practice_id, "
            "token_count, status, error_message) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (id, consent_id, source_practice_id, target_practice_id, token_count, status, error_message),
        )

    def get_transfers_for_consent(self, consent_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM transfer_log WHERE consent_id = %s ORDER BY created_at DESC",
            (consent_id,),
        )

    # --- Health Records ---

    def store_health_record(self, id: str, patient_id: str, domain: str,
                            field_type: str, label: str, value_encrypted: str,
                            recorded_date: str | None = None, source: str = "",
                            source_system: str = "") -> None:
        self._execute_commit(
            "INSERT INTO patient_health_data (id, patient_id, domain, field_type, "
            "label, value_encrypted, recorded_date, source, source_system) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (id, patient_id, domain, field_type, label, value_encrypted,
             recorded_date, source, source_system),
        )

    def get_patient_health_records(self, patient_id: str, domain: str | None = None) -> list[dict]:
        if domain:
            return self._fetchall(
                "SELECT * FROM patient_health_data WHERE patient_id = %s AND domain = %s "
                "ORDER BY imported_at DESC",
                (patient_id, domain),
            )
        return self._fetchall(
            "SELECT * FROM patient_health_data WHERE patient_id = %s ORDER BY imported_at DESC",
            (patient_id,),
        )

    def delete_patient_health_records(self, patient_id: str) -> int:
        cur = self._execute_commit(
            "DELETE FROM patient_health_data WHERE patient_id = %s",
            (patient_id,),
        )
        return cur.rowcount

    # --- Jobs ---

    def create_job(self, id: str, practice_id: str, user_id: str,
                   job_type: str, total_files: int) -> dict:
        self._execute_commit(
            "INSERT INTO jobs (id, practice_id, user_id, job_type, total_files) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id, practice_id, user_id, job_type, total_files),
        )
        return self.get_job(id)

    def get_job(self, id: str) -> dict | None:
        return self._fetchone("SELECT * FROM jobs WHERE id = %s", (id,))

    def update_job_progress(self, id: str, completed_files: int = None,
                            failed_files: int = None, current_file: str = None,
                            status: str = None, error_message: str = None,
                            result_json: str = None) -> None:
        updates = []
        params = []
        if completed_files is not None:
            updates.append("completed_files = %s")
            params.append(completed_files)
        if failed_files is not None:
            updates.append("failed_files = %s")
            params.append(failed_files)
        if current_file is not None:
            updates.append("current_file = %s")
            params.append(current_file)
        if status is not None:
            updates.append("status = %s")
            params.append(status)
            if status == "processing":
                updates.append("started_at = CURRENT_TIMESTAMP")
            elif status in ("completed", "failed"):
                updates.append("completed_at = CURRENT_TIMESTAMP")
        if error_message is not None:
            updates.append("error_message = %s")
            params.append(error_message)
        if result_json is not None:
            updates.append("result_json = %s")
            params.append(result_json)
        if not updates:
            return
        params.append(id)
        self._execute_commit(
            f"UPDATE jobs SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )

    def list_jobs(self, practice_id: str, limit: int = 20) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM jobs WHERE practice_id = %s ORDER BY created_at DESC LIMIT %s",
            (practice_id, limit),
        )

    # --- Additional methods for parity with StrataDB ---

    def update_practice_vault_key(self, practice_id: str, vault_key_encrypted: str) -> None:
        self._execute_commit(
            "UPDATE practices SET vault_key_encrypted = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (vault_key_encrypted, practice_id),
        )

    def count_patient_files(self, practice_id: str, patient_id: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) as count FROM processed_files pf "
            "INNER JOIN patient_practice_links ppl ON pf.practice_id = ppl.practice_id "
            "WHERE pf.practice_id = %s AND ppl.patient_id = %s",
            (practice_id, patient_id),
        )
        return row["count"] if row else 0

    def get_consents_for_practice(self, practice_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM consent_records WHERE source_practice_id = %s OR target_practice_id = %s "
            "ORDER BY created_at DESC",
            (practice_id, practice_id),
        )

    def get_transfers_for_practice(self, practice_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM transfer_log WHERE source_practice_id = %s OR target_practice_id = %s "
            "ORDER BY created_at DESC",
            (practice_id, practice_id),
        )

    def delete_file_record(self, id: str, practice_id: str) -> bool:
        cur = self._execute_commit(
            "DELETE FROM processed_files WHERE id = %s AND practice_id = %s",
            (id, practice_id),
        )
        return cur.rowcount > 0
