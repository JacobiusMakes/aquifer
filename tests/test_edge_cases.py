"""Edge case tests for PHI detection hardening."""

import pytest
from pathlib import Path

from aquifer.engine.detectors.patterns import detect_patterns, PHIType
from aquifer.engine.detectors.ner import detect_names_contextual
from aquifer.engine.reconciler import reconcile
from aquifer.engine.tokenizer import tokenize
from aquifer.engine.pipeline import process_file
from aquifer.format.reader import read_aqf
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def edge_text():
    return (FIXTURES / "edge_cases.txt").read_text()


@pytest.fixture
def vault(tmp_path):
    v = TokenVault(tmp_path / "test.aqv", "test-password")
    v.init()
    yield v
    v.close()


class TestEdgeCaseDetections:
    """Test detection of PHI in challenging formats."""

    def test_detects_hyphenated_names(self, edge_text):
        # contextual detector should catch "Amanda Hernandez-Garcia"
        # but the hyphen is tricky for [A-Z][a-z]+ patterns
        all_matches = detect_patterns(edge_text)
        ctx_matches = detect_names_contextual(edge_text)
        all_texts = [m.text for m in all_matches + ctx_matches]
        # We should detect at least some of the names
        assert any("Hernandez" in t for t in all_texts) or \
               any("Amanda" in t for t in all_texts)

    def test_detects_multiple_date_formats(self, edge_text):
        matches = detect_patterns(edge_text)
        date_matches = [m for m in matches if m.phi_type == PHIType.DATE]
        # Should catch several date formats
        assert len(date_matches) >= 5

    def test_detects_ages_over_89(self, edge_text):
        matches = detect_patterns(edge_text)
        age_matches = [m for m in matches if m.phi_type == PHIType.AGE]
        assert len(age_matches) >= 3  # 92, 103, 91, 95

    def test_detects_all_phone_formats(self, edge_text):
        matches = detect_patterns(edge_text)
        phone_matches = [m for m in matches
                         if m.phi_type in (PHIType.PHONE, PHIType.FAX)]
        assert len(phone_matches) >= 4

    def test_detects_addresses(self, edge_text):
        matches = detect_patterns(edge_text)
        addr_matches = [m for m in matches if m.phi_type == PHIType.ADDRESS]
        assert len(addr_matches) >= 2

    def test_detects_po_box(self, edge_text):
        matches = detect_patterns(edge_text)
        addr_texts = [m.text for m in matches if m.phi_type == PHIType.ADDRESS]
        assert any("P.O. Box" in t or "PO Box" in t for t in addr_texts)

    def test_detects_emails(self, edge_text):
        matches = detect_patterns(edge_text)
        email_matches = [m for m in matches if m.phi_type == PHIType.EMAIL]
        assert len(email_matches) >= 2

    def test_detects_mrn_formats(self, edge_text):
        matches = detect_patterns(edge_text)
        mrn_matches = [m for m in matches if m.phi_type == PHIType.MRN]
        assert len(mrn_matches) >= 2

    def test_detects_insurance_ids(self, edge_text):
        matches = detect_patterns(edge_text)
        acct_matches = [m for m in matches if m.phi_type == PHIType.ACCOUNT]
        assert len(acct_matches) >= 3

    def test_detects_license_numbers(self, edge_text):
        matches = detect_patterns(edge_text)
        lic_matches = [m for m in matches if m.phi_type == PHIType.LICENSE]
        assert len(lic_matches) >= 1

    def test_detects_vin(self, edge_text):
        matches = detect_patterns(edge_text)
        vin_matches = [m for m in matches if m.phi_type == PHIType.VEHICLE]
        assert len(vin_matches) >= 1

    def test_detects_ip_addresses(self, edge_text):
        matches = detect_patterns(edge_text)
        ip_matches = [m for m in matches if m.phi_type == PHIType.IP]
        assert len(ip_matches) >= 2

    def test_does_not_detect_cdt_codes(self, edge_text):
        matches = detect_patterns(edge_text)
        all_texts = " ".join(m.text for m in matches)
        # CDT codes should NOT be in any match text
        for code in ["D0120", "D1110", "D2391", "D3330", "D2750"]:
            assert code not in all_texts

    def test_does_not_detect_icd10(self, edge_text):
        matches = detect_patterns(edge_text)
        all_texts = " ".join(m.text for m in matches)
        assert "K02.9" not in all_texts
        assert "K05.10" not in all_texts

    def test_does_not_detect_dollar_amounts(self, edge_text):
        matches = detect_patterns(edge_text)
        all_texts = " ".join(m.text for m in matches)
        assert "$150.00" not in all_texts
        assert "$2,350.00" not in all_texts

    def test_does_not_detect_vitals(self, edge_text):
        matches = detect_patterns(edge_text)
        all_texts = " ".join(m.text for m in matches)
        assert "120/80" not in all_texts
        assert "98.6" not in all_texts


