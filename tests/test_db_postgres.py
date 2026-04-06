"""Tests for the PostgreSQL database backend.

Tests the PostgresDB class interface parity with StrataDB.
Uses mock connections since a real PG server may not be available.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from aquifer.strata.db_postgres import PostgresDB


class TestPostgresDBInterface:
    """Verify PostgresDB has the same method signatures as StrataDB."""

    def test_has_all_strata_db_methods(self):
        from aquifer.strata.database import StrataDB
        sqlite_methods = {m for m in dir(StrataDB) if not m.startswith("_") and callable(getattr(StrataDB, m))}
        pg_methods = {m for m in dir(PostgresDB) if not m.startswith("_") and callable(getattr(PostgresDB, m))}

        missing = sqlite_methods - pg_methods
        # conn property and db_path are attributes, not methods to check
        missing.discard("conn")
        assert not missing, f"PostgresDB missing methods: {missing}"

    def test_init(self):
        db = PostgresDB("postgresql://localhost/test")
        assert db.database_url == "postgresql://localhost/test"
        assert db.db_path == "postgresql://localhost/test"

    def test_raises_when_not_connected(self):
        db = PostgresDB("postgresql://localhost/test")
        with pytest.raises(RuntimeError, match="not connected"):
            _ = db.conn


class TestPlaceholderConversion:
    """Verify ? -> %s conversion in _execute."""

    @patch("aquifer.strata.db_postgres.psycopg")
    def test_converts_question_marks(self, mock_psycopg):
        mock_conn = MagicMock()
        mock_psycopg.connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        db = PostgresDB("postgresql://localhost/test")
        db._conn = mock_conn

        db._execute("SELECT * FROM users WHERE id = ? AND email = ?", ("uid", "test@test.com"))
        mock_cursor.execute.assert_called_once_with(
            "SELECT * FROM users WHERE id = %s AND email = %s",
            ("uid", "test@test.com"),
        )


class TestConfigIntegration:
    """Verify config loads database_url from environment."""

    @patch.dict("os.environ", {
        "AQUIFER_DATABASE_URL": "postgresql://user:pass@db.example.com/aquifer",
        "AQUIFER_MASTER_KEY": "test-key",
        "AQUIFER_JWT_SECRET": "test-jwt",
    })
    def test_config_from_env(self):
        from aquifer.strata.config import StrataConfig
        cfg = StrataConfig.from_env()
        assert cfg.database_url == "postgresql://user:pass@db.example.com/aquifer"

    def test_config_defaults_to_empty(self):
        from aquifer.strata.config import StrataConfig
        cfg = StrataConfig()
        assert cfg.database_url == ""
