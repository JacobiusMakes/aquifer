# Aquifer Architecture

**License:** Apache 2.0

## 1. Design Philosophy

**Recall over precision.** A false positive (flagging non-PHI as PHI) produces an over-anonymized document. A false negative (missing real PHI) is a HIPAA violation. The detection pipeline is tuned to maximize recall at the cost of some precision. Humans can review low-confidence flags; regulators cannot un-expose leaked data.

**Collapse the compliance surface area.** Every file processed by Aquifer produces a PHI-free `.aqf` output. The only location where PHI ever lives after processing is the encrypted vault (`.aqv`). Instead of every downstream system needing to protect PHI, you protect one vault.

**Modular pipeline.** Each stage — extraction, detection, reconciliation, tokenization, packaging, vault storage — is independently testable and replaceable. Detection stages are additive: adding a new detector requires no changes to other stages.

**Offline-first.** The core de-identification pipeline requires no network access. Extraction, detection, tokenization, `.aqf` creation, and vault writes all run entirely locally. Cloud sync via Strata is optional and never blocks local operation.

---

## 2. System Architecture

```
                          INPUT FILE
                              |
                    +---------v----------+
                    |   File Type        |
                    |   Detection        |
                    |  (extension map)   |
                    +---------+----------+
                              |
               +--------------v--------------+
               |         EXTRACTORS          |
               |  PDF (PyMuPDF)              |
               |  DOCX (python-docx)         |
               |  Image (Tesseract OCR)      |
               |  Text / CSV / JSON / XML    |
               +--------------+--------------+
                              |
                        raw text
                              |
              +---------------v--------------+
              |          DETECTORS           |
              |                              |
              |  +-----------+  +----------+ |
              |  | patterns  |  |   ner    | |
              |  | (regex)   |  | (spaCy)  | |
              |  +-----------+  +----------+ |
              |        |              |      |
              |        +-------+------+      |
              |                |             |
              |  +-------------v----------+  |
              |  | contextual name detect |  |
              |  +------------------------+  |
              +----------------+-------------+
                               |
                       PHIMatch list
                               |
               +---------------v--------------+
               |         RECONCILER           |
               |  union all spans             |
               |  resolve overlaps by         |
               |  confidence (max recall)     |
               +---------------+--------------+
                               |
                  deduplicated PHIMatch list
                               |
               +---------------v--------------+
               |         TOKENIZER            |
               |  UUIDv4 per unique value     |
               |  same value -> same token    |
               |  format: [AQ:TYPE:UUID]      |
               +-------+---------------+------+
                       |               |
              de-identified         token
                 text             mappings
                       |               |
          +------------v---+   +-------v-----------+
          |   .aqf WRITER  |   |    VAULT STORE    |
          |                |   |                   |
          | manifest.json  |   | SQLite + Fernet   |
          | metadata.json  |   | AES-128-CBC +     |
          | content/       |   | HMAC-SHA256       |
          |   text.zst     |   | PBKDF2 600K itr   |
          | tokens.json    |   |                   |
          | integrity.json |   | token_id ->       |
          |                |   | encrypted PHI     |
          +-------+--------+   +-------+-----------+
                  |                    |
               .aqf file           .aqv file
            (PHI-free,          (PHI encrypted,
           any storage)          protect this)
                                      |
                           (optional) |
                       +--------------v-----------+
                       |      STRATA SYNC         |
                       |  re-encrypt per vault    |
                       |  last-write-wins         |
                       |  manifest diffing        |
                       +--------------------------+
```

---

## 3. Module Structure

```
aquifer/
├── cli.py                  # Click CLI entry point
├── config.py               # aquifer.toml support
├── licensing.py            # License key validation
│
├── engine/                 # De-identification pipeline
│   ├── pipeline.py         # process_file() orchestrator
│   ├── reconciler.py       # Merge and deduplicate detections
│   ├── tokenizer.py        # Replace PHI spans with [AQ:TYPE:UUID]
│   ├── extractors/
│   │   ├── pdf.py          # PyMuPDF text extraction
│   │   ├── docx.py         # python-docx extraction
│   │   ├── image.py        # Tesseract OCR
│   │   └── text.py         # Plain text, CSV, JSON, XML
│   └── detectors/
│       ├── patterns.py     # Regex detectors for 18 Safe Harbor types
│       ├── ner.py          # spaCy NER + contextual name detection
│       └── ocr.py          # OCR-specific detection helpers
│
├── format/                 # .aqf container format
│   ├── writer.py           # Create .aqf (ZIP + zstd + integrity hashes)
│   ├── reader.py           # Read and verify .aqf files
│   └── schema.py           # Pydantic models: AQFManifest, AQFMetadata, etc.
│
├── vault/                  # Encrypted token-to-PHI storage
│   ├── store.py            # TokenVault CRUD (SQLite-backed)
│   ├── encryption.py       # derive_key, encrypt_value, decrypt_value
│   ├── models.py           # SQLite schema, WAL config, migrations
│   └── sync_client.py      # Push/pull tokens to Strata server
│
├── strata/                 # Hosted API server (enterprise)
│   ├── server.py           # FastAPI app
│   ├── auth.py             # JWT (HS256) + API key (aq_...) auth
│   ├── config.py           # StrataConfig (environment-based)
│   ├── database.py         # Server-side SQLite: users, practices, API keys
│   ├── cloud_vault.py      # Per-practice vault management
│   ├── sync.py             # SyncManager: diff, receive, export
│   └── routes/
│       ├── auth_routes.py
│       ├── deid_routes.py
│       ├── files_routes.py
│       ├── vault_routes.py
│       ├── practice_routes.py
│       └── dashboard_routes.py
│
├── dashboard/              # Web QC UI (FastAPI + Jinja2)
└── rehydrate/
    └── engine.py           # Restore original content from .aqf + vault
```

