"""Export Aquifer data as FHIR R4 resources.

Converts vault tokens, patient health records, and file metadata into
standards-compliant FHIR R4 JSON. Used by the FHIR bridge API and
for EHR integration exports.

Supported resource types:
- Patient (demographics from vault tokens)
- Condition (from health records)
- MedicationRequest (from health records)
- AllergyIntolerance (from health records)
- Observation (vitals from health records)
- DocumentReference (de-identified .aqf files)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def export_patient(patient_data: dict[str, str], patient_id: str) -> dict:
    """Convert patient demographic data to a FHIR R4 Patient resource.

    Args:
        patient_data: Flat dict from PatientHub.get_patient_data_summary(),
                      e.g. {"NAME": "Maria Garcia", "DATE": "07/22/1985", ...}
        patient_id: Aquifer patient ID (used as FHIR identifier).

    Returns:
        FHIR R4 Patient resource dict.
    """
    resource = {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [{"system": "urn:aquifer:patient", "value": patient_id}],
        "meta": {
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "source": "aquifer",
        },
    }

    # Name
    name = patient_data.get("NAME", "")
    if name:
        parts = name.strip().rsplit(" ", 1)
        given = [parts[0]] if parts else []
        family = parts[1] if len(parts) > 1 else parts[0] if parts else ""
        resource["name"] = [{"use": "official", "given": given, "family": family}]

    # Date of birth
    dob = patient_data.get("DATE", "") or patient_data.get("DOB", "")
    if dob:
        resource["birthDate"] = _normalize_date(dob)

    # Gender
    gender = patient_data.get("GENDER", "")
    if gender:
        resource["gender"] = gender.lower()

    # Phone
    telecom = []
    phone = patient_data.get("PHONE", "")
    if phone:
        telecom.append({"system": "phone", "value": phone, "use": "home"})
    email = patient_data.get("EMAIL", "")
    if email:
        telecom.append({"system": "email", "value": email})
    if telecom:
        resource["telecom"] = telecom

    # Address
    address = patient_data.get("ADDRESS", "")
    if address:
        resource["address"] = [{"use": "home", "text": address}]

    return resource


def export_health_records_as_bundle(
    records: list[dict],
    patient_id: str,
    bundle_type: str = "collection",
) -> dict:
    """Convert health records to a FHIR R4 Bundle.

    Args:
        records: List of health record dicts from PatientHub.get_health_records().
        patient_id: Aquifer patient ID.
        bundle_type: FHIR bundle type (default "collection").

    Returns:
        FHIR R4 Bundle resource dict.
    """
    entries = []

    for record in records:
        resource = _record_to_fhir_resource(record, patient_id)
        if resource:
            entries.append({
                "fullUrl": f"urn:uuid:{resource.get('id', str(uuid.uuid4()))}",
                "resource": resource,
            })

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": bundle_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(entries),
        "entry": entries,
        "meta": {"source": "aquifer"},
    }


def export_document_reference(
    file_id: str,
    practice_id: str,
    filename: str,
    source_type: str,
    status: str = "current",
    data_domain: str | None = None,
) -> dict:
    """Create a FHIR DocumentReference for a de-identified .aqf file.

    Args:
        file_id: Aquifer file ID.
        practice_id: Practice that owns the file.
        filename: Original filename (de-identified version).
        source_type: File type (pdf, docx, etc.).
        status: Document status (default "current").
        data_domain: Aquifer data domain classification.

    Returns:
        FHIR R4 DocumentReference resource dict.
    """
    resource = {
        "resourceType": "DocumentReference",
        "id": file_id,
        "status": status,
        "type": {
            "coding": [{
                "system": "urn:aquifer:data-domain",
                "code": data_domain or "unknown",
                "display": (data_domain or "unknown").replace("_", " ").title(),
            }],
        },
        "description": f"De-identified {source_type.upper()} document",
        "content": [{
            "attachment": {
                "contentType": _mime_type(source_type),
                "title": filename,
            },
            "format": {
                "system": "urn:aquifer:format",
                "code": "aqf",
                "display": "Aquifer De-identified Format",
            },
        }],
        "context": {
            "practiceSetting": {
                "coding": [{
                    "system": "urn:aquifer:practice",
                    "code": practice_id,
                }],
            },
        },
        "meta": {
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "source": "aquifer",
            "tag": [{"system": "urn:aquifer:phi-status", "code": "de-identified"}],
        },
    }

    return resource


def capability_statement(base_url: str) -> dict:
    """Generate a FHIR R4 CapabilityStatement for the Aquifer FHIR bridge.

    This advertises what FHIR operations Aquifer supports.
    """
    return {
        "resourceType": "CapabilityStatement",
        "id": "aquifer-fhir-bridge",
        "status": "active",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "publisher": "Aquifer Health",
        "kind": "instance",
        "software": {
            "name": "Aquifer FHIR Bridge",
            "version": "1.0.0",
        },
        "implementation": {
            "description": "Aquifer HIPAA De-Identification FHIR Bridge",
            "url": base_url,
        },
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [{
            "mode": "server",
            "resource": [
                _capability_resource("Patient", ["read", "search-type"]),
                _capability_resource("Bundle", ["read", "create"]),
                _capability_resource("DocumentReference", ["read", "search-type"]),
                _capability_resource("Condition", ["read", "search-type"]),
                _capability_resource("MedicationRequest", ["read", "search-type"]),
                _capability_resource("AllergyIntolerance", ["read", "search-type"]),
                _capability_resource("Observation", ["read", "search-type"]),
            ],
            "operation": [
                {
                    "name": "$de-identify",
                    "definition": f"{base_url}/fhir/$de-identify",
                },
                {
                    "name": "$export",
                    "definition": f"{base_url}/fhir/$export",
                },
            ],
        }],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _record_to_fhir_resource(record: dict, patient_id: str) -> dict | None:
    """Convert a single health record to the appropriate FHIR resource."""
    domain = record.get("domain", "")
    field_type = record.get("field_type", "")
    value = record.get("value", "")
    label = record.get("label", "")
    date = record.get("recorded_date") or record.get("date")

    resource_id = record.get("id", str(uuid.uuid4()))
    subject_ref = {"reference": f"Patient/{patient_id}"}

    if field_type in ("condition",) or domain == "medical_history" and field_type not in ("vital", "observation", "immunization", "procedure", "lab_result"):
        return {
            "resourceType": "Condition",
            "id": resource_id,
            "subject": subject_ref,
            "code": {"text": label or value},
            "onsetDateTime": date,
            "meta": {"source": "aquifer"},
        }

    if field_type == "medication" or domain == "medications":
        return {
            "resourceType": "MedicationRequest",
            "id": resource_id,
            "subject": subject_ref,
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {"text": label or value},
            "meta": {"source": "aquifer"},
        }

    if field_type == "allergy" or domain == "allergies":
        return {
            "resourceType": "AllergyIntolerance",
            "id": resource_id,
            "patient": subject_ref,
            "code": {"text": label or value},
            "recordedDate": date,
            "meta": {"source": "aquifer"},
        }

    if field_type in ("vital", "observation", "lab_result"):
        return {
            "resourceType": "Observation",
            "id": resource_id,
            "subject": subject_ref,
            "status": "final",
            "code": {"text": label},
            "valueString": value,
            "effectiveDateTime": date,
            "meta": {"source": "aquifer"},
        }

    if field_type == "immunization":
        return {
            "resourceType": "Immunization",
            "id": resource_id,
            "patient": subject_ref,
            "status": "completed",
            "vaccineCode": {"text": label or value},
            "occurrenceDateTime": date,
            "meta": {"source": "aquifer"},
        }

    if domain == "insurance":
        return {
            "resourceType": "Coverage",
            "id": resource_id,
            "beneficiary": subject_ref,
            "status": "active",
            "type": {"text": field_type.replace("_", " ").title()},
            "meta": {"source": "aquifer"},
        }

    # Demographics or unrecognized — skip (demographics go in Patient resource)
    return None


def _normalize_date(date_str: str) -> str:
    """Normalize a date string to FHIR format (YYYY-MM-DD)."""
    if not date_str:
        return ""
    # Already ISO format
    if len(date_str) >= 10 and date_str[4] == "-":
        return date_str[:10]
    # US format: MM/DD/YYYY
    parts = date_str.split("/")
    if len(parts) == 3:
        month, day, year = parts
        if len(year) == 2:
            year = f"19{year}" if int(year) > 50 else f"20{year}"
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return date_str


def _mime_type(source_type: str) -> str:
    """Map Aquifer source type to MIME type."""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
        "csv": "text/csv",
        "json": "application/json",
        "xml": "application/xml",
        "image": "image/jpeg",
    }.get(source_type, "application/octet-stream")


def _capability_resource(resource_type: str, interactions: list[str]) -> dict:
    return {
        "type": resource_type,
        "interaction": [{"code": i} for i in interactions],
    }
