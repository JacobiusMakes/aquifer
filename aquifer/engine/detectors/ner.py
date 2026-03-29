"""NER-based PHI detection using spaCy.

Uses en_core_web_lg for general NER (PERSON, ORG, GPE, DATE) and
optionally scispaCy en_ner_bc5cdr_md for biomedical entities.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from aquifer.engine.detectors.patterns import PHIMatch, PHIType

logger = logging.getLogger(__name__)

# Lazy-loaded models
_nlp_general = None
_nlp_sci = None

# Map spaCy entity labels to PHI types
_ENTITY_MAP: dict[str, PHIType] = {
    "PERSON": PHIType.NAME,
    "GPE": PHIType.ADDRESS,
    "LOC": PHIType.ADDRESS,
    "FAC": PHIType.ADDRESS,
    "DATE": PHIType.DATE,
    "ORG": PHIType.OTHER,
}

# Confidence scores by entity type
_CONFIDENCE: dict[str, float] = {
    "PERSON": 0.85,
    "GPE": 0.7,
    "LOC": 0.7,
    "FAC": 0.7,
    "DATE": 0.8,
    "ORG": 0.6,
}


def _load_general_model():
    global _nlp_general
    if _nlp_general is None:
        try:
            import spacy
            for model in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"):
                try:
                    _nlp_general = spacy.load(model)
                    logger.info(f"Loaded spaCy model: {model}")
                    break
                except OSError:
                    continue
            if _nlp_general is None:
                logger.error(
                    "No spaCy model found. Run: python -m spacy download en_core_web_sm"
                )
        except ImportError:
            logger.error("spaCy not installed. Run: pip install spacy")
            return None
    return _nlp_general


def _load_sci_model():
    global _nlp_sci
    if _nlp_sci is None:
        try:
            import spacy
            _nlp_sci = spacy.load("en_ner_bc5cdr_md")
        except (OSError, ImportError):
            logger.info("scispaCy model not available, skipping biomedical NER")
            return None
    return _nlp_sci


def detect_ner(text: str, use_sci: bool = True) -> list[PHIMatch]:
    """Run NER-based PHI detection on text."""
    matches: list[PHIMatch] = []

    nlp = _load_general_model()
    if nlp is not None:
        # Process in chunks if text is very long (spaCy has a default limit)
        max_length = nlp.max_length
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        offset = 0
        for chunk in chunks:
            doc = nlp(chunk)
            for ent in doc.ents:
                if ent.label_ in _ENTITY_MAP:
                    matches.append(PHIMatch(
                        start=ent.start_char + offset,
                        end=ent.end_char + offset,
                        phi_type=_ENTITY_MAP[ent.label_],
                        text=ent.text,
                        confidence=_CONFIDENCE.get(ent.label_, 0.7),
                        source="ner",
                    ))
            offset += len(chunk)

    if use_sci:
        sci_nlp = _load_sci_model()
        if sci_nlp is not None:
            doc = sci_nlp(text)
            for ent in doc.ents:
                # bc5cdr detects CHEMICAL and DISEASE — not PHI themselves,
                # but we could use them in future for context-aware filtering
                pass

    return matches


def detect_names_contextual(text: str) -> list[PHIMatch]:
    """Enhanced name detection using context clues.

    Looks for names preceded by role indicators like 'Patient:', 'Dr.',
    'entered by:', etc. Uses [^\\n] instead of \\s to avoid crossing
    line boundaries.
    """
    matches: list[PHIMatch] = []

    # NAME_WORD matches a capitalized word (no newlines in spaces)
    # Using [ \t] instead of \s to avoid matching across line breaks
    name_patterns = [
        # "PATIENT: First Last" or "PATIENT: First Middle Last"
        (r'(?:PATIENT|Patient)\s*:\s*([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,3})',
         PHIType.NAME, 0.95),
        # "entered by: First Last", "signed by:", "reviewed by:", etc.
        (r'(?:entered by|signed by|reviewed by|prepared by|Notes entered by)\s*:\s*'
         r'([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})',
         PHIType.NAME, 0.9),
        # "Dr. First Last" or "Doctor First Last" or "PROVIDER: Dr. First Last"
        (r'(?:PROVIDER|Provider)\s*:\s*(?:Dr\.?\s+)?([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2})',
         PHIType.NAME, 0.85),
        # "Dr. First Last" standalone
        (r'(?:Dr\.|Doctor)\s+([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2})',
         PHIType.NAME, 0.85),
        # "Patient First Last reports" — name in clinical narrative
        (r'(?:Patient|patient)[ \t]+([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})[ \t]+(?:reports?|presents?|states?|denies|complains?)',
         PHIType.NAME, 0.9),
    ]

    seen_spans: set[tuple[int, int]] = set()
    for pattern_str, phi_type, confidence in name_patterns:
        for m in re.finditer(pattern_str, text):
            name = m.group(1).strip()
            # Skip if ALL words are clinical terms (e.g. "General Dentistry")
            name_words = name.lower().split()
            if all(w in _CLINICAL_TERMS for w in name_words):
                continue
            name_start = m.start() + m.group(0).index(name)
            name_end = name_start + len(name)
            span = (name_start, name_end)
            if span not in seen_spans:
                seen_spans.add(span)
                matches.append(PHIMatch(
                    start=name_start, end=name_end,
                    phi_type=phi_type, text=name,
                    confidence=confidence, source="ner_contextual",
                ))

    return matches


# Common clinical/dental terms to NOT flag as names
_CLINICAL_TERMS = frozenset({
    "patient", "doctor", "provider", "dentist", "hygienist",
    "assistant", "radiograph", "periapical", "panoramic",
    "treatment", "diagnosis", "prognosis", "clinical",
    "recommended", "presented", "reported", "therapy",
    "general", "family", "dentistry", "practice",
})
