"""Dashboard routes — server-side rendered web UI for the Strata platform.

Uses cookie-based JWT sessions. All data is fetched by calling the API
layer internally (same DB/vault instances) rather than making HTTP calls
to ourselves.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates

from aquifer.strata.auth import (
    AuthContext, create_jwt, decode_jwt, hash_password, verify_password,
    encrypt_vault_key, generate_practice_vault_key, generate_api_key,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

SESSION_COOKIE = "aq_session"


# --- Helpers ---

def _get_session(request: Request) -> dict | None:
    """Extract session from JWT cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    config = request.app.state.config
    payload = decode_jwt(token, config.jwt_secret)
    if not payload:
        return None
    db = request.app.state.db
    user = db.get_user(payload.get("sub", ""))
    if not user or not user["is_active"]:
        return None
    practice = db.get_practice(user["practice_id"])
    if not practice:
        return None
    return {
        "user_id": user["id"],
        "practice_id": practice["id"],
        "email": user["email"],
        "role": user["role"],
        "tier": practice["tier"],
        "practice_name": practice["name"],
    }


def _ctx(request: Request, session: dict | None, page: str = "", **extra) -> dict:
    """Build template context."""
    return {
        "request": request,
        "session": session,
        "active_page": page,
        "flash_message": extra.pop("flash_message", None),
        "flash_type": extra.pop("flash_type", None),
        **extra,
    }


def _login_redirect():
    return RedirectResponse("/dashboard/login", status_code=303)


