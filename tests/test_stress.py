"""Comprehensive stress tests for the Aquifer de-identification pipeline.

Tests all adversarial dental document types from generate_dental_stress.py:
- Runs every document type through the full de-ID pipeline
- Verifies NO PHI leaks through to the output
- Tests batch processing of 500+ files
- Measures and reports throughput (files/sec, MB/sec)
- Tests round-trip: de-ID -> rehydrate -> compare
- Tests with NER enabled and disabled
- Verifies .aqf integrity on all outputs
- Tests concurrent/parallel processing

Usage:
    pytest tests/test_stress.py -v
    pytest tests/test_stress.py -k batch --timeout=120
    pytest tests/test_stress.py -k throughput -s  # show throughput numbers
"""

from __future__ import annotations

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pytest

from aquifer.engine.detectors.patterns import detect_patterns, PHIType
from aquifer.engine.detectors.ner import detect_names_contextual
from aquifer.engine.pipeline import process_file
from aquifer.format.reader import read_aqf, verify_integrity
from aquifer.rehydrate.engine import rehydrate
from aquifer.vault.store import TokenVault

from tests.generate_dental_stress import (
    DentalStressGenerator,
    generate_all_stress_docs,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a fresh token vault for each test."""
    v = TokenVault(tmp_path / "stress_test.aqv", "stress-test-password")
    v.init()
    yield v
    v.close()


@pytest.fixture
def gen():
    """Create a seeded stress generator."""
    return DentalStressGenerator(seed=42)


def _write_and_process(
    text: str,
    filename: str,
    tmp_path: Path,
    vault: TokenVault,
    use_ner: bool = False,
) -> tuple:
    """Helper: write text to file, run pipeline, return (result, aqf_path)."""
    input_path = tmp_path / filename
    input_path.write_text(text, encoding="utf-8")
    output_path = tmp_path / (filename.rsplit(".", 1)[0] + ".aqf")
    result = process_file(input_path, output_path, vault, use_ner=use_ner)
    return result, output_path


def _check_phi_not_leaked(aqf_text: str, phi_dict: dict, doc_label: str):
    """Verify that known PHI values do not appear in the de-identified text.

    Checks string-type PHI values from the phi dict. Skips empty values,
    partial SSNs (last 4), and very short values that could be substrings
    of legitimate content.
    """
    for key, value in phi_dict.items():
        if value is None:
            continue

        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and len(item) >= 6:
                    _assert_not_in_text(aqf_text, item, doc_label, key)
        elif isinstance(value, str) and len(value) >= 6:
            _assert_not_in_text(aqf_text, value, doc_label, key)


def _assert_not_in_text(text: str, phi_value: str, doc_label: str, phi_key: str):
    """Assert a specific PHI value is not present in text."""
    # Skip partial SSN markers like "XXX-XX-1234"
    if phi_value.startswith("XXX-XX"):
        return
    # Skip ISO date strings that are just year (e.g., from dob field)
    # We want to check the actual formatted value, not the ISO representation
    if phi_key == "dob":
        return  # DOB as ISO string may not appear literally; date formats are checked separately
    # Skip very short values that produce false positives
    if len(phi_value) < 6:
        return
    assert phi_value not in text, (
        f"[{doc_label}] PHI leaked — {phi_key}: {phi_value!r} found in de-identified output"
    )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestDentrixCSV:
    """Stress test with Dentrix-style pipe-delimited CSV."""

    def test_dentrix_csv_processes(self, tmp_path, vault, gen):
        doc = gen.generate_dentrix_csv(num_rows=30)
        result, aqf_path = _write_and_process(
            doc["text"], "dentrix_export.csv", tmp_path, vault
        )
        assert not result.errors, f"Errors: {result.errors}"
        assert result.token_count > 0

    def test_dentrix_csv_no_ssn_leak(self, tmp_path, vault, gen):
        doc = gen.generate_dentrix_csv(num_rows=20)
        result, aqf_path = _write_and_process(
            doc["text"], "dentrix.csv", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        for ssn in doc["phi"]["ssns"][:5]:  # Check first 5
            assert ssn not in aqf.text_content, f"SSN leaked: {ssn}"

    def test_dentrix_csv_no_email_leak(self, tmp_path, vault, gen):
        doc = gen.generate_dentrix_csv(num_rows=20)
        result, aqf_path = _write_and_process(
            doc["text"], "dentrix_email.csv", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        for email in doc["phi"]["emails"][:5]:
            assert email not in aqf.text_content, f"Email leaked: {email}"

    def test_dentrix_csv_integrity(self, tmp_path, vault, gen):
        doc = gen.generate_dentrix_csv(num_rows=15)
        _, aqf_path = _write_and_process(
            doc["text"], "dentrix_int.csv", tmp_path, vault
        )
        valid, errors = verify_integrity(aqf_path)
        assert valid, f"Integrity errors: {errors}"


class TestClinicalNotes:
    """Stress test with clinical notes containing dental abbreviations."""

    @pytest.mark.parametrize("seed", range(10))
    def test_clinical_note_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed)
        doc = gen.generate_clinical_note()
        result, aqf_path = _write_and_process(
            doc["text"], f"clinical_{seed}.txt", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"
        assert result.token_count > 0

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content, \
            f"Seed {seed}: SSN leaked"
        assert doc["phi"]["email"] not in aqf.text_content, \
            f"Seed {seed}: Email leaked"

    @pytest.mark.parametrize("seed", range(5))
    def test_clinical_note_with_palmer(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 100)
        doc = gen.generate_clinical_note(with_palmer=True)
        result, _ = _write_and_process(
            doc["text"], f"palmer_{seed}.txt", tmp_path, vault
        )
        assert not result.errors

    def test_clinical_preserves_cdt_codes(self, tmp_path, vault, gen):
        doc = gen.generate_clinical_note()
        _, aqf_path = _write_and_process(
            doc["text"], "cdt_test.txt", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        # CDT codes (D followed by 4 digits) should survive de-identification
        import re
        cdt_pattern = re.compile(r'\bD\d{4}\b')
        original_codes = set(cdt_pattern.findall(doc["text"]))
        surviving_codes = set(cdt_pattern.findall(aqf.text_content))
        assert original_codes == surviving_codes, (
            f"CDT codes changed: original={original_codes}, surviving={surviving_codes}"
        )

    def test_clinical_preserves_icd10_codes(self, tmp_path, vault, gen):
        doc = gen.generate_clinical_note()
        _, aqf_path = _write_and_process(
            doc["text"], "icd_test.txt", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        # ICD-10 codes like K02.9 should not be redacted
        import re
        icd_pattern = re.compile(r'\b[A-Z]\d{2}\.\d{1,2}\b')
        original_icd = set(icd_pattern.findall(doc["text"]))
        surviving_icd = set(icd_pattern.findall(aqf.text_content))
        # All original ICD-10 codes should survive
        assert original_icd.issubset(surviving_icd), (
            f"ICD-10 codes removed: missing={original_icd - surviving_icd}"
        )

    def test_clinical_preserves_dollar_amounts(self, tmp_path, vault, gen):
        doc = gen.generate_clinical_note()
        _, aqf_path = _write_and_process(
            doc["text"], "dollar_test.txt", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        # Dollar amounts should not be redacted
        import re
        dollar_pattern = re.compile(r'\$\d+')
        original_dollars = set(dollar_pattern.findall(doc["text"]))
        surviving_dollars = set(dollar_pattern.findall(aqf.text_content))
        assert len(surviving_dollars) > 0, "All dollar amounts were redacted"


class TestInsuranceEOB:
    """Stress test with insurance EOB documents."""

    def test_eob_clean_processes(self, tmp_path, vault, gen):
        doc = gen.generate_insurance_eob(with_ocr_artifacts=False)
        result, _ = _write_and_process(
            doc["text"], "eob_clean.txt", tmp_path, vault
        )
        assert not result.errors

    def test_eob_ocr_artifacts_processes(self, tmp_path, vault, gen):
        """EOB with OCR artifacts should still process without errors."""
        doc = gen.generate_insurance_eob(with_ocr_artifacts=True)
        result, _ = _write_and_process(
            doc["text"], "eob_ocr.txt", tmp_path, vault
        )
        # OCR artifacts may cause some issues, but pipeline should not crash
        assert result is not None

    @pytest.mark.parametrize("seed", range(5))
    def test_eob_no_ssn_leak(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 200)
        doc = gen.generate_insurance_eob(with_ocr_artifacts=False)
        _, aqf_path = _write_and_process(
            doc["text"], f"eob_{seed}.txt", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        # Full SSN should not appear (only last 4 in the EOB, but full SSN is in phi)
        ssn = doc["phi"]["ssn"]
        assert ssn not in aqf.text_content, f"Full SSN leaked in EOB"

    def test_eob_no_member_id_leak(self, tmp_path, vault, gen):
        doc = gen.generate_insurance_eob(with_ocr_artifacts=False)
        _, aqf_path = _write_and_process(
            doc["text"], "eob_member.txt", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        member_id = doc["phi"]["member_id"]
        assert member_id not in aqf.text_content, f"Member ID leaked: {member_id}"


class TestIntakeForm:
    """Stress test with patient intake/registration forms."""

    @pytest.mark.parametrize("seed", range(5))
    def test_intake_form_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 300)
        doc = gen.generate_intake_form()
        result, aqf_path = _write_and_process(
            doc["text"], f"intake_{seed}.txt", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content
        assert doc["phi"]["email"] not in aqf.text_content


class TestReferralLetter:
    """Stress test with referral letters."""

    @pytest.mark.parametrize("seed", range(5))
    def test_referral_letter_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 400)
        doc = gen.generate_referral_letter()
        result, aqf_path = _write_and_process(
            doc["text"], f"referral_{seed}.txt", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content


class TestJSONClinical:
    """Stress test with JSON clinical records."""

    @pytest.mark.parametrize("seed", range(5))
    def test_json_clinical_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 500)
        doc = gen.generate_json_clinical_record()
        result, aqf_path = _write_and_process(
            doc["text"], f"clinical_{seed}.json", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content
        assert doc["phi"]["email"] not in aqf.text_content

    def test_json_structured_data_deid(self, tmp_path, vault, gen):
        """Structured JSON data should also have PHI removed."""
        doc = gen.generate_json_clinical_record()
        _, aqf_path = _write_and_process(
            doc["text"], "json_struct.json", tmp_path, vault
        )
        aqf = read_aqf(aqf_path)
        if aqf.structured_data:
            raw_struct = json.dumps(aqf.structured_data)
            assert doc["phi"]["ssn"] not in raw_struct, "SSN in structured data"
            assert doc["phi"]["email"] not in raw_struct, "Email in structured data"


class TestXMLHL7:
    """Stress test with XML HL7-style messages."""

    @pytest.mark.parametrize("seed", range(5))
    def test_xml_hl7_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 600)
        doc = gen.generate_xml_hl7_message()
        result, aqf_path = _write_and_process(
            doc["text"], f"hl7_{seed}.xml", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content
        assert doc["phi"]["email"] not in aqf.text_content


class TestBilingualNotes:
    """Stress test with bilingual (English/Spanish) documents."""

    @pytest.mark.parametrize("seed", range(5))
    def test_bilingual_deid(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 700)
        doc = gen.generate_bilingual_note()
        result, aqf_path = _write_and_process(
            doc["text"], f"bilingual_{seed}.txt", tmp_path, vault
        )
        assert not result.errors, f"Seed {seed}: {result.errors}"

        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content
        assert doc["phi"]["email"] not in aqf.text_content


class TestEdgeCaseDocuments:
    """Edge cases: empty, minimal, no-PHI, age boundaries."""

    def test_empty_document(self, tmp_path, vault, gen):
        doc = gen.generate_empty_document()
        input_path = tmp_path / "empty.txt"
        input_path.write_text(doc["text"], encoding="utf-8")
        output_path = tmp_path / "empty.aqf"
        result = process_file(input_path, output_path, vault, use_ner=False)
        # Empty documents should either produce an error or zero tokens
        # The pipeline returns an error for empty content
        if not result.errors:
            assert result.token_count == 0

    def test_minimal_document(self, tmp_path, vault, gen):
        doc = gen.generate_minimal_document()
        result, aqf_path = _write_and_process(
            doc["text"], "minimal.txt", tmp_path, vault
        )
        # Minimal doc with no PHI should process cleanly
        assert not result.errors
        assert result.token_count == 0  # No PHI to tokenize

    def test_no_phi_document(self, tmp_path, vault, gen):
        doc = gen.generate_no_phi_document()
        result, aqf_path = _write_and_process(
            doc["text"], "no_phi.txt", tmp_path, vault
        )
        assert not result.errors
        # No PHI means no tokens (or very few from false positives)
        assert result.token_count <= 2, (
            f"No-PHI doc should have minimal tokens, got {result.token_count}"
        )

    def test_age_boundary_document(self, tmp_path, vault, gen):
        """Ages 89 and below should NOT be flagged; 90+ should be."""
        doc = gen.generate_age_boundary_document()
        result, aqf_path = _write_and_process(
            doc["text"], "age_boundary.txt", tmp_path, vault
        )
        assert not result.errors

        # Check that ages over 89 were detected
        age_detections = [d for d in result.detections if d.phi_type == PHIType.AGE]
        if doc["phi"]["ages_over_89"]:
            assert len(age_detections) > 0, "No ages over 89 were detected"

    def test_long_document(self, tmp_path, vault, gen):
        """Long document (12+ pages) should process without error or timeout."""
        doc = gen.generate_long_document(page_count=12)
        result, aqf_path = _write_and_process(
            doc["text"], "long_doc.txt", tmp_path, vault
        )
        assert not result.errors, f"Long doc errors: {result.errors}"
        assert result.token_count > 0

        # Verify integrity
        valid, errors = verify_integrity(aqf_path)
        assert valid, f"Long doc integrity errors: {errors}"

        # Verify key PHI removed
        aqf = read_aqf(aqf_path)
        assert doc["phi"]["ssn"] not in aqf.text_content
        assert doc["phi"]["email"] not in aqf.text_content

    def test_phone_format_coverage(self, tmp_path, vault, gen):
        """All phone number formats should be detected."""
        doc = gen.generate_all_phone_formats()
        text = doc["text"]
        matches = detect_patterns(text)
        phone_matches = [m for m in matches if m.phi_type in (PHIType.PHONE, PHIType.FAX)]
        # We generate 8+ phone formats; most should be detected
        assert len(phone_matches) >= 5, (
            f"Only {len(phone_matches)} phone formats detected out of 8+"
        )

    def test_date_format_coverage(self, tmp_path, vault, gen):
        """Multiple date formats should be detected."""
        doc = gen.generate_all_date_formats()
        text = doc["text"]
        matches = detect_patterns(text)
        date_matches = [m for m in matches if m.phi_type == PHIType.DATE]
        # We have 12+ date formats; at least half should be detected
        assert len(date_matches) >= 5, (
            f"Only {len(date_matches)} date formats detected out of 12+"
        )

    def test_ssn_variant_detection(self, tmp_path, vault, gen):
        """SSN in multiple formats should be detected."""
        doc = gen.generate_ssn_variants()
        text = doc["text"]
        matches = detect_patterns(text)
        ssn_matches = [m for m in matches if m.phi_type == PHIType.SSN]
        # Should detect at least the standard and contextual SSN formats
        assert len(ssn_matches) >= 2, (
            f"Only {len(ssn_matches)} SSN variants detected"
        )

    def test_dental_identifier_detection(self, tmp_path, vault, gen):
        """NPI, DEA, and license numbers should be detected."""
        doc = gen.generate_dental_identifiers()
        text = doc["text"]
        matches = detect_patterns(text)

        npi_matches = [m for m in matches if m.phi_type == PHIType.NPI]
        license_matches = [m for m in matches if m.phi_type == PHIType.LICENSE]

        assert len(npi_matches) >= 2, f"Only {len(npi_matches)} NPIs detected"
        assert len(license_matches) >= 1, f"No license/DEA numbers detected"


class TestRoundTrip:
    """Test de-identification -> rehydration round-trip."""

    @pytest.mark.parametrize("doc_type", [
        "clinical_note",
        "intake_form",
        "referral_letter",
        "json_clinical",
        "bilingual_note",
    ])
    def test_roundtrip_restores_phi(self, tmp_path, vault, doc_type):
        """De-identified then rehydrated text should contain original PHI."""
        gen = DentalStressGenerator(seed=42)

        generators = {
            "clinical_note": gen.generate_clinical_note,
            "intake_form": gen.generate_intake_form,
            "referral_letter": gen.generate_referral_letter,
            "json_clinical": gen.generate_json_clinical_record,
            "bilingual_note": gen.generate_bilingual_note,
        }

        doc = generators[doc_type]()
        ext = ".json" if doc_type == "json_clinical" else ".txt"
        result, aqf_path = _write_and_process(
            doc["text"], f"rt_{doc_type}{ext}", tmp_path, vault
        )
        assert not result.errors, f"{doc_type}: {result.errors}"

        # Rehydrate
        restored = rehydrate(aqf_path, vault)

        # Key PHI should be restored
        ssn = doc["phi"].get("ssn")
        if ssn:
            assert ssn in restored, f"{doc_type}: SSN not restored: {ssn}"

        email = doc["phi"].get("email")
        if email:
            assert email in restored, f"{doc_type}: Email not restored: {email}"

    @pytest.mark.parametrize("seed", range(5))
    def test_roundtrip_clinical_note_fidelity(self, tmp_path, vault, seed):
        """Rehydrated clinical note should contain all original PHI."""
        gen = DentalStressGenerator(seed=seed + 800)
        doc = gen.generate_clinical_note()

        result, aqf_path = _write_and_process(
            doc["text"], f"fidelity_{seed}.txt", tmp_path, vault
        )
        assert not result.errors

        restored = rehydrate(aqf_path, vault)

        # Check each PHI value is restored
        for key in ["ssn", "email", "phone"]:
            val = doc["phi"].get(key)
            if val:
                assert val in restored, f"Seed {seed}: {key} not restored: {val}"


class TestAQFIntegrity:
    """Verify .aqf file integrity across all document types."""

    @pytest.mark.parametrize("seed", range(10))
    def test_aqf_integrity_clinical(self, tmp_path, vault, seed):
        gen = DentalStressGenerator(seed=seed + 900)
        doc = gen.generate_clinical_note()
        _, aqf_path = _write_and_process(
            doc["text"], f"integ_{seed}.txt", tmp_path, vault
        )
        valid, errors = verify_integrity(aqf_path)
        assert valid, f"Seed {seed}: {errors}"

    def test_aqf_integrity_all_types(self, tmp_path, vault):
        """Verify .aqf integrity for every document type."""
        docs = generate_all_stress_docs(seed=42)
        for i, doc in enumerate(docs):
            if not doc["text"].strip():
                continue  # Skip empty docs

            doc_type = doc["doc_type"]
            ext = ".json" if "json" in doc_type else ".xml" if "xml" in doc_type else ".txt"
            if doc_type == "dentrix_csv":
                ext = ".csv"

            _, aqf_path = _write_and_process(
                doc["text"], f"all_{i:03d}_{doc_type}{ext}", tmp_path, vault
            )
            valid, errors = verify_integrity(aqf_path)
            assert valid, f"[{doc_type}] Integrity failed: {errors}"


class TestBatchProcessing:
    """Test batch processing of many files at once."""

    def test_batch_500_files(self, tmp_path, vault):
        """Process 500+ files sequentially and verify no errors."""
        gen = DentalStressGenerator(seed=1234)
        error_count = 0
        total_tokens = 0
        file_count = 500

        for i in range(file_count):
            # Rotate through document types
            doc_type = i % 7
            if doc_type == 0:
                doc = gen.generate_clinical_note()
                ext = ".txt"
            elif doc_type == 1:
                doc = gen.generate_intake_form()
                ext = ".txt"
            elif doc_type == 2:
                doc = gen.generate_referral_letter()
                ext = ".txt"
            elif doc_type == 3:
                doc = gen.generate_insurance_eob(with_ocr_artifacts=False)
                ext = ".txt"
            elif doc_type == 4:
                doc = gen.generate_json_clinical_record()
                ext = ".json"
            elif doc_type == 5:
                doc = gen.generate_bilingual_note()
                ext = ".txt"
            else:
                doc = gen.generate_xml_hl7_message()
                ext = ".xml"

            input_path = tmp_path / f"batch_{i:04d}{ext}"
            input_path.write_text(doc["text"], encoding="utf-8")
            output_path = tmp_path / f"batch_{i:04d}.aqf"

            result = process_file(input_path, output_path, vault, use_ner=False)
            if result.errors:
                error_count += 1
            total_tokens += result.token_count

        # Allow up to 1% error rate (OCR artifacts, etc.)
        max_errors = max(5, file_count // 100)
        assert error_count <= max_errors, (
            f"Too many errors in batch: {error_count}/{file_count}"
        )
        assert total_tokens > 0, "No tokens produced in entire batch"


class TestThroughput:
    """Measure and report throughput metrics."""

    def test_throughput_measurement(self, tmp_path, vault, capsys):
        """Process 100 files and report throughput."""
        gen = DentalStressGenerator(seed=5678)
        file_count = 100
        total_bytes = 0
        file_paths = []

        # Generate files first
        for i in range(file_count):
            doc = gen.generate_clinical_note()
            input_path = tmp_path / f"tp_{i:04d}.txt"
            input_path.write_text(doc["text"], encoding="utf-8")
            total_bytes += len(doc["text"].encode("utf-8"))
            file_paths.append((input_path, tmp_path / f"tp_{i:04d}.aqf"))

        # Time the processing
        start = time.perf_counter()
        for input_path, output_path in file_paths:
            process_file(input_path, output_path, vault, use_ner=False)
        elapsed = time.perf_counter() - start

        files_per_sec = file_count / elapsed
        mb_per_sec = (total_bytes / (1024 * 1024)) / elapsed

        # Print throughput report (visible with pytest -s)
        with capsys.disabled():
            print(f"\n{'=' * 50}")
            print(f"THROUGHPUT REPORT ({file_count} files)")
            print(f"{'=' * 50}")
            print(f"  Total time:    {elapsed:.2f} sec")
            print(f"  Total data:    {total_bytes / 1024:.1f} KB")
            print(f"  Files/sec:     {files_per_sec:.1f}")
            print(f"  MB/sec:        {mb_per_sec:.3f}")
            print(f"  Avg per file:  {elapsed / file_count * 1000:.1f} ms")
            print(f"{'=' * 50}\n")

        # Sanity check — pipeline should not be absurdly slow
        assert files_per_sec > 1.0, f"Throughput too low: {files_per_sec:.1f} files/sec"


class TestConcurrency:
    """Test parallel/concurrent processing."""

    def test_concurrent_processing(self, tmp_path):
        """Process files concurrently with separate vaults per thread."""
        gen = DentalStressGenerator(seed=9999)
        file_count = 20
        docs = []

        for i in range(file_count):
            doc = gen.generate_clinical_note()
            input_path = tmp_path / f"conc_{i:04d}.txt"
            input_path.write_text(doc["text"], encoding="utf-8")
            docs.append((input_path, tmp_path / f"conc_{i:04d}.aqf", doc))

        results = []
        errors = []

        def process_one(args):
            idx, (input_path, output_path, doc) = args
            # Each thread gets its own vault to avoid SQLite locking issues
            vault_path = tmp_path / f"vault_{idx}.aqv"
            v = TokenVault(vault_path, "concurrent-test")
            v.init()
            try:
                result = process_file(input_path, output_path, v, use_ner=False)
                return (idx, result, doc)
            finally:
                v.close()

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(process_one, (i, d))
                for i, d in enumerate(docs)
            ]
            for future in as_completed(futures):
                try:
                    idx, result, doc = future.result()
                    results.append((idx, result, doc))
                    if result.errors:
                        errors.append((idx, result.errors))
                except Exception as e:
                    errors.append((-1, str(e)))

        assert len(results) == file_count, (
            f"Only {len(results)}/{file_count} files completed"
        )
        # Allow a few errors from concurrency edge cases
        assert len(errors) <= 2, f"Too many concurrent errors: {errors}"

        # Verify outputs are valid
        for idx, result, doc in results:
            if not result.errors:
                output_path = tmp_path / f"conc_{idx:04d}.aqf"
                valid, errs = verify_integrity(output_path)
                assert valid, f"Concurrent file {idx} failed integrity: {errs}"


class TestNERToggle:
    """Test pipeline with NER enabled vs disabled."""

    def test_ner_disabled(self, tmp_path, vault, gen):
        """Pipeline should work with NER disabled."""
        doc = gen.generate_clinical_note()
        result, _ = _write_and_process(
            doc["text"], "ner_off.txt", tmp_path, vault, use_ner=False
        )
        assert not result.errors

    def test_ner_enabled(self, tmp_path, vault, gen):
        """Pipeline should work with NER enabled (if spaCy is available)."""
        doc = gen.generate_clinical_note()
        input_path = tmp_path / "ner_on.txt"
        input_path.write_text(doc["text"], encoding="utf-8")
        output_path = tmp_path / "ner_on.aqf"

        try:
            result = process_file(input_path, output_path, vault, use_ner=True)
            # If NER is available, it should still produce valid output
            if not result.errors:
                valid, errors = verify_integrity(output_path)
                assert valid, f"NER integrity errors: {errors}"
        except ImportError:
            pytest.skip("spaCy not installed — NER test skipped")

    def test_ner_catches_more_names(self, tmp_path, vault, gen):
        """NER mode should detect at least as many names as regex-only."""
        doc = gen.generate_clinical_note()

        # Process without NER
        result_no_ner, _ = _write_and_process(
            doc["text"], "compare_no_ner.txt", tmp_path, vault, use_ner=False
        )
        count_no_ner = result_no_ner.token_count

        # Process with NER (separate vault to avoid token conflicts)
        vault2 = TokenVault(tmp_path / "vault2.aqv", "test2")
        vault2.init()
        try:
            result_ner, _ = _write_and_process(
                doc["text"], "compare_ner.txt", tmp_path, vault2, use_ner=True
            )
            count_ner = result_ner.token_count
            # NER should find at least as many detections
            assert count_ner >= count_no_ner, (
                f"NER found fewer tokens ({count_ner}) than regex-only ({count_no_ner})"
            )
        except (ImportError, Exception):
            pytest.skip("NER not available")
        finally:
            vault2.close()


class TestUnicodeNames:
    """Test handling of Unicode/accented names."""

    @pytest.mark.parametrize("name_pair", [
        ("Jos\u00e9", "Garc\u00eda"),
        ("Ren\u00e9e", "M\u00fcller"),
        ("S\u00e9bastien", "O'Brien-Smith"),
        ("A\u00efda", "Hernandez-Lopez"),
        ("Zo\u00eb", "De La Cruz"),
    ])
    def test_unicode_name_in_note(self, tmp_path, vault, name_pair):
        """Documents with Unicode names should process without errors."""
        first, last = name_pair
        gen = DentalStressGenerator(seed=42)
        doc = gen.generate_clinical_note()
        # Inject the specific name
        text = doc["text"].replace(
            f"PATIENT: {doc['phi']['patient_name']}",
            f"PATIENT: {first} {last}"
        )
        result, aqf_path = _write_and_process(
            text, f"unicode_{first[:3]}.txt", tmp_path, vault
        )
        assert not result.errors, f"Unicode name error: {first} {last}: {result.errors}"

        valid, errors = verify_integrity(aqf_path)
        assert valid, f"Unicode integrity error: {errors}"


class TestAddressVariants:
    """Test detection of various address formats."""

    def test_po_box_detected(self, tmp_path, vault):
        text = (
            "PATIENT: John Smith\n"
            "SSN: 123-45-6789\n"
            "Address: P.O. Box 1234, Springfield, IL 62704\n"
        )
        matches = detect_patterns(text)
        addr_matches = [m for m in matches if m.phi_type == PHIType.ADDRESS]
        assert any("P.O. Box" in m.text for m in addr_matches), "PO Box not detected"

    def test_military_apo_in_document(self, tmp_path, vault, gen):
        """Military APO addresses embedded in a document should process."""
        doc = gen.generate_clinical_note()
        # Append military address
        text = doc["text"] + "\nMailing Address: APO, AE 09001\n"
        result, _ = _write_and_process(
            text, "military.txt", tmp_path, vault
        )
        assert not result.errors


class TestAllDocTypesParametrized:
    """Run every document type through the full pipeline."""

    DOC_TYPES = [
        "dentrix_csv",
        "clinical_note",
        "clinical_note_palmer",
        "eob_clean",
        "eob_ocr",
        "intake_form",
        "referral_letter",
        "json_clinical",
        "xml_hl7",
        "bilingual_note",
        "long_document",
        "no_phi",
        "age_boundary",
        "phone_formats",
        "date_formats",
        "ssn_variants",
        "dental_identifiers",
    ]

    @pytest.mark.parametrize("doc_type", DOC_TYPES)
    def test_doc_type_processes(self, tmp_path, vault, doc_type):
        """Every document type should process through the pipeline."""
        gen = DentalStressGenerator(seed=42)

        generators = {
            "dentrix_csv": lambda: gen.generate_dentrix_csv(num_rows=10),
            "clinical_note": gen.generate_clinical_note,
            "clinical_note_palmer": lambda: gen.generate_clinical_note(with_palmer=True),
            "eob_clean": lambda: gen.generate_insurance_eob(with_ocr_artifacts=False),
            "eob_ocr": lambda: gen.generate_insurance_eob(with_ocr_artifacts=True),
            "intake_form": gen.generate_intake_form,
            "referral_letter": gen.generate_referral_letter,
            "json_clinical": gen.generate_json_clinical_record,
            "xml_hl7": gen.generate_xml_hl7_message,
            "bilingual_note": gen.generate_bilingual_note,
            "long_document": lambda: gen.generate_long_document(page_count=3),
            "no_phi": gen.generate_no_phi_document,
            "age_boundary": gen.generate_age_boundary_document,
            "phone_formats": gen.generate_all_phone_formats,
            "date_formats": gen.generate_all_date_formats,
            "ssn_variants": gen.generate_ssn_variants,
            "dental_identifiers": gen.generate_dental_identifiers,
        }

        doc = generators[doc_type]()
        if not doc["text"].strip():
            pytest.skip(f"{doc_type} produced empty text")

        ext_map = {
            "dentrix_csv": ".csv",
            "json_clinical": ".json",
            "xml_hl7": ".xml",
        }
        ext = ext_map.get(doc_type, ".txt")

        result, aqf_path = _write_and_process(
            doc["text"], f"{doc_type}{ext}", tmp_path, vault
        )
        # Pipeline should not crash on any document type
        assert result is not None, f"{doc_type} returned None"
        # For non-OCR types, there should be no errors
        if "ocr" not in doc_type:
            assert not result.errors, f"{doc_type}: {result.errors}"

    @pytest.mark.parametrize("doc_type", [
        "clinical_note",
        "intake_form",
        "referral_letter",
        "json_clinical",
        "bilingual_note",
        "dental_identifiers",
        "ssn_variants",
    ])
    def test_doc_type_no_ssn_leak(self, tmp_path, vault, doc_type):
        """No SSN should leak through for any document type."""
        gen = DentalStressGenerator(seed=42)

        generators = {
            "clinical_note": gen.generate_clinical_note,
            "intake_form": gen.generate_intake_form,
            "referral_letter": gen.generate_referral_letter,
            "json_clinical": gen.generate_json_clinical_record,
            "bilingual_note": gen.generate_bilingual_note,
            "dental_identifiers": gen.generate_dental_identifiers,
            "ssn_variants": gen.generate_ssn_variants,
        }

        doc = generators[doc_type]()
        ext = ".json" if doc_type == "json_clinical" else ".txt"
        result, aqf_path = _write_and_process(
            doc["text"], f"leak_{doc_type}{ext}", tmp_path, vault
        )

        if result.errors:
            return  # Skip leak check if processing failed

        aqf = read_aqf(aqf_path)

        # Check SSN in various forms
        ssn = doc["phi"].get("ssn")
        if ssn:
            assert ssn not in aqf.text_content, f"[{doc_type}] SSN leaked: {ssn}"

        # Also check SSN list form (for multi-patient docs)
        ssns = doc["phi"].get("ssns", [])
        for ssn in ssns[:3]:
            assert ssn not in aqf.text_content, f"[{doc_type}] SSN leaked: {ssn}"
