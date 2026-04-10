"""Open Dental PMS integration.

Connects to Open Dental's REST API to:
- Pull patient demographics, insurance, medications, allergies
- Push pre-filled intake data back into Open Dental
- Sync patient records between Aquifer and Open Dental

Authentication: Developer API key + per-customer API key.
API base: https://api.opendental.com/api/v1

Reference: https://www.opendental.com/site/apispecification.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.opendental.com/api/v1"


@dataclass
class OpenDentalConfig:
    """Connection config for an Open Dental instance."""
    developer_key: str
    customer_key: str
    api_base: str = API_BASE


@dataclass
class ODPatient:
    """Open Dental patient record — normalized from API response."""
    pat_num: int
    first_name: str
    last_name: str
    birthdate: str = ""
    ssn: str = ""
    gender: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    phone_home: str = ""
    phone_work: str = ""
    phone_wireless: str = ""
    email: str = ""
    insurance_carrier: str = ""
    insurance_member_id: str = ""
    insurance_group: str = ""
    raw: dict = field(default_factory=dict)

    def to_aquifer_data(self) -> dict:
        """Convert to flat dict compatible with Aquifer's health import."""
        data = {}
        name = f"{self.first_name} {self.last_name}".strip()
        if name:
            data["name"] = name
        if self.birthdate:
            data["dob"] = self.birthdate
        if self.ssn:
            data["ssn"] = self.ssn
        if self.email:
            data["email"] = self.email
        if self.phone_wireless:
            data["phone"] = self.phone_wireless
        elif self.phone_home:
            data["phone"] = self.phone_home

        address_parts = [self.address, self.city, self.state, self.zip_code]
        address = ", ".join(p for p in address_parts if p)
        if address:
            data["address"] = address

        if self.insurance_carrier:
            data["insurance_carrier"] = self.insurance_carrier
        if self.insurance_member_id:
            data["insurance_member_id"] = self.insurance_member_id
        if self.insurance_group:
            data["insurance_group"] = self.insurance_group

        if self.gender:
            data["gender"] = self.gender

        return data


@dataclass
class ODMedication:
    name: str
    dosage: str = ""
    date_start: str = ""
    is_active: bool = True


@dataclass
class ODAllergy:
    name: str
    reaction: str = ""


