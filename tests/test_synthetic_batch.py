"""Tests using synthetic generated data to stress-test the pipeline."""

import pytest
from pathlib import Path

from aquifer.engine.pipeline import process_file
from aquifer.format.reader import read_aqf, verify_integrity
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault
from tests.generate_synthetic import generate_clinical_note, generate_claim_json


@pytest.fixture
def vault(tmp_path):
    v = TokenVault(tmp_path / "test.aqv", "test-password")
    v.init()
    yield v
    v.close()


class TestSyntheticClinicalNotes:
    """Test pipeline against randomly generated clinical notes."""

    @pytest.mark.parametrize("seed", range(10))
    def test_clinical_note_roundtrip(self, tmp_path, vault, seed):
        """Each synthetic note should de-identify and rehydrate cleanly."""
        import random
        random.seed(seed)

        record = generate_clinical_note(seed)
        input_path = tmp_path / f"note_{seed}.txt"
        input_path.write_text(record["text"])
        output_path = tmp_path / f"note_{seed}.aqf"

        # De-identify
        result = process_file(input_path, output_path, vault, use_ner=False)
        assert not result.errors, f"Seed {seed}: {result.errors}"
        assert result.token_count > 0

        # Verify .aqf integrity
        valid, errors = verify_integrity(output_path)
        assert valid, f"Seed {seed}: integrity errors: {errors}"

        # Verify no SSN in output
        aqf = read_aqf(output_path)
        ssn = record["ssn"]
        assert ssn not in aqf.text_content, f"Seed {seed}: SSN leaked: {ssn}"

        # Verify no email in output
        email = record["email"]
        assert email not in aqf.text_content, f"Seed {seed}: Email leaked: {email}"

        # Rehydrate and verify SSN restored
        restored = rehydrate(output_path, vault)
        assert ssn in restored, f"Seed {seed}: SSN not restored: {ssn}"
        assert email in restored, f"Seed {seed}: Email not restored: {email}"


class TestSyntheticClaims:
    """Test pipeline against randomly generated claim JSON files."""

    @pytest.mark.parametrize("seed", range(5))
    def test_claim_roundtrip(self, tmp_path, vault, seed):
        import json, random
        random.seed(seed + 100)

        claim = generate_claim_json(seed)
        input_path = tmp_path / f"claim_{seed}.json"
        input_path.write_text(json.dumps(claim, indent=2))
        output_path = tmp_path / f"claim_{seed}.aqf"

        result = process_file(input_path, output_path, vault, use_ner=False)
        assert not result.errors, f"Seed {seed}: {result.errors}"
        assert result.token_count > 0

        # Verify no SSN in output
        aqf = read_aqf(output_path)
        ssn = claim["patient"]["ssn"]
        assert ssn not in aqf.text_content, f"Seed {seed}: SSN leaked"

        # Structured data should also be de-identified
        if aqf.structured_data:
            raw = json.dumps(aqf.structured_data)
            assert ssn not in raw, f"Seed {seed}: SSN in structured data"
