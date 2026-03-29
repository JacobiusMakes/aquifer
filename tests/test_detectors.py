"""Tests for PHI pattern detectors."""

import pytest
from pathlib import Path
from aquifer.engine.detectors.patterns import (
    SSNDetector, PhoneDetector, EmailDetector, URLDetector, IPDetector,
    DateDetector, MRNDetector, NPIDetector, AccountDetector, AddressDetector,
    AgeDetector, LicenseDetector, VehicleDetector, DeviceDetector,
    ZIPCodeDetector, detect_patterns, PHIType,
)
from aquifer.engine.detectors.ner import detect_names_contextual

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def clinical_note():
    return (FIXTURES / "sample_clinical_note.txt").read_text()


class TestSSNDetector:
    def test_standard_ssn(self):
        matches = SSNDetector().detect("SSN: 123-45-6789")
        assert len(matches) == 1
        assert matches[0].text == "123-45-6789"
        assert matches[0].phi_type == PHIType.SSN

    def test_ssn_with_spaces(self):
        matches = SSNDetector().detect("SSN: 123 45 6789")
        assert len(matches) == 1

    def test_invalid_ssn_000(self):
        matches = SSNDetector().detect("SSN: 000-12-3456")
        assert len(matches) == 0

    def test_invalid_ssn_666(self):
        matches = SSNDetector().detect("SSN: 666-12-3456")
        assert len(matches) == 0

    def test_bare_9_digits_not_matched_without_context(self):
        """A bare 9-digit number without separators or SSN context should not match."""
        matches = SSNDetector().detect("Code: 123456789")
        assert len(matches) == 0

    def test_bare_9_digits_matched_with_ssn_context(self):
        matches = SSNDetector().detect("Social Security: 123456789")
        assert len(matches) == 1


class TestPhoneDetector:
    def test_parenthesized(self):
        matches = PhoneDetector().detect("Phone: (555) 867-5309")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.PHONE

    def test_dashed(self):
        matches = PhoneDetector().detect("Call 555-867-5309")
        assert len(matches) == 1

    def test_fax_context(self):
        matches = PhoneDetector().detect("Fax: (555) 867-5309")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.FAX

    def test_with_country_code(self):
        matches = PhoneDetector().detect("Phone: +1 555-867-5309")
        assert len(matches) == 1

    def test_npi_not_matched_as_phone(self):
        """NPI numbers labeled as 'NPI:' should NOT be detected as phone."""
        matches = PhoneDetector().detect("NPI: 1234567890")
        assert len(matches) == 0

    def test_bare_10_digits_not_matched(self):
        """A bare 10-digit number without separators or phone context should not match."""
        matches = PhoneDetector().detect("ID: 1234567890")
        assert len(matches) == 0

    def test_dotted_format(self):
        matches = PhoneDetector().detect("Phone: 555.867.5309")
        assert len(matches) == 1


class TestEmailDetector:
    def test_standard_email(self):
        matches = EmailDetector().detect("Email: john.smith@gmail.com")
        assert len(matches) == 1
        assert matches[0].text == "john.smith@gmail.com"
        assert matches[0].phi_type == PHIType.EMAIL

    def test_email_with_plus(self):
        matches = EmailDetector().detect("user+tag@example.com")
        assert len(matches) == 1

    def test_email_with_subdomain(self):
        matches = EmailDetector().detect("admin@mail.hospital.org")
        assert len(matches) == 1


class TestURLDetector:
    def test_https(self):
        matches = URLDetector().detect("Visit https://example.com/path")
        assert len(matches) == 1

    def test_www(self):
        matches = URLDetector().detect("Visit www.example.com")
        assert len(matches) == 1

    def test_http(self):
        matches = URLDetector().detect("http://patient-portal.example.com/login")
        assert len(matches) == 1


class TestIPDetector:
    def test_private_ip(self):
        matches = IPDetector().detect("IP: 192.168.1.105")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.IP

    def test_public_ip(self):
        matches = IPDetector().detect("From: 8.8.8.8")
        assert len(matches) == 1

    def test_invalid_ip(self):
        matches = IPDetector().detect("IP: 999.999.999.999")
        assert len(matches) == 0


class TestDateDetector:
    def test_mm_dd_yyyy(self):
        matches = DateDetector().detect("DOB: 03/15/1987")
        assert len(matches) == 1

    def test_yyyy_mm_dd(self):
        matches = DateDetector().detect("Date: 2024-01-15")
        assert len(matches) == 1

    def test_month_name_full(self):
        matches = DateDetector().detect("on January 15, 2024")
        assert len(matches) == 1

    def test_month_name_abbreviated(self):
        matches = DateDetector().detect("on Jan 15, 2024")
        assert len(matches) == 1

    def test_dd_month_yyyy(self):
        matches = DateDetector().detect("15 January 2024")
        assert len(matches) == 1

    def test_two_digit_year(self):
        matches = DateDetector().detect("DOB: 03/15/87")
        assert len(matches) == 1

    def test_multiple_dates(self):
        text = "DOB: 03/15/1987 Visit: 01/15/2024 Next: 01/22/2024"
        matches = DateDetector().detect(text)
        assert len(matches) >= 3

    def test_time_with_appointment_context(self):
        matches = DateDetector().detect("Next appointment: 01/22/2024 at 2:30 PM")
        # Should catch both the date and possibly the time
        assert len(matches) >= 1


