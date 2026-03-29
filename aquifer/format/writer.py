"""Create .aqf files from de-identification engine output.

An .aqf file is a ZIP archive with a defined internal layout:
  manifest.json — version, source type, creation time, etc.
  metadata.json — non-PHI document metadata
  content/text.zst — zstd-compressed de-identified text
  content/structured.json.zst — zstd-compressed structured data (optional)
  tokens.json — token manifest (IDs + types, NOT values)
  integrity.json — SHA-256 hashes of all internal files
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional

import zstandard as zstd

from aquifer.engine.tokenizer import TokenizationResult
from aquifer.format.schema import AQFManifest, AQFMetadata, AQFTokenEntry, AQFIntegrity


def write_aqf(
    output_path: Path,
    tokenization: TokenizationResult,
    source_hash: str,
    source_type: str,
    metadata: AQFMetadata | None = None,
    structured_data: dict | None = None,
    compression_level: int = 3,
) -> str:
    """Write an .aqf file.

    Args:
        output_path: Where to write the .aqf file.
        tokenization: Result from the tokenizer.
        source_hash: SHA-256 of the original file.
        source_type: File type (pdf, docx, txt, etc.)
        metadata: Optional non-PHI metadata.
        structured_data: Optional structured data dict.
        compression_level: Zstd compression level (1-22, default 3).

    Returns:
        SHA-256 hash of the output .aqf file.
    """
    if metadata is None:
        metadata = AQFMetadata()

    # Build manifest
    manifest = AQFManifest(
        source_type=source_type,
        source_hash=source_hash,
        token_count=len(tokenization.mappings),
    )

    # Build token manifest (no PHI values!)
    token_entries = [
        AQFTokenEntry(
            token_id=m.token_id,
            phi_type=m.phi_type.value,
            confidence=m.confidence,
            source=m.source,
        )
        for m in tokenization.mappings
    ]

    # Compress text content
    compressor = zstd.ZstdCompressor(level=compression_level)
    text_compressed = compressor.compress(tokenization.deidentified_text.encode("utf-8"))

    # Track hashes for integrity
    file_hashes: dict[str, str] = {}

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        # manifest.json
        manifest_bytes = manifest.model_dump_json(indent=2).encode()
        zf.writestr("manifest.json", manifest_bytes)
        file_hashes["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()

        # metadata.json
        metadata_bytes = metadata.model_dump_json(indent=2).encode()
        zf.writestr("metadata.json", metadata_bytes)
        file_hashes["metadata.json"] = hashlib.sha256(metadata_bytes).hexdigest()

        # content/text.zst
        zf.writestr("content/text.zst", text_compressed)
        file_hashes["content/text.zst"] = hashlib.sha256(text_compressed).hexdigest()

        # content/structured.json.zst (optional)
        if structured_data is not None:
            struct_bytes = json.dumps(structured_data, indent=2).encode()
            struct_compressed = compressor.compress(struct_bytes)
            zf.writestr("content/structured.json.zst", struct_compressed)
            file_hashes["content/structured.json.zst"] = hashlib.sha256(struct_compressed).hexdigest()

        # tokens.json
        tokens_data = [t.model_dump() for t in token_entries]
        tokens_bytes = json.dumps(tokens_data, indent=2).encode()
        zf.writestr("tokens.json", tokens_bytes)
        file_hashes["tokens.json"] = hashlib.sha256(tokens_bytes).hexdigest()

        # integrity.json
        integrity = AQFIntegrity(file_hashes=file_hashes)
        integrity_bytes = integrity.model_dump_json(indent=2).encode()
        zf.writestr("integrity.json", integrity_bytes)

    # Write to disk
    output_path.write_bytes(buf.getvalue())

    # Return SHA-256 of the complete .aqf file
    return hashlib.sha256(buf.getvalue()).hexdigest()