def _cookie_secure(request: Request) -> bool:
    """Honor HTTPS both directly and through common proxy headers."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


# --- Auth Pages ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    session = _get_session(request)
    if session:
        return RedirectResponse("/dashboard/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, None))


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "")

    db = request.app.state.db
    config = request.app.state.config

    user = db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request, "login.html", _ctx(
            request, None, error="Invalid email or password", email=email,
        ))

    token = create_jwt(
        {"sub": user["id"], "practice_id": user["practice_id"], "role": user["role"]},
        config.jwt_secret, expiry_hours=config.jwt_expiry_hours,
    )

    response = RedirectResponse("/dashboard/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=_cookie_secure(request),
        max_age=config.jwt_expiry_hours * 3600,
    )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    session = _get_session(request)
    if session:
        return RedirectResponse("/dashboard/", status_code=303)
    return templates.TemplateResponse(request, "register.html", _ctx(request, None))


@router.post("/register")
async def register_submit(request: Request):
    import re
    form = await request.form()
    practice_name = form.get("practice_name", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")

    db = request.app.state.db
    config = request.app.state.config

    # Validation
    errors = []
    if len(practice_name) < 2:
        errors.append("Practice name must be at least 2 characters")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        errors.append("Invalid email address")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if db.get_user_by_email(email):
        errors.append("Email already registered")

    if errors:
        return templates.TemplateResponse(request, "register.html", _ctx(
            request, None, error="; ".join(errors),
            practice_name=practice_name, email=email,
        ))

    # Create practice
    slug = re.sub(r"[^a-z0-9]+", "-", practice_name.lower()).strip("-")
    if db.get_practice_by_slug(slug):
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    practice_id = str(uuid.uuid4())
    vault_key = generate_practice_vault_key()
    encrypted_key = encrypt_vault_key(vault_key, config.master_key)

    db.create_practice(
        id=practice_id, name=practice_name, slug=slug,
        vault_key_encrypted=encrypted_key,
    )
    request.app.state.vault_manager.init_practice(practice_id, vault_key)

    user_id = str(uuid.uuid4())
    db.create_user(
        id=user_id, practice_id=practice_id,
        email=email, password_hash=hash_password(password),
        role="admin",
    )

    token = create_jwt(
        {"sub": user_id, "practice_id": practice_id, "role": "admin"},
        config.jwt_secret, expiry_hours=config.jwt_expiry_hours,
    )

    response = RedirectResponse("/dashboard/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=_cookie_secure(request),
        max_age=config.jwt_expiry_hours * 3600,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=_cookie_secure(request))
    return response


# --- Dashboard Home ---

@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager
    practice = db.get_practice(session["practice_id"])

    # Get vault stats
    try:
        vault = vault_mgr.open_vault(session["practice_id"], practice["vault_key_encrypted"])
        vault_stats = vault.get_stats()
    except Exception:
        vault_stats = {"total_tokens": 0, "total_files": 0, "tokens_by_type": {}}

    usage = db.get_usage_stats(session["practice_id"], days=30)
    recent = db.list_files(session["practice_id"], limit=10)

    return templates.TemplateResponse(request, "home.html", _ctx(
        request, session, page="home",
        practice=practice, vault_stats=vault_stats,
        usage=usage, recent_files=recent,
    ))


# --- Upload ---

@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()
    return templates.TemplateResponse(request, "upload.html", _ctx(request, session, page="upload"))


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Process an uploaded file. Returns JSON for the JS upload handler."""
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    from aquifer.engine.pipeline import process_file

    db = request.app.state.db
    config = request.app.state.config
    vault_mgr = request.app.state.vault_manager

    suffix = Path(file.filename or "unknown.txt").suffix.lower()
    supported = {".pdf", ".docx", ".doc", ".txt", ".csv", ".json", ".xml",
                 ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}
    if suffix not in supported:
        return JSONResponse({"error": f"Unsupported file type: {suffix}"}, status_code=400)

    file_id = str(uuid.uuid4())
    upload_dir = vault_mgr.upload_dir(session["practice_id"])
    tmp_path = upload_dir / f"{file_id}{suffix}"
    file_size = 0

    with tmp_path.open("wb") as tmp_file:
        while chunk := await file.read(1024 * 1024):
            file_size += len(chunk)
            if file_size > config.max_upload_bytes:
                tmp_file.close()
                tmp_path.unlink(missing_ok=True)
                return JSONResponse({"error": "File too large"}, status_code=413)
            tmp_file.write(chunk)

    db.create_file_record(
        id=file_id, practice_id=session["practice_id"],
        original_filename=file.filename or "unknown",
        source_type=suffix.lstrip("."),
        source_hash="pending", file_size_bytes=file_size,
    )

    try:
        practice = db.get_practice(session["practice_id"])
        vault = vault_mgr.open_vault(session["practice_id"], practice["vault_key_encrypted"])
        aqf_output = vault_mgr.aqf_dir(session["practice_id"]) / f"{file_id}.aqf"

        result = process_file(tmp_path, aqf_output, vault, use_ner=config.use_ner)

        if result.errors:
            db.update_file_record(file_id, status="failed", error_message=result.errors[0])
            return JSONResponse({"error": result.errors[0]}, status_code=422)

        db.update_file_record(
            file_id, status="completed", aqf_hash=result.aqf_hash,
            aqf_storage_path=str(aqf_output), token_count=result.token_count,
        )
        db.log_usage(
            session["practice_id"], "deid", user_id=session["user_id"],
            file_id=file_id, bytes_processed=file_size,
        )

        return JSONResponse({
            "file_id": file_id,
            "token_count": result.token_count,
            "status": "completed",
        })
    except Exception as e:
        db.update_file_record(file_id, status="failed", error_message=str(e))
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        tmp_path.unlink(missing_ok=True)


# --- Files ---

@router.get("/files", response_class=HTMLResponse)
async def files_page(request: Request, offset: int = 0):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    limit = 50
    files = db.list_files(session["practice_id"], limit=limit, offset=offset)
    total = db.count_files(session["practice_id"])

    return templates.TemplateResponse(request, "files.html", _ctx(
        request, session, page="files",
        files=files, total=total, limit=limit, offset=offset,
    ))


@router.get("/files/{file_id}", response_class=HTMLResponse)
async def file_detail_page(request: Request, file_id: str):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != session["practice_id"]:
        return RedirectResponse("/dashboard/files", status_code=303)

    tokens = []
    metadata = {}
    integrity_valid = False

    if record["status"] == "completed" and record["aqf_storage_path"]:
        try:
            from aquifer.format.reader import read_aqf, verify_integrity
            aqf_path = Path(record["aqf_storage_path"])
            if aqf_path.exists():
                aqf = read_aqf(aqf_path)
                tokens = [
                    {"token_id": t.token_id, "phi_type": t.phi_type, "confidence": t.confidence}
                    for t in aqf.tokens
                ]
                metadata = aqf.metadata.model_dump()
                integrity_valid, _ = verify_integrity(aqf_path)
        except Exception:
            pass

    return templates.TemplateResponse(request, "file_detail.html", _ctx(
        request, session, page="files",
        file=record, tokens=tokens, metadata=metadata,
        integrity_valid=integrity_valid,
    ))