---

## 4. Detection Pipeline Detail

The pipeline runs three detection stages in sequence, then reconciles their output.

### Stage 1: Regex patterns (`engine/detectors/patterns.py`)

Fifteen detector classes cover all 18 HIPAA Safe Harbor identifier categories:

| Detector | PHIType | Notes |
|---|---|---|
| `SSNDetector` | SSN | Requires separator or "SSN:" context to avoid false positives on 9-digit account numbers |
| `PhoneDetector` | PHONE / FAX | Skips NPI-labeled 10-digit numbers |
| `EmailDetector` | EMAIL | Standard RFC-5321 local-part pattern |
| `URLDetector` | URL | http/https and www. prefixes |
| `IPDetector` | IP | IPv4 (validated octets) and IPv6 |
| `DateDetector` | DATE | Six date formats including ISO 8601, MM/DD/YYYY, "Month DD, YYYY", two-digit year; also appointment times in context |
| `AgeDetector` | AGE | Ages > 89 only, per 45 CFR 164.514(b)(2)(i)(C) |
| `MRNDetector` | MRN | MR-YYYY-ID pattern, label-prefixed IDs, chart numbers |
| `NPIDetector` | NPI | 10-digit NPI numbers with label context |
| `AccountDetector` | ACCOUNT | Member IDs, policy numbers, claim IDs, authorization numbers |
| `AddressDetector` | ADDRESS | Street addresses with suffix words, PO Box, "Address:" labels |
| `ZIPCodeDetector` | ADDRESS | ZIP+4; restricted 3-digit prefixes (< 20K population) flagged at higher confidence |
| `LicenseDetector` | LICENSE | Driver's licenses, DEA numbers |
| `VehicleDetector` | VEHICLE | VINs (17-char, no I/O/Q) |
| `DeviceDetector` | DEVICE | Serial numbers, UDIs |

Each detector returns `PHIMatch` objects: `(start, end, phi_type, text, confidence, source="regex")`.

### Stage 2: NER (`engine/detectors/ner.py`)

Loads spaCy `en_core_web_lg` (falls back to `md`, then `sm` if unavailable). Maps entity labels to PHI types:

- `PERSON` → `NAME` (confidence 0.85)
- `GPE`, `LOC`, `FAC` → `ADDRESS` (confidence 0.70)
- `DATE` → `DATE` (confidence 0.80)
- `ORG` → `OTHER` (confidence 0.60)

Long documents are chunked at `nlp.max_length` to stay within spaCy's limits.

### Stage 2.5: Contextual name detection (`detect_names_contextual`)

Pattern-based, context-aware name extraction using labeled fields: `PATIENT:`, `Dr.`, `entered by:`, `Provider:`, and clinical narrative patterns (`Patient X reports...`). Confidence ranges from 0.85 to 0.95. A blocklist of common clinical terms prevents false positives on phrases like "Patient General Dentistry."

### Reconciliation (`engine/reconciler.py`)

Takes the union of all matches from all stages. For overlapping spans, keeps the higher-confidence match while extending the span to cover both. This is intentional: the reconciler prefers broader coverage. Matches below a configurable threshold (default 0.70) are returned in a separate `low_confidence` list for human review.

---

## 5. Tokenization

`engine/tokenizer.py` processes the reconciled match list:

1. Sort matches by start position.
2. For each unique `(phi_type, text)` pair within a document, generate one UUIDv4. Repeated occurrences of the same value get the same token (consistency within document).
3. Apply replacements from end to start to preserve character offsets.
4. Output: `deidentified_text` with inline `[AQ:TYPE:UUID]` tokens, and a `mappings` list of `TokenMapping` objects.

