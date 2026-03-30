# Aquifer

**HIPAA De-Identification Engine + Portable Medical Data Format**

Aquifer strips Protected Health Information (PHI) from medical and dental files, replaces it with cryptographically random tokens, and packages the de-identified output into `.aqf` container files. De-identified files contain zero PHI and can be stored on any commodity storage — Google Drive, Dropbox, local NAS — without HIPAA-grade security. Only the token vault needs protection.

## Why

Every medical/dental practice pays enterprise-grade security costs to protect entire files when only a small fraction of each file's content is actually PHI. A dental X-ray with a patient name burned into one corner gets treated as PHI because of that one data point.

Aquifer collapses the compliance surface area from "every file everywhere" to "one small encrypted database."

## Try It

```bash
pip install aquifer
```

Or step by step:

```bash
# De-identify a file
aquifer deid clinical_note.txt -o output.aqf --vault vault.aqv --password mypassword

# Inspect the .aqf (no PHI shown)
aquifer inspect output.aqf

# Rehydrate (restore PHI from vault)
aquifer rehydrate output.aqf --vault vault.aqv --password mypassword

# Batch process a directory
aquifer deid ./patient_files/ -o ./deidentified/ --vault vault.aqv --password mypassword

# Launch the QC dashboard
aquifer dashboard --vault vault.aqv --password mypassword
```

## What It Detects

All 18 HIPAA Safe Harbor identifier categories (45 CFR Section 164.514(b)(2)):

| Category | Detection Method |
|----------|-----------------|
| Names | Contextual patterns + spaCy NER |
| Dates (except year) | Multi-format regex (MM/DD/YYYY, ISO, natural language, 2-digit year) |
| Ages > 89 | Age pattern matching |
| Phone / Fax | Format-aware regex with context |
| Email | Standard email regex |
| SSN | Pattern + context validation |
| Medical record numbers | Label-aware pattern matching |
| Health plan / Account IDs | Labeled identifier patterns |
| Addresses | Street + PO Box + labeled patterns |
| ZIP codes | Population threshold checking (Census Bureau) |
| IP addresses | IPv4 + IPv6 |
| URLs | HTTP/HTTPS/WWW patterns |
| License / DEA numbers | Professional license patterns |
| Vehicle / Device IDs | VIN + serial number patterns |
| NPI numbers | Labeled 10-digit detection |

Detection prioritizes **recall over precision** — a false positive (over-redaction) is safe but annoying. A false negative (missed PHI) is a HIPAA violation.

## .aqf File Format

An `.aqf` file is a standard ZIP archive anyone can read:

```
file.aqf
├── manifest.json       # Version, source type, timestamps
├── metadata.json       # Non-PHI metadata (CDT codes, doc type)
├── content/text.zst    # Zstd-compressed de-identified text
├── tokens.json         # Token IDs + types (NOT values)
└── integrity.json      # SHA-256 hashes for tamper detection
```

PHI is replaced with `[AQ:TYPE:UUID]` tokens — cryptographically random UUIDv4 with zero derivation from source data, per 45 CFR Section 164.514(c).

Full format spec: [docs/AQF_FORMAT_SPEC.md](docs/AQF_FORMAT_SPEC.md)

## Architecture

```
File In → Text Extraction → PHI Detection → Tokenization → .aqf Out + Vault
                                                              ↓
                                                    Rehydration ← Vault
```

```
aquifer/
├── engine/
│   ├── pipeline.py          # Main orchestrator
│   ├── extractors/          # PDF, DOCX, TXT, CSV, JSON, XML, image
│   ├── detectors/
│   │   ├── patterns.py      # 15 regex-based PHI detectors
│   │   ├── ner.py           # spaCy NER + contextual name detection
│   │   └── ocr.py           # Tesseract OCR pipeline
│   ├── reconciler.py        # Merge + deduplicate detections
│   └── tokenizer.py         # PHI → [AQ:TYPE:UUID] replacement
├── format/
│   ├── writer.py            # Create .aqf files
│   ├── reader.py            # Read + verify .aqf files
│   └── schema.py            # Pydantic schemas
├── vault/
│   ├── store.py             # Token CRUD with encrypted storage
│   ├── encryption.py        # Fernet + PBKDF2 (600k iterations)
│   └── models.py            # SQLite schema
├── rehydrate/engine.py      # .aqf + vault → original content
├── dashboard/               # FastAPI + Jinja2 QC web UI
├── cli.py                   # Click CLI
└── config.py                # aquifer.toml support
```

## Installation

```bash
# Core (regex detection, all file types except OCR)
pip install aquifer

# With NER detection (recommended)
pip install "aquifer[ner]"
python -m spacy download en_core_web_sm

# With OCR for scanned documents
pip install "aquifer[ocr]"  # requires tesseract: brew install tesseract

# Everything
pip install "aquifer[all]"
```

For development:

```bash
git clone https://github.com/aquifer-health/aquifer.git
cd aquifer
pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/ -v                    # 173 tests
python tests/generate_synthetic.py  # Generate synthetic test data
./demo.sh                           # Full end-to-end demo
```

## Docker

```bash
docker build -t aquifer .
docker run -v ./data:/data -p 8080:8080 aquifer
```

## Security

- PHI exists in memory only during processing — never written to disk unencrypted
- Vault uses Fernet encryption (AES-128-CBC + HMAC-SHA256) with PBKDF2 key derivation (600,000 iterations)
- Tokens are UUIDv4 — zero mathematical relationship to source PHI
- .aqf files contain zero PHI and pass integrity verification via SHA-256

See [docs/HIPAA_COMPLIANCE.md](docs/HIPAA_COMPLIANCE.md) for the full compliance analysis.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: recall over precision, test with synthetic data, never use real patient files.

## License

Apache 2.0 — See [LICENSE](LICENSE)
