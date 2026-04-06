"""FHIR R4 bridge API routes.

Provides standards-compliant FHIR endpoints for EHR integration:
- GET  /fhir/metadata               — CapabilityStatement
- POST /fhir/Bundle                  — De-identify a FHIR bundle
- GET  /fhir/Patient/{id}            — Patient demographics from vault
- GET  /fhir/Patient/{id}/$everything — All data for a patient as a Bundle
- POST /fhir/$de-identify            — De-identify a FHIR bundle (operation)
- GET  /fhir/DocumentReference       — Search de-identified documents
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aquifer.fhir.exporter import (
    capability_statement,
    export_document_reference,
    export_health_records_as_bundle,
    export_patient,
)
from aquifer.strata.auth import AuthContext, has_api_key_scopes

router = APIRouter(prefix="/fhir", tags=["fhir"])


def _require_scope(auth: AuthContext, scope: str) -> None:
    if not has_api_key_scopes(auth, scope):
        raise HTTPException(403, f"API key missing required '{scope}' scope")


# ---------------------------------------------------------------------------
# CapabilityStatement (metadata)
# ---------------------------------------------------------------------------

@router.get("/metadata")
async def metadata(request: Request):
    """FHIR CapabilityStatement — advertises supported operations."""
    base_url = str(request.base_url).rstrip("/")
    return capability_statement(base_url)


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

@router.get("/Patient/{patient_id}")
async def get_patient(patient_id: str, request: Request):
    """Read a Patient resource with demographics from linked practice vaults."""
    auth: AuthContext = request.state.auth
    _require_scope(auth, "vault")
    hub = request.app.state.patient_hub
    db = request.app.state.db

    patient = db.get_patient(patient_id)
    if not patient:
        raise HTTPException(404, _operation_outcome("not-found", f"Patient/{patient_id} not found"))

    patient_data = hub.get_patient_data_summary(patient_id)
    return export_patient(patient_data, patient_id)


@router.get("/Patient/{patient_id}/$everything")
async def patient_everything(patient_id: str, request: Request):
    """Return all data for a patient as a FHIR Bundle.

    Includes demographics (Patient), conditions, medications, allergies,
    observations, and document references.
    """
    auth: AuthContext = request.state.auth
    _require_scope(auth, "vault")
    hub = request.app.state.patient_hub
    db = request.app.state.db

    patient = db.get_patient(patient_id)
    if not patient:
        raise HTTPException(404, _operation_outcome("not-found", f"Patient/{patient_id} not found"))

    # Get demographics
    patient_data = hub.get_patient_data_summary(patient_id)
    patient_resource = export_patient(patient_data, patient_id)

    # Get health records
    health_records = hub.get_health_records(patient_id, decrypt=True)
    records_bundle = export_health_records_as_bundle(health_records, patient_id)

    # Combine into a single Bundle
    entries = [{"fullUrl": f"urn:uuid:{patient_id}", "resource": patient_resource}]
    entries.extend(records_bundle.get("entry", []))

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "searchset",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(entries),
        "entry": entries,
        "meta": {"source": "aquifer"},
    }


# ---------------------------------------------------------------------------
# Bundle operations
# ---------------------------------------------------------------------------

@router.post("/Bundle")
async def create_bundle(request: Request):
    """Accept a FHIR Bundle for de-identification.

    Parses the bundle, runs PHI through the de-identification engine,
    and returns a clean FHIR Bundle with PHI replaced by tokens.
    """
    auth: AuthContext = request.state.auth
    _require_scope(auth, "deid")

    body = await request.json()
    if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
        raise HTTPException(400, _operation_outcome("invalid", "Request body must be a FHIR Bundle"))

    return await _deidentify_bundle(body, request)


@router.post("/$de-identify")
async def deidentify_operation(request: Request):
    """FHIR operation: de-identify a Bundle.

    Accepts a FHIR Bundle, strips PHI from all resources, stores tokens
    in the practice vault, and returns the de-identified Bundle.
    """
    auth: AuthContext = request.state.auth
    _require_scope(auth, "deid")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, _operation_outcome("invalid", "Request body must be JSON"))

    # Accept either a Bundle directly or a Parameters resource wrapping one
    bundle = body
    if body.get("resourceType") == "Parameters":
        for param in body.get("parameter", []):
            if param.get("name") == "resource" and param.get("resource", {}).get("resourceType") == "Bundle":
                bundle = param["resource"]
                break

    if bundle.get("resourceType") != "Bundle":
        raise HTTPException(400, _operation_outcome("invalid", "Expected a FHIR Bundle"))

    return await _deidentify_bundle(bundle, request)


# ---------------------------------------------------------------------------
# DocumentReference
# ---------------------------------------------------------------------------

@router.get("/DocumentReference")
async def search_document_references(
    request: Request,
    _count: int = 20,
    _offset: int = 0,
    status: str = "current",
):
    """Search de-identified documents as FHIR DocumentReferences."""
    auth: AuthContext = request.state.auth
    _require_scope(auth, "files")
    db = request.app.state.db

    files = db.list_files(auth.practice_id, limit=_count, offset=_offset)
    total = db.count_files(auth.practice_id)

    entries = []
    for f in files:
        if f["status"] != "completed":
            continue
        resource = export_document_reference(
            file_id=f["id"],
            practice_id=auth.practice_id,
            filename=f["original_filename"],
            source_type=f["source_type"],
            data_domain=f.get("data_domain"),
        )
        entries.append({"fullUrl": f"urn:uuid:{f['id']}", "resource": resource})

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "entry": entries,
        "meta": {"source": "aquifer"},
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _deidentify_bundle(bundle: dict, request: Request) -> dict:
    """De-identify all resources in a FHIR Bundle.

    Extracts text from each resource, runs through the de-identification
    pipeline, and replaces PHI values with vault tokens.
    """
    from aquifer.engine.detectors.patterns import detect_patterns
    from aquifer.strata.auth import AuthContext

    auth: AuthContext = request.state.auth
    db = request.app.state.db
    vault_mgr = request.app.state.vault_manager

    practice = db.get_practice(auth.practice_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    vault = vault_mgr.open_vault(auth.practice_id, practice["vault_key_encrypted"])

    entries = bundle.get("entry", [])
    deid_entries = []
    total_tokens = 0

    for entry in entries:
        resource = entry.get("resource", entry)
        if not isinstance(resource, dict):
            deid_entries.append(entry)
            continue

        # Serialize resource to text for detection
        resource_text = json.dumps(resource)
        detections = detect_patterns(resource_text)

        if not detections:
            deid_entries.append(entry)
            continue

        # Replace detected PHI with tokens
        deid_text = resource_text
        # Process detections in reverse order to maintain offsets
        for detection in sorted(detections, key=lambda d: d.start, reverse=True):
            token_id = str(uuid.uuid4())
            vault.store_token(
                token_id=token_id,
                phi_type=detection.phi_type,
                phi_value=detection.text,
                source_file_hash=f"fhir-{bundle.get('id', 'unknown')}",
            )
            placeholder = f"[REDACTED-{detection.phi_type}]"
            deid_text = deid_text[:detection.start] + placeholder + deid_text[detection.end:]
            total_tokens += 1

        try:
            deid_resource = json.loads(deid_text)
        except json.JSONDecodeError:
            deid_resource = resource

        deid_entries.append({
            "fullUrl": entry.get("fullUrl", f"urn:uuid:{str(uuid.uuid4())}"),
            "resource": deid_resource,
        })

    # Log
    file_id = str(uuid.uuid4())
    db.create_file_record(
        id=file_id,
        practice_id=auth.practice_id,
        original_filename=f"fhir-bundle-{bundle.get('id', 'unknown')}.json",
        source_type="json",
        source_hash=f"fhir-{bundle.get('id', 'unknown')}",
        file_size_bytes=len(json.dumps(bundle)),
        data_domain="fhir",
    )
    db.update_file_record(file_id, status="completed", token_count=total_tokens)

    db.log_audit(
        practice_id=auth.practice_id,
        action="fhir.deidentify",
        resource_type="bundle",
        resource_id=bundle.get("id"),
        user_id=auth.user_id,
        detail=f"entries={len(entries)} tokens={total_tokens}",
    )

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": bundle.get("type", "collection"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(deid_entries),
        "entry": deid_entries,
        "meta": {
            "source": "aquifer",
            "tag": [{"system": "urn:aquifer:phi-status", "code": "de-identified"}],
        },
    }


def _operation_outcome(code: str, message: str) -> dict:
    """Create a FHIR OperationOutcome for error responses."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [{
            "severity": "error",
            "code": code,
            "diagnostics": message,
        }],
    }
