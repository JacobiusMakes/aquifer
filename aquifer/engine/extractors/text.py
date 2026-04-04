"""Plain text, CSV, JSON, and XML text extraction."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path


_MAX_READ_BYTES = 50 * 1024 * 1024  # 50 MB cap for raw text reads


def extract_text(path: Path) -> str:
    """Extract text content from plain text, CSV, JSON, or XML files.

    Reads up to _MAX_READ_BYTES to avoid loading huge files entirely into memory.
    """
    suffix = path.suffix.lower()

    # Size-limited read to prevent OOM on huge text files
    file_size = path.stat().st_size
    if file_size > _MAX_READ_BYTES:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(_MAX_READ_BYTES)
    else:
        content = path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".csv":
        return _extract_csv(content)
    elif suffix == ".json":
        return _extract_json(content)
    elif suffix == ".xml":
        return _extract_xml(content)
    else:
        # .txt and any other plain text
        return content


def _extract_csv(content: str) -> str:
    """Flatten CSV rows into labeled text for better PHI detection.

    If headers are detected, each cell is prefixed with its column name
    to give the pattern detectors context (e.g., "Patient Name: John Smith").
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return ""

    # Heuristic: first row is headers if it contains known label-like words
    header_keywords = {
        "name", "patient", "ssn", "dob", "date", "phone", "email",
        "address", "mrn", "id", "birth", "member", "account",
        "provider", "npi", "fax", "zip", "city", "state",
    }
    first_row_lower = [c.lower().strip() for c in rows[0]]
    has_headers = any(
        any(kw in cell for kw in header_keywords)
        for cell in first_row_lower
    )

    lines = []
    if has_headers and len(rows) > 1:
        headers = [c.strip() for c in rows[0]]
        for row in rows[1:]:
            parts = []
            for i, cell in enumerate(row):
                if cell.strip():
                    label = headers[i] if i < len(headers) else f"Column{i}"
                    parts.append(f"{label}: {cell.strip()}")
            if parts:
                lines.append(" | ".join(parts))
    else:
        for row in rows:
            lines.append(" | ".join(row))

    return "\n".join(lines)


def _extract_json(content: str) -> str:
    """Recursively extract all string values from JSON."""
    data = json.loads(content)
    parts: list[str] = []
    _walk_json(data, parts)
    return "\n".join(parts)


def _walk_json(obj: object, parts: list[str], prefix: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_json(v, parts, f"{prefix}{k}: ")
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, parts, prefix)
    elif isinstance(obj, str):
        parts.append(f"{prefix}{obj}")
    elif obj is not None:
        parts.append(f"{prefix}{obj}")


def _extract_xml(content: str) -> str:
    """Extract all text content from XML elements."""
    root = ET.fromstring(content)
    parts: list[str] = []
    _walk_xml(root, parts)
    return "\n".join(parts)


def _walk_xml(el: ET.Element, parts: list[str]) -> None:
    if el.text and el.text.strip():
        parts.append(f"{el.tag}: {el.text.strip()}")
    for child in el:
        _walk_xml(child, parts)
    if el.tail and el.tail.strip():
        parts.append(el.tail.strip())
