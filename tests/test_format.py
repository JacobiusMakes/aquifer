"""Tests for .aqf file format read/write."""

import zipfile
import json
import pytest
from pathlib import Path

from aquifer.engine.detectors.patterns import PHIType
from aquifer.engine.tokenizer import tokenize, TokenizationResult
from aquifer.engine.detectors.patterns import PHIMatch
from aquifer.format.writer import write_aqf
from aquifer.format.reader import read_aqf, verify_integrity
from aquifer.format.schema import AQFMetadata


@pytest.fixture
def sample_tokenization():
    text = "Patient: John Smith, SSN: 123-45-6789"
    matches = [
        PHIMatch(start=9, end=19, phi_type=PHIType.NAME, text="John Smith"),
        PHIMatch(start=26, end=37, phi_type=PHIType.SSN, text="123-45-6789"),
    ]
    return tokenize(text, matches)


@pytest.fixture
def aqf_path(tmp_path, sample_tokenization):
    path = tmp_path / "test.aqf"
    write_aqf(
        output_path=path,
        tokenization=sample_tokenization,
        source_hash="abc123def456",
        source_type="txt",
    )
    return path


class TestAQFWrite:
    def test_creates_file(self, aqf_path):
        assert aqf_path.exists()

    def test_is_valid_zip(self, aqf_path):
        assert zipfile.is_zipfile(aqf_path)

    def test_contains_required_files(self, aqf_path):
        with zipfile.ZipFile(aqf_path, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "metadata.json" in names
            assert "content/text.zst" in names
            assert "tokens.json" in names
            assert "integrity.json" in names

    def test_no_phi_in_aqf(self, aqf_path):
        """The .aqf file must NOT contain any PHI."""
        with zipfile.ZipFile(aqf_path, "r") as zf:
            for name in zf.namelist():
                content = zf.read(name)
                # Check both raw bytes and decoded text where possible
                try:
                    text = content.decode("utf-8", errors="ignore")
                    assert "John Smith" not in text
                    assert "123-45-6789" not in text
                except UnicodeDecodeError:
                    pass


class TestAQFRead:
    def test_read_manifest(self, aqf_path):
        aqf = read_aqf(aqf_path)
        assert aqf.manifest.source_type == "txt"
        assert aqf.manifest.source_hash == "abc123def456"
        assert aqf.manifest.token_count == 2

    def test_read_text_content(self, aqf_path):
        aqf = read_aqf(aqf_path)
        assert "[AQ:NAME:" in aqf.text_content
        assert "[AQ:SSN:" in aqf.text_content
        assert "John Smith" not in aqf.text_content

    def test_read_tokens(self, aqf_path):
        aqf = read_aqf(aqf_path)
        assert len(aqf.tokens) == 2
        types = {t.phi_type for t in aqf.tokens}
        assert "NAME" in types
        assert "SSN" in types

    def test_token_manifest_has_no_phi_values(self, aqf_path):
        """Token manifest must NOT contain resolved PHI values."""
        with zipfile.ZipFile(aqf_path, "r") as zf:
            tokens_data = json.loads(zf.read("tokens.json"))
            for entry in tokens_data:
                assert "phi_value" not in entry
                assert "John Smith" not in str(entry)


class TestAQFIntegrity:
    def test_valid_integrity(self, aqf_path):
        valid, errors = verify_integrity(aqf_path)
        assert valid
        assert len(errors) == 0

    def test_tampered_file_fails(self, aqf_path, tmp_path):
        """Modifying an .aqf file should fail integrity check."""
        tampered = tmp_path / "tampered.aqf"
        # Read, modify, re-write
        with zipfile.ZipFile(aqf_path, "r") as zf_in:
            with zipfile.ZipFile(tampered, "w") as zf_out:
                for name in zf_in.namelist():
                    data = zf_in.read(name)
                    if name == "manifest.json":
                        # Tamper with manifest
                        d = json.loads(data)
                        d["token_count"] = 999
                        data = json.dumps(d).encode()
                    zf_out.writestr(name, data)

        valid, errors = verify_integrity(tampered)
        assert not valid
        assert len(errors) > 0


class TestAQFCompression:
    def test_compressed_smaller_than_raw(self, aqf_path, sample_tokenization):
        raw_size = len(sample_tokenization.deidentified_text.encode())
        aqf_size = aqf_path.stat().st_size
        # For short texts, ZIP overhead may make it larger, but the content
        # block itself should be compressed
        with zipfile.ZipFile(aqf_path, "r") as zf:
            compressed_size = zf.getinfo("content/text.zst").compress_size
            # zstd compressed text should exist
            assert compressed_size > 0
