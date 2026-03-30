"""Vault management routes: stats, token lookup, sync."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aquifer.strata.auth import AuthContext, has_api_key_scopes
from aquifer.strata.sync import SyncManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vault", tags=["vault"])


# --- Request/Response Models ---

class VaultStatsResponse(BaseModel):
    total_tokens: int
    total_files: int
    tokens_by_type: dict[str, int]
    storage: dict


class ManifestEntry(BaseModel):
    token_id: str
    phi_type: str
    source_file_hash: str
    updated_at: str | None = None


class SyncManifestRequest(BaseModel):
    manifest: list[ManifestEntry]
    vault_key: str  # Local vault's Fernet key (base64 string)
    direction: str = "sync"  # "push", "pull", or "sync"


class SyncManifestResponse(BaseModel):
    push_token_ids: list[str]
    pull_token_ids: list[str]
    conflicts: list[dict]
    conflict_count: int
    local_only_count: int
    cloud_only_count: int
    in_sync_count: int


class SyncTokenEntry(BaseModel):
    token_id: str
    phi_type: str
    phi_value_encrypted: str
    source_file_hash: str
    aqf_file_hash: str | None = None
    confidence: float = 1.0
    created_at: str | None = None
    updated_at: str | None = None


class SyncPushRequest(BaseModel):
    tokens: list[SyncTokenEntry]
    vault_key: str  # Local vault's Fernet key


class SyncPushResponse(BaseModel):
    stored: int
    errors: int = 0


class SyncPullRequest(BaseModel):
    token_ids: list[str]
    vault_key: str  # Local vault's Fernet key


class SyncPullResponse(BaseModel):
    tokens: list[dict]
    count: int


class SyncStatusResponse(BaseModel):
    total_tokens: int
    total_files: int
    last_sync: dict | None = None
    recent_syncs: list[dict] = []


# --- Helper ---

def _get_sync_manager(request: Request) -> SyncManager:
    """Get a SyncManager for the authenticated practice's cloud vault."""
    auth: AuthContext = request.state.auth
    if not has_api_key_scopes(auth, "vault"):
        raise HTTPException(403, "API key missing required 'vault' scope")
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    practice = db.get_practice(auth.practice_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])
    return SyncManager(vault)


# --- Existing Endpoints ---

@router.get("/stats", response_model=VaultStatsResponse)
async def vault_stats(request: Request):
    """Get vault statistics for the current practice."""
    auth: AuthContext = request.state.auth
    if not has_api_key_scopes(auth, "vault"):
        raise HTTPException(403, "API key missing required 'vault' scope")
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    practice = db.get_practice(auth.practice_id)
    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])
    token_stats = vault.get_stats()
    storage_stats = vault_mgr.get_practice_stats(auth.practice_id)

    return VaultStatsResponse(
        total_tokens=token_stats["total_tokens"],
        total_files=token_stats["total_files"],
        tokens_by_type=token_stats["tokens_by_type"],
        storage=storage_stats,
    )


@router.get("/tokens/{token_id}")
async def lookup_token(token_id: str, request: Request):
    """Look up a specific token's PHI type (NOT value).

    Returns type and confidence only. Use rehydrate for actual values.
    """
    auth: AuthContext = request.state.auth
    if not has_api_key_scopes(auth, "vault"):
        raise HTTPException(403, "API key missing required 'vault' scope")
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    practice = db.get_practice(auth.practice_id)
    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])
    token = vault.get_token(token_id)

    if not token:
        raise HTTPException(404, "Token not found")

    # Never return PHI value through this endpoint
    return {
        "token_id": token.token_id,
        "phi_type": token.phi_type,
        "confidence": token.confidence,
        "source_file_hash": token.source_file_hash,
    }


# --- Sync Endpoints ---

@router.post("/sync/manifest", response_model=SyncManifestResponse)
async def sync_manifest(body: SyncManifestRequest, request: Request):
    """Receive a local manifest and return the diff.

    The client sends its token manifest (no PHI values!) and the server
    compares with its cloud vault to determine what needs to sync.
    """
    sync_mgr = _get_sync_manager(request)

    local_manifest = [entry.model_dump() for entry in body.manifest]
    diff = sync_mgr.compute_diff(local_manifest)

    # Filter based on direction
    response = SyncManifestResponse(
        push_token_ids=diff.push_token_ids if body.direction in ("push", "sync") else [],
        pull_token_ids=diff.pull_token_ids if body.direction in ("pull", "sync") else [],
        conflicts=diff.conflicts,
        conflict_count=diff.conflict_count,
        local_only_count=diff.local_only_count,
        cloud_only_count=diff.cloud_only_count,
        in_sync_count=diff.in_sync_count,
    )

    return response


@router.post("/sync/push", response_model=SyncPushResponse)
async def sync_push(body: SyncPushRequest, request: Request):
    """Receive tokens from local vault and store in cloud vault.

    Tokens arrive encrypted with the local vault's key. The server
    decrypts and re-encrypts with the cloud vault's key.
    """
    sync_mgr = _get_sync_manager(request)

    local_key = body.vault_key.encode() if isinstance(body.vault_key, str) else body.vault_key
    tokens = [t.model_dump() for t in body.tokens]

    stored = sync_mgr.receive_tokens(tokens, local_key)

    # Log the sync on the cloud side
    auth: AuthContext = request.state.auth
    sync_mgr.cloud_vault.log_sync(
        direction="push_received",
        token_count=stored,
        server_url=f"practice:{auth.practice_id}",
        status="completed",
    )

    return SyncPushResponse(stored=stored, errors=len(tokens) - stored)


@router.post("/sync/pull", response_model=SyncPullResponse)
async def sync_pull(body: SyncPullRequest, request: Request):
    """Return cloud tokens that the local vault is missing.

    Tokens are re-encrypted with the local vault's key so the client
    can import them directly.
    """
    sync_mgr = _get_sync_manager(request)

    local_key = body.vault_key.encode() if isinstance(body.vault_key, str) else body.vault_key
    tokens = sync_mgr.export_tokens_for_pull(body.token_ids, local_key)

    # Log the sync on the cloud side
    auth: AuthContext = request.state.auth
    sync_mgr.cloud_vault.log_sync(
        direction="pull_served",
        token_count=len(tokens),
        server_url=f"practice:{auth.practice_id}",
        status="completed",
    )

    return SyncPullResponse(tokens=tokens, count=len(tokens))


@router.get("/sync/status", response_model=SyncStatusResponse)
async def sync_status(request: Request):
    """Get sync status for the current practice's cloud vault."""
    sync_mgr = _get_sync_manager(request)
    status = sync_mgr.get_sync_status()

    return SyncStatusResponse(**status)
