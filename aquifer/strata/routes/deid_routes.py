"""De-identification routes: upload files, process, return .aqf."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from aquifer.core import SUPPORTED_EXTENSIONS
from aquifer.strata.auth import AuthContext, has_api_key_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deid", tags=["de-identification"])


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

    # Sanitize filename — strip directory components to prevent path traversal
    safe_filename = Path(file.filename or "unknown.txt").name
    suffix = Path(safe_filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400, f"Unsupported file type: {suffix}. "
                 f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # Reject obviously oversized uploads early via Content-Length header
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
            if declared_size > config.max_upload_bytes:
                raise HTTPException(
                    413, f"File too large. Maximum: {config.max_upload_bytes // (1024*1024)} MB"
                )
        except ValueError:
            pass  # Malformed header — streaming check will still enforce the limit

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
        original_filename=safe_filename,
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

        # Extract data_domain from pipeline metadata if available
        data_domain = None
        if result.aqf_path:
            try:
                from aquifer.format.reader import read_aqf
                aqf_data = read_aqf(Path(result.aqf_path))
                data_domain = aqf_data.metadata.data_domain
            except Exception:
                pass

        db.update_file_record(
            file_id, status="completed",
            aqf_hash=result.aqf_hash,
            aqf_storage_path=str(aqf_output),
            token_count=result.token_count,
            data_domain=data_domain,
        )

        db.log_usage(
            auth.practice_id, "deid", user_id=auth.user_id,
            file_id=file_id, bytes_processed=file_size,
        )

        return DeidResponse(
            file_id=file_id,
            original_filename=safe_filename,
            source_type=result.source_type,
            status="completed",
            token_count=result.token_count,
            aqf_hash=result.aqf_hash,
            message=f"De-identified successfully. {result.token_count} PHI tokens replaced.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"De-identification failed for file_id={file_id}: {e}", exc_info=True
        )
        db.update_file_record(file_id, status="failed", error_message=str(e))
        raise HTTPException(
            500, f"De-identification failed for file {file_id}. "
                 "Check server logs for details."
        )
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
                original_filename=Path(file.filename or "unknown").name,
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


# ---------------------------------------------------------------------------
# Async batch processing with real-time progress
# ---------------------------------------------------------------------------

class JobSubmitResponse(BaseModel):
    job_id: str
    total_files: int
    status: str
    ws_url: str
    poll_url: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_files: int
    completed_files: int
    failed_files: int
    current_file: str | None
    percent: float
    result: dict | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None


class JobListResponse(BaseModel):
    jobs: list[JobStatusResponse]


@router.post("/batch-async", response_model=JobSubmitResponse, status_code=202)
async def deid_batch_async(request: Request, files: list[UploadFile] = File(...)):
    """Submit a batch de-identification job for background processing.

    Returns immediately with a job_id. Connect to the WebSocket at
    /ws/jobs/{job_id} for real-time progress, or poll GET /deid/jobs/{job_id}.
    """
    auth: AuthContext = request.state.auth
    config = request.app.state.config
    vault_mgr = request.app.state.vault_manager
    _require_deid_scope(auth)

    if len(files) > config.max_batch_size:
        raise HTTPException(
            400, f"Too many files. Maximum batch size: {config.max_batch_size}"
        )

    from aquifer.strata.jobs import FileSpec

    file_specs: list[FileSpec] = []
    upload_dir = vault_mgr.upload_dir(auth.practice_id)

    for file in files:
        safe_filename = Path(file.filename or "unknown.txt").name
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                400, f"Unsupported file type: {suffix} (file: {safe_filename})"
            )

        file_id = str(uuid.uuid4())
        tmp_path = upload_dir / f"{file_id}{suffix}"
        file_size = 0

        with tmp_path.open("wb") as tmp_file:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > config.max_upload_bytes:
                    tmp_file.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(
                        413, f"File too large: {safe_filename}. "
                             f"Maximum: {config.max_upload_bytes // (1024*1024)} MB"
                    )
                tmp_file.write(chunk)

        file_specs.append(FileSpec(
            filename=safe_filename,
            path=tmp_path,
            suffix=suffix,
            file_size=file_size,
        ))

    job_runner = request.app.state.job_runner
    job_id = job_runner.submit(auth.practice_id, auth.user_id, file_specs)

    base_url = str(request.base_url).rstrip("/")
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"

    return JobSubmitResponse(
        job_id=job_id,
        total_files=len(file_specs),
        status="pending",
        ws_url=f"{ws_scheme}://{request.url.netloc}/ws/jobs/{job_id}",
        poll_url=f"{base_url}/api/v1/deid/jobs/{job_id}",
        message=f"Job submitted. {len(file_specs)} files queued for processing.",
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request):
    """Get the current status of a de-identification job."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    job = db.get_job(job_id)
    if not job or job["practice_id"] != auth.practice_id:
        raise HTTPException(404, "Job not found")

    total = job["total_files"]
    done = job["completed_files"] + job["failed_files"]
    percent = round(done / total * 100, 1) if total > 0 else 100.0

    result = None
    if job["result_json"]:
        try:
            result = json.loads(job["result_json"])
        except (json.JSONDecodeError, ValueError):
            pass

    return JobStatusResponse(
        job_id=job["id"],
        status=job["status"],
        total_files=total,
        completed_files=job["completed_files"],
        failed_files=job["failed_files"],
        current_file=job["current_file"] or None,
        percent=percent,
        result=result,
        created_at=str(job["created_at"]) if job["created_at"] else None,
        started_at=str(job["started_at"]) if job.get("started_at") else None,
        completed_at=str(job["completed_at"]) if job.get("completed_at") else None,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(request: Request, limit: int = 20):
    """List recent jobs for the current practice."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    jobs = db.list_jobs(auth.practice_id, limit=limit)
    items = []
    for job in jobs:
        total = job["total_files"]
        done = job["completed_files"] + job["failed_files"]
        percent = round(done / total * 100, 1) if total > 0 else 100.0

        result = None
        if job["result_json"]:
            try:
                result = json.loads(job["result_json"])
            except (json.JSONDecodeError, ValueError):
                pass

        items.append(JobStatusResponse(
            job_id=job["id"],
            status=job["status"],
            total_files=total,
            completed_files=job["completed_files"],
            failed_files=job["failed_files"],
            current_file=job["current_file"] or None,
            percent=percent,
            result=result,
            created_at=str(job["created_at"]) if job["created_at"] else None,
            started_at=str(job["started_at"]) if job.get("started_at") else None,
            completed_at=str(job["completed_at"]) if job.get("completed_at") else None,
        ))

    return JobListResponse(jobs=items)
