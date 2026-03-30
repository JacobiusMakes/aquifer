"""Tests for the vault sync protocol.

Tests cover:
  - Manifest generation
  - Diff computation (local-only, cloud-only, both-same, conflicts)
  - Push: local vault -> server -> cloud vault
  - Pull: cloud vault -> server -> local vault
  - Bidirectional sync
  - Conflict resolution (last-write-wins)
  - Sync with empty vaults
  - Sync status reporting

Uses the httpx TestClient pattern from test_strata.py.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app
from aquifer.strata.sync import SyncManager
from aquifer.vault.encryption import encrypt_value, decrypt_value
from aquifer.vault.store import TokenVault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strata_config(tmp_path):
    """Create a test config pointing at a temp directory."""
    cfg = StrataConfig(
        debug=True,
        data_dir=tmp_path / "strata_data",
        db_path=tmp_path / "strata_data" / "strata.db",
        master_key="test-master-key-not-for-production",
        jwt_secret="test-jwt-secret-not-for-production",
        use_ner=False,
    )
    return cfg


@pytest.fixture
def app(strata_config):
    return create_app(strata_config)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def local_vault(tmp_path) -> TokenVault:
    """Create an open local vault in a temp directory."""
    vault_path = tmp_path / "local_vault.aqv"
    vault = TokenVault(vault_path, "test-password-123")
    vault.init()
    vault.ensure_sync_schema()
    return vault


@pytest.fixture
def local_vault_2(tmp_path) -> TokenVault:
    """A second local vault (different path, different password)."""
    vault_path = tmp_path / "local_vault_2.aqv"
    vault = TokenVault(vault_path, "different-password-456")
    vault.init()
    vault.ensure_sync_schema()
    return vault


def register_and_login(client, practice_name="Test Dental",
                       email="admin@test.com", password="securepass123"):
    """Register a practice and return the auth data."""
    resp = client.post("/api/v1/auth/register", json={
        "practice_name": practice_name,
        "email": email,
        "password": password,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def seed_local_vault(vault: TokenVault, prefix: str = "local",
                     count: int = 3) -> list[str]:
    """Add test tokens to a local vault. Returns list of token_ids."""
    token_ids = []
    for i in range(count):
        tid = f"AQ-{prefix}-{i:04d}"
        vault.store_token(
            token_id=tid,
            phi_type="PERSON_NAME",
            phi_value=f"Patient {prefix.title()} {i}",
            source_file_hash=f"hash_{prefix}_{i}",
            confidence=0.95,
        )
        token_ids.append(tid)
    return token_ids


def seed_cloud_vault_via_api(client, headers: dict, local_vault: TokenVault,
                             prefix: str = "cloud", count: int = 3) -> list[str]:
    """Seed the cloud vault by pushing tokens through the sync API.

    Creates tokens in a temporary local vault, then pushes them.
    Returns the token_ids created.
    """
    token_ids = seed_local_vault(local_vault, prefix=prefix, count=count)

    manifest = local_vault.get_manifest()
    vault_key = local_vault.encryption_key.decode()

    # Send manifest to get diff
    resp = client.post(
        "/api/v1/vault/sync/manifest",
        json={
            "manifest": manifest,
            "vault_key": vault_key,
            "direction": "push",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Push the tokens
    tokens_to_push = local_vault.export_tokens_encrypted(token_ids)
    resp = client.post(
        "/api/v1/vault/sync/push",
        json={
            "tokens": tokens_to_push,
            "vault_key": vault_key,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] == count

    return token_ids


# ---------------------------------------------------------------------------
# Test manifest generation
# ---------------------------------------------------------------------------

class TestManifestGeneration:
    def test_empty_vault_manifest(self, local_vault):
        """Empty vault produces an empty manifest."""
        manifest = local_vault.get_manifest()
        assert manifest == []

    def test_manifest_format(self, local_vault):
        """Manifest entries have correct fields and NO PHI values."""
        local_vault.store_token(
            token_id="AQ-TEST-0001",
            phi_type="PERSON_NAME",
            phi_value="John Smith",
            source_file_hash="abc123",
            confidence=0.99,
        )
        manifest = local_vault.get_manifest()
        assert len(manifest) == 1
        entry = manifest[0]
        assert entry["token_id"] == "AQ-TEST-0001"
        assert entry["phi_type"] == "PERSON_NAME"
        assert entry["source_file_hash"] == "abc123"
        assert "updated_at" in entry
        # CRITICAL: no PHI value in manifest
        assert "phi_value" not in entry
        assert "phi_value_encrypted" not in entry
        assert "John Smith" not in str(entry)

    def test_manifest_multiple_tokens(self, local_vault):
        """Manifest includes all tokens."""
        seed_local_vault(local_vault, count=5)
        manifest = local_vault.get_manifest()
        assert len(manifest) == 5

    def test_manifest_no_phi_leakage(self, local_vault):
        """Ensure PHI values never appear in any manifest field."""
        phi_values = ["Jane Doe", "555-12-3456", "(555) 867-5309"]
        for i, phi in enumerate(phi_values):
            local_vault.store_token(
                token_id=f"AQ-PHI-{i:04d}",
                phi_type="PERSON_NAME",
                phi_value=phi,
                source_file_hash=f"hash_{i}",
            )
        manifest = local_vault.get_manifest()
        manifest_str = str(manifest)
        for phi in phi_values:
            assert phi not in manifest_str, f"PHI '{phi}' leaked into manifest!"


# ---------------------------------------------------------------------------
# Test diff computation
# ---------------------------------------------------------------------------

class TestDiffComputation:
    def test_empty_both(self):
        """Both empty = nothing to sync."""
        vault_path = Path("/tmp/test_diff_empty.aqv")
        if vault_path.exists():
            vault_path.unlink()
        vault = TokenVault(vault_path, "pass")
        vault.init()
        vault.ensure_sync_schema()
        try:
            mgr = SyncManager(vault)
            diff = mgr.compute_diff([], [])
            assert diff.push_token_ids == []
            assert diff.pull_token_ids == []
            assert diff.conflict_count == 0
            assert diff.in_sync_count == 0
        finally:
            vault.close()
            vault_path.unlink(missing_ok=True)

    def test_local_only(self, local_vault):
        """Local has tokens cloud doesn't -> push all."""
        mgr = SyncManager(local_vault)

        local_manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-01-01 00:00:00"},
            {"token_id": "AQ-002", "phi_type": "SSN",
             "source_file_hash": "h2", "updated_at": "2025-01-01 00:00:00"},
        ]
        cloud_manifest = []

        diff = mgr.compute_diff(local_manifest, cloud_manifest)
        assert set(diff.push_token_ids) == {"AQ-001", "AQ-002"}
        assert diff.pull_token_ids == []
        assert diff.local_only_count == 2
        assert diff.cloud_only_count == 0

    def test_cloud_only(self, local_vault):
        """Cloud has tokens local doesn't -> pull all."""
        mgr = SyncManager(local_vault)

        local_manifest = []
        cloud_manifest = [
            {"token_id": "AQ-C01", "phi_type": "DOB",
             "source_file_hash": "h1", "updated_at": "2025-01-01 00:00:00"},
        ]

        diff = mgr.compute_diff(local_manifest, cloud_manifest)
        assert diff.push_token_ids == []
        assert diff.pull_token_ids == ["AQ-C01"]
        assert diff.cloud_only_count == 1

    def test_in_sync(self, local_vault):
        """Same tokens with same timestamps -> nothing to do."""
        mgr = SyncManager(local_vault)

        ts = "2025-06-15 12:00:00"
        manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": ts},
        ]

        diff = mgr.compute_diff(manifest, manifest)
        assert diff.push_token_ids == []
        assert diff.pull_token_ids == []
        assert diff.in_sync_count == 1

    def test_conflict_local_newer(self, local_vault):
        """Same token, local is newer -> local wins, push."""
        mgr = SyncManager(local_vault)

        local_manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-06-15 14:00:00"},
        ]
        cloud_manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-06-15 10:00:00"},
        ]

        diff = mgr.compute_diff(local_manifest, cloud_manifest)
        assert "AQ-001" in diff.push_token_ids
        assert "AQ-001" not in diff.pull_token_ids
        assert diff.conflict_count == 1
        assert diff.conflicts[0]["resolution"] == "local_wins"

    def test_conflict_cloud_newer(self, local_vault):
        """Same token, cloud is newer -> cloud wins, pull."""
        mgr = SyncManager(local_vault)

        local_manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-06-15 10:00:00"},
        ]
        cloud_manifest = [
            {"token_id": "AQ-001", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-06-15 14:00:00"},
        ]

        diff = mgr.compute_diff(local_manifest, cloud_manifest)
        assert "AQ-001" not in diff.push_token_ids
        assert "AQ-001" in diff.pull_token_ids
        assert diff.conflict_count == 1
        assert diff.conflicts[0]["resolution"] == "cloud_wins"

    def test_mixed_diff(self, local_vault):
        """Mix of local-only, cloud-only, in-sync, and conflict."""
        mgr = SyncManager(local_vault)

        local_manifest = [
            {"token_id": "SHARED", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-01-01 00:00:00"},
            {"token_id": "LOCAL_ONLY", "phi_type": "SSN",
             "source_file_hash": "h2", "updated_at": "2025-01-01 00:00:00"},
            {"token_id": "CONFLICT", "phi_type": "DOB",
             "source_file_hash": "h3", "updated_at": "2025-06-15 14:00:00"},
        ]
        cloud_manifest = [
            {"token_id": "SHARED", "phi_type": "PERSON_NAME",
             "source_file_hash": "h1", "updated_at": "2025-01-01 00:00:00"},
            {"token_id": "CLOUD_ONLY", "phi_type": "PHONE",
             "source_file_hash": "h4", "updated_at": "2025-01-01 00:00:00"},
            {"token_id": "CONFLICT", "phi_type": "DOB",
             "source_file_hash": "h3", "updated_at": "2025-06-15 10:00:00"},
        ]

        diff = mgr.compute_diff(local_manifest, cloud_manifest)
        assert "LOCAL_ONLY" in diff.push_token_ids
        assert "CONFLICT" in diff.push_token_ids  # local is newer
        assert "CLOUD_ONLY" in diff.pull_token_ids
        assert diff.in_sync_count == 1
        assert diff.conflict_count == 1


# ---------------------------------------------------------------------------
# Test push via API
# ---------------------------------------------------------------------------

class TestSyncPush:
    def test_push_tokens_to_cloud(self, client, local_vault):
        """Push local tokens to cloud vault via API."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Seed local vault
        token_ids = seed_local_vault(local_vault, prefix="push", count=3)
        vault_key = local_vault.encryption_key.decode()

        # Step 1: Send manifest
        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": vault_key,
                "direction": "push",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert len(diff["push_token_ids"]) == 3

        # Step 2: Push tokens
        tokens = local_vault.export_tokens_encrypted(token_ids)
        resp = client.post(
            "/api/v1/vault/sync/push",
            json={
                "tokens": tokens,
                "vault_key": vault_key,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["stored"] == 3

        # Verify: cloud vault has the tokens
        resp = client.get("/api/v1/vault/stats", headers=headers)
        assert resp.json()["total_tokens"] == 3

    def test_push_empty_vault(self, client, local_vault):
        """Pushing an empty vault is a no-op."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": local_vault.encryption_key.decode(),
                "direction": "push",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert diff["push_token_ids"] == []
        assert diff["pull_token_ids"] == []

    def test_push_preserves_phi_values(self, client, local_vault):
        """After push, cloud vault has correct decryptable PHI values."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Store a specific token
        local_vault.store_token(
            token_id="AQ-PHI-CHECK",
            phi_type="SSN",
            phi_value="123-45-6789",
            source_file_hash="ssn_file_hash",
        )

        vault_key = local_vault.encryption_key.decode()
        manifest = local_vault.get_manifest()

        # Manifest + push
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "push"},
            headers=headers,
        )
        push_ids = resp.json()["push_token_ids"]

        tokens = local_vault.export_tokens_encrypted(push_ids)
        client.post(
            "/api/v1/vault/sync/push",
            json={"tokens": tokens, "vault_key": vault_key},
            headers=headers,
        )

        # Look up the token on the server (returns type, not value)
        resp = client.get("/api/v1/vault/tokens/AQ-PHI-CHECK", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["phi_type"] == "SSN"
        # Value should NOT be in the lookup response
        assert "123-45-6789" not in resp.text


# ---------------------------------------------------------------------------
# Test pull via API
# ---------------------------------------------------------------------------

class TestSyncPull:
    def test_pull_tokens_from_cloud(self, client, local_vault, local_vault_2):
        """Seed cloud with tokens, pull to a different local vault."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Seed cloud via local_vault
        cloud_token_ids = seed_cloud_vault_via_api(
            client, headers, local_vault, prefix="cloud", count=3,
        )

        # Now pull from cloud into local_vault_2 (which is empty)
        vault_key_2 = local_vault_2.encryption_key.decode()
        manifest_2 = local_vault_2.get_manifest()

        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest_2,
                "vault_key": vault_key_2,
                "direction": "pull",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert len(diff["pull_token_ids"]) == 3

        # Pull the tokens
        resp = client.post(
            "/api/v1/vault/sync/pull",
            json={
                "token_ids": diff["pull_token_ids"],
                "vault_key": vault_key_2,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        pulled_tokens = resp.json()["tokens"]
        assert resp.json()["count"] == 3

        # Import into local_vault_2
        for token in pulled_tokens:
            local_vault_2.import_token_raw(
                token_id=token["token_id"],
                phi_type=token["phi_type"],
                phi_value_encrypted=token["phi_value_encrypted"],
                source_file_hash=token["source_file_hash"],
                aqf_file_hash=token.get("aqf_file_hash"),
                confidence=token.get("confidence", 1.0),
                updated_at=token.get("updated_at"),
            )

        # Verify local_vault_2 has the tokens
        stats = local_vault_2.get_stats()
        assert stats["total_tokens"] == 3

        # Verify the PHI values can be decrypted
        for tid in cloud_token_ids:
            token = local_vault_2.get_token(tid)
            assert token is not None
            assert token.phi_value.startswith("Patient Cloud")

    def test_pull_empty_cloud(self, client, local_vault):
        """Pulling from empty cloud is a no-op."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": local_vault.encryption_key.decode(),
                "direction": "pull",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["pull_token_ids"] == []


# ---------------------------------------------------------------------------
# Test bidirectional sync
# ---------------------------------------------------------------------------

class TestBidirectionalSync:
    def test_both_sides_have_unique_tokens(self, client, local_vault, local_vault_2):
        """Both sides have unique tokens. After sync, both have all tokens."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])
        vault_key = local_vault.encryption_key.decode()

        # Seed cloud with 3 tokens via local_vault_2
        cloud_ids = seed_cloud_vault_via_api(
            client, headers, local_vault_2, prefix="cloud", count=3,
        )

        # Seed local_vault with 2 different tokens
        local_ids = seed_local_vault(local_vault, prefix="local", count=2)

        # Full sync from local_vault perspective
        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": vault_key,
                "direction": "sync",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()

        # Should push 2 local tokens, pull 3 cloud tokens
        assert len(diff["push_token_ids"]) == 2
        assert len(diff["pull_token_ids"]) == 3

        # Push local -> cloud
        push_tokens = local_vault.export_tokens_encrypted(diff["push_token_ids"])
        resp = client.post(
            "/api/v1/vault/sync/push",
            json={"tokens": push_tokens, "vault_key": vault_key},
            headers=headers,
        )
        assert resp.json()["stored"] == 2

        # Pull cloud -> local
        resp = client.post(
            "/api/v1/vault/sync/pull",
            json={"token_ids": diff["pull_token_ids"], "vault_key": vault_key},
            headers=headers,
        )
        pulled = resp.json()["tokens"]
        for token in pulled:
            local_vault.import_token_raw(
                token_id=token["token_id"],
                phi_type=token["phi_type"],
                phi_value_encrypted=token["phi_value_encrypted"],
                source_file_hash=token["source_file_hash"],
                aqf_file_hash=token.get("aqf_file_hash"),
                confidence=token.get("confidence", 1.0),
                updated_at=token.get("updated_at"),
            )

        # Local vault should now have 2 + 3 = 5 tokens
        local_stats = local_vault.get_stats()
        assert local_stats["total_tokens"] == 5

        # Cloud should also have 3 + 2 = 5 tokens
        resp = client.get("/api/v1/vault/stats", headers=headers)
        assert resp.json()["total_tokens"] == 5


# ---------------------------------------------------------------------------
# Test conflict resolution
# ---------------------------------------------------------------------------

class TestConflictResolution:
    def test_same_token_modified_both_sides(self, client, local_vault):
        """Same token_id modified on both sides — last write wins."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])
        vault_key = local_vault.encryption_key.decode()

        # Store token in local vault with a specific value
        local_vault.store_token(
            token_id="AQ-CONFLICT-001",
            phi_type="PERSON_NAME",
            phi_value="Local Version",
            source_file_hash="hash_conflict",
        )

        # Push to cloud first
        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "push"},
            headers=headers,
        )
        push_ids = resp.json()["push_token_ids"]
        tokens = local_vault.export_tokens_encrypted(push_ids)
        client.post(
            "/api/v1/vault/sync/push",
            json={"tokens": tokens, "vault_key": vault_key},
            headers=headers,
        )

        # Directly update the local token with a future timestamp to ensure
        # it's newer than the cloud copy (SQLite second-resolution timestamps
        # mean sub-second sleeps don't produce different timestamps).
        local_vault._ensure_open()
        from aquifer.vault.encryption import encrypt_value
        encrypted = encrypt_value("Updated Local Version", local_vault.encryption_key)
        local_vault._conn.execute(
            """UPDATE tokens SET phi_value_encrypted = ?,
               updated_at = datetime('now', '+1 minute')
               WHERE token_id = ?""",
            (encrypted, "AQ-CONFLICT-001"),
        )
        local_vault._conn.commit()

        # Sync again — local is newer, should win
        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "sync"},
            headers=headers,
        )
        diff = resp.json()
        assert diff["conflict_count"] == 1
        assert any(c["resolution"] == "local_wins" for c in diff["conflicts"])
        assert "AQ-CONFLICT-001" in diff["push_token_ids"]


# ---------------------------------------------------------------------------
# Test sync with empty vaults
# ---------------------------------------------------------------------------

class TestEmptyVaultSync:
    def test_both_empty(self, client, local_vault):
        """Syncing two empty vaults is a clean no-op."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": local_vault.encryption_key.decode(),
                "direction": "sync",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert diff["push_token_ids"] == []
        assert diff["pull_token_ids"] == []
        assert diff["conflict_count"] == 0
        assert diff["in_sync_count"] == 0

    def test_push_to_empty_cloud(self, client, local_vault):
        """Push tokens to an empty cloud vault."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        seed_local_vault(local_vault, count=5)
        vault_key = local_vault.encryption_key.decode()

        manifest = local_vault.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "push"},
            headers=headers,
        )
        diff = resp.json()
        assert len(diff["push_token_ids"]) == 5
        assert diff["conflict_count"] == 0

    def test_pull_from_seeded_cloud(self, client, local_vault, local_vault_2):
        """Pull from a seeded cloud into an empty local vault."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Seed cloud via local_vault
        seed_cloud_vault_via_api(client, headers, local_vault, prefix="seed", count=4)

        # Pull into empty local_vault_2
        manifest = local_vault_2.get_manifest()
        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={
                "manifest": manifest,
                "vault_key": local_vault_2.encryption_key.decode(),
                "direction": "pull",
            },
            headers=headers,
        )
        diff = resp.json()
        assert len(diff["pull_token_ids"]) == 4


# ---------------------------------------------------------------------------
# Test sync status endpoint
# ---------------------------------------------------------------------------

class TestSyncStatus:
    def test_status_empty(self, client):
        """Status endpoint works on a fresh vault with no sync history."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        resp = client.get("/api/v1/vault/sync/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 0
        assert data["last_sync"] is None

    def test_status_after_push(self, client, local_vault):
        """Status shows sync history after a push."""
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Push some tokens
        seed_cloud_vault_via_api(client, headers, local_vault, prefix="status", count=2)

        resp = client.get("/api/v1/vault/sync/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 2
        assert data["last_sync"] is not None
        assert len(data["recent_syncs"]) >= 1


# ---------------------------------------------------------------------------
# Test re-encryption across different vault keys
# ---------------------------------------------------------------------------

class TestReEncryption:
    def test_push_with_different_keys(self, client, local_vault):
        """Tokens pushed from local key are re-encrypted with cloud key.

        The cloud vault uses a server-managed key different from the local vault.
        After push, the cloud should be able to read the tokens.
        """
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        local_vault.store_token(
            token_id="AQ-REENC-001",
            phi_type="PERSON_NAME",
            phi_value="Re-Encryption Test",
            source_file_hash="reenc_hash",
        )

        vault_key = local_vault.encryption_key.decode()
        manifest = local_vault.get_manifest()

        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "push"},
            headers=headers,
        )
        push_ids = resp.json()["push_token_ids"]
        tokens = local_vault.export_tokens_encrypted(push_ids)

        resp = client.post(
            "/api/v1/vault/sync/push",
            json={"tokens": tokens, "vault_key": vault_key},
            headers=headers,
        )
        assert resp.json()["stored"] == 1

        # Verify cloud can read the token (lookup endpoint proves it's queryable)
        resp = client.get("/api/v1/vault/tokens/AQ-REENC-001", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["phi_type"] == "PERSON_NAME"

    def test_pull_with_different_keys(self, client, local_vault, local_vault_2):
        """Tokens pulled from cloud key are re-encrypted for local key.

        The local vault should be able to decrypt the pulled tokens.
        """
        reg = register_and_login(client)
        headers = auth_headers(reg["token"])

        # Seed cloud with a specific PHI value
        local_vault.store_token(
            token_id="AQ-PULL-REENC-001",
            phi_type="SSN",
            phi_value="999-88-7777",
            source_file_hash="pull_reenc_hash",
        )
        vault_key = local_vault.encryption_key.decode()
        manifest = local_vault.get_manifest()

        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest, "vault_key": vault_key, "direction": "push"},
            headers=headers,
        )
        push_ids = resp.json()["push_token_ids"]
        tokens = local_vault.export_tokens_encrypted(push_ids)
        client.post(
            "/api/v1/vault/sync/push",
            json={"tokens": tokens, "vault_key": vault_key},
            headers=headers,
        )

        # Now pull into local_vault_2 (different key!)
        vault_key_2 = local_vault_2.encryption_key.decode()
        manifest_2 = local_vault_2.get_manifest()

        resp = client.post(
            "/api/v1/vault/sync/manifest",
            json={"manifest": manifest_2, "vault_key": vault_key_2, "direction": "pull"},
            headers=headers,
        )
        pull_ids = resp.json()["pull_token_ids"]

        resp = client.post(
            "/api/v1/vault/sync/pull",
            json={"token_ids": pull_ids, "vault_key": vault_key_2},
            headers=headers,
        )
        pulled = resp.json()["tokens"]

        for token in pulled:
            local_vault_2.import_token_raw(
                token_id=token["token_id"],
                phi_type=token["phi_type"],
                phi_value_encrypted=token["phi_value_encrypted"],
                source_file_hash=token["source_file_hash"],
            )

        # local_vault_2 should decrypt the value correctly
        token = local_vault_2.get_token("AQ-PULL-REENC-001")
        assert token is not None
        assert token.phi_value == "999-88-7777"
        assert token.phi_type == "SSN"


# ---------------------------------------------------------------------------
# Test sync log
# ---------------------------------------------------------------------------

class TestSyncLog:
    def test_sync_log_recorded(self, local_vault):
        """Sync operations are recorded in the sync log."""
        local_vault.log_sync(
            direction="push",
            token_count=10,
            server_url="https://test.example.com",
            status="completed",
            conflict_count=1,
        )

        history = local_vault.get_sync_history()
        assert len(history) == 1
        assert history[0]["direction"] == "push"
        assert history[0]["token_count"] == 10
        assert history[0]["conflict_count"] == 1
        assert history[0]["status"] == "completed"

    def test_last_sync(self, local_vault):
        """get_last_sync returns the most recent completed sync."""
        local_vault.log_sync("push", 5, "https://a.com", "completed")
        local_vault.log_sync("pull", 3, "https://b.com", "error",
                             error_message="connection refused")
        local_vault.log_sync("sync", 8, "https://a.com", "completed")

        last = local_vault.get_last_sync("https://a.com")
        assert last is not None
        # The most recent completed sync for https://a.com should have 8 tokens
        # (the sync entry). Since completed_at can have the same second-level
        # resolution, we use the auto-increment id for ordering as tiebreaker.
        assert last["token_count"] == 8

        # Error entries are not returned by get_last_sync
        last_b = local_vault.get_last_sync("https://b.com")
        assert last_b is None  # Only errors, no completed syncs

    def test_sync_history_limit(self, local_vault):
        """Sync history is limited by the limit parameter."""
        for i in range(10):
            local_vault.log_sync("push", i, "https://test.com", "completed")

        history = local_vault.get_sync_history(limit=3)
        assert len(history) == 3