Token format: `[AQ:NAME:a3f7b2c1-4e5f-6789-abcd-ef0123456789]`

UUIDv4 tokens satisfy 45 CFR 164.514(c): they are cryptographically random and non-derivable from the original PHI.

---

## 6. .aqf Container Format

`.aqf` files are ZIP archives (stored, not compressed at container level). Internal files:

```
manifest.json          # version, source_type, source_hash, token_count, deid_method
metadata.json          # non-PHI: document_type, CDT/CPT codes, year-only date, specialty
content/text.zst       # zstd level 3, UTF-8 de-identified text
content/structured.json.zst  # optional, for JSON source files
tokens.json            # token manifest: [{ token_id, phi_type, confidence, source }]
                       # NO phi_value ever written here
integrity.json         # SHA-256 of every other internal file
```

The `writer.py` returns the SHA-256 hash of the complete `.aqf` file, which is stored in the vault alongside each token mapping for cross-referencing.

ZIP is used because it is a universal standard inspectable with any unzip tool, and because it allows storing internally compressed members (zstd blobs) without double-compression.

---

## 7. Vault Design

### Storage

`TokenVault` (`vault/store.py`) wraps a SQLite database with three tables:

```sql
vault_meta   -- salt (base64), schema version
tokens       -- token_id PK, phi_type, phi_value_encrypted, source_file_hash,
             --   aqf_file_hash, confidence, created_at, updated_at
files        -- file_hash PK, original_filename, source_type, aqf_hash,
             --   token_count, processed_at
sync_log     -- id, direction, token_count, conflict_count, server_url,
             --   status, error_message, started_at, completed_at
```

WAL mode is enabled for concurrent reads. The vault is a single portable file (`.aqv` by convention, though the extension is not enforced).

### Encryption

`vault/encryption.py` uses Fernet (AES-128-CBC + HMAC-SHA256). Key derivation:

```
key = PBKDF2HMAC(SHA-256, length=32, salt=random_16_bytes, iterations=600_000)
```

600,000 iterations is the OWASP minimum recommendation as of this writing. The salt is stored in `vault_meta` and loaded on `open()`. Each PHI value is independently Fernet-encrypted before insert. The in-memory key is never written to disk.

### TokenVault lifecycle

```python
with TokenVault(path, password) as vault:
    # init() called if new, open() if existing
    # open() validates required tables before accepting any operations
    vault.store_tokens_batch(...)
# close() called automatically
```

---

## 8. Strata (Enterprise Cloud)

Strata is a FastAPI server that provides:

- **Multi-tenant vault storage**: one vault per practice, isolated at the data layer.
- **REST API** for de-identification, file management, and vault queries.
- **Bidirectional sync** between local CLI vaults and cloud vaults.
- **Web dashboard** for QC review of de-identified documents.

### Authentication

Two methods are supported, resolved by `auth.resolve_auth()`:

- **JWT (HS256)**: Browser/dashboard sessions. Generated at login, 24-hour expiry enforced. Tokens without an `exp` claim are rejected. Implemented without pyjwt — the HS256 sign/verify loop is ~30 lines and fully auditable.
- **API keys** (`aq_` prefix): Programmatic access. Only the SHA-256 hash of the key is stored. Keys have explicit scope lists (`deid`, `files`, `vault`, `admin`). JWT sessions implicitly have all scopes.

Passwords are stored as `PBKDF2-SHA256` with a random 16-byte salt and 600,000 iterations in the format `iterations$salt_hex$hash_hex`.

### Practice vault key management

Each practice gets a randomly generated Fernet key at registration. That key is encrypted with the server master key (itself derived via PBKDF2) and stored in the server database. The plaintext vault key exists in memory only during an active request.

### Sync protocol (`strata/sync.py`)

`SyncManager` implements a manifest-diff sync:

1. Local vault sends its token manifest (token IDs + `updated_at` timestamps, no PHI values).
2. Server computes the set difference: tokens only local, tokens only cloud, tokens on both with different timestamps.
3. Conflicts resolved by `updated_at` timestamp (last-write-wins).
4. Tokens pushed from local to cloud are decrypted with the local key and re-encrypted with the cloud vault key server-side. The plaintext PHI value is never written to disk or logged during transit.
5. Tokens pulled from cloud to local are similarly re-encrypted for the local vault before transmission.

---

## 9. Security Model

