"""Tests for the health data import module.

Covers Apple Health XML parsing, FHIR R4 bundle parsing, and manual entry.
"""

from __future__ import annotations

import json

import pytest

from aquifer.patient_app.health_import import (
    HealthRecord,
    from_manual_entry,
    parse_apple_health,
    parse_fhir_bundle,
)


# ---------------------------------------------------------------------------
# Apple Health XML fixtures
# ---------------------------------------------------------------------------

APPLE_HEALTH_MINIMAL = """\
<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierBodyMass" value="72.5" unit="kg"
          startDate="2025-01-15T08:00:00-06:00" />
  <Record type="HKQuantityTypeIdentifierHeartRate" value="68" unit="count/min"
          startDate="2025-01-15T09:30:00-06:00" />
  <Record type="HKQuantityTypeIdentifierHeight" value="175" unit="cm"
          startDate="2024-06-01T10:00:00-06:00" />
</HealthData>
"""

APPLE_HEALTH_CLINICAL = """\
<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKClinicalTypeIdentifierConditionRecord" startDate="2024-03-01T00:00:00Z">
    <ClinicalRecord FHIRData='{"code": {"text": "Type 2 Diabetes"}}' />
  </Record>
  <Record type="HKClinicalTypeIdentifierMedicationRecord" startDate="2024-03-01T00:00:00Z">
    <ClinicalRecord FHIRData='{"medicationCodeableConcept": {"text": "Metformin 500mg"}, "dosage": [{"text": "Twice daily"}]}' />
  </Record>
  <Record type="HKClinicalTypeIdentifierAllergyRecord" startDate="2024-03-01T00:00:00Z">
    <ClinicalRecord FHIRData='{"code": {"text": "Penicillin"}, "reaction": [{"manifestation": [{"text": "Rash"}]}]}' />
  </Record>
</HealthData>
"""

APPLE_HEALTH_UNKNOWN_TYPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Record type="HKQuantityTypeIdentifierStepCount" value="10000" unit="count"
          startDate="2025-01-15" />
