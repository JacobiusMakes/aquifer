"""End-to-end integration tests for the full pipeline."""

import pytest
from pathlib import Path
from unittest.mock import patch

from aquifer.engine.pipeline import (
    process_file,
    MAX_INPUT_FILE_BYTES,
    MAX_TEXT_CHARS,
    _extract_metadata,
)
from aquifer.format.reader import read_aqf, verify_integrity
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def vault(tmp_path):
    v = TokenVault(tmp_path / "test.aqv", "test-password")
    v.init()
    yield v
    v.close()


class TestPipelineIntegration:
    def test_clinical_note_end_to_end(self, tmp_path, vault):
        """Full pipeline: txt file → .aqf → rehydrate → compare."""
        input_file = FIXTURES / "sample_clinical_note.txt"
        output_file = tmp_path / "output.aqf"

        # Process
        result = process_file(input_file, output_file, vault, use_ner=False)

        # Verify no errors
        assert not result.errors, f"Pipeline errors: {result.errors}"
        assert result.token_count > 0
        assert output_file.exists()

        # Verify .aqf is valid
        valid, errors = verify_integrity(output_file)
        assert valid, f"Integrity errors: {errors}"

        # Verify no PHI in .aqf
        aqf = read_aqf(output_file)
        assert "123-45-6789" not in aqf.text_content
        assert "john.smith@gmail.com" not in aqf.text_content
        assert "John Michael Smith" not in aqf.text_content

        # Verify clinical content preserved
        assert "D3330" in aqf.text_content  # CDT code should survive
        assert "periapical" in aqf.text_content  # Clinical term

    def test_rehydrate_after_pipeline(self, tmp_path, vault):
        """Verify rehydration restores original content."""
        input_file = FIXTURES / "sample_clinical_note.txt"
        original_text = input_file.read_text()
        output_file = tmp_path / "output.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)
        assert not result.errors

        # Rehydrate
        restored = rehydrate(output_file, vault)

        # Key PHI should be restored
        assert "123-45-6789" in restored
        assert "john.smith@gmail.com" in restored
        assert "192.168.1.105" in restored

    def test_json_file_processing(self, tmp_path, vault):
        """Process a JSON file through the pipeline."""
        input_file = FIXTURES / "sample_claim.json"
        output_file = tmp_path / "claim.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)
        assert not result.errors
        assert result.token_count > 0

        # Verify no PHI in output
        aqf = read_aqf(output_file)
        assert "123-45-6789" not in aqf.text_content
        assert "john.smith@gmail.com" not in aqf.text_content

    def test_detects_expected_phi_types(self, tmp_path, vault):
        """Verify the pipeline detects the expected PHI types."""
        input_file = FIXTURES / "sample_clinical_note.txt"
        output_file = tmp_path / "output.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)

        detected_types = {d.phi_type.value for d in result.detections}
        # Should detect at least these types
        assert "SSN" in detected_types
        assert "EMAIL" in detected_types
        assert "IP" in detected_types
        assert "DATE" in detected_types


class TestPipelineLimits:
    def test_file_over_max_size_rejected(self, tmp_path, vault):
        """Files exceeding MAX_INPUT_FILE_BYTES must be rejected with an error."""
        large_file = tmp_path / "large.txt"
        # Write exactly one byte over the limit
        large_file.write_bytes(b"x" * (MAX_INPUT_FILE_BYTES + 1))
        output_file = tmp_path / "output.aqf"

        result = process_file(large_file, output_file, vault, use_ner=False)

        assert result.errors, "Expected an error for oversized file"
        assert any("too large" in e.lower() or "maximum" in e.lower() for e in result.errors)
        assert not output_file.exists()

    def test_extracted_text_over_limit_is_truncated(self, tmp_path, vault):
        """Text extracted beyond MAX_TEXT_CHARS must be silently truncated, not error."""
        # Build text slightly over the limit — no PHI so token count will be 0,
        # but the pipeline should complete without errors.
        oversized_text = "A" * (MAX_TEXT_CHARS + 1000)
        input_file = tmp_path / "big.txt"
        input_file.write_text(oversized_text)
        output_file = tmp_path / "output.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)

        # Must not surface a size-related error
        assert not any("too large" in e.lower() for e in result.errors)
        assert output_file.exists(), "Output should still be written after truncation"

    def test_empty_file_returns_extraction_error(self, tmp_path, vault):
        """Processing a file with no text content must add an error to the result."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_bytes(b"")
        output_file = tmp_path / "output.aqf"

        result = process_file(empty_file, output_file, vault, use_ner=False)

        assert result.errors, "Expected an error for empty file"
        assert any("no text" in e.lower() for e in result.errors)
        assert not output_file.exists()

    def test_unsupported_extension_falls_back_to_text(self, tmp_path, vault):
        """A file with an unknown extension should be processed as plain text."""
        input_file = tmp_path / "note.unknownext"
        input_file.write_text("Patient SSN: 123-45-6789")
        output_file = tmp_path / "output.aqf"

        result = process_file(input_file, output_file, vault, use_ner=False)

        # Pipeline should not error — unknown type falls back to text extraction
        assert not result.errors, f"Unexpected errors: {result.errors}"
        assert result.token_count > 0
        assert output_file.exists()

    def test_invalid_pdf_content_handled_gracefully(self, tmp_path, vault):
        """A .pdf file containing garbage bytes should fail gracefully."""
        bad_pdf = tmp_path / "corrupt.pdf"
        bad_pdf.write_bytes(b"\x00\xff\xfe" * 50)  # Not a real PDF
        output_file = tmp_path / "output.aqf"

        result = process_file(bad_pdf, output_file, vault, use_ner=False)

        # Either it extracts empty text (error) or raises and records an error — never crashes
        assert isinstance(result.errors, list)


class TestMetadataExtraction:
    def test_detects_claim_form_doc_type(self):
        text = "This is an 837 dental claim form for patient services."
        meta = _extract_metadata(text, "txt")
        assert meta.document_type == "claim_form"

    def test_detects_radiograph_doc_type(self):
        text = "Periapical radiograph shows bone loss at #14."
        meta = _extract_metadata(text, "txt")
        assert meta.document_type == "radiograph"

    def test_detects_treatment_plan_doc_type(self):
        text = "Treatment plan approved: extraction of #32 followed by implant."
        meta = _extract_metadata(text, "txt")
        assert meta.document_type == "treatment_plan"

    def test_defaults_to_clinical_note_doc_type(self):
        text = "Patient presents with sensitivity in upper left quadrant."
        meta = _extract_metadata(text, "txt")
        assert meta.document_type == "clinical_note"

    def test_extracts_cdt_codes(self):
        text = "Procedure: D3330 (root canal molar), follow-up D0330 (panoramic)."
        meta = _extract_metadata(text, "txt")
        assert "D3330" in meta.cdt_codes
        assert "D0330" in meta.cdt_codes

    def test_deduplicates_cdt_codes(self):
        text = "D1234 was performed. Another D1234 appointment scheduled."
        meta = _extract_metadata(text, "txt")
        assert meta.cdt_codes.count("D1234") == 1

    def test_no_cdt_codes_returns_empty_list(self):
        text = "No procedure codes present in this note."
        meta = _extract_metadata(text, "txt")
        assert meta.cdt_codes == []
