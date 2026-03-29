"""Tests for file extractors (PDF, DOCX, TXT, JSON)."""

import pytest
from pathlib import Path

from aquifer.engine.extractors.text import extract_text
from aquifer.engine.extractors.pdf import extract_pdf
from aquifer.engine.extractors.docx import extract_docx

FIXTURES = Path(__file__).parent / "fixtures"


class TestTextExtractor:
    def test_plain_text(self):
        text = extract_text(FIXTURES / "sample_clinical_note.txt")
        assert "John Michael Smith" in text
        assert "D3330" in text

    def test_json_extraction(self):
        text = extract_text(FIXTURES / "sample_claim.json")
        assert "123-45-6789" in text
        assert "john.smith@gmail.com" in text
        assert "D3330" in text

    def test_edge_cases(self):
        text = extract_text(FIXTURES / "edge_cases.txt")
        assert "Amanda Hernandez-Garcia" in text
        assert "92-year-old" in text


class TestPDFExtractor:
    def test_extract_text_from_pdf(self):
        text = extract_pdf(FIXTURES / "sample_dental_record.pdf")
        assert "William Robert Thompson" in text
        assert "456-78-9012" in text
        assert "wthompson@outlook.com" in text
        assert "D0150" in text

    def test_pdf_preserves_clinical_content(self):
        text = extract_pdf(FIXTURES / "sample_dental_record.pdf")
        assert "periodontal" in text.lower()
        assert "impacted" in text.lower()


class TestDOCXExtractor:
    def test_extract_text_from_docx(self):
        text = extract_docx(FIXTURES / "sample_dental_record.docx")
        assert "Jane Elizabeth Doe" in text
        assert "321-65-4987" in text
        assert "jane.doe@yahoo.com" in text

    def test_docx_extracts_tables(self):
        text = extract_docx(FIXTURES / "sample_dental_record.docx")
        assert "D1110" in text
        assert "Prophylaxis" in text

    def test_docx_preserves_clinical_content(self):
        text = extract_docx(FIXTURES / "sample_dental_record.docx")
        assert "interproximal caries" in text
