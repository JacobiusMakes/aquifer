"""Patient-facing API routes for the Aquifer form-filler app.

Separate from practice-facing Strata routes. Authentication uses share keys
(X-Share-Key header or body field) rather than practice JWTs.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patient", tags=["patient-app"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ScanFormResponse(BaseModel):
    fields: list[dict]
    form_text: str


class FillFormRequest(BaseModel):
    share_key: str
    form_text: str


class FillFormResponse(BaseModel):
    filled_text: str
    summary: str


class MyDataRequest(BaseModel):
    share_key: str
    otp: str | None = None


class MyDataResponse(BaseModel):
    patient_id: str
    email_masked: str
    fields: dict[str, str]
    otp_verified: bool


class ShareEmailRequest(BaseModel):
    share_key: str
    practice_email: str
    message: str | None = None


class ShareEmailResponse(BaseModel):
    sent: bool
    message: str


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _resolve_patient(share_key: str, db) -> dict:
    """Look up patient by share key. Raises 401 if invalid."""
    patient = db.get_patient_by_share_key(share_key.upper().strip())
    if not patient:
        raise HTTPException(401, "Invalid share key")
    if not patient["email_verified"]:
        raise HTTPException(403, "Email not verified. Complete verification first.")
    return patient


def _get_patient_data(patient: dict, app_state) -> dict[str, str]:
    """Retrieve all stored PHI for a patient across all linked practices.

    Delegates to PatientHub.get_patient_data_summary(), which opens each
    linked practice's vault, decrypts tokens, and returns a merged flat dict.
    """
    hub = app_state.patient_hub
    return hub.get_patient_data_summary(patient["id"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/scan-form", response_model=ScanFormResponse)
async def scan_form(
    request: Request,
    file: UploadFile = File(...),
    share_key: str | None = None,
):
    """Upload a photo or PDF of a blank intake form.

    Performs OCR on the uploaded image, identifies form fields, and
    auto-fills them from the patient's stored Aquifer data.

    Requires X-Share-Key header (or share_key form field).
    """
    from aquifer.patient_app.form_filler import FormFiller

    key = share_key or request.headers.get("X-Share-Key", "")
    if not key:
        raise HTTPException(400, "Share key required (X-Share-Key header or share_key field)")

    patient = _resolve_patient(key, request.app.state.db)
    patient_data = _get_patient_data(patient, request.app.state)
    filler = FormFiller(patient_data)

    content = await file.read()
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower() if filename else ""

    form_text = _ocr_upload(content, suffix)
    fields = filler.identify_fields(form_text)

    request.app.state.db.log_audit(
        practice_id="patient_app",
        action="patient_app.scan_form",
        resource_type="patient",
        resource_id=patient["id"],
        detail=f"filename={filename} fields_found={len(fields)}",
    )

    return ScanFormResponse(fields=fields, form_text=form_text)


@router.post("/fill-form", response_model=FillFormResponse)
async def fill_form(body: FillFormRequest, request: Request):
    """Generate a filled form output from previously scanned form text.

    Accepts the raw form_text returned by /scan-form and returns the
    filled version plus a formatted summary ready to email or copy.
    """
    from aquifer.patient_app.form_filler import FormFiller

    patient = _resolve_patient(body.share_key, request.app.state.db)
    patient_data = _get_patient_data(patient, request.app.state)
    filler = FormFiller(patient_data)

    filled_text = filler.fill_form(body.form_text)
    summary = filler.to_summary()

    request.app.state.db.log_audit(
        practice_id="patient_app",
        action="patient_app.fill_form",
        resource_type="patient",
        resource_id=patient["id"],
    )

    return FillFormResponse(filled_text=filled_text, summary=summary)


@router.post("/my-data", response_model=MyDataResponse)
async def my_data(body: MyDataRequest, request: Request):
    """Patient views their own stored data.

    Without OTP: returns masked values (e.g. "M***a G***a").
    With a valid OTP: returns the full plaintext values.

    The OTP must be generated first via the practice-side
    POST /api/v1/patients/{id}/otp endpoint.
    """
    hub = request.app.state.patient_hub
    db = request.app.state.db

    patient = _resolve_patient(body.share_key, db)
    patient_id = patient["id"]

    otp_verified = False
    if body.otp:
        # hub.verify_patient is safe to call here — it only marks email verified
        # if not already done, and we use it purely as an OTP gate.
        otp_verified = hub.verify_patient(patient_id, body.otp)

    patient_data = _get_patient_data(patient, request.app.state)

    if otp_verified:
        fields = patient_data
    else:
        fields = {k: _mask_value(v) for k, v in patient_data.items()}

    # Always mask the email in the response metadata
    email = patient["email"]
    at = email.index("@")
    email_masked = email[:2] + ("*" * (at - 2)) + email[at:]

    db.log_audit(
        practice_id="patient_app",
        action="patient_app.my_data",
        resource_type="patient",
        resource_id=patient_id,
        detail=f"otp_verified={otp_verified}",
    )

    return MyDataResponse(
        patient_id=patient_id,
        email_masked=email_masked,
        fields=fields,
        otp_verified=otp_verified,
    )


@router.post("/share-email", response_model=ShareEmailResponse)
async def share_email(body: ShareEmailRequest, request: Request):
    """Email filled form data to a dental practice.

    Sends the patient's full data summary to the given practice_email address.
    This is the viral hook — the practice sees the Aquifer footer and looks it up.

    Requires email to be configured on the server (AQUIFER_SMTP_* env vars).
    """
    from aquifer.patient_app.form_filler import FormFiller
    from aquifer.strata.notifications import send_notification

    patient = _resolve_patient(body.share_key, request.app.state.db)
    patient_data = _get_patient_data(patient, request.app.state)
    filler = FormFiller(patient_data)
    summary = filler.to_summary()

    email_body = summary
    if body.message:
        email_body = body.message.strip() + "\n\n" + summary

    email_body += "\n\n--\nPowered by Aquifer — aquifer.health"

    email_config = getattr(request.app.state, "email_config", None)
    sent = False
    if email_config is not None:
        sent = send_notification(
            email_config,
            to=body.practice_email,
            subject="Patient intake information (via Aquifer)",
            body=email_body,
        )

    request.app.state.db.log_audit(
        practice_id="patient_app",
        action="patient_app.share_email",
        resource_type="patient",
        resource_id=patient["id"],
        detail=f"to={body.practice_email} sent={sent}",
    )

    if sent:
        return ShareEmailResponse(
            sent=True,
            message=f"Your information was sent to {body.practice_email}.",
        )
    else:
        # Return the summary so the patient can copy-paste even if email is unavailable
        return ShareEmailResponse(
            sent=False,
            message=(
                "Email delivery is not configured on this server. "
                "Copy the summary below and paste it into your email client.\n\n"
                + summary
            ),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ocr_upload(content: bytes, suffix: str) -> str:
    """Extract text from an uploaded file's raw bytes.

    Writes to a temp file so existing extract_image / extract_pdf can operate
    on a path as they expect.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix or ".jpg", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        if suffix == ".pdf":
            from aquifer.engine.extractors.pdf import extract_pdf, is_scanned_pdf
            text = extract_pdf(tmp_path)
            # If scanned PDF (image-based), fall through to OCR
            if not text.strip() or is_scanned_pdf(tmp_path):
                text = _ocr_path_as_image(tmp_path)
        else:
            # Treat as image (JPEG, PNG, TIFF, etc.)
            from aquifer.engine.extractors.image import extract_image
            text = extract_image(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return text


def _ocr_path_as_image(pdf_path: Path) -> str:
    """OCR a scanned PDF by rendering each page to an image."""
    try:
        import fitz
        from PIL import Image
        import pytesseract
    except ImportError:
        return ""

    doc = fitz.open(str(pdf_path))
    pages_text: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        pages_text.append(pytesseract.image_to_string(img))
    doc.close()
    return "\n\n".join(pages_text)


def _mask_value(value: str) -> str:
    """Lightly mask a PHI value: keep first and last characters, obscure middle."""
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + ("*" * (len(value) - 2)) + value[-1]