</HealthData>
"""

APPLE_HEALTH_MALFORMED = "<HealthData><broken"


# ---------------------------------------------------------------------------
# FHIR R4 fixtures
# ---------------------------------------------------------------------------

FHIR_PATIENT = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "Patient",
                "name": [{"given": ["Maria"], "family": "Garcia"}],
                "birthDate": "1985-07-22",
                "gender": "female",
                "telecom": [
                    {"system": "phone", "value": "(512) 555-0147"},
                    {"system": "email", "value": "maria@example.com"},
                ],
                "address": [
                    {
                        "line": ["123 Main St"],
                        "city": "Austin",
                        "state": "TX",
                        "postalCode": "78701",
                    }
                ],
            }
        }
    ],
}

FHIR_CONDITION = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "Condition",
                "code": {"text": "Hypertension"},
                "onsetDateTime": "2020-01-15T00:00:00Z",
            }
        }
    ],
}

FHIR_MEDICATION = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "MedicationRequest",
                "medicationCodeableConcept": {"text": "Lisinopril 10mg"},
                "dosageInstruction": [{"text": "Once daily"}],
                "authoredOn": "2024-06-01T00:00:00Z",
            }
        }
    ],
}

FHIR_ALLERGY = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "AllergyIntolerance",
                "code": {"text": "Sulfa drugs"},
                "reaction": [
                    {"manifestation": [{"text": "Hives"}]}
                ],
                "recordedDate": "2023-05-10",
            }
        }
    ],
}

FHIR_OBSERVATION_VITAL = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "Observation",
                "code": {
                    "text": "Blood Pressure",
                    "coding": [{"code": "55284-4", "display": "Blood Pressure Panel"}],
                },
                "category": [
                    {"coding": [{"code": "vital-signs"}]}
                ],
                "component": [
                    {
                        "code": {"text": "Systolic"},
                        "valueQuantity": {"value": 120, "unit": "mmHg"},
                    },
                    {
                        "code": {"text": "Diastolic"},
                        "valueQuantity": {"value": 80, "unit": "mmHg"},
                    },
                ],
                "effectiveDateTime": "2025-01-15T10:00:00Z",
            }
        }
    ],
}

FHIR_IMMUNIZATION = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "Immunization",
                "vaccineCode": {"text": "COVID-19 mRNA Vaccine"},
                "occurrenceDateTime": "2024-09-15",
            }
        }
    ],
}

FHIR_COVERAGE = {
    "resourceType": "Bundle",
    "entry": [
        {
            "resource": {
                "resourceType": "Coverage",
                "payor": [{"display": "BlueCross BlueShield"}],
                "identifier": [{"value": "MEM-9876543"}],
                "class": [{"value": "GRP-1234"}],
            }
        }
    ],
}

FHIR_COMPREHENSIVE = {
    "resourceType": "Bundle",
    "entry": [
        {"resource": FHIR_PATIENT["entry"][0]["resource"]},
        {"resource": FHIR_CONDITION["entry"][0]["resource"]},
        {"resource": FHIR_MEDICATION["entry"][0]["resource"]},
        {"resource": FHIR_ALLERGY["entry"][0]["resource"]},
        {"resource": FHIR_IMMUNIZATION["entry"][0]["resource"]},
        {"resource": FHIR_COVERAGE["entry"][0]["resource"]},
    ],
}


# ---------------------------------------------------------------------------
# TestAppleHealthParsing
# ---------------------------------------------------------------------------

class TestAppleHealthParsing:
    def test_parses_vitals(self):
        records = parse_apple_health(APPLE_HEALTH_MINIMAL)
        assert len(records) == 3
        assert all(isinstance(r, HealthRecord) for r in records)

        weight = next(r for r in records if r.label == "Body Weight")
        assert "72.5" in weight.value
        assert "kg" in weight.value
        assert weight.domain == "medical_history"
        assert weight.field_type == "vital"
        assert weight.source == "apple_health"
        assert weight.source_system == "HealthKit"

    def test_parses_clinical_records(self):
        records = parse_apple_health(APPLE_HEALTH_CLINICAL)
        assert len(records) == 3

        condition = next(r for r in records if r.field_type == "condition")
        assert "Type 2 Diabetes" in condition.value

        medication = next(r for r in records if r.field_type == "medication")
        assert "Metformin" in medication.value
        assert "Twice daily" in medication.value

        allergy = next(r for r in records if r.field_type == "allergy")
        assert "Penicillin" in allergy.value
        assert "Rash" in allergy.value

    def test_skips_unknown_types(self):
        records = parse_apple_health(APPLE_HEALTH_UNKNOWN_TYPE)
        assert len(records) == 0

    def test_handles_malformed_xml(self):
        records = parse_apple_health(APPLE_HEALTH_MALFORMED)
        assert isinstance(records, list)

    def test_empty_xml(self):
        records = parse_apple_health("")
        assert records == []

    def test_date_normalization(self):
        records = parse_apple_health(APPLE_HEALTH_MINIMAL)
        weight = next(r for r in records if r.label == "Body Weight")
        assert weight.date == "2025-01-15"


# ---------------------------------------------------------------------------
# TestFHIRParsing
# ---------------------------------------------------------------------------

class TestFHIRParsing:
    def test_parses_patient_demographics(self):
        records = parse_fhir_bundle(json.dumps(FHIR_PATIENT))
        assert len(records) >= 4

        name = next(r for r in records if r.field_type == "name")
        assert name.value == "Maria Garcia"
        assert name.domain == "demographics"

        dob = next(r for r in records if r.field_type == "dob")
        assert dob.value == "1985-07-22"

        phone = next(r for r in records if r.field_type == "phone")
        assert phone.value == "(512) 555-0147"

        email = next(r for r in records if r.field_type == "email")
        assert email.value == "maria@example.com"

        address = next(r for r in records if r.field_type == "address")
        assert "Austin" in address.value
        assert "TX" in address.value

    def test_parses_condition(self):
        records = parse_fhir_bundle(json.dumps(FHIR_CONDITION))
        assert len(records) == 1
        assert records[0].label == "Hypertension"
        assert records[0].domain == "medical_history"
        assert records[0].date == "2020-01-15"

    def test_parses_medication(self):
        records = parse_fhir_bundle(json.dumps(FHIR_MEDICATION))
        assert len(records) == 1
        assert "Lisinopril" in records[0].value
        assert "Once daily" in records[0].value
        assert records[0].domain == "medications"

    def test_parses_allergy(self):
        records = parse_fhir_bundle(json.dumps(FHIR_ALLERGY))
        assert len(records) == 1
        assert "Sulfa drugs" in records[0].value
        assert "Hives" in records[0].value
        assert records[0].domain == "allergies"

    def test_parses_vital_observation(self):
        records = parse_fhir_bundle(json.dumps(FHIR_OBSERVATION_VITAL))
        assert len(records) == 1
        assert records[0].field_type == "vital"
        assert "Systolic" in records[0].value
        assert "120" in records[0].value

    def test_parses_immunization(self):
        records = parse_fhir_bundle(json.dumps(FHIR_IMMUNIZATION))
        assert len(records) == 1
        assert "COVID-19" in records[0].value
        assert records[0].domain == "medical_history"

    def test_parses_coverage(self):
        records = parse_fhir_bundle(json.dumps(FHIR_COVERAGE))
        assert len(records) == 3
        carrier = next(r for r in records if r.field_type == "carrier")
        assert "BlueCross" in carrier.value
        member = next(r for r in records if r.field_type == "member_id")
        assert member.value == "MEM-9876543"
        group = next(r for r in records if r.field_type == "group_number")
        assert group.value == "GRP-1234"

    def test_comprehensive_bundle(self):
        records = parse_fhir_bundle(json.dumps(FHIR_COMPREHENSIVE))
        domains = {r.domain for r in records}
        assert "demographics" in domains
        assert "medical_history" in domains
        assert "medications" in domains
        assert "allergies" in domains
        assert "insurance" in domains

    def test_invalid_json(self):
        records = parse_fhir_bundle("not json")
        assert records == []

    def test_empty_bundle(self):
        records = parse_fhir_bundle(json.dumps({"resourceType": "Bundle", "entry": []}))
        assert records == []

    def test_single_resource_not_bundle(self):
        """A bare resource without 'entry' is wrapped as a single-entry bundle."""
        patient = FHIR_PATIENT["entry"][0]["resource"]
        records = parse_fhir_bundle(json.dumps(patient))
        assert len(records) >= 1


# ---------------------------------------------------------------------------
# TestManualEntry
# ---------------------------------------------------------------------------

class TestManualEntry:
    def test_demographics(self):
        data = {
            "name": "John Smith",
            "dob": "1990-05-10",
            "phone": "555-0123",
            "email": "john@example.com",
            "address": "456 Oak Ave",
            "gender": "male",
        }
        records = from_manual_entry(data)
        assert len(records) == 6
        assert all(r.domain == "demographics" for r in records)
        assert all(r.source == "manual" for r in records)

    def test_insurance_fields(self):
        data = {
            "insurance_carrier": "Aetna",
            "insurance_member_id": "MEM-111",
            "insurance_group": "GRP-222",
            "insurance_policy_holder": "Self",
        }
        records = from_manual_entry(data)
        assert len(records) == 4
        assert all(r.domain == "insurance" for r in records)

    def test_medications_list(self):
        data = {"medications": ["Aspirin 81mg", "Atorvastatin 20mg"]}
        records = from_manual_entry(data)
        assert len(records) == 2
        assert all(r.domain == "medications" for r in records)
        labels = {r.label for r in records}
        assert "Aspirin 81mg" in labels

    def test_allergies_list(self):
        data = {"allergies": ["Penicillin", "Shellfish"]}
        records = from_manual_entry(data)
        assert len(records) == 2
        assert all(r.domain == "allergies" for r in records)

    def test_conditions_list(self):
        data = {"conditions": ["Hypertension", "Type 2 Diabetes"]}
        records = from_manual_entry(data)
        assert len(records) == 2
        assert all(r.domain == "medical_history" for r in records)

    def test_surgeries_list(self):
        data = {"surgeries": ["Appendectomy 2019"]}
        records = from_manual_entry(data)
        assert len(records) == 1
        assert records[0].domain == "surgical"

    def test_empty_data(self):
        records = from_manual_entry({})
        assert records == []

    def test_skips_empty_values(self):
        data = {"name": "", "dob": None, "medications": [""]}
        records = from_manual_entry(data)
        assert records == []

    def test_full_patient_intake(self):
        data = {
            "name": "Jane Doe",
            "dob": "1975-12-01",
            "phone": "555-9876",
            "email": "jane@example.com",
            "address": "789 Pine St",
            "insurance_carrier": "United Healthcare",
            "insurance_member_id": "UHC-12345",
            "medications": ["Metformin 500mg", "Lisinopril 10mg"],
            "allergies": ["Sulfa"],
            "conditions": ["Diabetes", "Hypertension"],
            "surgeries": ["Knee replacement 2022"],
        }
        records = from_manual_entry(data)
        domains = {r.domain for r in records}
        assert domains == {"demographics", "insurance", "medications", "allergies", "medical_history", "surgical"}
        assert len(records) == 13
