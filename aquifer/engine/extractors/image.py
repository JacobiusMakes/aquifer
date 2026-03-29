"""Image text extraction using Pillow + pytesseract OCR."""

from __future__ import annotations

from pathlib import Path


def extract_image(path: Path) -> str:
    """Extract text from an image file using OCR.

    Supports JPEG, PNG, TIFF, and BMP.
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError(
            "Pillow is required for image processing. "
            "Install with: pip install Pillow"
        )

    try:
        import pytesseract
    except ImportError:
        raise ImportError(
            "pytesseract is required for OCR. "
            "Install with: pip install pytesseract"
        )

    img = Image.open(str(path))

    # Basic preprocessing for better OCR
    if img.mode != "RGB":
        img = img.convert("RGB")

    text = pytesseract.image_to_string(img)
    return text


def is_image_file(path: Path) -> bool:
    """Check if a file is a supported image format."""
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}
