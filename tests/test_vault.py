"""Tests for token vault encryption and CRUD operations."""

import sqlite3
import pytest
from pathlib import Path

from aquifer.vault.encryption import derive_key, encrypt_value, decrypt_value
from aquifer.vault.store import TokenVault


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "test_vault.aqv"


@pytest.fixture
def vault(vault_path):
    v = TokenVault(vault_path, "test-password-123")
    v.init()
    yield v
    v.close()


class TestEncryption:
    def test_key_derivation_deterministic(self):
        key1, salt = derive_key("password")
        key2, _ = derive_key("password", salt)
        assert key1 == key2

    def test_different_passwords_different_keys(self):
        key1, salt = derive_key("password1")
        key2, _ = derive_key("password2", salt)
        assert key1 != key2

    def test_encrypt_decrypt_roundtrip(self):
        key, _ = derive_key("test-password")
        original = "John Michael Smith"
        encrypted = encrypt_value(original, key)
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == original

    def test_encrypted_value_is_not_plaintext(self):
        key, _ = derive_key("test-password")
        original = "123-45-6789"
        encrypted = encrypt_value(original, key)
        assert original not in encrypted

    def test_wrong_key_fails(self):
        key1, _ = derive_key("password1")
        key2, _ = derive_key("password2")
        encrypted = encrypt_value("secret", key1)
        with pytest.raises(Exception):
            decrypt_value(encrypted, key2)


class TestTokenVault:
    def test_store_and_retrieve(self, vault):
        vault.store_token(
            token_id="test-uuid-1234",
            phi_type="NAME",
            phi_value="John Smith",
            source_file_hash="abc123",
            confidence=0.95,
        )
        result = vault.get_token("test-uuid-1234")
        assert result is not None
        assert result.phi_value == "John Smith"
        assert result.phi_type == "NAME"

    def test_encrypted_at_rest(self, vault, vault_path):
        vault.store_token(
            token_id="test-uuid-5678",
            phi_type="SSN",
            phi_value="123-45-6789",
            source_file_hash="abc123",
            confidence=1.0,
        )
        # Open the database directly with sqlite3 — PHI should be encrypted
        conn = sqlite3.connect(str(vault_path))
        row = conn.execute(
            "SELECT phi_value_encrypted FROM tokens WHERE token_id = ?",
            ("test-uuid-5678",),
        ).fetchone()
        conn.close()
        assert row is not None
        # The raw value should NOT be readable as the original PHI
        assert "123-45-6789" not in row[0]

    def test_get_tokens_for_file(self, vault):
        vault.store_token("t1", "NAME", "John", "file_hash_1", confidence=1.0)
        vault.store_token("t2", "SSN", "123", "file_hash_1", confidence=1.0)
        vault.store_token("t3", "NAME", "Jane", "file_hash_2", confidence=1.0)

        tokens = vault.get_tokens_for_file("file_hash_1")
        assert len(tokens) == 2

    def test_delete_tokens_for_file(self, vault):
        vault.store_token("t1", "NAME", "John", "file_hash_1", confidence=1.0)
        vault.store_token("t2", "NAME", "Jane", "file_hash_2", confidence=1.0)

        deleted = vault.delete_tokens_for_file("file_hash_1")
        assert deleted == 1
        assert vault.get_token("t1") is None
        assert vault.get_token("t2") is not None

    def test_stats(self, vault):
        vault.store_token("t1", "NAME", "John", "fh1", confidence=1.0)
        vault.store_token("t2", "SSN", "123", "fh1", confidence=1.0)
        stats = vault.get_stats()
        assert stats["total_tokens"] == 2
        assert stats["tokens_by_type"]["NAME"] == 1

    def test_file_records(self, vault):
        vault.store_file_record("hash1", "test.pdf", "pdf", "aqf1", 5)
        record = vault.get_file_record("hash1")
        assert record is not None
        assert record["original_filename"] == "test.pdf"

    def test_context_manager(self, vault_path):
        with TokenVault(vault_path, "test-password-123") as v:
            v.store_token("t1", "NAME", "Test", "fh1", confidence=1.0)
            result = v.get_token("t1")
            assert result.phi_value == "Test"

    def test_wrong_password_fails(self, vault_path, vault):
        vault.store_token("t1", "NAME", "John", "fh1", confidence=1.0)
        vault.close()

        # Open with wrong password — should derive wrong key, decrypt will fail
        bad_vault = TokenVault(vault_path, "wrong-password")
        bad_vault.open()
        with pytest.raises(Exception):
            bad_vault.get_token("t1")
        bad_vault.close()