| Threat | Mitigation |
|---|---|
| PHI in output files | `.aqf` files contain zero PHI. Tokens are random UUIDs with no connection to source values. |
| Vault file compromise | Fernet encryption with PBKDF2-derived key. Brute-force cost: 600K iterations per password guess. |
| Vault corruption | `open()` validates required tables before accepting operations; raises `ValueError` with details on schema violations. |
| Token derivation | UUIDv4 satisfies 45 CFR 164.514(c) — statistically unique, non-derivable from PHI. |
| API key exposure | Only SHA-256 hashes stored server-side. Full key shown exactly once at creation. |
| JWT tampering | HMAC-SHA256 signature verified with `hmac.compare_digest` (constant-time). Expiry and presence of `exp` claim are both checked. |
| Multi-tenant data bleed | Each practice has its own vault with its own Fernet key. Server-side queries always filter by `practice_id` derived from the auth context. |
| PHI in transit (sync) | Tokens never transmitted as plaintext. Re-encryption happens in memory; the decrypted value is never written to disk or included in logs. |
| Large file OOM | `process_file` rejects files over 100 MB before extraction. Extracted text truncated at 10 MB with a logged warning. |

---

## 10. Data Flow: Complete Lifecycle

```
1. UPLOAD
   User provides: input file, vault path, vault password

2. EXTRACT
   File type detected by extension.
   Text extracted by appropriate extractor
   (PyMuPDF / python-docx / Tesseract / text parser).

3. DETECT
   Three stages run in sequence:
     a. Regex patterns (15 detectors, 18 PHI types)
     b. spaCy NER (PERSON, GPE, LOC, FAC, DATE, ORG)
     c. Contextual name detection (labeled fields + clinical narrative)

4. RECONCILE
   Union all PHIMatch objects.
   Resolve overlapping spans by confidence.
   Flag low-confidence matches (< 0.70) for human review.

5. TOKENIZE
   Map each unique (phi_type, text) -> UUIDv4.
   Replace spans in original text with [AQ:TYPE:UUID] tokens.
   Produce deidentified_text + mappings list.

6. WRITE .aqf
   Package deidentified_text (zstd compressed), token manifest
   (IDs + types, no values), metadata, and integrity hashes into
   a ZIP archive. Compute SHA-256 of the .aqf file.

7. STORE IN VAULT
   For each token mapping, encrypt phi_value with vault Fernet key.
   Batch insert (token_id, phi_type, encrypted_phi, file_hash, aqf_hash)
   into SQLite tokens table. Store file record.

8. (OPTIONAL) SYNC TO CLOUD
   Local vault sends manifest (no PHI).
   SyncManager diffs with cloud vault.
   Tokens re-encrypted per-vault during transfer.

9. (OPTIONAL) REHYDRATE
   Open .aqf file. Read tokens.json for token IDs.
   Look up each token_id in vault; decrypt phi_value.
   Substitute [AQ:TYPE:UUID] back to original text.
   Output restored document.
```

---

## 11. Key Design Decisions

**Why regex + NER (dual detection)?**
Regex catches structured PHI (SSN, phone, dates, MRN) with high precision and zero model dependency. NER catches unstructured names and locations that regex cannot reliably identify. The two approaches have complementary failure modes; running both and unioning the results produces better recall than either alone.

**Why Fernet for vault encryption?**
Fernet provides authenticated encryption in a single, well-audited primitive (AES-128-CBC + HMAC-SHA256). It prevents silent decryption of corrupted or tampered ciphertext. The alternative — raw AES without authentication — requires separate MAC computation and is easier to misuse.

**Why 600K PBKDF2 iterations?**
This is the OWASP-recommended minimum for PBKDF2-SHA256 as of 2023. It makes offline brute-force attacks on a stolen vault file expensive without meaningfully impacting the user experience (key derivation happens once per vault open).

**Why Zstandard compression?**
Better compression ratios than gzip at comparable or faster speed. Level 3 gives a good size/speed tradeoff for medical text. Zstd frames are self-delimiting, which simplifies streaming and concatenation.

**Why SQLite for vaults?**
Single-file, no server process, portable across platforms, battle-tested, and supports WAL mode for concurrent reads without write blocking. Aligns with the offline-first philosophy — there is no background process to keep running.

**Why ZIP for .aqf?**
ZIP is universally supported and inspectable with standard tools (`unzip -l file.aqf`). Interoperability matters: a future reader implementation in any language can parse an `.aqf` without an Aquifer library.

**Why custom JWT (no pyjwt)?**
Removes a dependency with a history of CVEs. The Aquifer JWT implementation supports only HS256, is ~30 lines, and is straightforward to audit. The tradeoff is that RS256 and other algorithms are not supported, which is acceptable for a server that controls both signing and verification.

**Why union (not intersection) in the reconciler?**
Intersection would require all detectors to agree before flagging PHI, which would reduce recall. Union means a single detector's positive result is sufficient. This is the correct choice given the recall-over-precision philosophy: the only cost of a false positive is an over-anonymized document.
