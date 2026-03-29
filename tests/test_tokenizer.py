"""Tests for PHI tokenization."""

import re
import uuid

import pytest
from aquifer.engine.detectors.patterns import PHIMatch, PHIType
from aquifer.engine.tokenizer import tokenize


def _make_match(start, end, text, phi_type=PHIType.NAME, confidence=1.0):
    return PHIMatch(start=start, end=end, phi_type=phi_type, text=text,
                    confidence=confidence)


class TestTokenize:
    def test_single_replacement(self):
        text = "Patient: John Smith"
        matches = [_make_match(9, 19, "John Smith")]
        result = tokenize(text, matches)
        assert "John Smith" not in result.deidentified_text
        assert "[AQ:NAME:" in result.deidentified_text
        assert len(result.mappings) == 1

    def test_multiple_replacements(self):
        text = "Patient: John Smith, SSN: 123-45-6789"
        matches = [
            _make_match(9, 19, "John Smith", PHIType.NAME),
            _make_match(26, 37, "123-45-6789", PHIType.SSN),
        ]
        result = tokenize(text, matches)
        assert "John Smith" not in result.deidentified_text
        assert "123-45-6789" not in result.deidentified_text
        assert "[AQ:NAME:" in result.deidentified_text
        assert "[AQ:SSN:" in result.deidentified_text
        assert len(result.mappings) == 2

    def test_same_phi_gets_same_token(self):
        """Same PHI value in one document must get the same token."""
        text = "John Smith visited. John Smith returned."
        matches = [
            _make_match(0, 10, "John Smith"),
            _make_match(20, 30, "John Smith"),
        ]
        result = tokenize(text, matches)
        # Both should map to the same token ID
        assert result.mappings[0].token_id == result.mappings[1].token_id

    def test_tokens_are_uuidv4(self):
        """Verify tokens are valid UUIDv4 format."""
        text = "Patient: John Smith"
        matches = [_make_match(9, 19, "John Smith")]
        result = tokenize(text, matches)
        token_id = result.mappings[0].token_id
        # Should be valid UUID
        parsed = uuid.UUID(token_id, version=4)
        assert str(parsed) == token_id

    def test_different_phi_different_tokens(self):
        """Different PHI values must get different tokens."""
        text = "John Smith and Jane Doe"
        matches = [
            _make_match(0, 10, "John Smith"),
            _make_match(15, 23, "Jane Doe"),
        ]
        result = tokenize(text, matches)
        assert result.mappings[0].token_id != result.mappings[1].token_id

    def test_token_format(self):
        text = "SSN: 123-45-6789"
        matches = [_make_match(5, 16, "123-45-6789", PHIType.SSN)]
        result = tokenize(text, matches)
        # Token should match [AQ:SSN:UUID] format
        pattern = re.compile(r'\[AQ:SSN:[0-9a-f\-]{36}\]')
        assert pattern.search(result.deidentified_text)

    def test_preserves_non_phi_text(self):
        text = "Patient: John Smith. CDT code D3330."
        matches = [_make_match(9, 19, "John Smith")]
        result = tokenize(text, matches)
        assert "CDT code D3330" in result.deidentified_text
        assert "Patient: " in result.deidentified_text
