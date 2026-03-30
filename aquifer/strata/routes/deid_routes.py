"""De-identification routes: upload files, process, return .aqf."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from aquifer.strata.auth import AuthContext, has_api_key_scopes

router = APIRouter(prefix="/deid", tags=["de-identification"])

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".csv", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp",
}


class DeidResponse(BaseModel):
    file_id: str
    original_filename: str
    source_type: str
    status: str
    token_count: int
    aqf_hash: str | None
    message: str


class BatchDeidResponse(BaseModel):
    results: list[DeidResponse]
    total: int
    succeeded: int
    failed: int


def _require_deid_scope(auth: AuthContext) -> None:
    if not has_api_key_scopes(auth, "deid"):
        raise HTTPException(403, "API key missing required 'deid' scope")


@router.post("", response_model=DeidResponse, status_code=201)
async def deid_file(request: Request, file: UploadFile = File(...)):
    """Upload and de-identify a single file.

    Returns the processing result with file_id for subsequent operations
    (download .aqf, rehydrate, inspect).
    """
    auth: AuthContext = request.state.auth
    app = request.app
    db = app.state.db
    config = app.state.config
    vault_mgr = app.state.vault_manager
    _require_deid_scope(auth)

    # Validate file
    suffix = Path(file.filename or "unknown.txt").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400, f"Unsupported file type: {suffix}. "
                 f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    file_id = str(uuid.uuid4())
    upload_dir = vault_mgr.upload_dir(auth.practice_id)
    tmp_path = upload_dir / f"{file_id}{suffix}"
    file_size = 0

    # Stream the upload to disk so oversize files do not get materialized in RAM.
    with tmp_path.open("wb") as tmp_file:
        while chunk := await file.read(1024 * 1024):
            file_size += len(chunk)
            if file_size > config.max_upload_bytes:
                tmp_file.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"File too large. Maximum: {config.max_upload_bytes // (1024*1024)} MB"
                )
            tmp_file.write(chunk)

    # Create DB record
    db.create_file_record(
        id=file_id, practice_id=auth.practice_id,
        original_filename=file.filename or "unknown",
        source_type=suffix.lstrip("."),
        source_hash="pending",
        file_size_bytes=file_size,
    )
    db.update_file_record(file_id, status="processing")

    # Process through pipeline
    try:
        from aquifer.engine.pipeline import process_file

        practice = db.get_practice(auth.practice_id)
        vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])

        aqf_output = vault_mgr.aqf_dir(auth.practice_id) / f"{file_id}.aqf"
        result = process_file(
            tmp_path, aqf_output, vault,
            use_ner=config.use_ner, verbose=False,
        )

        if result.errors:
            db.update_file_record(
                file_id, status="failed", error_message=result.errors[0],
            )
            raise HTTPException(422, f"De-identification failed: {result.errors[0]}")

        db.update_file_record(
            file_id, status="completed",
            aqf_hash=result.aqf_hash,
            aqf_storage_path=str(aqf_output),
            token_count=result.token_count,
        )

        db.log_usage(
            auth.practice_id, "deid", user_id=auth.user_id,
            file_id=file_id, bytes_processed=file_size,
        )

        return DeidResponse(
            file_id=file_id,
            original_filename=file.filename or "unknown",
            source_type=result.source_type,
            status="completed",
            token_count=result.token_count,
            aqf_hash=result.aqf_hash,
            message=f"De-identified successfully. {result.token_count} PHI tokens replaced.",
        )

    except HTTPException:
        raise
    except Exception as e:
        db.update_file_record(file_id, status="failed", error_message=str(e))
        raise HTTPException(500, f"Processing error: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/batch", response_model=BatchDeidResponse, status_code=201)
async def deid_batch(request: Request, files: list[UploadFile] = File(...)):
    """Upload and de-identify multiple files in a single request."""
    auth: AuthContext = request.state.auth
    config = request.app.state.config

    if len(files) > config.max_batch_size:
        raise HTTPException(
            400, f"Too many files. Maximum batch size: {config.max_batch_size}"
        )

    results = []
    succeeded = 0
    failed = 0

    for file in files:
        try:
            # Reuse the single-file endpoint logic
            result = await deid_file(request, file)
            results.append(result)
            succeeded += 1
        except HTTPException as e:
            results.append(DeidResponse(
                file_id="",
                original_filename=file.filename or "unknown",
                source_type="unknown",
                status="failed",
                token_count=0,
                aqf_hash=None,
                message=str(e.detail),
            ))
            failed += 1

    return BatchDeidResponse(
        results=results, total=len(files),
        succeeded=succeeded, failed=failed,
    )
