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
from typing import Callable, Optional

from aquifer.core import FILE_TYPE_MAP, ExtractionError, DetectionError
from aquifer.engine.detectors.patterns import PHIMatch, detect_patterns
from aquifer.engine.detectors.ner import detect_ner, detect_names_contextual
from aquifer.engine.reconciler import reconcile, flag_low_confidence
from aquifer.engine.tokenizer import tokenize, TokenizationResult
from aquifer.format.schema import AQFMetadata
from aquifer.format.writer import write_aqf
from aquifer.vault.store import TokenVault

logger = logging.getLogger(__name__)

# Maximum input file size before extraction (default 100 MB).
# Prevents OOM when large files are loaded entirely into memory.
MAX_INPUT_FILE_BYTES = 100 * 1024 * 1024

# Maximum extracted text size to process through detection (default 10 MB of text).
# A 100 MB PDF typically yields far less text, but this is a safety net.
MAX_TEXT_CHARS = 10 * 1024 * 1024


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
    return FILE_TYPE_MAP.get(path.suffix.lower(), "txt")


def _ocr_available() -> bool:
    """Return True if pytesseract is importable."""
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


# --- Extractor registry ---

_EXTRACTORS: dict[str, Callable[[Path], str]] = {}
_extractors_initialized = False


def register_extractor(file_type: str, func: Callable[[Path], str]) -> None:
    """Register an extractor function for a given file type.

    Third-party packages can call this to add support for new file types
    without modifying pipeline.py.
    """
    _EXTRACTORS[file_type] = func


def _init_extractors() -> None:
    """Lazily import and register the built-in extractors."""
    global _extractors_initialized
    if _extractors_initialized:
        return
    try:
        from aquifer.engine.extractors.pdf import extract_pdf
        register_extractor("pdf", extract_pdf)
    except ImportError:
        pass
    try:
        from aquifer.engine.extractors.docx import extract_docx
        register_extractor("docx", extract_docx)
    except ImportError:
        pass
    try:
        from aquifer.engine.extractors.image import extract_image
        register_extractor("image", extract_image)
    except ImportError:
        pass
    # text is always available — registered as fallback but also explicitly
    from aquifer.engine.extractors.text import extract_text
    register_extractor("txt", extract_text)
    register_extractor("csv", extract_text)
    register_extractor("json", extract_text)
    register_extractor("xml", extract_text)
    _extractors_initialized = True


def _extract_text(path: Path, file_type: str) -> str:
    """Extract text from a file based on its type, using the extractor registry."""
    _init_extractors()

    extractor = _EXTRACTORS.get(file_type)

    if file_type == "pdf" and extractor is not None:
        text = extractor(path)
        if not text.strip() and _ocr_available():
            try:
                from aquifer.engine.extractors.pdf import is_scanned_pdf
                if is_scanned_pdf(path):
                    logger.info(f"Scanned PDF detected, falling back to OCR: {path.name}")
                    try:
                        from aquifer.engine.extractors.pdf import extract_pdf_ocr
                        text = extract_pdf_ocr(path)
                    except (ImportError, AttributeError):
                        pass
            except ImportError:
                pass
        return text

    if file_type == "image":
        if extractor is None or not _ocr_available():
            logger.warning(
                "pytesseract not installed; cannot extract text from image %s. "
                "Install with: pip install pytesseract", path.name
            )
            return ""
        return extractor(path)

    if extractor is not None:
        return extractor(path)

    # Fallback: plain text extraction for unknown types
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
        # Step 0: Check file size before any processing
        file_size = input_path.stat().st_size
        if file_size > MAX_INPUT_FILE_BYTES:
            result.errors.append(
                f"File too large ({file_size / (1024*1024):.1f} MB). "
                f"Maximum supported: {MAX_INPUT_FILE_BYTES // (1024*1024)} MB. "
                f"Split large files before processing."
            )
            return result

        # Step 1: Detect file type and compute hash
        file_type = _detect_file_type(input_path)
        result.source_type = file_type
        result.source_hash = _compute_file_hash(input_path)

        if verbose:
            logger.info(f"Processing {input_path} (type={file_type}, size={file_size} bytes)")

        # Step 2: Extract text
        text = _extract_text(input_path, file_type)
        if not text.strip():
            result.errors.append("No text content extracted")
            return result

        # Guard against extracted text exceeding memory limits
        if len(text) > MAX_TEXT_CHARS:
            logger.warning(
                f"Extracted text is very large ({len(text)} chars). "
                f"Truncating to {MAX_TEXT_CHARS} chars for processing."
            )
            text = text[:MAX_TEXT_CHARS]

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
        if file_type in ("json", "csv"):
            structured = _deidentify_structured(input_path, tokenization, file_type)

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