@router.get("/files/{file_id}/download")
async def file_download(request: Request, file_id: str):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != session["practice_id"]:
        return RedirectResponse("/dashboard/files", status_code=303)

    aqf_path = Path(record["aqf_storage_path"])
    if not aqf_path.exists():
        return RedirectResponse(f"/dashboard/files/{file_id}", status_code=303)

    safe_name = Path(record["original_filename"]).stem + ".aqf"
    return FileResponse(aqf_path, media_type="application/octet-stream", filename=safe_name)


@router.post("/files/{file_id}/rehydrate")
async def file_rehydrate(request: Request, file_id: str):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    if session["role"] != "admin":
        return JSONResponse({"error": "Admin role required"}, status_code=403)

    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != session["practice_id"]:
        return JSONResponse({"error": "File not found"}, status_code=404)

    try:
        from aquifer.rehydrate.engine import rehydrate
        practice = db.get_practice(session["practice_id"])
        vault = vault_mgr.open_vault(session["practice_id"], practice["vault_key_encrypted"])
        text = rehydrate(Path(record["aqf_storage_path"]), vault)
        db.log_usage(session["practice_id"], "rehydrate", user_id=session["user_id"], file_id=file_id)
        return PlainTextResponse(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Settings ---

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    practice = db.get_practice(session["practice_id"])
    api_keys = db.list_api_keys(session["practice_id"])
    usage = db.get_usage_stats(session["practice_id"], days=30)
    usage["file_count"] = db.count_files(session["practice_id"])

    from aquifer.licensing import Tier, TIER_FILE_LIMITS
    tier = Tier(practice["tier"]) if practice["tier"] in [t.value for t in Tier] else Tier.COMMUNITY
    usage["file_limit"] = TIER_FILE_LIMITS.get(tier)
    usage["usage_pct"] = None
    if usage["file_limit"]:
        usage["usage_pct"] = round((usage["file_count"] / usage["file_limit"]) * 100, 1)

    server_url = str(request.base_url).rstrip("/")

    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, session, page="settings",
        practice=practice, api_keys=api_keys, usage=usage,
        server_url=server_url,
    ))


@router.post("/settings/api-keys")
async def create_api_key_dashboard(request: Request):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    body = await request.json()
    db = request.app.state.db

    full_key, key_hash = generate_api_key()
    key_id = str(uuid.uuid4())

    db.create_api_key(
        id=key_id, practice_id=session["practice_id"], user_id=session["user_id"],
        key_hash=key_hash, key_prefix=full_key[:11],
        name=body.get("name"), scopes="deid,files",
    )

    return JSONResponse({"id": key_id, "key": full_key, "key_prefix": full_key[:11]})


@router.delete("/settings/api-keys/{key_id}")
async def revoke_api_key_dashboard(request: Request, key_id: str):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    db = request.app.state.db
    if db.revoke_api_key(key_id, session["practice_id"]):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Key not found"}, status_code=404)


