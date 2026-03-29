"""Tests for NER integration when spaCy is available."""

import pytest
from pathlib import Path

from aquifer.engine.detectors.patterns import PHIType

FIXTURES = Path(__file__).parent / "fixtures"


def _spacy_available():
    try:
        import spacy
        spacy.load("en_core_web_sm")
        return True
    except (ImportError, OSError):
        return False


@pytest.mark.skipif(not _spacy_available(), reason="spaCy model not installed")
class TestNERIntegration:
    def test_detects_person_names(self):
        from aquifer.engine.detectors.ner import detect_ner
        text = "Patient John Michael Smith was seen by Dr. Sarah Johnson today."
        matches = detect_ner(text, use_sci=False)
        name_matches = [m for m in matches if m.phi_type == PHIType.NAME]
        name_texts = [m.text for m in name_matches]
        assert any("John" in t for t in name_texts)
        assert any("Sarah Johnson" in t for t in name_texts)

    def test_detects_locations(self):
        from aquifer.engine.detectors.ner import detect_ner
        text = "Patient lives in Springfield, Illinois."
        matches = detect_ner(text, use_sci=False)
        addr_matches = [m for m in matches if m.phi_type == PHIType.ADDRESS]
        assert any("Springfield" in m.text for m in addr_matches)

    def test_detects_dates(self):
        from aquifer.engine.detectors.ner import detect_ner
        text = "Follow-up visit scheduled for January 15th."
        matches = detect_ner(text, use_sci=False)
        date_matches = [m for m in matches if m.phi_type == PHIType.DATE]
        assert len(date_matches) >= 1

    def test_ner_with_pipeline(self):
        """Test that NER detections are merged into the full pipeline."""
        from aquifer.engine.pipeline import process_file
        from aquifer.vault.store import TokenVault
        from aquifer.format.reader import read_aqf
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            vault = TokenVault(Path(tmp) / "v.aqv", "test")
            vault.init()
            out = Path(tmp) / "out.aqf"

            # Process with NER enabled
            result = process_file(
                FIXTURES / "sample_clinical_note.txt", out, vault,
                use_ner=True,
            )
            assert not result.errors
            # NER should add additional detections beyond regex
            assert result.token_count > 0

            # Verify no PHI in output
            aqf = read_aqf(out)
            assert "John Michael Smith" not in aqf.text_content
            assert "123-45-6789" not in aqf.text_content
            vault.close()

    def test_ner_catches_names_regex_misses(self):
        """NER should catch names without explicit label context."""
        from aquifer.engine.detectors.ner import detect_ner
        # This name appears without a "PATIENT:" label
        text = "Spoke with Margaret Thompson regarding her treatment options."
        matches = detect_ner(text, use_sci=False)
        name_matches = [m for m in matches if m.phi_type == PHIType.NAME]
        assert any("Margaret Thompson" in m.text for m in name_matches)