def _classify_domain(text: str, file_type: str) -> str:
    """Classify which data domain a document belongs to based on content."""
    text_lower = text.lower()

    # Check for domain-specific keywords
    dental_keywords = {"tooth", "teeth", "crown", "filling", "periodontal", "perio",
                       "extraction", "implant", "denture", "orthodont", "caries",
                       "endodontic", "root canal", "x-ray", "radiograph", "bitewing"}
    insurance_keywords = {"member id", "group number", "policy", "subscriber",
                         "carrier", "coverage", "copay", "deductible", "benefits"}
    allergy_keywords = {"allergy", "allergies", "allergic", "reaction", "anaphylaxis",
                       "latex", "penicillin", "sulfa"}
    medication_keywords = {"medication", "prescription", "rx", "dosage", "mg",
                          "tablet", "capsule", "twice daily", "once daily"}
    medical_keywords = {"diagnosis", "condition", "surgery", "hospital",
                       "blood pressure", "diabetes", "hypertension"}
    intake_keywords = {"patient information", "intake form", "new patient",
                      "date of birth", "emergency contact", "marital status"}

    # Score each domain
    scores = {}
    for keyword_set, domain in [
        (dental_keywords, "dental"),
        (insurance_keywords, "insurance"),
        (allergy_keywords, "allergies"),
        (medication_keywords, "medications"),
        (medical_keywords, "medical_history"),
        (intake_keywords, "demographics"),
    ]:
        score = sum(1 for kw in keyword_set if kw in text_lower)
        if score > 0:
            scores[domain] = score

    if not scores:
        return "demographics"  # Default for unclassified documents

    return max(scores, key=scores.get)


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

    data_domain = _classify_domain(text, file_type)

    return AQFMetadata(
        document_type=doc_type,
        cdt_codes=sorted(cdt_codes),
        data_domain=data_domain,
    )


def _deidentify_structured(input_path: Path, tokenization, file_type: str) -> dict | None:
    """Return a de-identified version of structured data (JSON or CSV).

    For JSON: replaces PHI values in the serialised JSON string, then
    re-parses back to a dict so write_aqf can embed it in the .aqf output.

    For CSV: replaces PHI values cell-by-cell and returns the result as a
    dict with keys ``headers`` and ``rows`` for embedding in the .aqf output.
    """
    import csv
    import io
    import json

    if file_type == "json":
        try:
            with open(input_path) as f:
                data = json.load(f)
            raw = json.dumps(data)
            for mapping in tokenization.mappings:
                raw = raw.replace(mapping.phi_value, mapping.token_string)
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Failed to de-identify JSON structured data for %s: %s", input_path.name, exc)
            return None

    if file_type == "csv":
        try:
            content = input_path.read_text(encoding="utf-8", errors="replace")
            reader = csv.reader(io.StringIO(content))
            rows = list(reader)
            if not rows:
                return None

            # Build a lookup from phi_value → token_string for fast replacement
            phi_map: dict[str, str] = {
                m.phi_value: m.token_string for m in tokenization.mappings
            }

            def _replace_cell(cell: str) -> str:
                for phi, token in phi_map.items():
                    cell = cell.replace(phi, token)
                return cell

            deidentified = [[_replace_cell(cell) for cell in row] for row in rows]
            headers = deidentified[0] if len(deidentified) > 1 else []
            data_rows = deidentified[1:] if len(deidentified) > 1 else deidentified
            return {"headers": headers, "rows": data_rows}
        except Exception as exc:
            logger.warning("Failed to de-identify CSV structured data for %s: %s", input_path.name, exc)
            return None

    return None