class TestAgeDetector:
    def test_age_over_89(self):
        matches = AgeDetector().detect("Patient is 92 years old")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.AGE

    def test_age_under_90_not_matched(self):
        matches = AgeDetector().detect("Patient is 45 years old")
        assert len(matches) == 0

    def test_age_yo_format(self):
        matches = AgeDetector().detect("93 y/o female")
        assert len(matches) == 1

    def test_age_label(self):
        matches = AgeDetector().detect("Age: 95")
        assert len(matches) == 1

    def test_age_89_not_matched(self):
        matches = AgeDetector().detect("Age: 89")
        assert len(matches) == 0


class TestMRNDetector:
    def test_mr_format(self):
        matches = MRNDetector().detect("MRN: MR-2024-0847291")
        assert len(matches) >= 1
        assert any(m.phi_type == PHIType.MRN for m in matches)

    def test_mrn_label(self):
        matches = MRNDetector().detect("MRN: 12345678")
        assert len(matches) >= 1

    def test_chart_number(self):
        matches = MRNDetector().detect("Chart #: ABC12345")
        assert len(matches) >= 1


class TestNPIDetector:
    def test_npi_with_label(self):
        matches = NPIDetector().detect("NPI: 1234567890")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.NPI

    def test_npi_with_equals(self):
        matches = NPIDetector().detect("NPI=1234567890")
        assert len(matches) == 1


class TestAccountDetector:
    def test_member_id(self):
        matches = AccountDetector().detect("Member ID: DDI-98765432")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.ACCOUNT

    def test_policy_number(self):
        matches = AccountDetector().detect("Policy Number: POL-2024-123456")
        assert len(matches) == 1

    def test_group_number(self):
        matches = AccountDetector().detect("Group #: GRP-44821")
        assert len(matches) == 1

    def test_claim_id(self):
        matches = AccountDetector().detect("Claim ID: CLM-2024-001234")
        assert len(matches) == 1


class TestAddressDetector:
    def test_full_address(self):
        matches = AddressDetector().detect(
            "742 Evergreen Terrace, Springfield, IL 62704"
        )
        assert len(matches) >= 1
        assert matches[0].phi_type == PHIType.ADDRESS

    def test_po_box(self):
        matches = AddressDetector().detect("P.O. Box 1234")
        assert len(matches) == 1

    def test_labeled_address(self):
        matches = AddressDetector().detect(
            "Address: 123 Main St Apt 4, Anytown, CA 90210"
        )
        assert len(matches) >= 1

    def test_various_street_types(self):
        for street in ["Avenue", "Boulevard", "Drive", "Lane", "Road", "Court", "Highway"]:
            text = f"123 Oak {street}"
            matches = AddressDetector().detect(text)
            assert len(matches) >= 1, f"Failed to detect address with {street}"


class TestZIPCodeDetector:
    def test_zip_in_address_context(self):
        matches = ZIPCodeDetector().detect("Springfield, IL 62704")
        assert len(matches) == 1

    def test_zip_plus_four(self):
        matches = ZIPCodeDetector().detect("city, ST 62704-1234")
        assert len(matches) == 1

    def test_zip_not_in_context(self):
        """ZIP codes without address context should not match."""
        matches = ZIPCodeDetector().detect("Code: 12345")
        assert len(matches) == 0


class TestLicenseDetector:
    def test_drivers_license(self):
        matches = LicenseDetector().detect("DL#: D12345678")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.LICENSE

    def test_dea_number(self):
        matches = LicenseDetector().detect("DEA: AB1234567")
        assert len(matches) == 1


class TestVehicleDetector:
    def test_vin(self):
        matches = VehicleDetector().detect("VIN: 1HGCM82633A004352")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.VEHICLE


class TestDeviceDetector:
    def test_serial_number(self):
        matches = DeviceDetector().detect("Serial Number: SN-ABC123-456789")
        assert len(matches) == 1
        assert matches[0].phi_type == PHIType.DEVICE

    def test_device_id(self):
        matches = DeviceDetector().detect("Device ID: DEV-2024-001")
        assert len(matches) == 1


