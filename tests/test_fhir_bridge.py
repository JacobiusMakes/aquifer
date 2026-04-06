"""Tests for the FHIR R4 bridge — exporter and API routes."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquifer.fhir.exporter import (
    capability_statement,
    export_document_reference,
    export_health_records_as_bundle,
    export_patient,
)
from aquifer.strata.config import StrataConfig
from aquifer.strata.server import create_app


# ---------------------------------------------------------------------------
# Exporter unit tests
# ---------------------------------------------------------------------------

class TestExportPatient:
    def test_basic_demographics(self):
        data = {
            "NAME": "Maria Garcia",
            "DATE": "07/22/1985",
            "PHONE": "(512) 555-0147",
            "EMAIL": "maria@example.com",
            "ADDRESS": "123 Main St, Austin, TX",
        }
        resource = export_patient(data, "patient-001")
        assert resource["resourceType"] == "Patient"
        assert resource["id"] == "patient-001"
        assert resource["name"][0]["family"] == "Garcia"
        assert resource["name"][0]["given"] == ["Maria"]
        assert resource["birthDate"] == "1985-07-22"
        assert len(resource["telecom"]) == 2
        assert resource["address"][0]["text"] == "123 Main St, Austin, TX"

    def test_empty_data(self):
        resource = export_patient({}, "patient-empty")
        assert resource["resourceType"] == "Patient"
        assert "name" not in resource
        assert "birthDate" not in resource

    def test_iso_date_passthrough(self):
        data = {"DATE": "1985-07-22"}
        resource = export_patient(data, "p1")
        assert resource["birthDate"] == "1985-07-22"


class TestExportHealthRecords:
    def test_condition_record(self):
        records = [{"domain": "medical_history", "field_type": "condition",
                     "label": "Hypertension", "value": "Hypertension", "id": "r1"}]
        bundle = export_health_records_as_bundle(records, "p1")
        assert bundle["resourceType"] == "Bundle"
        assert len(bundle["entry"]) == 1
        assert bundle["entry"][0]["resource"]["resourceType"] == "Condition"

    def test_medication_record(self):
        records = [{"domain": "medications", "field_type": "medication",
                     "label": "Metformin", "value": "Metformin 500mg", "id": "r2"}]
        bundle = export_health_records_as_bundle(records, "p1")
        assert bundle["entry"][0]["resource"]["resourceType"] == "MedicationRequest"

    def test_allergy_record(self):
        records = [{"domain": "allergies", "field_type": "allergy",
                     "label": "Penicillin", "value": "Penicillin", "id": "r3"}]
        bundle = export_health_records_as_bundle(records, "p1")
        assert bundle["entry"][0]["resource"]["resourceType"] == "AllergyIntolerance"

    def test_vital_observation(self):
        records = [{"domain": "medical_history", "field_type": "vital",
                     "label": "Blood Pressure", "value": "120/80 mmHg", "id": "r4"}]
        bundle = export_health_records_as_bundle(records, "p1")
        assert bundle["entry"][0]["resource"]["resourceType"] == "Observation"

    def test_mixed_records(self):
        records = [
            {"domain": "medical_history", "field_type": "condition", "label": "Diabetes", "value": "Diabetes", "id": "r1"},
            {"domain": "medications", "field_type": "medication", "label": "Metformin", "value": "Metformin", "id": "r2"},
            {"domain": "allergies", "field_type": "allergy", "label": "Sulfa", "value": "Sulfa", "id": "r3"},
        ]
        bundle = export_health_records_as_bundle(records, "p1")
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        assert types == {"Condition", "MedicationRequest", "AllergyIntolerance"}

    def test_empty_records(self):
        bundle = export_health_records_as_bundle([], "p1")
        assert bundle["total"] == 0
        assert bundle["entry"] == []


class TestExportDocumentReference:
    def test_basic(self):
        doc = export_document_reference("f1", "p1", "intake.pdf", "pdf", data_domain="demographics")
        assert doc["resourceType"] == "DocumentReference"
        assert doc["content"][0]["attachment"]["contentType"] == "application/pdf"
        assert doc["meta"]["tag"][0]["code"] == "de-identified"


class TestCapabilityStatement:
    def test_structure(self):
        cs = capability_statement("http://localhost:8080")
        assert cs["resourceType"] == "CapabilityStatement"
        assert cs["fhirVersion"] == "4.0.1"
        assert len(cs["rest"][0]["resource"]) >= 5


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    cfg = StrataConfig()
    cfg.debug = True
    cfg.master_key = "test-master-key-fhir"
    cfg.jwt_secret = "test-jwt-secret-fhir"
    cfg.data_dir = Path(tempfile.mkdtemp())
    cfg.db_path = cfg.data_dir / "test.db"
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    shutil.rmtree(cfg.data_dir, ignore_errors=True)


def _setup_practice(client):
    reg = client.post("/api/v1/auth/register", json={
        "practice_name": "FHIR Test Practice",
        "email": "fhir@test.com",
        "password": "SecurePass123",
    })
    assert reg.status_code == 201
    return reg.json()


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


class TestFHIRMetadata:
    def test_metadata_public(self, client):
        resp = client.get("/api/v1/fhir/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resourceType"] == "CapabilityStatement"
        assert data["fhirVersion"] == "4.0.1"


class TestFHIRPatient:
    def test_patient_not_found(self, client):
        reg = _setup_practice(client)
        headers = _headers(reg["token"])
        resp = client.get("/api/v1/fhir/Patient/nonexistent", headers=headers)
        assert resp.status_code == 404


class TestFHIRDeidentify:
    def test_deidentify_bundle(self, client):
        reg = _setup_practice(client)
        headers = _headers(reg["token"])

        bundle = {
            "resourceType": "Bundle",
            "id": "test-bundle",
            "type": "collection",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "name": [{"given": ["John"], "family": "Doe"}],
                        "telecom": [{"system": "phone", "value": "555-123-4567"}],
                    }
                }
            ],
        }

        resp = client.post("/api/v1/fhir/Bundle", json=bundle, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["resourceType"] == "Bundle"
        assert data["meta"]["tag"][0]["code"] == "de-identified"

    def test_deidentify_operation(self, client):
        reg = _setup_practice(client)
        headers = _headers(reg["token"])

        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {"resource": {"resourceType": "Observation", "valueString": "SSN: 123-45-6789"}}
            ],
        }

        resp = client.post("/api/v1/fhir/$de-identify", json=bundle, headers=headers)
        assert resp.status_code == 200

    def test_invalid_input(self, client):
        reg = _setup_practice(client)
        headers = _headers(reg["token"])

        resp = client.post("/api/v1/fhir/Bundle", json={"not": "a bundle"}, headers=headers)
        assert resp.status_code == 400


class TestFHIRDocumentReference:
    def test_search_empty(self, client):
        reg = _setup_practice(client)
        headers = _headers(reg["token"])
        resp = client.get("/api/v1/fhir/DocumentReference", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["resourceType"] == "Bundle"
        assert data["type"] == "searchset"
