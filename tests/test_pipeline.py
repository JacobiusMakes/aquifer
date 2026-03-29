"""End-to-end integration tests for the full pipeline."""

import pytest
from pathlib import Path

from aquifer.engine.pipeline import process_file
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