@router.post("/settings")
async def settings_submit(request: Request):
    """Handle password change from the settings form."""
    import re

    session = _get_session(request)
    if not session:
        return _login_redirect()

    form = await request.form()
    current_password = form.get("current_password", "")
    new_password = form.get("new_password", "")
    confirm_password = form.get("confirm_password", "")

    db = request.app.state.db
    practice = db.get_practice(session["practice_id"])
    api_keys = db.list_api_keys(session["practice_id"])
    usage = db.get_usage_stats(session["practice_id"], days=30)
    usage["file_count"] = db.count_files(session["practice_id"])

    from aquifer.licensing import Tier, TIER_FILE_LIMITS
    tier = Tier(practice["tier"]) if practice["tier"] in [t.value for t in Tier] else Tier.COMMUNITY
    usage["file_limit"] = TIER_FILE_LIMITS.get(tier)
    usage["usage_pct"] = None
    if usage["file_limit"]:
        usage["usage_pct"] = round((usage["file_count"] / usage["file_limit"]) * 100, 1)

    server_url = str(request.base_url).rstrip("/")

    def _render_settings(error=None, success=None):
        return templates.TemplateResponse(request, "settings.html", _ctx(
            request, session, page="settings",
            practice=practice, api_keys=api_keys, usage=usage,
            server_url=server_url,
            flash_message=error or success,
            flash_type="error" if error else "success",
        ))

    # Validate inputs
    errors = []
    user = db.get_user(session["user_id"])
    if not user or not verify_password(current_password, user["password_hash"]):
        errors.append("Current password is incorrect")
    if new_password != confirm_password:
        errors.append("New passwords do not match")
    if len(new_password) < 10:
        errors.append("New password must be at least 10 characters")
    if not re.search(r"[A-Z]", new_password):
        errors.append("New password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", new_password):
        errors.append("New password must contain at least one lowercase letter")
    if not re.search(r"\d", new_password):
        errors.append("New password must contain at least one digit")

    if errors:
        return _render_settings(error="; ".join(errors))

    db.update_user_password(session["user_id"], hash_password(new_password))
    return _render_settings(success="Password updated successfully")


# --- Patients ---

@router.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    links = db.get_practice_patients(session["practice_id"])

    # Enrich each link with patient details and file count
    patients = []
    for link in links:
        patient = db.get_patient(link["patient_id"])
        if not patient:
            continue
        file_count = db.count_patient_files(session["practice_id"], link["patient_id"])
        patients.append({
            "patient_id": patient["id"],
            "email": patient["email"],
            "email_verified": patient["email_verified"],
            "linked_at": link["linked_at"],
            "file_count": file_count,
        })

    return templates.TemplateResponse(request, "patients.html", _ctx(
        request, session, page="patients",
        patients=patients,
    ))


@router.get("/patients/{patient_id}", response_class=HTMLResponse)
async def patient_detail_page(request: Request, patient_id: str, flash: str = "", flash_type: str = ""):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db

    # Verify this patient is linked to the current practice
    links = db.get_patient_practices(patient_id)
    practice_ids = {l["practice_id"] for l in links}
    if session["practice_id"] not in practice_ids:
        return RedirectResponse("/dashboard/patients", status_code=303)

    patient = db.get_patient(patient_id)
    if not patient:
        return RedirectResponse("/dashboard/patients", status_code=303)

    link = next((l for l in links if l["practice_id"] == session["practice_id"]), None)
    consents = db.list_consents_for_patient(patient_id)
    # Only show consents involving this practice
    consents = [c for c in consents if
                c["source_practice_id"] == session["practice_id"] or
                c["target_practice_id"] == session["practice_id"]]

    transfer_history = []
    for consent in consents:
        for t in db.get_transfers_for_consent(consent["id"]):
            transfer_history.append(t)
    transfer_history.sort(key=lambda t: t["created_at"], reverse=True)

    return templates.TemplateResponse(request, "patient_detail.html", _ctx(
        request, session, page="patients",
        patient=patient, link=link,
        consents=consents, transfer_history=transfer_history,
        flash_message=flash or None,
        flash_type=flash_type or "info",
    ))