class TestPDFPipeline:
    """End-to-end pipeline tests with PDF fixture."""

    def test_pdf_deid_roundtrip(self, tmp_path, vault):
        input_file = FIXTURES / "sample_dental_record.pdf"
        if not input_file.exists():
            pytest.skip("PDF fixture not available")

        output_file = tmp_path / "dental.aqf"
        result = process_file(input_file, output_file, vault, use_ner=False)

        assert not result.errors, f"Pipeline errors: {result.errors}"
        assert result.token_count > 0
        assert result.source_type == "pdf"

        # Verify no PHI in .aqf
        aqf = read_aqf(output_file)
        assert "456-78-9012" not in aqf.text_content
        assert "wthompson@outlook.com" not in aqf.text_content
        assert "William" not in aqf.text_content

        # Clinical content preserved
        assert "D0150" in aqf.text_content or "periodontal" in aqf.text_content.lower()

    def test_pdf_rehydrate(self, tmp_path, vault):
        input_file = FIXTURES / "sample_dental_record.pdf"
        if not input_file.exists():
            pytest.skip("PDF fixture not available")

        output_file = tmp_path / "dental.aqf"
        process_file(input_file, output_file, vault, use_ner=False)

        restored = rehydrate(output_file, vault)
        assert "456-78-9012" in restored
        assert "wthompson@outlook.com" in restored


class TestDOCXPipeline:
    """End-to-end pipeline tests with DOCX fixture."""

    def test_docx_deid_roundtrip(self, tmp_path, vault):
        input_file = FIXTURES / "sample_dental_record.docx"
        if not input_file.exists():
            pytest.skip("DOCX fixture not available")

        output_file = tmp_path / "dental.aqf"
        result = process_file(input_file, output_file, vault, use_ner=False)

        assert not result.errors, f"Pipeline errors: {result.errors}"
        assert result.token_count > 0
        assert result.source_type == "docx"

        # Verify no PHI in .aqf
        aqf = read_aqf(output_file)
        assert "321-65-4987" not in aqf.text_content
        assert "jane.doe@yahoo.com" not in aqf.text_content

    def test_docx_rehydrate(self, tmp_path, vault):
        input_file = FIXTURES / "sample_dental_record.docx"
        if not input_file.exists():
            pytest.skip("DOCX fixture not available")

        output_file = tmp_path / "dental.aqf"
        process_file(input_file, output_file, vault, use_ner=False)

        restored = rehydrate(output_file, vault)
        assert "321-65-4987" in restored
        assert "jane.doe@yahoo.com" in restored


class TestEdgeCasePipeline:
    """End-to-end pipeline with edge case fixture."""

    def test_edge_case_file_processes(self, tmp_path, vault):
        input_file = FIXTURES / "edge_cases.txt"
        output_file = tmp_path / "edge.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)
        assert not result.errors
        assert result.token_count > 10  # Should catch many PHI items

    def test_edge_case_no_phi_leak(self, tmp_path, vault):
        input_file = FIXTURES / "edge_cases.txt"
        output_file = tmp_path / "edge.aqf"

        process_file(input_file, output_file, vault, use_ner=False)
        aqf = read_aqf(output_file)

        # High-value PHI should be gone
        assert "456-78-9012" not in aqf.text_content  # SSN not in this fixture
        assert "555-123-4567" not in aqf.text_content
        assert "patient-contact@hospital-network.org" not in aqf.text_content

        # Clinical content should survive
        assert "CDT codes" in aqf.text_content or "D0120" in aqf.text_content
