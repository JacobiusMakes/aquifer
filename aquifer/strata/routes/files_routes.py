"""File management routes: list, inspect, download, rehydrate."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from aquifer.strata.auth import AuthContext, has_api_key_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    id: str
    original_filename: str
    source_type: str
    source_hash: str
    aqf_hash: str | None
    token_count: int
    file_size_bytes: int
    status: str
    created_at: str
    completed_at: str | None


class FileListResponse(BaseModel):
    files: list[FileInfo]
    total: int
    limit: int
    offset: int


class FileInspectResponse(BaseModel):
    id: str
    original_filename: str
    source_type: str
    token_count: int
    tokens: list[dict]  # token_id, phi_type, confidence (NO phi_value)
    metadata: dict
    integrity_valid: bool


def _require_files_scope(auth: AuthContext) -> None:
    if not has_api_key_scopes(auth, "files"):
        raise HTTPException(403, "API key missing required 'files' scope")


def _require_vault_scope(auth: AuthContext) -> None:
    if not has_api_key_scopes(auth, "vault"):
        raise HTTPException(403, "API key missing required 'vault' scope")


@router.get("", response_model=FileListResponse)
async def list_files(
    request: Request, limit: int = 50, offset: int = 0,
):
    """List processed files for the current practice."""
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    db = request.app.state.db

    files = db.list_files(auth.practice_id, limit=min(limit, 200), offset=offset)
    total = db.count_files(auth.practice_id)

    return FileListResponse(files=files, total=total, limit=limit, offset=offset)


@router.get("/{file_id}", response_model=FileInfo)
async def get_file(file_id: str, request: Request):
    """Get details for a specific processed file."""
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    db = request.app.state.db

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != auth.practice_id:
        raise HTTPException(404, "File not found")

    return record


@router.get("/{file_id}/download")
async def download_aqf(file_id: str, request: Request):
    """Download the .aqf file."""
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    db = request.app.state.db

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != auth.practice_id:
        raise HTTPException(404, "File not found")
    if record["status"] != "completed":
        raise HTTPException(400, f"File not ready: status={record['status']}")

    aqf_path = Path(record["aqf_storage_path"])
    if not aqf_path.exists():
        raise HTTPException(404, "AQF file not found on disk")

    db.log_usage(auth.practice_id, "download", user_id=auth.user_id, file_id=file_id)

    safe_name = Path(record["original_filename"]).stem + ".aqf"
    return FileResponse(
        aqf_path, media_type="application/octet-stream",
        filename=safe_name,
    )


@router.get("/{file_id}/inspect", response_model=FileInspectResponse)
async def inspect_file(file_id: str, request: Request):
    """Inspect an .aqf file — view tokens and metadata without PHI."""
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    db = request.app.state.db

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != auth.practice_id:
        raise HTTPException(404, "File not found")
    if record["status"] != "completed":
        raise HTTPException(400, f"File not ready: status={record['status']}")

    aqf_path = Path(record["aqf_storage_path"])
    if not aqf_path.exists():
        raise HTTPException(404, "AQF file not found on disk")

    from aquifer.format.reader import read_aqf, verify_integrity

    aqf = read_aqf(aqf_path)
    is_valid, _ = verify_integrity(aqf_path)

    tokens = [
        {"token_id": t.token_id, "phi_type": t.phi_type, "confidence": t.confidence}
        for t in aqf.tokens
    ]

    return FileInspectResponse(
        id=file_id,
        original_filename=record["original_filename"],
        source_type=record["source_type"],
        token_count=len(tokens),
        tokens=tokens,
        metadata=aqf.metadata.model_dump(),
        integrity_valid=is_valid,
    )


@router.post("/{file_id}/rehydrate")
async def rehydrate_file(file_id: str, request: Request):
    """Rehydrate an .aqf file — restore original PHI content.

    Requires 'admin' role. Returns the full rehydrated text.
    This is an audited operation.
    """
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    _require_vault_scope(auth)
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    # Only admins can rehydrate
    if auth.role not in ("admin",):
        raise HTTPException(403, "Rehydration requires admin role")

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != auth.practice_id:
        raise HTTPException(404, "File not found")
    if record["status"] != "completed":
        raise HTTPException(400, f"File not ready: status={record['status']}")

    aqf_path = Path(record["aqf_storage_path"])
    if not aqf_path.exists():
        raise HTTPException(404, "AQF file not found on disk")

    from aquifer.rehydrate.engine import rehydrate

    practice = db.get_practice(auth.practice_id)
    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])

    text = rehydrate(aqf_path, vault)

    # Audit log
    db.log_usage(auth.practice_id, "rehydrate", user_id=auth.user_id, file_id=file_id)

    return PlainTextResponse(text)


@router.get("/{file_id}/rehydrate-stream")
async def rehydrate_file_stream(file_id: str, request: Request):
    """Rehydrate an .aqf file as a streaming response — for large files.

    Requires 'admin' role. Yields rehydrated text line by line to avoid
    holding the entire document in memory.
    This is an audited operation.
    """
    auth: AuthContext = request.state.auth
    _require_files_scope(auth)
    _require_vault_scope(auth)
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    if auth.role not in ("admin",):
        raise HTTPException(403, "Rehydration requires admin role")

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != auth.practice_id:
        raise HTTPException(404, "File not found")
    if record["status"] != "completed":
        raise HTTPException(400, f"File not ready: status={record['status']}")

    aqf_path = Path(record["aqf_storage_path"])
    if not aqf_path.exists():
        raise HTTPException(404, "AQF file not found on disk")

    from aquifer.rehydrate.engine import rehydrate_to_stream_simple

    practice = db.get_practice(auth.practice_id)
    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])

    db.log_usage(auth.practice_id, "rehydrate", user_id=auth.user_id, file_id=file_id)

    def line_generator():
        for line in rehydrate_to_stream_simple(aqf_path, vault):
            yield line + "\n"

    return StreamingResponse(line_generator(), media_type="text/plain")
