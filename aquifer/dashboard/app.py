"""Aquifer QC Dashboard — FastAPI + Jinja2 web UI."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aquifer.core import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

app = FastAPI(title="Aquifer Dashboard", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def configure(vault_path: Path, password: str, output_dir: Path | None = None):
    """Configure the dashboard with vault connection."""
    from aquifer.vault.store import TokenVault

    output_dir = output_dir or Path("./aqf_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    vault = TokenVault(vault_path, password)
    if not vault_path.exists():
        vault.init()
    else:
        vault.open()

    app.state.vault = vault
    app.state.vault_path = vault_path
    app.state.output_dir = output_dir


def _get_vault(request: Request):
    vault = getattr(request.app.state, "vault", None)
    if vault is None:
        raise RuntimeError("Dashboard not configured. Call configure() first.")
    return vault


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    vault = _get_vault(request)
    stats = vault.get_stats()
    files = vault.get_all_files()
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "stats": stats,
        "recent_files": files[:10],
    })


@app.get("/files", response_class=HTMLResponse)
async def files_list(request: Request):
    vault = _get_vault(request)
    files = vault.get_all_files()
    return templates.TemplateResponse(request, "files.html", {
        "request": request,
        "files": files,
    })


@app.get("/files/{file_hash}", response_class=HTMLResponse)
async def file_detail(request: Request, file_hash: str):
    vault = _get_vault(request)
    file_record = vault.get_file_record(file_hash)
    if file_record is None:
        return templates.TemplateResponse(request, "error.html", {
            "request": request,
            "error": "File not found",
            "detail": f"No file with hash {file_hash[:16]}... exists in the vault.",
        }, status_code=404)

    tokens = vault.get_tokens_for_file(file_hash)
    safe_tokens = [
        {"token_id": t.token_id, "phi_type": t.phi_type,
         "confidence": t.confidence}
        for t in tokens
    ]

    return templates.TemplateResponse(request, "file_detail.html", {
        "request": request,
        "file_record": file_record,
        "tokens": safe_tokens,
    })


@app.get("/review/{file_hash}", response_class=HTMLResponse)
async def review(request: Request, file_hash: str):
    vault = _get_vault(request)
    file_record = vault.get_file_record(file_hash)
    if file_record is None:
        return templates.TemplateResponse(request, "error.html", {
            "request": request,
            "error": "File not found",
            "detail": f"No file with hash {file_hash[:16]}... exists in the vault.",
        }, status_code=404)

    tokens = vault.get_tokens_for_file(file_hash)
    low_conf = [
        {"token_id": t.token_id, "phi_type": t.phi_type,
         "confidence": t.confidence}
        for t in tokens if t.confidence < 0.7
    ]

    return templates.TemplateResponse(request, "review.html", {
        "request": request,
        "file_record": file_record,
        "low_confidence_tokens": low_conf,
    })


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {
        "request": request,
        "message": None,
        "error": None,
    })


@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload and de-identify a file."""
    from aquifer.engine.pipeline import process_file

    vault = _get_vault(request)

    # Validate file extension
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return templates.TemplateResponse(request, "upload.html", {
            "request": request,
            "message": None,
            "error": f"Unsupported file type: {suffix}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        }, status_code=400)

    # Save upload to temp file
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)
    except Exception as e:
        logger.error(f"Failed to save upload: {e}")
        return templates.TemplateResponse(request, "upload.html", {
            "request": request,
            "message": None,
            "error": f"Failed to save uploaded file: {e}",
        }, status_code=500)

    try:
        output_path = request.app.state.output_dir / Path(file.filename).with_suffix(".aqf").name
        result = process_file(tmp_path, output_path, vault, use_ner=False)

        if result.errors:
            return templates.TemplateResponse(request, "upload.html", {
                "request": request,
                "message": None,
                "error": f"Processing error: {result.errors[0]}",
            }, status_code=400)

        return RedirectResponse(f"/files/{result.source_hash}", status_code=303)
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        return templates.TemplateResponse(request, "upload.html", {
            "request": request,
            "message": None,
            "error": f"De-identification failed: {e}",
        }, status_code=500)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/api/stats")
async def api_stats(request: Request):
    """JSON API for vault statistics."""
    vault = _get_vault(request)
    return vault.get_stats()


@app.get("/api/files")
async def api_files(request: Request):
    """JSON API for file listing."""
    vault = _get_vault(request)
    return vault.get_all_files()


def run(vault_path: str, password: str, host: str = "127.0.0.1", port: int = 8080):
    """Run the dashboard server."""
    import uvicorn
    configure(Path(vault_path), password)
    uvicorn.run(app, host=host, port=port)

