"""Tests for rehydration engine — round-trip integrity."""

import pytest
from pathlib import Path

from aquifer.engine.detectors.patterns import PHIMatch, PHIType, detect_patterns
from aquifer.engine.reconciler import reconcile
from aquifer.engine.tokenizer import tokenize
from aquifer.format.writer import write_aqf
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault


SAMPLE_TEXT = """\
PATIENT: John Michael Smith
DOB: 03/15/1987
SSN: 123-45-6789
Phone: (555) 867-5309
Email: john.smith@gmail.com

CLINICAL NOTES:
Patient John Smith reports intermittent pain for 2 weeks.
IP: 192.168.1.105
"""


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "test.aqv"


@pytest.fixture
def vault(vault_path):
    v = TokenVault(vault_path, "test-password")
    v.init()
    yield v
    v.close()


@pytest.fixture
def aqf_file(tmp_path, vault):
    """Create an .aqf file from sample text through the full pipeline."""
    # Detect PHI
    matches = detect_patterns(SAMPLE_TEXT)
    reconciled = reconcile(matches)

    # Tokenize
    result = tokenize(SAMPLE_TEXT, reconciled)

    # Write .aqf
    aqf_path = tmp_path / "test.aqf"
    aqf_hash = write_aqf(
        output_path=aqf_path,
        tokenization=result,
        source_hash="test_source_hash",
        source_type="txt",
    )

    # Store tokens in vault
    for m in result.mappings:
        vault.store_token(
            token_id=m.token_id,
            phi_type=m.phi_type.value,
            phi_value=m.phi_value,
            source_file_hash="test_source_hash",
            aqf_file_hash=aqf_hash,
            confidence=m.confidence,
        )

    return aqf_path


class TestRoundTrip:
    def test_rehydration_restores_ssn(self, aqf_file, vault):
        text = rehydrate(aqf_file, vault)
        assert "123-45-6789" in text

    def test_rehydration_restores_email(self, aqf_file, vault):
        text = rehydrate(aqf_file, vault)
        assert "john.smith@gmail.com" in text

    def test_rehydration_restores_ip(self, aqf_file, vault):
        text = rehydrate(aqf_file, vault)
        assert "192.168.1.105" in text

    def test_rehydration_restores_phone(self, aqf_file, vault):
        text = rehydrate(aqf_file, vault)
        assert "867-5309" in text

    def test_clinical_content_preserved(self, aqf_file, vault):
        """Non-PHI clinical content should survive the round trip."""
        text = rehydrate(aqf_file, vault)
        assert "intermittent pain" in text
        assert "CLINICAL NOTES" in text

    def test_deidentified_has_no_phi(self, aqf_file):
        """The .aqf file itself must not contain PHI."""
        from aquifer.format.reader import read_aqf
        aqf = read_aqf(aqf_file)
        assert "123-45-6789" not in aqf.text_content
        assert "john.smith@gmail.com" not in aqf.text_content
        assert "192.168.1.105" not in aqf.text_content
