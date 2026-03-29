"""Main pipeline orchestrator: file → detections → tokens → .aqf + vault.

This is the top-level entry point that wires together:
1. File type detection + text extraction
2. PHI detection (regex patterns + NER + contextual names)
3. Detection reconciliation
4. Tokenization
5. .aqf file creation
6. Vault storage
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from aquifer.engine.detectors.patterns import PHIMatch, detect_patterns
from aquifer.engine.detectors.ner import detect_ner, detect_names_contextual
from aquifer.engine.reconciler import reconcile, flag_low_confidence
from aquifer.engine.tokenizer import tokenize, TokenizationResult
from aquifer.format.schema import AQFMetadata
from aquifer.format.writer import write_aqf
from aquifer.vault.store import TokenVault

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing a single file through the pipeline."""
    source_path: str
    source_hash: str
    source_type: str
    aqf_path: Optional[str] = None
    aqf_hash: Optional[str] = None
    token_count: int = 0
    detections: list[PHIMatch] = field(default_factory=list)
    low_confidence: list[PHIMatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _detect_file_type(path: Path) -> str:
    """Determine file type from extension."""
    ext_map = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".doc": "docx",
        ".txt": "txt",
        ".csv": "csv",
        ".json": "json",
        ".xml": "xml",
        ".jpg": "image", ".jpeg": "image",
        ".png": "image", ".tiff": "image", ".tif": "image",
        ".bmp": "image",
    }
    return ext_map.get(path.suffix.lower(), "txt")


def _extract_text(path: Path, file_type: str) -> str:
    """Extract text from a file based on its type."""
    if file_type == "pdf":
        from aquifer.engine.extractors.pdf import extract_pdf
        return extract_pdf(path)
    elif file_type == "docx":
        from aquifer.engine.extractors.docx import extract_docx
        return extract_docx(path)
    elif file_type == "image":
        from aquifer.engine.extractors.image import extract_image
        return extract_image(path)
    else:
        from aquifer.engine.extractors.text import extract_text
        return extract_text(path)


def _compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def process_file(
    input_path: Path,
    output_path: Path,
    vault: TokenVault,
    use_ner: bool = True,
    confidence_threshold: float = 0.0,
    verbose: bool = False,
) -> PipelineResult:
    """Process a single file through the de-identification pipeline.

    Args:
        input_path: Path to the input file.
        output_path: Path for the output .aqf file.
        vault: An opened TokenVault instance.
        use_ner: Whether to use NER detection (requires spaCy).
        confidence_threshold: Minimum confidence for detections.
        verbose: Whether to log detailed detection info.

    Returns:
        PipelineResult with processing details.
    """
    result = PipelineResult(
        source_path=str(input_path),
        source_hash="",
        source_type="",
    )

    try:
        # Step 1: Detect file type and compute hash
        file_type = _detect_file_type(input_path)
        result.source_type = file_type
        result.source_hash = _compute_file_hash(input_path)

        if verbose:
            logger.info(f"Processing {input_path} (type={file_type})")

        # Step 2: Extract text
        text = _extract_text(input_path, file_type)
        if not text.strip():
            result.errors.append("No text content extracted")
            return result

        if verbose:
            logger.info(f"Extracted {len(text)} characters")

        # Step 3: Detect PHI
        all_matches: list[PHIMatch] = []

        # Stage 1: Regex patterns
        pattern_matches = detect_patterns(text)
        all_matches.extend(pattern_matches)
        if verbose:
            logger.info(f"Pattern detections: {len(pattern_matches)}")

        # Stage 2: NER (if available)
        if use_ner:
            try:
                ner_matches = detect_ner(text, use_sci=False)
                all_matches.extend(ner_matches)
                if verbose:
                    logger.info(f"NER detections: {len(ner_matches)}")
            except Exception as e:
                logger.warning(f"NER detection failed: {e}")

        # Stage 2.5: Contextual name detection
        ctx_matches = detect_names_contextual(text)
        all_matches.extend(ctx_matches)
        if verbose:
            logger.info(f"Contextual name detections: {len(ctx_matches)}")

        # Step 4: Reconcile detections
        reconciled = reconcile(all_matches)
        result.detections = reconciled
        result.low_confidence = flag_low_confidence(reconciled)

        if verbose:
            logger.info(f"Reconciled detections: {len(reconciled)}")
            logger.info(f"Low confidence (for review): {len(result.low_confidence)}")

        # Step 5: Tokenize
        tokenization = tokenize(text, reconciled)
        result.token_count = len(tokenization.mappings)

        # Step 5.5: Extract non-PHI metadata
        metadata = _extract_metadata(text, file_type)

        # Step 5.6: For JSON/CSV, preserve de-identified structured data
        structured = None
        if file_type == "json":
            structured = _deidentify_structured(input_path, tokenization)

        # Step 6: Write .aqf file
        aqf_hash = write_aqf(
            output_path=output_path,
            tokenization=tokenization,
            source_hash=result.source_hash,
            source_type=file_type,
            metadata=metadata,
            structured_data=structured,
        )
        result.aqf_path = str(output_path)
        result.aqf_hash = aqf_hash

        # Step 7: Store tokens in vault
        batch = [
            (m.token_id, m.phi_type.value, m.phi_value,
             result.source_hash, aqf_hash, m.confidence)
            for m in tokenization.mappings
        ]
        vault.store_tokens_batch(batch)

        # Store file record
        vault.store_file_record(
            file_hash=result.source_hash,
            original_filename=input_path.name,
            source_type=file_type,
            aqf_hash=aqf_hash,
            token_count=result.token_count,
        )

        if verbose:
            logger.info(f"Created {output_path} with {result.token_count} tokens")

    except Exception as e:
        result.errors.append(str(e))
        logger.error(f"Pipeline error for {input_path}: {e}")

    return result


# CDT code pattern for metadata extraction
import re as _re
_CDT_PATTERN = _re.compile(r'\bD\d{4}\b')


def _extract_metadata(text: str, file_type: str) -> AQFMetadata:
    """Extract non-PHI metadata from text."""
    cdt_codes = list(set(_CDT_PATTERN.findall(text)))

    doc_type = "clinical_note"
    text_lower = text.lower()
    if "claim" in text_lower or "837" in text_lower:
        doc_type = "claim_form"
    elif "x-ray" in text_lower or "radiograph" in text_lower:
        doc_type = "radiograph"
    elif "treatment plan" in text_lower:
        doc_type = "treatment_plan"

    return AQFMetadata(
        document_type=doc_type,
        cdt_codes=sorted(cdt_codes),
    )


def _deidentify_structured(input_path: Path, tokenization) -> dict | None:
    """For JSON files, return a de-identified version of the structured data."""
    import json
    try:
        with open(input_path) as f:
            data = json.load(f)
        raw = json.dumps(data)
        for mapping in tokenization.mappings:
            raw = raw.replace(mapping.phi_value, mapping.token_string)
        return json.loads(raw)
    except Exception:
        return None