@router.post("/patients/{patient_id}/consent")
async def create_consent(request: Request, patient_id: str):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    patient_hub = request.app.state.patient_hub

    # Verify patient is linked to this practice
    links = db.get_patient_practices(patient_id)
    practice_ids = {l["practice_id"] for l in links}
    if session["practice_id"] not in practice_ids:
        return RedirectResponse("/dashboard/patients", status_code=303)

    form = await request.form()
    target_slug = form.get("target_practice_slug", "").strip()
    scope_list = form.getlist("scope")
    scope = ",".join(scope_list) if scope_list else "all"

    target_practice = db.get_practice_by_slug(target_slug)
    if not target_practice:
        patient = db.get_patient(patient_id)
        consents = db.list_consents_for_patient(patient_id)
        consents = [c for c in consents if
                    c["source_practice_id"] == session["practice_id"] or
                    c["target_practice_id"] == session["practice_id"]]
        link = next((l for l in links if l["practice_id"] == session["practice_id"]), None)
        return templates.TemplateResponse(request, "patient_detail.html", _ctx(
            request, session, page="patients",
            patient=patient, link=link, consents=consents, transfer_history=[],
            flash_message="Target practice not found. Check the slug and try again.",
            flash_type="error",
        ))

    try:
        patient_hub.create_consent(
            patient_id=patient_id,
            source_practice_id=session["practice_id"],
            target_practice_id=target_practice["id"],
            scope=scope,
        )
    except ValueError as e:
        patient = db.get_patient(patient_id)
        consents = db.list_consents_for_patient(patient_id)
        consents = [c for c in consents if
                    c["source_practice_id"] == session["practice_id"] or
                    c["target_practice_id"] == session["practice_id"]]
        link = next((l for l in links if l["practice_id"] == session["practice_id"]), None)
        return templates.TemplateResponse(request, "patient_detail.html", _ctx(
            request, session, page="patients",
            patient=patient, link=link, consents=consents, transfer_history=[],
            flash_message=str(e),
            flash_type="error",
        ))

    return RedirectResponse(
        f"/dashboard/patients/{patient_id}?flash=Consent+created+successfully&flash_type=success",
        status_code=303,
    )


# --- Transfers ---

@router.get("/transfers", response_class=HTMLResponse)
async def transfers_page(request: Request, flash: str = "", flash_type: str = ""):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    practice_id = session["practice_id"]

    all_consents = db.get_consents_for_practice(practice_id)

    outgoing = [c for c in all_consents if c["source_practice_id"] == practice_id]
    incoming = [c for c in all_consents if c["target_practice_id"] == practice_id]

    # Annotate each consent with practice names
    practice_cache: dict[str, dict] = {}

    def _practice_name(pid: str) -> str:
        if pid not in practice_cache:
            p = db.get_practice(pid)
            practice_cache[pid] = p or {}
        return practice_cache[pid].get("name", pid[:8])

    for c in outgoing:
        c["target_practice_name"] = _practice_name(c["target_practice_id"])
        patient = db.get_patient(c["patient_id"])
        c["patient_email"] = patient["email"] if patient else c["patient_id"][:8]

    for c in incoming:
        c["source_practice_name"] = _practice_name(c["source_practice_id"])
        patient = db.get_patient(c["patient_id"])
        c["patient_email"] = patient["email"] if patient else c["patient_id"][:8]

    transfers = db.get_transfers_for_practice(practice_id)

    return templates.TemplateResponse(request, "transfers.html", _ctx(
        request, session, page="transfers",
        outgoing=outgoing, incoming=incoming, transfers=transfers,
        flash_message=flash or None,
        flash_type=flash_type or "info",
    ))


@router.post("/transfers/{consent_id}/authorize")
async def authorize_transfer(request: Request, consent_id: str):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    patient_hub = request.app.state.patient_hub

    consent = db.get_consent(consent_id)
    if not consent or consent["source_practice_id"] != session["practice_id"]:
        return RedirectResponse("/dashboard/transfers", status_code=303)

    try:
        patient_hub.authorize_consent(consent_id, consent["patient_id"])
        flash_msg = "Consent authorized. Transfer is now ready to execute."
        flash_type = "success"
    except ValueError as e:
        flash_msg = str(e)
        flash_type = "error"

    # Flash via query param since we're redirecting
    import urllib.parse
    qs = urllib.parse.urlencode({"flash": flash_msg, "flash_type": flash_type})
    return RedirectResponse(f"/dashboard/transfers?{qs}", status_code=303)


@router.post("/transfers/{consent_id}/execute")
async def execute_transfer(request: Request, consent_id: str):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    patient_hub = request.app.state.patient_hub

    consent = db.get_consent(consent_id)
    if not consent or consent["source_practice_id"] != session["practice_id"]:
        return RedirectResponse("/dashboard/transfers", status_code=303)

    try:
        result = patient_hub.execute_transfer(consent_id)
        flash_msg = f"Transfer complete. {result.token_count} token(s) transferred."
        flash_type = "success"
    except ValueError as e:
        flash_msg = str(e)
        flash_type = "error"

    import urllib.parse
    qs = urllib.parse.urlencode({"flash": flash_msg, "flash_type": flash_type})
    return RedirectResponse(f"/dashboard/transfers?{qs}", status_code=303)


