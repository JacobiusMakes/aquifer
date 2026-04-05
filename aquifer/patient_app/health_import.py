"""Import patient health data from external sources.

Supports:
- Apple Health (HealthKit XML export)
- FHIR R4 (MyChart, Epic, Cerner — JSON bundles)
- Manual structured entry (JSON dict)

Imported data is stored as patient-owned records in the vault,
tagged with data domains for scoped sharing.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import StringIO


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HealthRecord:
    domain: str          # DataDomain value
    field_type: str      # e.g., "condition", "medication", "allergy", "vital"
    label: str           # human-readable label, e.g., "Type 2 Diabetes"
    value: str           # the data value
    date: str | None     # when recorded (ISO format)
    source: str          # "apple_health", "fhir", "manual"
    source_system: str   # "HealthKit", "MyChart", "Epic", etc.


# ---------------------------------------------------------------------------
# Apple Health XML parser
# ---------------------------------------------------------------------------

# Map from HealthKit type identifiers to (domain, field_type, label)
_HKTYPE_MAP: dict[str, tuple[str, str, str]] = {
    "HKQuantityTypeIdentifierBloodPressureSystolic": ("medical_history", "vital", "Blood Pressure Systolic"),
    "HKQuantityTypeIdentifierBloodPressureDiastolic": ("medical_history", "vital", "Blood Pressure Diastolic"),
    "HKQuantityTypeIdentifierBodyMass": ("medical_history", "vital", "Body Weight"),
    "HKQuantityTypeIdentifierHeight": ("medical_history", "vital", "Height"),
    "HKQuantityTypeIdentifierHeartRate": ("medical_history", "vital", "Heart Rate"),
    "HKQuantityTypeIdentifierBodyMassIndex": ("medical_history", "vital", "BMI"),
    "HKQuantityTypeIdentifierBloodGlucose": ("medical_history", "vital", "Blood Glucose"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("medical_history", "vital", "Oxygen Saturation"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("medical_history", "vital", "Respiratory Rate"),
    "HKQuantityTypeIdentifierBodyTemperature": ("medical_history", "vital", "Body Temperature"),
    "HKClinicalTypeIdentifierConditionRecord": ("medical_history", "condition", "Condition"),
    "HKClinicalTypeIdentifierMedicationRecord": ("medications", "medication", "Medication"),
    "HKClinicalTypeIdentifierAllergyRecord": ("allergies", "allergy", "Allergy"),
    "HKClinicalTypeIdentifierImmunizationRecord": ("medical_history", "immunization", "Immunization"),
    "HKClinicalTypeIdentifierLabResultRecord": ("medical_history", "lab_result", "Lab Result"),
    "HKClinicalTypeIdentifierProcedureRecord": ("medical_history", "procedure", "Procedure"),
}


def parse_apple_health(xml_content: str) -> list[HealthRecord]:
    """Parse an Apple Health export XML and return normalized HealthRecords.

    Uses iterparse for memory efficiency — Apple Health exports can be 100MB+.
    Focuses on clinical types and key vitals relevant to intake forms.
    """
    records: list[HealthRecord] = []

    try:
        context = ET.iterparse(StringIO(xml_content), events=("start",))
        for _event, elem in context:
            if elem.tag != "Record":
                elem.clear()
                continue

            hk_type = elem.get("type", "")
            mapping = _HKTYPE_MAP.get(hk_type)
            if mapping is None:
                elem.clear()
                continue

            domain, field_type, default_label = mapping

            # Extract value and unit
            value = elem.get("value", "")
            unit = elem.get("unit", "")
            if unit:
                value = f"{value} {unit}"

            # For clinical types, try to pull the FHIR JSON payload for a better label/value
            if hk_type.startswith("HKClinicalType"):
                fhir_data = elem.find("ClinicalRecord")
                if fhir_data is not None:
                    fhir_resource = fhir_data.get("FHIRData", "")
                    extracted = _extract_clinical_label(fhir_resource, field_type)
                    if extracted:
                        default_label, value = extracted

            start_date = elem.get("startDate", "") or elem.get("creationDate", "")
            # Normalize ISO date — take just the date portion if it has time
            if start_date and "T" in start_date:
                start_date = start_date.split("T")[0]

            if not value and not default_label:
                elem.clear()
                continue

            records.append(HealthRecord(
                domain=domain,
                field_type=field_type,
                label=default_label,
                value=value if value else default_label,
                date=start_date or None,
                source="apple_health",
                source_system="HealthKit",
            ))

            elem.clear()

    except ET.ParseError:
        # Return whatever we managed to parse before the error
        pass

    return records


def _extract_clinical_label(fhir_json: str, field_type: str) -> tuple[str, str] | None:
    """Pull a human-readable label and value from embedded FHIR JSON in a clinical record."""
    if not fhir_json:
        return None
    try:
        resource = json.loads(fhir_json)
    except (json.JSONDecodeError, ValueError):
        return None

    if field_type == "condition":
        code = resource.get("code", {})
        label = _fhir_coding_text(code)
        return (label, label) if label else None

    if field_type == "medication":
        med = resource.get("medicationCodeableConcept", {}) or resource.get("medicationReference", {})
        label = _fhir_coding_text(med) or resource.get("medicationDisplay", "")
        dosage = ""
        dosage_list = resource.get("dosage", []) or resource.get("dosageInstruction", [])
        if dosage_list:
            first = dosage_list[0]
            dosage = first.get("text", "") or first.get("patientInstruction", "")
        value = f"{label} — {dosage}" if dosage else label
        return (label, value) if label else None

    if field_type == "allergy":
        code = resource.get("code", {}) or resource.get("substance", {})
        label = _fhir_coding_text(code)
        reaction = ""
        reactions = resource.get("reaction", [])
        if reactions:
            manifestations = reactions[0].get("manifestation", [])
            if manifestations:
                reaction = _fhir_coding_text(manifestations[0])
        value = f"{label} — {reaction}" if reaction else label
        return (label, value) if label else None

    if field_type == "immunization":
        code = resource.get("vaccineCode", {})
        label = _fhir_coding_text(code)
        return (label, label) if label else None

    if field_type == "lab_result":
        code = resource.get("code", {})
        label = _fhir_coding_text(code)
        value_qty = resource.get("valueQuantity", {})
        if value_qty:
            val = f"{value_qty.get('value', '')} {value_qty.get('unit', '')}".strip()
        else:
            val = resource.get("valueString", "") or label
        return (label, val) if label else None

    return None


def _fhir_coding_text(concept: dict) -> str:
    """Extract the best human-readable text from a FHIR CodeableConcept."""
    if not concept:
        return ""
    # Prefer the top-level text field
    text = concept.get("text", "")
    if text:
        return text
    # Fall back to first coding display
    for coding in concept.get("coding", []):
        display = coding.get("display", "")
        if display:
            return display
    return ""


# ---------------------------------------------------------------------------
# FHIR R4 Bundle parser
# ---------------------------------------------------------------------------

def parse_fhir_bundle(json_content: str) -> list[HealthRecord]:
    """Parse a FHIR R4 Bundle JSON and return normalized HealthRecords.

    Handles: Patient, Condition, MedicationRequest, MedicationStatement,
    AllergyIntolerance, Observation (vitals), Immunization, Coverage.
    """
    try:
        bundle = json.loads(json_content)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(bundle, dict):
        return []

    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        # Some bundles are a single resource, not a bundle
        entries = [{"resource": bundle}]
    elif not entries and bundle.get("resourceType") and bundle.get("resourceType") != "Bundle":
        # Bare resource (not wrapped in a Bundle) — treat as single entry
        entries = [{"resource": bundle}]

    records: list[HealthRecord] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource", entry)
        if not isinstance(resource, dict):
            continue

        resource_type = resource.get("resourceType", "")

        if resource_type == "Patient":
            records.extend(_parse_fhir_patient(resource))
        elif resource_type == "Condition":
            r = _parse_fhir_condition(resource)
            if r:
                records.append(r)
        elif resource_type in ("MedicationRequest", "MedicationStatement"):
            r = _parse_fhir_medication(resource)
            if r:
                records.append(r)
        elif resource_type == "AllergyIntolerance":
            r = _parse_fhir_allergy(resource)
            if r:
                records.append(r)
        elif resource_type == "Observation":
            r = _parse_fhir_observation(resource)
            if r:
                records.append(r)
        elif resource_type == "Immunization":
            r = _parse_fhir_immunization(resource)
            if r:
                records.append(r)
        elif resource_type == "Coverage":
            records.extend(_parse_fhir_coverage(resource))

    return records


def _parse_fhir_patient(resource: dict) -> list[HealthRecord]:
    records: list[HealthRecord] = []
    date = resource.get("birthDate")

    # Name
    names = resource.get("name", [])
    if names:
        n = names[0]
        given = " ".join(n.get("given", []))
        family = n.get("family", "")
        full_name = f"{given} {family}".strip()
        if full_name:
            records.append(HealthRecord(
                domain="demographics",
                field_type="name",
                label="Name",
                value=full_name,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))

    # DOB
    if resource.get("birthDate"):
        records.append(HealthRecord(
            domain="demographics",
            field_type="dob",
            label="Date of Birth",
            value=resource["birthDate"],
            date=None,
            source="fhir",
            source_system="FHIR R4",
        ))

    # Gender
    gender = resource.get("gender")
    if gender:
        records.append(HealthRecord(
            domain="demographics",
            field_type="gender",
            label="Gender",
            value=gender,
            date=None,
            source="fhir",
            source_system="FHIR R4",
        ))

    # Phone / email
    for telecom in resource.get("telecom", []):
        system = telecom.get("system", "")
        value = telecom.get("value", "")
        if not value:
            continue
        if system == "phone":
            records.append(HealthRecord(
                domain="demographics",
                field_type="phone",
                label="Phone",
                value=value,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))
        elif system == "email":
            records.append(HealthRecord(
                domain="demographics",
                field_type="email",
                label="Email",
                value=value,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))

    # Address
    for addr in resource.get("address", []):
        lines = addr.get("line", [])
        city = addr.get("city", "")
        state = addr.get("state", "")
        postal = addr.get("postalCode", "")
        parts = list(lines) + [p for p in [city, state, postal] if p]
        address_str = ", ".join(parts)
        if address_str:
            records.append(HealthRecord(
                domain="demographics",
                field_type="address",
                label="Address",
                value=address_str,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))
            break  # take first address only

    return records


def _parse_fhir_condition(resource: dict) -> HealthRecord | None:
    label = _fhir_coding_text(resource.get("code", {}))
    if not label:
        return None

    onset = (
        resource.get("onsetDateTime")
        or resource.get("onsetString")
        or resource.get("recordedDate")
    )
    if onset and "T" in onset:
        onset = onset.split("T")[0]

    return HealthRecord(
        domain="medical_history",
        field_type="condition",
        label=label,
        value=label,
        date=onset or None,
        source="fhir",
        source_system="FHIR R4",
    )


def _parse_fhir_medication(resource: dict) -> HealthRecord | None:
    # MedicationRequest and MedicationStatement share similar structure
    med_concept = (
        resource.get("medicationCodeableConcept")
        or resource.get("medication", {}).get("concept", {})
        or {}
    )
    label = _fhir_coding_text(med_concept)
    if not label:
        # Try medicationReference display
        label = (
            resource.get("medicationReference", {}).get("display", "")
            or resource.get("medication", {}).get("reference", {}).get("display", "")
        )
    if not label:
        return None

    dosage = ""
    for instruction in resource.get("dosageInstruction", []) or resource.get("dosage", []):
        dosage = instruction.get("text", "") or instruction.get("patientInstruction", "")
        if dosage:
            break

    value = f"{label} — {dosage}" if dosage else label

    authored = resource.get("authoredOn") or resource.get("effectiveDateTime") or resource.get("dateAsserted")
    if authored and "T" in authored:
        authored = authored.split("T")[0]

    return HealthRecord(
        domain="medications",
        field_type="medication",
        label=label,
        value=value,
        date=authored or None,
        source="fhir",
        source_system="FHIR R4",
    )


def _parse_fhir_allergy(resource: dict) -> HealthRecord | None:
    label = _fhir_coding_text(resource.get("code", {}))
    if not label:
        return None

    reaction = ""
    for react in resource.get("reaction", []):
        for manifestation in react.get("manifestation", []):
            reaction = _fhir_coding_text(manifestation)
            if reaction:
                break
        if reaction:
            break

    value = f"{label} — {reaction}" if reaction else label

    recorded = resource.get("recordedDate") or resource.get("onsetDateTime")
    if recorded and "T" in recorded:
        recorded = recorded.split("T")[0]

    return HealthRecord(
        domain="allergies",
        field_type="allergy",
        label=label,
        value=value,
        date=recorded or None,
        source="fhir",
        source_system="FHIR R4",
    )


# LOINC codes commonly used for vitals observations
_VITAL_LOINC: set[str] = {
    "8480-6",   # Systolic BP
    "8462-4",   # Diastolic BP
    "55284-4",  # BP panel
    "29463-7",  # Body weight
    "8302-2",   # Body height
    "8867-4",   # Heart rate
    "2708-6",   # Oxygen saturation
    "9279-1",   # Respiratory rate
    "8310-5",   # Body temperature
    "39156-5",  # BMI
    "2339-0",   # Blood glucose
}


def _parse_fhir_observation(resource: dict) -> HealthRecord | None:
    code_concept = resource.get("code", {})
    label = _fhir_coding_text(code_concept)

    # Check if it looks like a vital sign by LOINC code
    is_vital = False
    for coding in code_concept.get("coding", []):
        if coding.get("code") in _VITAL_LOINC:
            is_vital = True
            break
    # Also check category
    for cat in resource.get("category", []):
        for coding in cat.get("coding", []):
            if coding.get("code") in ("vital-signs", "laboratory"):
                is_vital = True
                break

    if not label:
        return None

    # Extract value
    value = ""
    if "valueQuantity" in resource:
        qty = resource["valueQuantity"]
        value = f"{qty.get('value', '')} {qty.get('unit', '')}".strip()
    elif "valueString" in resource:
        value = resource["valueString"]
    elif "valueCodeableConcept" in resource:
        value = _fhir_coding_text(resource["valueCodeableConcept"])
    elif "component" in resource:
        # Blood pressure panels have components
        parts = []
        for comp in resource["component"]:
            comp_label = _fhir_coding_text(comp.get("code", {}))
            comp_qty = comp.get("valueQuantity", {})
            if comp_qty:
                parts.append(f"{comp_label}: {comp_qty.get('value', '')} {comp_qty.get('unit', '')}".strip())
        value = "; ".join(parts)

    if not value:
        value = label

    effective = resource.get("effectiveDateTime") or resource.get("effectivePeriod", {}).get("start")
    if effective and "T" in effective:
        effective = effective.split("T")[0]

    field_type = "vital" if is_vital else "observation"
    domain = "medical_history"

    return HealthRecord(
        domain=domain,
        field_type=field_type,
        label=label,
        value=value,
        date=effective or None,
        source="fhir",
        source_system="FHIR R4",
    )


def _parse_fhir_immunization(resource: dict) -> HealthRecord | None:
    label = _fhir_coding_text(resource.get("vaccineCode", {}))
    if not label:
        return None

    occurrence = resource.get("occurrenceDateTime") or resource.get("recorded")
    if occurrence and "T" in occurrence:
        occurrence = occurrence.split("T")[0]

    return HealthRecord(
        domain="medical_history",
        field_type="immunization",
        label=label,
        value=label,
        date=occurrence or None,
        source="fhir",
        source_system="FHIR R4",
    )


def _parse_fhir_coverage(resource: dict) -> list[HealthRecord]:
    records: list[HealthRecord] = []

    # Payor name
    for payor in resource.get("payor", []):
        name = payor.get("display") or payor.get("reference", "")
        if name:
            records.append(HealthRecord(
                domain="insurance",
                field_type="carrier",
                label="Insurance Carrier",
                value=name,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))
            break

    # Member ID
    for identifier in resource.get("identifier", []):
        member_id = identifier.get("value", "")
        if member_id:
            records.append(HealthRecord(
                domain="insurance",
                field_type="member_id",
                label="Member ID",
                value=member_id,
                date=None,
                source="fhir",
                source_system="FHIR R4",
            ))
            break

    # Group number
    group_id = resource.get("class", [{}])[0].get("value", "") if resource.get("class") else ""
    if group_id:
        records.append(HealthRecord(
            domain="insurance",
            field_type="group_number",
            label="Group Number",
            value=group_id,
            date=None,
            source="fhir",
            source_system="FHIR R4",
        ))

    return records


# ---------------------------------------------------------------------------
# Manual entry parser
# ---------------------------------------------------------------------------

def from_manual_entry(data: dict) -> list[HealthRecord]:
    """Convert a flat patient intake dict to a list of HealthRecords.

    Accepts fields: name, dob, phone, email, address, insurance_carrier,
    insurance_member_id, medications (list), allergies (list), conditions (list).
    """
    records: list[HealthRecord] = []

    _demographics = [
        ("name", "Name", "name"),
        ("dob", "Date of Birth", "dob"),
        ("phone", "Phone", "phone"),
        ("email", "Email", "email"),
        ("address", "Address", "address"),
        ("gender", "Gender", "gender"),
        ("emergency_contact", "Emergency Contact", "emergency_contact"),
    ]
    for key, label, field_type in _demographics:
        value = data.get(key)
        if value:
            records.append(HealthRecord(
                domain="demographics",
                field_type=field_type,
                label=label,
                value=str(value),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    _insurance = [
        ("insurance_carrier", "Insurance Carrier", "carrier"),
        ("insurance_member_id", "Member ID", "member_id"),
        ("insurance_group", "Group Number", "group_number"),
        ("insurance_policy_holder", "Policy Holder", "policy_holder"),
    ]
    for key, label, field_type in _insurance:
        value = data.get(key)
        if value:
            records.append(HealthRecord(
                domain="insurance",
                field_type=field_type,
                label=label,
                value=str(value),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    for med in data.get("medications", []) or []:
        if med:
            records.append(HealthRecord(
                domain="medications",
                field_type="medication",
                label=str(med),
                value=str(med),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    for allergy in data.get("allergies", []) or []:
        if allergy:
            records.append(HealthRecord(
                domain="allergies",
                field_type="allergy",
                label=str(allergy),
                value=str(allergy),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    for condition in data.get("conditions", []) or []:
        if condition:
            records.append(HealthRecord(
                domain="medical_history",
                field_type="condition",
                label=str(condition),
                value=str(condition),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    for surgery in data.get("surgeries", []) or []:
        if surgery:
            records.append(HealthRecord(
                domain="surgical",
                field_type="surgery",
                label=str(surgery),
                value=str(surgery),
                date=None,
                source="manual",
                source_system="Manual Entry",
            ))

    return records
