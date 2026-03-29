"""Read and parse .aqf files."""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional

import zstandard as zstd

from aquifer.format.schema import AQFManifest, AQFMetadata, AQFTokenEntry, AQFIntegrity


class AQFFile:
    """Parsed .aqf file contents."""

    def __init__(
        self,
        manifest: AQFManifest,
        metadata: AQFMetadata,
        text_content: str,
        structured_data: dict | None,
        tokens: list[AQFTokenEntry],
        integrity: AQFIntegrity,
        aqf_hash: str,
    ):
        self.manifest = manifest
        self.metadata = metadata
        self.text_content = text_content
        self.structured_data = structured_data
        self.tokens = tokens
        self.integrity = integrity
        self.aqf_hash = aqf_hash

    @property
    def token_count(self) -> int:
        return len(self.tokens)


def read_aqf(path: Path) -> AQFFile:
    """Read and parse an .aqf file.

    Args:
        path: Path to the .aqf file.

    Returns:
        Parsed AQFFile object.

    Raises:
        ValueError: If the file is not a valid .aqf archive.
        zipfile.BadZipFile: If the file is not a valid ZIP.
    """
    raw = path.read_bytes()
    aqf_hash = hashlib.sha256(raw).hexdigest()

    with zipfile.ZipFile(BytesIO(raw), "r") as zf:
        names = zf.namelist()

        # Required files
        for required in ["manifest.json", "metadata.json", "content/text.zst",
                         "tokens.json", "integrity.json"]:
            if required not in names:
                raise ValueError(f"Invalid .aqf file: missing {required}")

        # Parse manifest
        manifest = AQFManifest.model_validate_json(zf.read("manifest.json"))

        # Parse metadata
        metadata = AQFMetadata.model_validate_json(zf.read("metadata.json"))

        # Decompress text content
        decompressor = zstd.ZstdDecompressor()
        text_content = decompressor.decompress(
            zf.read("content/text.zst")
        ).decode("utf-8")

        # Structured data (optional)
        structured_data = None
        if "content/structured.json.zst" in names:
            struct_raw = decompressor.decompress(
                zf.read("content/structured.json.zst")
            )
            structured_data = json.loads(struct_raw)

        # Parse token manifest
        tokens_raw = json.loads(zf.read("tokens.json"))
        tokens = [AQFTokenEntry.model_validate(t) for t in tokens_raw]

        # Parse integrity
        integrity = AQFIntegrity.model_validate_json(zf.read("integrity.json"))

    return AQFFile(
        manifest=manifest,
        metadata=metadata,
        text_content=text_content,
        structured_data=structured_data,
        tokens=tokens,
        integrity=integrity,
        aqf_hash=aqf_hash,
    )


def verify_integrity(path: Path) -> tuple[bool, list[str]]:
    """Verify the integrity of an .aqf file.

    Returns:
        Tuple of (is_valid, list_of_errors).
    """
    errors: list[str] = []
    raw = path.read_bytes()

    try:
        with zipfile.ZipFile(BytesIO(raw), "r") as zf:
            # Read integrity manifest
            if "integrity.json" not in zf.namelist():
                return False, ["Missing integrity.json"]

            integrity = AQFIntegrity.model_validate_json(zf.read("integrity.json"))

            # Verify each file hash
            for fname, expected_hash in integrity.file_hashes.items():
                if fname not in zf.namelist():
                    errors.append(f"Missing file: {fname}")
                    continue
                actual_hash = hashlib.sha256(zf.read(fname)).hexdigest()
                if actual_hash != expected_hash:
                    errors.append(
                        f"Hash mismatch for {fname}: "
                        f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
                    )
    except zipfile.BadZipFile:
        return False, ["Not a valid ZIP archive"]

    return len(errors) == 0, errors