# --- Check-in ---

@router.get("/checkin", response_class=HTMLResponse)
async def checkin_page(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    from aquifer.core import PRACTICE_TYPE_DEFAULTS
    practice_types = sorted(PRACTICE_TYPE_DEFAULTS.keys())

    return templates.TemplateResponse(request, "checkin.html", _ctx(
        request, session, page="checkin",
        practice_types=practice_types,
    ))


@router.post("/checkin", response_class=HTMLResponse)
async def checkin_submit(request: Request):
    session = _get_session(request)
    if not session:
        return _login_redirect()

    from aquifer.core import PRACTICE_TYPE_DEFAULTS
    practice_types = sorted(PRACTICE_TYPE_DEFAULTS.keys())

    form = await request.form()
    share_key = form.get("share_key", "").strip().upper()
    practice_type = form.get("practice_type", "").strip() or None

    if not share_key:
        return templates.TemplateResponse(request, "checkin.html", _ctx(
            request, session, page="checkin",
            practice_types=practice_types,
            flash_message="Please enter a share key.",
            flash_type="error",
        ))

    hub = request.app.state.patient_hub
    db = request.app.state.db

    patient = db.get_patient_by_share_key(share_key)
    if not patient:
        return templates.TemplateResponse(request, "checkin.html", _ctx(
            request, session, page="checkin",
            practice_types=practice_types,
            share_key=share_key,
            flash_message="Share key not found. Please ask the patient to verify their key.",
            flash_type="error",
        ))

    try:
        transfers = hub.pull_records(
            share_key=share_key,
            target_practice_id=session["practice_id"],
            target_practice_type=practice_type,
        )
    except ValueError as exc:
        return templates.TemplateResponse(request, "checkin.html", _ctx(
            request, session, page="checkin",
            practice_types=practice_types,
            share_key=share_key,
            flash_message=str(exc),
            flash_type="error",
        ))

    db.log_audit(
        practice_id=session["practice_id"],
        action="patient.pull_records",
        resource_type="patient",
        resource_id=patient["id"],
        user_id=session["user_id"],
        detail=f"share_key={share_key} practice_type={practice_type} transfers={len(transfers)}",
    )

    # Collect domains from all source practices
    domains: set[str] = set()
    for transfer in transfers:
        consent = db.get_consent(transfer.consent_id)
        if consent and consent["scope"] != "all":
            for d in consent["scope"].split(","):
                d = d.strip()
                if d:
                    domains.add(d)

    total_tokens = sum(t.token_count for t in transfers)
    email = patient["email"]
    at = email.index("@")
    masked_email = email[:2] + ("*" * (at - 2)) + email[at:]

    return templates.TemplateResponse(request, "checkin.html", _ctx(
        request, session, page="checkin",
        practice_types=practice_types,
        result={
            "transfers": len(transfers),
            "total_tokens": total_tokens,
            "domains": sorted(domains),
            "masked_email": masked_email,
        },
        flash_message=(
            f"Records pulled from {len(transfers)} practice(s). "
            f"{total_tokens} data point(s) transferred."
        ),
        flash_type="success",
    ))


# --- File Deletion ---

@router.post("/files/{file_id}/delete")
async def file_delete(request: Request, file_id: str):
    """Delete a processed file record and its .aqf file from disk."""
    session = _get_session(request)
    if not session:
        return _login_redirect()

    db = request.app.state.db
    record = db.get_file_record(file_id)
    if not record or record["practice_id"] != session["practice_id"]:
        return RedirectResponse("/dashboard/files", status_code=303)

    # Remove the .aqf file from disk if present
    if record.get("aqf_storage_path"):
        aqf_path = Path(record["aqf_storage_path"])
        aqf_path.unlink(missing_ok=True)

    db.delete_file_record(file_id, session["practice_id"])
    return RedirectResponse("/dashboard/files", status_code=303)
