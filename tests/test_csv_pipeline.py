"""Tests for CSV column-aware PHI detection and pipeline processing."""

import pytest
from pathlib import Path

from aquifer.engine.extractors.text import extract_text
from aquifer.engine.detectors.patterns import detect_patterns, PHIType
from aquifer.engine.pipeline import process_file
from aquifer.format.reader import read_aqf
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def vault(tmp_path):
    v = TokenVault(tmp_path / "test.aqv", "test-password")
    v.init()
    yield v
    v.close()


class TestCSVExtraction:
    def test_csv_labeled_extraction(self):
        """CSV with headers should produce labeled text for better detection."""
        text = extract_text(FIXTURES / "sample_patients.csv")
        # Column headers should be used as labels
        assert "Patient Name:" in text or "DOB:" in text
        # PHI values should be present in extracted text
        assert "123-45-6789" in text
        assert "john.smith@gmail.com" in text

    def test_csv_all_rows_extracted(self):
        text = extract_text(FIXTURES / "sample_patients.csv")
        assert "John Smith" in text
        assert "Jane Doe" in text
        assert "Robert Johnson" in text


class TestCSVDetection:
    def test_detects_ssns_from_csv(self):
        text = extract_text(FIXTURES / "sample_patients.csv")
        matches = detect_patterns(text)
        ssn_matches = [m for m in matches if m.phi_type == PHIType.SSN]
        assert len(ssn_matches) >= 3  # Three patients

    def test_detects_emails_from_csv(self):
        text = extract_text(FIXTURES / "sample_patients.csv")
        matches = detect_patterns(text)
        email_matches = [m for m in matches if m.phi_type == PHIType.EMAIL]
        assert len(email_matches) >= 3

    def test_detects_phones_from_csv(self):
        text = extract_text(FIXTURES / "sample_patients.csv")
        matches = detect_patterns(text)
        phone_matches = [m for m in matches if m.phi_type == PHIType.PHONE]
        assert len(phone_matches) >= 3


class TestCSVPipeline:
    def test_csv_full_pipeline(self, tmp_path, vault):
        output = tmp_path / "patients.aqf"
        result = process_file(
            FIXTURES / "sample_patients.csv", output, vault, use_ner=False
        )
        assert not result.errors
        assert result.token_count >= 9  # At least 3 SSNs + 3 emails + 3 phones

        # Verify no PHI in output
        aqf = read_aqf(output)
        assert "123-45-6789" not in aqf.text_content
        assert "john.smith@gmail.com" not in aqf.text_content

    def test_csv_roundtrip(self, tmp_path, vault):
        output = tmp_path / "patients.aqf"
        process_file(FIXTURES / "sample_patients.csv", output, vault, use_ner=False)

        restored = rehydrate(output, vault)
        assert "123-45-6789" in restored
        assert "john.smith@gmail.com" in restored
        assert "321-65-4987" in restored
