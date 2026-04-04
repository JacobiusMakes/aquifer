# Changelog

All notable changes to Aquifer will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Patient share key system (AQ-XXXX-XXXX) for instant check-in at any practice
- Patient-initiated record pull (tap and go — no waiting on source practice)
- Dashboard check-in page for front desk patient onboarding
- Form scanner and auto-fill for paper intake forms
- Patient data summary and email-to-practice sharing
- Apple Health (HealthKit XML) import
- FHIR R4 (MyChart/Epic) health data import
- Manual structured health data entry
- Patient health data encrypted storage
- Watchfolder daemon for automatic file processing
- `aquifer watch` command for background de-identification
- `aquifer batch` command with --resume and --workers for backlog processing
- `aquifer vault rekey` command for password rotation
- `aquifer health` command for deployment verification
- HIPAA audit trail with admin-accessible audit log endpoint
- Request correlation IDs on all API responses (X-Request-ID header)
- Rate limiting middleware (sliding window, per-client enforcement)
- Vault re-keying support (`vault.rekey(new_password)`)
- Master key rotation with transparent vault re-encryption
- Email notification infrastructure (SMTP configuration)
- Streaming rehydration endpoint for large files
- CSV structured de-identification in .aqf output
- OCR fallback for scanned PDFs and images
- Extractor plugin registry for third-party file type support
- Custom exception hierarchy (AquiferError, VaultError, ExtractionError, DetectionError, FormatError)
- Unified core constants module (SUPPORTED_EXTENSIONS, FILE_TYPE_MAP)
- Comprehensive test suite expansion (382 -> 413 tests)
- ARCHITECTURE.md, DEPLOYMENT.md, ROADMAP.md documentation
- CI pipeline: ruff linting, bandit security scanning, pytest-cov coverage

### Changed
- Migrated custom JWT implementation to PyJWT library
- API key hashing upgraded to HMAC-SHA256 with server-side secret
- Password validation strengthened (10+ chars, uppercase, lowercase, digit)
- Insecure development defaults now require explicit AQUIFER_ALLOW_INSECURE_DEFAULTS=1
- Error responses standardized with request_id and consistent format
- Vault open() now validates schema integrity on load
- NER model loading failures surfaced as warnings instead of silent fallback

### Fixed
- Path traversal vulnerability in file upload (filename sanitization)
- Missing Content-Length validation on uploads
- JWT expiry validation with specific error messages for expired tokens
- Vault corruption silent failure on open
- OOM risk from unbounded file extraction (100MB input limit, 10MB text cap)
- ReDoS vulnerability in PhoneDetector regex (bounded quantifiers)
- Batch token storage partial failure (atomic rollback)
- Error messages leaking internal details to API clients
- Dashboard global vault state not thread-safe
- Missing database indexes on foreign key columns

### Security
- Completed full security audit: 39 issues identified, all resolved
- 6 CRITICAL, 8 HIGH, 15 MEDIUM, 10 LOW findings remediated

## [0.1.0] - 2026-03-30

### Added
- Core de-identification engine with 18 HIPAA Safe Harbor detectors
- Multi-format text extraction (PDF, DOCX, TXT, CSV, JSON, XML, images)
- .aqf portable container format with Zstandard compression
- Encrypted token vault (.aqv) with Fernet + PBKDF2 (600K iterations)
- CLI: deid, inspect, rehydrate, vault, server, dashboard commands
- Strata API server (FastAPI, JWT/API key auth, multi-tenant)
- Web dashboard for quality control review
- Bidirectional vault sync protocol
- Docker support (multi-stage build, non-root user, health checks)
- 382 automated tests across all modules
- Apache 2.0 license
