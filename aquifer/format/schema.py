"""JSON schemas for .aqf manifest and metadata files."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class AQFManifest(BaseModel):
    """Manifest for an .aqf file."""
    version: str = "0.1.0"
    source_type: str  # pdf, docx, txt, csv, json, xml, image
    source_hash: str  # SHA-256 of original file
    creation_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    token_count: int = 0
    compression: str = "zstd"
    deid_method: str = "SafeHarbor"
    schema_version: str = "1"


class AQFMetadata(BaseModel):
    """Non-PHI metadata for an .aqf file."""
    document_type: str = "clinical_note"  # clinical_note, xray, claim_form, etc.
    practice_id: Optional[str] = None  # Anonymized practice identifier
    document_category: Optional[str] = None
    cdt_codes: list[str] = Field(default_factory=list)
    cpt_codes: list[str] = Field(default_factory=list)
    year: Optional[int] = None  # Year only per Safe Harbor
    specialty: Optional[str] = None


class AQFTokenEntry(BaseModel):
    """A token entry in the token manifest (no PHI values)."""
    token_id: str
    phi_type: str
    confidence: float
    source: str


class AQFIntegrity(BaseModel):
    """Integrity verification data."""
    file_hashes: dict[str, str]  # internal_path -> SHA-256
    creation_signature: Optional[str] = None
