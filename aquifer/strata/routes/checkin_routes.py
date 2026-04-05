"""QR code check-in routes — patient-facing self-service intake.

Flow:
1. Practice generates a QR code (GET /api/v1/practice/qr-checkin)
2. Patient scans QR → lands on GET /checkin/{practice_slug}
3. Patient enters share key → POST /checkin/{practice_slug}/pull
4. Records are pulled into the practice's vault automatically

The /checkin/* routes are public (no JWT required) — the share key
is the patient's authorization. The QR generation endpoint requires
practice auth.
"""

from __future__ import annotations

import io
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from aquifer.core import PRACTICE_TYPE_DEFAULTS

router = APIRouter(tags=["checkin"])


# ---------------------------------------------------------------------------
# QR code generation (practice-facing, requires auth)
# ---------------------------------------------------------------------------

class QRResponse(BaseModel):
    checkin_url: str
    practice_name: str
    practice_slug: str


@router.get("/api/v1/practice/qr-checkin")
async def generate_qr_code(request: Request, format: str = "svg"):
    """Generate a QR code for patient self-service check-in.

    The QR encodes: {server_url}/checkin/{practice_slug}

    Supports format=svg (default) or format=json (returns the URL only).
    Requires practice JWT auth.
    """
    from aquifer.strata.auth import AuthContext
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    practice = db.get_practice(auth.practice_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    # Build the check-in URL from the request's base URL
    base_url = str(request.base_url).rstrip("/")
    checkin_url = f"{base_url}/checkin/{practice['slug']}"

    if format == "json":
        return QRResponse(
            checkin_url=checkin_url,
            practice_name=practice["name"],
            practice_slug=practice["slug"],
        )

    # Generate QR code as SVG
    try:
        import qrcode
        import qrcode.image.svg

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(checkin_url)
        qr.make(fit=True)

        factory = qrcode.image.svg.SvgPathImage
        img = qr.make_image(image_factory=factory)

        buf = io.BytesIO()
        img.save(buf)
        svg_bytes = buf.getvalue()

        return Response(
            content=svg_bytes,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )
    except ImportError:
        raise HTTPException(
            501,
            "QR code generation requires the 'qrcode' package. "
            "Install with: pip install qrcode[pil]"
        )


# ---------------------------------------------------------------------------
# Patient-facing check-in page (public, no JWT)
# ---------------------------------------------------------------------------

_CHECKIN_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Check In — {practice_name} via Aquifer</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f7fafc; color: #1a202c; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; padding: 1rem; }}
  .card {{ background: #fff; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.08);
           padding: 2.5rem; max-width: 440px; width: 100%; }}
  .logo {{ text-align: center; margin-bottom: 1.5rem; }}
  .logo span {{ font-size: 1.5rem; font-weight: 700; color: #2b6cb0; }}
  .logo small {{ display: block; color: #718096; font-size: 0.85rem; margin-top: 0.25rem; }}
  h1 {{ font-size: 1.25rem; text-align: center; margin-bottom: 0.5rem; }}
  .subtitle {{ text-align: center; color: #718096; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  label {{ display: block; font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; color: #4a5568; }}
  input[type=text], select {{
    width: 100%; padding: 0.75rem; border: 1px solid #e2e8f0; border-radius: 8px;
    font-size: 1.1rem; font-family: 'SF Mono', 'Fira Code', monospace;
    text-align: center; text-transform: uppercase; letter-spacing: 0.15em;
    outline: none; transition: border-color 0.2s;
  }}
  input:focus {{ border-color: #4299e1; box-shadow: 0 0 0 3px rgba(66,153,225,0.15); }}
  select {{ font-family: inherit; text-align: left; letter-spacing: normal; text-transform: none; font-size: 0.95rem; }}
  .form-group {{ margin-bottom: 1.25rem; }}
  button {{
    width: 100%; padding: 0.85rem; background: #2b6cb0; color: #fff; border: none;
    border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer;
    transition: background 0.2s;
  }}
  button:hover {{ background: #2c5282; }}
  button:disabled {{ background: #a0aec0; cursor: not-allowed; }}
  .help {{ color: #a0aec0; font-size: 0.8rem; margin-top: 0.3rem; }}
  .result {{ background: #f0fff4; border: 1px solid #9ae6b4; border-radius: 8px;
             padding: 1.25rem; margin-top: 1.25rem; }}
  .result h3 {{ color: #276749; font-size: 0.95rem; margin-bottom: 0.75rem; }}
  .stat {{ display: flex; justify-content: space-between; padding: 0.3rem 0;
           border-bottom: 1px solid #c6f6d5; font-size: 0.85rem; }}
  .stat:last-child {{ border-bottom: none; }}
  .stat .label {{ color: #718096; }}
  .stat .value {{ font-weight: 600; }}
  .error {{ background: #fff5f5; border: 1px solid #feb2b2; color: #9b2c2c;
            border-radius: 8px; padding: 1rem; margin-top: 1rem; font-size: 0.9rem; }}
  .footer {{ text-align: center; margin-top: 1.5rem; font-size: 0.75rem; color: #a0aec0; }}
  .footer a {{ color: #4299e1; text-decoration: none; }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <span>Aquifer</span>
    <small>Secure Patient Check-in</small>
  </div>
  <h1>Welcome to {practice_name}</h1>
  <p class="subtitle">Enter your share key to pull your records automatically.</p>

  <form id="checkin-form">
    <div class="form-group">
      <label for="share_key">Share Key</label>
      <input type="text" id="share_key" name="share_key" placeholder="AQ-XXXX-XXXX"
             maxlength="12" autocomplete="off" autofocus required>
      <p class="help">You received this when you first registered with Aquifer.</p>
    </div>
    <button type="submit" id="submit-btn">Check In</button>
  </form>

  <div id="result" style="display:none;"></div>
  <div id="error" style="display:none;" class="error"></div>

  <div class="footer">
    Powered by <a href="https://aquifer.health">Aquifer</a> — your data, your choice.
  </div>
</div>

<script>
const form = document.getElementById('checkin-form');
const resultDiv = document.getElementById('result');
const errorDiv = document.getElementById('error');
const submitBtn = document.getElementById('submit-btn');

form.addEventListener('submit', async (e) => {{
  e.preventDefault();
  resultDiv.style.display = 'none';
  errorDiv.style.display = 'none';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Pulling records...';

  const shareKey = document.getElementById('share_key').value.trim().toUpperCase();

  try {{
    const resp = await fetch('/checkin/{practice_slug}/pull', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ share_key: shareKey }}),
    }});
    const data = await resp.json();

    if (!resp.ok) {{
      errorDiv.textContent = data.detail || data.error || 'Check-in failed. Please verify your share key.';
      errorDiv.style.display = 'block';
      return;
    }}

    resultDiv.innerHTML = `
      <div class="result">
        <h3>Check-in Complete</h3>
        <div class="stat"><span class="label">Patient</span><span class="value">${{data.patient_email_masked}}</span></div>
        <div class="stat"><span class="label">Records pulled</span><span class="value">${{data.total_tokens}} data points</span></div>
        <div class="stat"><span class="label">Source practices</span><span class="value">${{data.transfers.length}}</span></div>
      </div>
    `;
    resultDiv.style.display = 'block';
    submitBtn.textContent = 'Done';
  }} catch (err) {{
    errorDiv.textContent = 'Network error. Please try again.';
    errorDiv.style.display = 'block';
  }} finally {{
    submitBtn.disabled = false;
    if (submitBtn.textContent === 'Pulling records...') submitBtn.textContent = 'Check In';
  }}
}});
</script>
</body>
</html>
"""


@router.get("/checkin/{practice_slug}", response_class=HTMLResponse)
async def checkin_page(practice_slug: str, request: Request):
    """Patient-facing check-in page. Loaded by scanning the practice's QR code."""
    db = request.app.state.db
    practice = db.get_practice_by_slug(practice_slug)
    if not practice:
        raise HTTPException(404, "Practice not found")

    html = _CHECKIN_PAGE.format(
        practice_name=practice["name"],
        practice_slug=practice["slug"],
    )
    return HTMLResponse(content=html)


class CheckinPullRequest(BaseModel):
    share_key: str


@router.post("/checkin/{practice_slug}/pull")
async def checkin_pull(practice_slug: str, body: CheckinPullRequest, request: Request):
    """Execute a record pull from the patient-facing check-in page.

    This is the public-facing equivalent of POST /api/v1/patients/pull,
    but keyed by practice slug instead of requiring JWT auth.
    """
    db = request.app.state.db
    hub = request.app.state.patient_hub

    practice = db.get_practice_by_slug(practice_slug)
    if not practice:
        raise HTTPException(404, "Practice not found")

    share_key = body.share_key.strip().upper()
    if not re.match(r"^AQ-[A-Z0-9]{4}-[A-Z0-9]{4}$", share_key):
        raise HTTPException(422, "Share key must be in AQ-XXXX-XXXX format")

    patient = db.get_patient_by_share_key(share_key)
    if not patient:
        raise HTTPException(404, "Share key not found")

    try:
        transfers = hub.pull_records(
            share_key=share_key,
            target_practice_id=practice["id"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    db.log_audit(
        practice_id=practice["id"],
        action="checkin.qr_pull",
        resource_type="patient",
        resource_id=patient["id"],
        detail=f"share_key={share_key} transfers={len(transfers)} source=qr_checkin",
    )

    email = patient["email"]
    at = email.index("@")
    masked = email[:2] + ("*" * (at - 2)) + email[at:]

    summaries = []
    for t in transfers:
        consent = db.get_consent(t.consent_id)
        source_id = consent["source_practice_id"] if consent else t.consent_id
        summaries.append({
            "transfer_id": t.transfer_id,
            "source_practice_id": source_id,
            "token_count": t.token_count,
            "status": t.status,
        })

    return {
        "patient_email_masked": masked,
        "transfers": summaries,
        "total_tokens": sum(t.token_count for t in transfers),
    }
