"""PDF text extraction using PyMuPDF (fitz)."""

from __future__ import annotations

from pathlib import Path


def extract_pdf(path: Path) -> str:
    """Extract text from a PDF file.

    Uses PyMuPDF's text extraction. Falls back to empty string
    if the PDF contains only images (OCR should handle those).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF extraction. "
            "Install with: pip install PyMuPDF"
        )

    doc = fitz.open(str(path))
    pages: list[str] = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)


def is_scanned_pdf(path: Path) -> bool:
    """Check if a PDF is image-based (scanned) with little/no text layer."""
    try:
        import fitz
    except ImportError:
        return False

    doc = fitz.open(str(path))
    total_text = ""
    has_images = False
    for page in doc:
        total_text += page.get_text("text")
        if page.get_images():
            has_images = True
    doc.close()

    # If there are images but very little text, it's likely scanned
    return has_images and len(total_text.strip()) < 50
