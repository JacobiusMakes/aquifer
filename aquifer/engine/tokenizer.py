"""PHI tokenization: replace detected PHI spans with random tokens.

Token format: [AQ:<TYPE>:<UUIDv4>]

Key properties:
- Tokens are UUIDv4 — cryptographically random, zero derivation from source PHI (§164.514(c))
- Same PHI value within a single document gets the same token (consistency)
- Different documents get different tokens unless persistent mapping is configured
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from aquifer.engine.detectors.patterns import PHIMatch, PHIType


@dataclass(frozen=True, slots=True)
class TokenMapping:
    """A single token-to-PHI mapping."""
    token_id: str         # UUIDv4
    token_string: str     # [AQ:TYPE:UUID]
    phi_type: PHIType
    phi_value: str        # Original PHI text
    start: int            # Position in original text
    end: int              # Position in original text
    confidence: float
    source: str


@dataclass
class TokenizationResult:
    """Result of tokenizing a text."""
    deidentified_text: str
    mappings: list[TokenMapping] = field(default_factory=list)
    original_text: str = ""


def tokenize(text: str, matches: list[PHIMatch]) -> TokenizationResult:
    """Replace PHI spans in text with random tokens.

    Args:
        text: Original text containing PHI.
        matches: Sorted, reconciled PHI matches (non-overlapping).

    Returns:
        TokenizationResult with de-identified text and token mappings.
    """
    # Sort matches by position (should already be sorted, but ensure)
    sorted_matches = sorted(matches, key=lambda m: m.start)

    # Map PHI values to tokens for consistency within this document
    value_to_token: dict[tuple[PHIType, str], str] = {}
    mappings: list[TokenMapping] = []

    # Build replacement list
    replacements: list[tuple[int, int, str, TokenMapping]] = []

    for match in sorted_matches:
        phi_key = (match.phi_type, match.text)

        if phi_key in value_to_token:
            token_id = value_to_token[phi_key]
        else:
            token_id = str(uuid.uuid4())
            value_to_token[phi_key] = token_id

        token_string = f"[AQ:{match.phi_type.value}:{token_id}]"

        mapping = TokenMapping(
            token_id=token_id,
            token_string=token_string,
            phi_type=match.phi_type,
            phi_value=match.text,
            start=match.start,
            end=match.end,
            confidence=match.confidence,
            source=match.source,
        )
        mappings.append(mapping)
        replacements.append((match.start, match.end, token_string, mapping))

    # Apply replacements from end to start to preserve positions
    result = text
    for start, end, token_string, _ in reversed(replacements):
        result = result[:start] + token_string + result[end:]

    return TokenizationResult(
        deidentified_text=result,
        mappings=mappings,
        original_text=text,
    )