class OpenDentalClient:
    """Client for the Open Dental REST API."""

    def __init__(self, config: OpenDentalConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"ODFHIR {self.config.developer_key}/{self.config.customer_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.config.api_base,
            headers=self._headers(),
            timeout=30.0,
        )

    # --- Patient operations ---

    def search_patients(
        self,
        last_name: str = "",
        first_name: str = "",
        phone: str = "",
        birthdate: str = "",
        email: str = "",
        limit: int = 50,
    ) -> list[ODPatient]:
        """Search for patients in Open Dental."""
        params = {}
        if last_name:
            params["LName"] = last_name
        if first_name:
            params["FName"] = first_name
        if phone:
            params["Phone"] = phone
        if birthdate:
            params["Birthdate"] = birthdate
        if email:
            params["Email"] = email
        params["Limit"] = str(limit)

        with self._client() as client:
            resp = client.get("/patients", params=params)
            resp.raise_for_status()
            patients = resp.json()

        return [self._parse_patient(p) for p in patients]

    def get_patient(self, pat_num: int) -> ODPatient:
        """Get a single patient by PatNum."""
        with self._client() as client:
            resp = client.get(f"/patients/{pat_num}")
            resp.raise_for_status()
            return self._parse_patient(resp.json())

    def create_patient(self, patient: ODPatient) -> ODPatient:
        """Create a new patient in Open Dental."""
        body = {
            "LName": patient.last_name,
            "FName": patient.first_name,
            "Birthdate": patient.birthdate,
            "Gender": _gender_to_od(patient.gender),
            "Address": patient.address,
            "City": patient.city,
            "State": patient.state,
            "Zip": patient.zip_code,
            "HmPhone": patient.phone_home,
            "WkPhone": patient.phone_work,
            "WirelessPhone": patient.phone_wireless,
            "Email": patient.email,
            "SSN": patient.ssn,
        }
        # Remove empty fields
        body = {k: v for k, v in body.items() if v}

        with self._client() as client:
            resp = client.post("/patients", json=body)
            resp.raise_for_status()
            return self._parse_patient(resp.json())

    def update_patient(self, pat_num: int, updates: dict) -> ODPatient:
        """Update patient fields in Open Dental."""
        with self._client() as client:
            resp = client.put(f"/patients/{pat_num}", json=updates)
            resp.raise_for_status()
            return self._parse_patient(resp.json())

    # --- Medical history ---

    def get_medications(self, pat_num: int) -> list[ODMedication]:
        """Get active medications for a patient."""
        with self._client() as client:
            resp = client.get(f"/medicationpats", params={"PatNum": str(pat_num)})
            resp.raise_for_status()
            meds = resp.json()

        return [
            ODMedication(
                name=m.get("MedDescript", "") or m.get("DrugName", ""),
                dosage=m.get("PatNote", ""),
                date_start=m.get("DateStart", ""),
                is_active=m.get("DateStop", "0001-01-01") == "0001-01-01",
            )
            for m in meds
            if m.get("MedDescript") or m.get("DrugName")
        ]

    def get_allergies(self, pat_num: int) -> list[ODAllergy]:
        """Get allergies for a patient."""
        with self._client() as client:
            resp = client.get(f"/allergies", params={"PatNum": str(pat_num)})
            resp.raise_for_status()
            allergies = resp.json()

        return [
            ODAllergy(
                name=a.get("Description", "") or a.get("AllergyDefDescription", ""),
                reaction=a.get("Reaction", ""),
            )
            for a in allergies
            if a.get("Description") or a.get("AllergyDefDescription")
        ]

    # --- Aquifer integration ---

    def pull_patient_to_aquifer(self, pat_num: int) -> dict:
        """Pull a full patient record from Open Dental, formatted for Aquifer import.

        Returns a dict compatible with aquifer.patient_app.health_import.from_manual_entry().
        """
        patient = self.get_patient(pat_num)
        data = patient.to_aquifer_data()

        # Add medications
        try:
            meds = self.get_medications(pat_num)
            active_meds = [m.name + (f" — {m.dosage}" if m.dosage else "") for m in meds if m.is_active]
            if active_meds:
                data["medications"] = active_meds
        except Exception as e:
            logger.warning(f"Failed to fetch medications for PatNum {pat_num}: {e}")

        # Add allergies
        try:
            allergies = self.get_allergies(pat_num)
            allergy_list = [a.name + (f" — {a.reaction}" if a.reaction else "") for a in allergies]
            if allergy_list:
                data["allergies"] = allergy_list
        except Exception as e:
            logger.warning(f"Failed to fetch allergies for PatNum {pat_num}: {e}")

        return data

    def push_aquifer_to_patient(self, pat_num: int, aquifer_data: dict[str, str]) -> ODPatient:
        """Push Aquifer patient data into an Open Dental patient record.

        Updates existing fields. Does NOT overwrite non-empty fields in OD
        unless the Aquifer data is different.
        """
        updates = {}

        field_map = {
            "NAME": ("FName", "LName"),
            "DATE": "Birthdate",
            "PHONE": "WirelessPhone",
            "EMAIL": "Email",
            "ADDRESS": "Address",
            "SSN": "SSN",
        }

        for aq_key, od_key in field_map.items():
            value = aquifer_data.get(aq_key, "")
            if not value:
                continue
            if isinstance(od_key, tuple):
                # Split name into first/last
                parts = value.rsplit(" ", 1)
                updates[od_key[0]] = parts[0]
                if len(parts) > 1:
                    updates[od_key[1]] = parts[1]
            else:
                updates[od_key] = value

        if not updates:
            return self.get_patient(pat_num)

        return self.update_patient(pat_num, updates)

    # --- Internal helpers ---

    def _parse_patient(self, data: dict) -> ODPatient:
        return ODPatient(
            pat_num=data.get("PatNum", 0),
            first_name=data.get("FName", ""),
            last_name=data.get("LName", ""),
            birthdate=data.get("Birthdate", ""),
            ssn=data.get("SSN", ""),
            gender=_od_to_gender(data.get("Gender", "")),
            address=data.get("Address", ""),
            city=data.get("City", ""),
            state=data.get("State", ""),
            zip_code=data.get("Zip", ""),
            phone_home=data.get("HmPhone", ""),
            phone_work=data.get("WkPhone", ""),
            phone_wireless=data.get("WirelessPhone", ""),
            email=data.get("Email", ""),
            raw=data,
        )

    def test_connection(self) -> bool:
        """Test that the API connection works."""
        try:
            with self._client() as client:
                resp = client.get("/patients", params={"Limit": "1"})
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Open Dental connection test failed: {e}")
            return False


def _od_to_gender(value: str | int) -> str:
    mapping = {"0": "male", "1": "female", "2": "other", 0: "male", 1: "female", 2: "other"}
    return mapping.get(value, str(value).lower() if value else "")


def _gender_to_od(value: str) -> str:
    mapping = {"male": "0", "female": "1", "other": "2", "m": "0", "f": "1"}
    return mapping.get(value.lower(), "0") if value else "0"
