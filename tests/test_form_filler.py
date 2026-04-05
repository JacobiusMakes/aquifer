"""Tests for the patient form filler module.

Covers FormFiller: identify_fields, fill_form, to_summary, from_vault_tokens.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aquifer.patient_app.form_filler import FormFiller


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_PATIENT_DATA = {
    "NAME": "Maria Garcia",
    "DATE": "07/22/1985",
    "SSN": "287-65-4321",
    "PHONE": "(512) 555-0147",
    "EMAIL": "maria.garcia@example.com",
    "ADDRESS": "123 Main St, Austin, TX 78701",
    "ACCOUNT": "BlueCross PPO / MEM-9876543",
    "ALLERGIES": "Penicillin",
    "MEDICATIONS": "Metformin 500mg",
}

SAMPLE_FORM_TEXT = """\
Patient Intake Form
====================

Patient Name: ___________________
Date of Birth: ___________________
Social Security Number: ___________________
Phone: ___________________
Email: ___________________
Address: ___________________

Insurance Carrier: ___________________
Member ID: ___________________

Allergies: ___________________
Current Medications: ___________________

Emergency Contact: ___________________
"""

FORM_TEXT_NO_BLANKS = """\
Name: please fill in
DOB: enter here
SSN: required
Telephone: required
E-mail: required
"""


# ---------------------------------------------------------------------------
# TestFormFillerIdentifyFields
# ---------------------------------------------------------------------------

class TestFormFillerIdentifyFields:
    def test_identifies_standard_fields(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields(SAMPLE_FORM_TEXT)
        types = {f["field_type"] for f in fields}

        assert "NAME" in types
        assert "DATE" in types
        assert "SSN" in types
        assert "PHONE" in types
        assert "EMAIL" in types
        assert "ADDRESS" in types
        assert "ACCOUNT" in types
        assert "ALLERGIES" in types
        assert "MEDICATIONS" in types

    def test_fills_values_from_patient_data(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields(SAMPLE_FORM_TEXT)
        name_field = next(f for f in fields if f["field_type"] == "NAME")
        assert name_field["value"] == "Maria Garcia"

    def test_missing_data_returns_none_value(self):
        filler = FormFiller({"NAME": "Test"})
        fields = filler.identify_fields(SAMPLE_FORM_TEXT)
        ssn_field = next(f for f in fields if f["field_type"] == "SSN")
        assert ssn_field["value"] is None

    def test_empty_form_returns_empty(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields("")
        assert fields == []

    def test_identifies_alternate_labels(self):
        alt_form = "Full Name: ___\nBirthdate: ___\nCell Phone: ___\nMailing Address: ___\n"
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields(alt_form)
        types = {f["field_type"] for f in fields}
        assert "NAME" in types
        assert "DATE" in types
        assert "PHONE" in types
        assert "ADDRESS" in types

    def test_deduplicates_same_type(self):
        duped = "Name: ___\nPatient Name: ___\nFull Name: ___\n"
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields(duped)
        name_fields = [f for f in fields if f["field_type"] == "NAME"]
        assert len(name_fields) == 1

    def test_emergency_contact_not_deduped(self):
        form = "Emergency Contact: ___\nEmergency Contact: ___\n"
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        fields = filler.identify_fields(form)
        ec_fields = [f for f in fields if f["field_type"] == "EMERGENCY_CONTACT"]
        assert len(ec_fields) >= 1


# ---------------------------------------------------------------------------
# TestFormFillerFillForm
# ---------------------------------------------------------------------------

class TestFormFillerFillForm:
    def test_replaces_underscores(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        result = filler.fill_form(SAMPLE_FORM_TEXT)
        assert "Maria Garcia" in result
        assert "(512) 555-0147" in result
        assert "287-65-4321" in result

    def test_handles_no_blank_placeholder(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        result = filler.fill_form(FORM_TEXT_NO_BLANKS)
        assert "Maria Garcia" in result
        assert "(512) 555-0147" in result

    def test_leaves_unmatched_lines_unchanged(self):
        form = "Random line with no label\nName: ___\n"
        filler = FormFiller({"NAME": "Test"})
        result = filler.fill_form(form)
        assert "Random line with no label" in result

    def test_no_data_leaves_form_unchanged(self):
        filler = FormFiller({})
        result = filler.fill_form(SAMPLE_FORM_TEXT)
        assert "___________________" in result

    def test_replaces_dotted_lines(self):
        form = "Name: .................\n"
        filler = FormFiller({"NAME": "Jane Doe"})
        result = filler.fill_form(form)
        assert "Jane Doe" in result

    def test_replaces_brackets(self):
        form = "Name: [   ]\n"
        filler = FormFiller({"NAME": "Jane Doe"})
        result = filler.fill_form(form)
        assert "Jane Doe" in result


# ---------------------------------------------------------------------------
# TestFormFillerSummary
# ---------------------------------------------------------------------------

class TestFormFillerSummary:
    def test_summary_contains_header(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        summary = filler.to_summary()
        assert "Patient Information Summary (Aquifer)" in summary

    def test_summary_contains_demographics(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        summary = filler.to_summary()
        assert "Maria Garcia" in summary
        assert "07/22/1985" in summary
        assert "(512) 555-0147" in summary

    def test_summary_contains_insurance(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        summary = filler.to_summary()
        assert "BlueCross PPO" in summary

    def test_summary_contains_medical(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        summary = filler.to_summary()
        assert "Penicillin" in summary
        assert "Metformin" in summary

    def test_summary_contains_footer(self):
        filler = FormFiller(SAMPLE_PATIENT_DATA)
        summary = filler.to_summary()
        assert "aquifer.health" in summary

    def test_summary_with_extras(self):
        data = {"NAME": "Test", "CUSTOM_FIELD": "custom_value"}
        filler = FormFiller(data)
        summary = filler.to_summary()
        assert "custom_value" in summary

    def test_summary_empty_data(self):
        filler = FormFiller({})
        summary = filler.to_summary()
        assert "Patient Information Summary" in summary


# ---------------------------------------------------------------------------
# TestFromVaultTokens
# ---------------------------------------------------------------------------

class TestFromVaultTokens:
    def _make_token(self, phi_type, phi_value):
        token = MagicMock()
        token.phi_type = phi_type
        token.phi_value = phi_value
        return token

    def test_maps_phi_types_to_field_types(self):
        tokens = [
            self._make_token("NAME", "John Doe"),
            self._make_token("DATE", "01/15/1990"),
            self._make_token("SSN", "123-45-6789"),
            self._make_token("PHONE", "555-1234"),
            self._make_token("EMAIL", "john@example.com"),
        ]
        filler = FormFiller.from_vault_tokens(tokens)
        assert filler.data["NAME"] == "John Doe"
        assert filler.data["DATE"] == "01/15/1990"
        assert filler.data["SSN"] == "123-45-6789"
        assert filler.data["PHONE"] == "555-1234"
        assert filler.data["EMAIL"] == "john@example.com"

    def test_first_token_wins_for_same_type(self):
        tokens = [
            self._make_token("NAME", "First Name"),
            self._make_token("NAME", "Second Name"),
        ]
        filler = FormFiller.from_vault_tokens(tokens)
        assert filler.data["NAME"] == "First Name"

    def test_maps_fax_to_phone(self):
        tokens = [self._make_token("FAX", "555-9999")]
        filler = FormFiller.from_vault_tokens(tokens)
        assert filler.data["PHONE"] == "555-9999"

    def test_maps_mrn_to_account(self):
        tokens = [self._make_token("MRN", "MRN-12345")]
        filler = FormFiller.from_vault_tokens(tokens)
        assert filler.data["ACCOUNT"] == "MRN-12345"

    def test_ignores_unmapped_types(self):
        tokens = [self._make_token("UNKNOWN_TYPE", "something")]
        filler = FormFiller.from_vault_tokens(tokens)
        assert len(filler.data) == 0
