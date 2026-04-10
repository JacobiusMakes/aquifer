"""Tests for the Open Dental PMS integration client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aquifer.integrations.open_dental import (
    OpenDentalClient, OpenDentalConfig, ODPatient, ODMedication, ODAllergy,
)


@pytest.fixture
def config():
    return OpenDentalConfig(
        developer_key="dev-key-123",
        customer_key="cust-key-456",
        api_base="https://api.opendental.com/api/v1",
    )


@pytest.fixture
def client(config):
    return OpenDentalClient(config)


class TestODPatient:
    def test_to_aquifer_data(self):
        patient = ODPatient(
            pat_num=1234,
            first_name="Maria",
            last_name="Garcia",
            birthdate="1985-07-22",
            ssn="287-65-4321",
            email="maria@example.com",
            phone_wireless="512-555-0147",
            address="123 Main St",
            city="Austin",
            state="TX",
            zip_code="78701",
            insurance_carrier="BlueCross",
            insurance_member_id="MEM-9876",
        )
        data = patient.to_aquifer_data()
        assert data["name"] == "Maria Garcia"
        assert data["dob"] == "1985-07-22"
        assert data["email"] == "maria@example.com"
        assert data["phone"] == "512-555-0147"
        assert "Austin" in data["address"]
        assert data["insurance_carrier"] == "BlueCross"

    def test_to_aquifer_data_empty(self):
        patient = ODPatient(pat_num=0, first_name="", last_name="")
        data = patient.to_aquifer_data()
        assert data == {}

    def test_falls_back_to_home_phone(self):
        patient = ODPatient(pat_num=1, first_name="Test", last_name="User",
                            phone_home="555-1234", phone_wireless="")
        data = patient.to_aquifer_data()
        assert data["phone"] == "555-1234"


class TestOpenDentalClient:
    def test_headers(self, client):
        headers = client._headers()
        assert "ODFHIR dev-key-123/cust-key-456" in headers["Authorization"]

    @patch("aquifer.integrations.open_dental.httpx.Client")
    def test_search_patients(self, mock_client_cls, client):
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"PatNum": 1, "FName": "John", "LName": "Doe", "Birthdate": "1990-01-15"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_instance.get.return_value = mock_resp

        patients = client.search_patients(last_name="Doe")
        assert len(patients) == 1
        assert patients[0].first_name == "John"
        assert patients[0].pat_num == 1

    @patch("aquifer.integrations.open_dental.httpx.Client")
    def test_pull_patient_to_aquifer(self, mock_client_cls, client):
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Patient response
        patient_resp = MagicMock()
        patient_resp.json.return_value = {
            "PatNum": 1, "FName": "Jane", "LName": "Smith",
            "Birthdate": "1985-03-20", "Email": "jane@test.com",
            "WirelessPhone": "555-9876",
        }
        patient_resp.raise_for_status = MagicMock()

        # Meds response
        meds_resp = MagicMock()
        meds_resp.json.return_value = [
            {"MedDescript": "Metformin 500mg", "PatNote": "Twice daily", "DateStop": "0001-01-01"},
        ]
        meds_resp.raise_for_status = MagicMock()

        # Allergies response
        allergy_resp = MagicMock()
        allergy_resp.json.return_value = [
            {"Description": "Penicillin", "Reaction": "Rash"},
        ]
        allergy_resp.raise_for_status = MagicMock()

        mock_instance.get.side_effect = [patient_resp, meds_resp, allergy_resp]

        data = client.pull_patient_to_aquifer(1)
        assert data["name"] == "Jane Smith"
        assert data["dob"] == "1985-03-20"
        assert data["medications"] == ["Metformin 500mg — Twice daily"]
        assert data["allergies"] == ["Penicillin — Rash"]

    @patch("aquifer.integrations.open_dental.httpx.Client")
    def test_test_connection(self, mock_client_cls, client):
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_instance.get.return_value = mock_resp

        assert client.test_connection() is True

    @patch("aquifer.integrations.open_dental.httpx.Client")
    def test_test_connection_failure(self, mock_client_cls, client):
        mock_client_cls.return_value.__enter__ = MagicMock(
            side_effect=ConnectionError("refused")
        )
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        assert client.test_connection() is False
