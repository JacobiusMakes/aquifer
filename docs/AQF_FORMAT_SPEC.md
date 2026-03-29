# Aquifer File Format (.aqf) Specification

**Version:** 0.1.0
**Status:** Draft
**License:** Apache 2.0

## Overview

An `.aqf` (Aquifer File) is a ZIP archive containing de-identified medical/dental document data in a standardized, compressed, hashable container format.

AQF files contain **zero Protected Health Information (PHI)**. All PHI has been replaced with cryptographically random tokens prior to packaging. This means AQF files can be stored on any commodity storage medium without HIPAA-grade security requirements.

## Container Structure

```
file.aqf (ZIP archive)
├── manifest.json          # File metadata and processing info
├── metadata.json          # Non-PHI document metadata
├── content/
│   ├── text.zst           # Zstd-compressed de-identified text
│   └── structured.json.zst # Zstd-compressed structured data (optional)
├── tokens.json            # Token manifest (IDs + types only, NO values)
└── integrity.json         # SHA-256 hashes of all internal files
```

## File Descriptions

### manifest.json

```json
{
  "version": "0.1.0",
  "source_type": "pdf",
  "source_hash": "<SHA-256 of original file>",
  "creation_time": "2024-01-15T10:30:00Z",
  "token_count": 15,
  "compression": "zstd",
  "deid_method": "SafeHarbor",
  "schema_version": "1"
}
```

### metadata.json

Non-PHI metadata about the document. CDT/CPT codes, document type, and year-only dates are preserved since they are not individually identifying.

```json
{
  "document_type": "clinical_note",
  "practice_id": null,
  "cdt_codes": ["D3330", "D2750"],
  "year": 2024,
  "specialty": "general_dentistry"
}
```

### content/text.zst

Zstandard-compressed UTF-8 text. The text contains `[AQ:TYPE:UUID]` tokens in place of all detected PHI.

### tokens.json

Token manifest listing all tokens used in this file. Contains token IDs and types but **never** the resolved PHI values. This enables vault lookup during re-hydration.

```json
[
  {
    "token_id": "a3f7b2c1-...",
    "phi_type": "NAME",
    "confidence": 0.95,
    "source": "regex"
  }
]
```

### integrity.json

SHA-256 hashes of all other files in the archive for tamper detection.

```json
{
  "file_hashes": {
    "manifest.json": "<sha256>",
    "metadata.json": "<sha256>",
    "content/text.zst": "<sha256>",
    "tokens.json": "<sha256>"
  }
}
```

## Token Format

Tokens follow the pattern: `[AQ:<TYPE>:<UUIDv4>]`

Supported types: NAME, DATE, SSN, PHONE, FAX, EMAIL, ADDRESS, MRN, ACCOUNT, LICENSE, VEHICLE, DEVICE, URL, IP, BIOMETRIC, PHOTO, NPI, OTHER.

## Compression

- Text content: Zstandard (zstd) level 3
- Structured data: Zstandard (zstd) level 3
- Images: Stored as-is (already compressed)

## Integrity Verification

To verify an AQF file:
1. Read `integrity.json`
2. For each entry in `file_hashes`, compute SHA-256 of the corresponding file
3. Compare computed hashes with stored hashes
4. Any mismatch indicates tampering

## Interoperability

AQF files are standard ZIP archives readable by any ZIP-compatible tool. The internal JSON files use standard UTF-8 encoding. No proprietary formats or dependencies required.
