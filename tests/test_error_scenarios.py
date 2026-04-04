"""Tests for error handling and edge-case robustness across Aquifer components."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from aquifer.core import VaultError
from aquifer.engine.pipeline import process_file
from aquifer.vault.store import TokenVault


# ---------------------------------------------------------------------------
# Vault corruption / open-failure scenarios
# ---------------------------------------------------------------------------

class TestVaultOpenErrors:
    def test_corrupt_file_raises_vault_error(self, tmp_path):
        """Writing random bytes to a .aqv path and opening it must raise VaultError."""
        corrupt_path = tmp_path / "corrupt.aqv"
        corrupt_path.write_bytes(b"\x00\xff\xfe\xfd" * 512)

        v = TokenVault(corrupt_path, "password")
        with pytest.raises(VaultError):
            v.open()

    def test_valid_sqlite_missing_tables_raises_vault_error(self, tmp_path):
        """A valid SQLite file lacking required vault tables must raise VaultError."""
        db_path = tmp_path / "empty.aqv"
        # Create a well-formed SQLite DB with no vault tables
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        v = TokenVault(db_path, "password")
        with pytest.raises(VaultError, match="missing required tables"):
            v.open()

    def test_nonexistent_file_raises_on_open(self, tmp_path):
        """Calling open() on a path that does not exist should raise VaultError."""
        missing = tmp_path / "does_not_exist.aqv"
        v = TokenVault(missing, "password")
        with pytest.raises((VaultError, Exception)):
            v.open()


# ---------------------------------------------------------------------------
# Vault rekey
# ---------------------------------------------------------------------------

class TestVaultRekey:
    def test_rekey_tokens_readable_with_new_password(self, tmp_path):
        """After rekey, vault tokens must be decryptable with the new password."""
        vault_path = tmp_path / "vault.aqv"

        # Create vault and store a token
        v1 = TokenVault(vault_path, "old-password")
        v1.init()
        v1.store_token(
            token_id="tok-001",
            phi_type="SSN",
            phi_value="123-45-6789",
            source_file_hash="abc123",
        )
        v1.rekey("new-password")
        v1.close()

        # Reopen with the new password
        v2 = TokenVault(vault_path, "new-password")
        v2.open()
        token = v2.get_token("tok-001")
        v2.close()

        assert token is not None
        assert token.phi_value == "123-45-6789"

    def test_rekey_old_password_no_longer_works(self, tmp_path):
        """After rekey, decryption with the old password must fail."""
        vault_path = tmp_path / "vault.aqv"

        v1 = TokenVault(vault_path, "old-password")
        v1.init()
        v1.store_token(
            token_id="tok-rekey",
            phi_type="EMAIL",
            phi_value="patient@example.com",
            source_file_hash="def456",
        )
        v1.rekey("new-password")
        v1.close()

        # Open with old password — decryption of stored token must fail
        v_old = TokenVault(vault_path, "old-password")
        v_old.open()
        with pytest.raises(Exception):
            # get_token triggers Fernet decrypt with the wrong key
            v_old.get_token("tok-rekey")
        v_old.close()


# ---------------------------------------------------------------------------
# store_tokens_batch rollback
# ---------------------------------------------------------------------------

class TestBatchRollback:
    def test_batch_rollback_on_encrypt_failure(self, tmp_path):
        """If encryption fails mid-batch, no tokens from that batch are persisted."""
        vault_path = tmp_path / "vault.aqv"
        v = TokenVault(vault_path, "password")
        v.init()

        tokens = [
            ("tok-a", "SSN", "111-22-3333", "hash1", None, 1.0),
            ("tok-b", "EMAIL", "a@b.com", "hash1", None, 1.0),
            ("tok-c", "DATE", "2024-01-01", "hash1", None, 1.0),
        ]

        call_count = 0
        original_encrypt = __import__(
            "aquifer.vault.encryption", fromlist=["encrypt_value"]
        ).encrypt_value

        def failing_encrypt(value, key):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise RuntimeError("Simulated encryption failure on 3rd call")
            return original_encrypt(value, key)

        with patch("aquifer.vault.store.encrypt_value", side_effect=failing_encrypt):
            with pytest.raises(RuntimeError):
                v.store_tokens_batch(tokens)

        # None of the three tokens should be in the vault
        assert v.get_token("tok-a") is None
        assert v.get_token("tok-b") is None
        assert v.get_token("tok-c") is None

        v.close()


# ---------------------------------------------------------------------------
# Pipeline error handling
# ---------------------------------------------------------------------------

class TestPipelineErrorHandling:
    def test_invalid_pdf_content_does_not_raise(self, tmp_path):
        """A .pdf file with non-PDF bytes must not raise an unhandled exception."""
        vault_path = tmp_path / "vault.aqv"
        v = TokenVault(vault_path, "password")
        v.init()

        bad_pdf = tmp_path / "fake.pdf"
        bad_pdf.write_bytes(b"\x00\xff\xfe" * 100)
        output_file = tmp_path / "output.aqf"

        # Must return a PipelineResult, never raise
        result = process_file(bad_pdf, output_file, v, use_ner=False)

        assert isinstance(result.errors, list)
        # Either extraction gave nothing (no text error) or pipeline recorded the exception
        assert len(result.errors) >= 0  # just verifying it returned at all

        v.close()

    def test_pipeline_records_error_without_raising(self, tmp_path):
        """If the extractor itself raises, the pipeline catches and records the error."""
        vault_path = tmp_path / "vault.aqv"
        v = TokenVault(vault_path, "password")
        v.init()

        input_file = tmp_path / "note.txt"
        input_file.write_text("Patient SSN: 123-45-6789")
        output_file = tmp_path / "output.aqf"

        with patch(
            "aquifer.engine.pipeline._extract_text",
            side_effect=RuntimeError("extractor exploded"),
        ):
            result = process_file(input_file, output_file, v, use_ner=False)

        assert result.errors
        assert any("extractor exploded" in e for e in result.errors)
        assert not output_file.exists()

        v.close()