class TestNameDetectorContextual:
    """Test the contextual name detector (from ner.py)."""

    def test_patient_name(self):
        matches = detect_names_contextual("PATIENT: John Michael Smith\nDOB: 03/15/1987")
        assert len(matches) == 1
        assert matches[0].text == "John Michael Smith"
        # Should NOT include "DOB" or cross the newline
        assert "DOB" not in matches[0].text

    def test_provider_name(self):
        matches = detect_names_contextual("Dr. Sarah Johnson, DDS")
        assert len(matches) == 1
        assert "Sarah Johnson" in matches[0].text

    def test_entered_by(self):
        matches = detect_names_contextual("Notes entered by: Maria Garcia, RDA")
        assert len(matches) == 1
        assert "Maria Garcia" in matches[0].text

    def test_patient_narrative(self):
        matches = detect_names_contextual("Patient John Smith reports intermittent pain")
        assert len(matches) == 1
        assert matches[0].text == "John Smith"

    def test_no_false_positive_on_clinical_terms(self):
        """Should not flag clinical terms as names."""
        text = "PATIENT: General Dentistry"
        matches = detect_names_contextual(text)
        # "General Dentistry" should be filtered out as clinical terms
        for m in matches:
            assert m.text != "General Dentistry"


class TestClinicalNoteIntegration:
    """Test all detectors against the sample clinical note."""

    def test_detects_ssn(self, clinical_note):
        matches = detect_patterns(clinical_note)
        ssn_matches = [m for m in matches if m.phi_type == PHIType.SSN]
        assert any("123-45-6789" in m.text for m in ssn_matches)

    def test_detects_phone(self, clinical_note):
        matches = detect_patterns(clinical_note)
        phone_matches = [m for m in matches if m.phi_type == PHIType.PHONE]
        assert any("867-5309" in m.text for m in phone_matches)

    def test_detects_email(self, clinical_note):
        matches = detect_patterns(clinical_note)
        email_matches = [m for m in matches if m.phi_type == PHIType.EMAIL]
        assert any("john.smith@gmail.com" in m.text for m in email_matches)

    def test_detects_ip(self, clinical_note):
        matches = detect_patterns(clinical_note)
        ip_matches = [m for m in matches if m.phi_type == PHIType.IP]
        assert any("192.168.1.105" in m.text for m in ip_matches)

    def test_detects_dates(self, clinical_note):
        matches = detect_patterns(clinical_note)
        date_matches = [m for m in matches if m.phi_type == PHIType.DATE]
        assert len(date_matches) >= 3

    def test_detects_mrn(self, clinical_note):
        matches = detect_patterns(clinical_note)
        mrn_matches = [m for m in matches if m.phi_type == PHIType.MRN]
        assert len(mrn_matches) >= 1

    def test_detects_npi(self, clinical_note):
        matches = detect_patterns(clinical_note)
        npi_matches = [m for m in matches if m.phi_type == PHIType.NPI]
        assert len(npi_matches) >= 1

    def test_npi_not_also_phone(self, clinical_note):
        """NPI: 1234567890 should be detected as NPI, NOT as phone."""
        matches = detect_patterns(clinical_note)
        phone_texts = [m.text for m in matches if m.phi_type == PHIType.PHONE]
        # The NPI value should not appear as a phone match
        assert not any("1234567890" in t for t in phone_texts)

    def test_detects_address(self, clinical_note):
        matches = detect_patterns(clinical_note)
        addr_matches = [m for m in matches if m.phi_type == PHIType.ADDRESS]
        assert any("742" in m.text and "Evergreen" in m.text for m in addr_matches)

    def test_detects_account(self, clinical_note):
        matches = detect_patterns(clinical_note)
        acct_matches = [m for m in matches if m.phi_type == PHIType.ACCOUNT]
        assert any("DDI-98765432" in m.text for m in acct_matches)

    def test_does_not_detect_cdt_codes(self, clinical_note):
        """CDT codes (D3330, D2750) should NOT be detected as PHI."""
        matches = detect_patterns(clinical_note)
        all_texts = [m.text for m in matches]
        assert not any("D3330" in t for t in all_texts)
        assert not any("D2750" in t for t in all_texts)

    def test_does_not_detect_dollar_amounts(self, clinical_note):
        matches = detect_patterns(clinical_note)
        all_texts = [m.text for m in matches]
        assert not any("$150" in t for t in all_texts)

    def test_does_not_detect_tooth_numbers(self, clinical_note):
        matches = detect_patterns(clinical_note)
        all_texts = [m.text for m in matches]
        assert not any(t.strip() == "#30" for t in all_texts)

    def test_detects_names_contextually(self, clinical_note):
        matches = detect_names_contextual(clinical_note)
        name_texts = [m.text for m in matches]
        # Should catch patient name, provider name, and entered-by name
        assert any("John" in t for t in name_texts)
        assert any("Sarah Johnson" in t for t in name_texts)
        assert any("Maria Garcia" in t for t in name_texts)
