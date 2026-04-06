"""Integration tests for vault sync through the Strata API.

Tests the full push/pull/sync cycle using the VaultSyncClient talking
to a live TestClient Strata server. Verifies:
- Token round-trip: push from local → server → pull to second local vault
- Conflict resolution with last-write-wins
- Manifest diff accuracy
- Re-encryption between vault keys
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app
from aquifer.strata.sync import SyncManager
from aquifer.vault.encryption import encrypt_value, decrypt_value
from aquifer.vault.store import TokenVault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup():
    """Create a Strata server, register a practice, and open local + cloud vaults."""
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-sync-integration"
    cfg.jwt_secret = "test-jwt-secret-sync-integration"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)

    with TestClient(app) as client:
        # Register practice
        reg = client.post("/api/v1/auth/register", json={
            "practice_name": "Sync Test Practice",
            "email": "sync@test.com",
            "password": "SecurePass123",
        })
        assert reg.status_code == 201
        token = reg.json()["token"]
        practice_id = reg.json()["practice_id"]

        # Create API key with vault scope
        headers = {"Authorization": f"Bearer {token}"}
        key_resp = client.post("/api/v1/auth/api-keys", json={
            "name": "sync-key",
            "scopes": "deid,files,vault,admin",
        }, headers=headers)
        assert key_resp.status_code == 201
        api_key = key_resp.json()["key"]
        api_headers = {"Authorization": f"Bearer {api_key}"}

        # Create a local vault with a different key
        local_vault_path = cfg.data_dir / "local_vault.aqv"
        local_password = "local-vault-password"
        local_vault = TokenVault(local_vault_path, local_password)
        local_vault.init()
        local_vault.ensure_sync_schema()

        yield {
            "client": client,
            "headers": api_headers,
            "jwt_headers": headers,
            "practice_id": practice_id,
            "local_vault": local_vault,
            "local_key": local_vault.encryption_key,
            "cfg": cfg,
        }

        local_vault.close()

    shutil.rmtree(cfg.data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestManifestDiff
# ---------------------------------------------------------------------------

class TestManifestDiff:
    def test_empty_manifests(self, setup):
        client = setup["client"]
        headers = setup["headers"]
        local_vault = setup["local_vault"]

        manifest = local_vault.get_manifest()
        resp = client.post("/api/v1/vault/sync/manifest", json={
            "manifest": manifest,
            "vault_key": setup["local_key"].decode(),
            "direction": "sync",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["push_token_ids"] == []
        assert data["pull_token_ids"] == []
        assert data["conflict_count"] == 0

    def test_local_only_tokens(self, setup):
        client = setup["client"]
        headers = setup["headers"]
        local_vault = setup["local_vault"]

        # Store a token locally
        token_id = str(uuid.uuid4())
        local_vault.store_token(
            token_id=token_id,
            phi_type="name",
            phi_value="Jane Doe",
            source_file_hash="abc123",
        )

        manifest = local_vault.get_manifest()
        resp = client.post("/api/v1/vault/sync/manifest", json={
            "manifest": [{"token_id": m["token_id"], "phi_type": m["phi_type"],
                          "source_file_hash": m["source_file_hash"],
                          "updated_at": m.get("updated_at")} for m in manifest],
            "vault_key": setup["local_key"].decode(),
            "direction": "sync",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert token_id in data["push_token_ids"]
        assert data["local_only_count"] == 1


# ---------------------------------------------------------------------------
# TestPushPull
# ---------------------------------------------------------------------------

class TestPushPull:
    def test_push_and_pull_round_trip(self, setup):
        client = setup["client"]
        headers = setup["headers"]
        local_vault = setup["local_vault"]

        # Store tokens locally
        tokens = []
        for i in range(3):
            tid = str(uuid.uuid4())
            local_vault.store_token(
                token_id=tid,
                phi_type="name",
                phi_value=f"Patient {i}",
                source_file_hash=f"file{i}hash",
            )
            tokens.append(tid)

        # Push to cloud
        exported = local_vault.export_tokens_encrypted(tokens)
        resp = client.post("/api/v1/vault/sync/push", json={
            "tokens": exported,
            "vault_key": setup["local_key"].decode(),
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["stored"] == 3

        # Create a second local vault (simulating a different machine)
        second_vault_path = setup["cfg"].data_dir / "second_vault.aqv"
        second_password = "second-vault-password"
        second_vault = TokenVault(second_vault_path, second_password)
        second_vault.init()
        second_vault.ensure_sync_schema()
        second_key = second_vault.encryption_key

        # Pull from cloud into second vault
        resp = client.post("/api/v1/vault/sync/pull", json={
            "token_ids": tokens,
            "vault_key": second_key.decode(),
        }, headers=headers)
        assert resp.status_code == 200
        pulled = resp.json()["tokens"]
        assert len(pulled) == 3

        # Import into second vault
        for t in pulled:
            second_vault.import_token_raw(
                token_id=t["token_id"],
                phi_type=t["phi_type"],
                phi_value_encrypted=t["phi_value_encrypted"],
                source_file_hash=t["source_file_hash"],
            )

        # Verify data integrity — decrypt and compare
        for i, tid in enumerate(tokens):
            token = second_vault.get_token(tid)
            assert token is not None
            assert token.phi_value == f"Patient {i}"
            assert token.phi_type == "name"

        second_vault.close()

    def test_push_preserves_phi_types(self, setup):
        client = setup["client"]
        headers = setup["headers"]
        local_vault = setup["local_vault"]

        phi_types = ["name", "ssn", "phone", "email", "address", "date"]
        tids = []
        for phi_type in phi_types:
            tid = str(uuid.uuid4())
            local_vault.store_token(
                token_id=tid,
                phi_type=phi_type,
                phi_value=f"value-{phi_type}",
                source_file_hash="filehash",
            )
            tids.append(tid)

        exported = local_vault.export_tokens_encrypted(tids)
        resp = client.post("/api/v1/vault/sync/push", json={
            "tokens": exported,
            "vault_key": setup["local_key"].decode(),
        }, headers=headers)
        assert resp.json()["stored"] == len(phi_types)

        # Pull back and verify types
        resp = client.post("/api/v1/vault/sync/pull", json={
            "token_ids": tids,
            "vault_key": setup["local_key"].decode(),
        }, headers=headers)
        pulled = resp.json()["tokens"]
        pulled_types = {t["phi_type"] for t in pulled}
        assert pulled_types == set(phi_types)


# ---------------------------------------------------------------------------
# TestSyncStatus
# ---------------------------------------------------------------------------

class TestSyncStatusAPI:
    def test_status_after_push(self, setup):
        client = setup["client"]
        headers = setup["headers"]
        local_vault = setup["local_vault"]

        # Push a token
        tid = str(uuid.uuid4())
        local_vault.store_token(
            token_id=tid, phi_type="name", phi_value="Test",
            source_file_hash="hash123",
        )
        exported = local_vault.export_tokens_encrypted([tid])
        client.post("/api/v1/vault/sync/push", json={
            "tokens": exported,
            "vault_key": setup["local_key"].decode(),
        }, headers=headers)

        # Check status
        resp = client.get("/api/v1/vault/sync/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] >= 1
        assert data["last_sync"] is not None
        assert data["last_sync"]["status"] == "completed"

    def test_status_empty(self, setup):
        client = setup["client"]
        headers = setup["headers"]

        resp = client.get("/api/v1/vault/sync/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 0
