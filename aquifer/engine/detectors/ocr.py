"""OCR pipeline wrapper for image-based PHI detection.

Extracts text from images via pytesseract, then runs pattern + NER
detection on the extracted text.
"""

from __future__ import annotations

from pathlib import Path

from aquifer.engine.detectors.patterns import PHIMatch, detect_patterns


def detect_ocr(image_path: Path) -> tuple[str, list[PHIMatch]]:
    """Extract text from image and detect PHI in it.

    Args:
        image_path: Path to an image file.

    Returns:
        Tuple of (extracted_text, phi_matches).
        OCR-derived matches have lower confidence (0.5-0.95 multiplier).
    """
    from aquifer.engine.extractors.image import extract_image

    text = extract_image(image_path)
    if not text.strip():
        return "", []

    # Run pattern detection on OCR text
    matches = detect_patterns(text)

    # Lower confidence for OCR-derived detections
    ocr_matches = [
        PHIMatch(
            start=m.start, end=m.end,
            phi_type=m.phi_type, text=m.text,
            confidence=m.confidence * 0.8,  # OCR confidence penalty
            source="ocr",
        )
        for m in matches
    ]

    return text, ocr_matches
