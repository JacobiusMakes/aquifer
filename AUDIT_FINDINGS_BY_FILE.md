# Aquifer Audit: Detailed Findings by File

## Core Engine

### `/aquifer/engine/pipeline.py` — CRITICAL
**Risk Level:** HIGH | **Lines:** 252

**Issues:**
1. **Line 62-75: Dynamic import of extractors with no fallback**
   - If extractor module missing, ImportError kills entire pipeline
   - No validation that extracted text is non-empty before processing
   ```python
   if not text.strip():
       result.errors.append("No text content extracted")
   ```
   Better: Validate in extractor itself

2. **Line 210-212: Broad exception catch**
   ```python
   except Exception as e:
       result.errors.append(str(e))
   ```
   Should distinguish:
   - Extraction failures (recoverable)
   - Tokenization failures (data issue)
   - Vault errors (fatal)

3. **Line 124: Full text extraction to memory**
   - No streaming for large files
   - 100MB PDF extracted fully to RAM
   - Impact: OOM with concurrent requests

4. **Line 241-252: `_deidentify_structured()` too permissive**
   ```python
   except Exception:
       return None
   ```
   Silently discards structured data on any error

**Fixes:**
```python
# Create extraction_errors = [] at top
try:
    text = _extract_text(input_path, file_type)
except FileNotFoundError as e:
    result.errors.append(f"File not found: {e}")
except PermissionError as e:
    result.errors.append(f"Permission denied: {e}")
except IOError as e:
    result.errors.append(f"Extraction failed: {e}")
    return result
```

---

### `/aquifer/engine/detectors/patterns.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 549

**Issues:**
1. **Line 88-94: `PhoneDetector._PATTERN` ReDoS vulnerability**
   ```python
   _PATTERN = re.compile(
       r'(?:(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4})'
   )
   ```
   Nested quantifiers `[-.\s]?` could hang on malformed input
   Example: `"123" * 1000 + " x 999"` could timeout

2. **Line 100+: No regex timeout**
   - `Pattern.finditer()` can hang indefinitely
   - Should use `regex` library with timeout:
   ```python
   import regex
   pattern = regex.compile(..., timeout=1)  # 1 second max
   ```

3. **Line 300+: `MRNDetector` allows 20+ character IDs**
   - Could match randomly in text
   - Should require MRN context prefix

**Fixes:**
- Replace `re` with `regex` library
- Add timeout to all pattern matching
- Increase MRN specificity

---

### `/aquifer/engine/detectors/ner.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 171

**Issues:**
1. **Line 52-75: Silent model loading failures**
   ```python
   try:
       nlp = spacy.load("en_core_web_sm")
   except OSError:
       logger.warning(f"NER model not found. Proceeding with regex only.")
       return []
   ```
   User gets no warning that NER is disabled
   - Should emit to request context (e.g., `result.low_confidence`)

2. **Line 70: No timeout on model loading**
   - First call to `spacy.load()` could hang downloading model
   - No progress indication

3. **Line 107: Dead code**
   ```python
   except ImportError:
       pass  # spacy not installed
   ```
   If spacy not installed but use_ner=True, silently returns empty list

**Fixes:**
```python
def detect_ner(text: str, use_sci: bool = True) -> list[PHIMatch]:
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError as e:
        raise PHIDetectionError(f"NER model unavailable: {e}") from e
    except ImportError as e:
        raise PHIDetectionError("spaCy not installed") from e
```

---

## Vault & Encryption

### `/aquifer/vault/store.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 344

**Issues:**
1. **Line 44-48: No vault corruption detection**
   ```python
   def open(self) -> None:
       self._conn = get_connection(self.db_path)
       salt = get_salt(self._conn)
       self._key, _ = derive_key(self._password, salt)
   ```
   If `.aqv` is corrupted SQLite file, `get_salt()` fails with cryptic error
   Should validate DB schema first

2. **Line 91-108: No batch transaction rollback**
   ```python
   def store_tokens_batch(self, tokens: list[...]) -> None:
       # ...
       self._conn.executemany(...)
       self._conn.commit()
   ```
   If commit fails mid-batch, some tokens may be stored
   Should use `try/except` with rollback:
   ```python
   try:
       self._conn.executemany(...)
       self._conn.commit()
   except sqlite3.IntegrityError:
       self._conn.rollback()
       raise
   ```

3. **Line 226-245: SQL injection risk in `export_tokens_encrypted()`**
   ```python
   placeholders = ",".join("?" for _ in token_ids)
   rows = self._conn.execute(
       f"SELECT * FROM tokens WHERE token_id IN ({placeholders})",
       token_ids,
   )
   ```
   Actually safe (placeholders are `?`), but brittle pattern
   Use `WHERE token_id IN (?, ?, ...)` with proper binding instead

4. **Line 262-281: `updated_at` timestamp handling**
   ```python
   if updated_at:
       # Use provided timestamp
   else:
       # Use CURRENT_TIMESTAMP
   ```
   Two different code paths for same operation
   Should always use CURRENT_TIMESTAMP, ignore passed value

5. **Line 307: No limit on sync history**
   ```python
   rows = self._conn.execute(
       "SELECT * FROM sync_log ORDER BY started_at DESC LIMIT ?", (limit,)
   ).fetchall()
   ```
   Called with `limit=20` by default, but no max enforcement
   If user passes `limit=1000000`, returns all

**Fixes:**
```python
def open(self) -> None:
    if not self.db_path.exists():
        raise FileNotFoundError(f"Vault not found: {self.db_path}")
    try:
        self._conn = get_connection(self.db_path)
    except sqlite3.DatabaseError as e:
        raise VaultCorruptedError(f"Vault database corrupted: {e}") from e
    salt = get_salt(self._conn)
    self._key, _ = derive_key(self._password, salt)

def get_sync_history(self, limit: int = 20) -> list[dict]:
    limit = min(limit, 100)  # Enforce max
    # ...
```

---

### `/aquifer/vault/encryption.py` — GOOD
**Risk Level:** LOW | **Lines:** 54

**Assessment:** Solid implementation using Fernet (AES-128-CBC + HMAC)
- Proper key derivation (PBKDF2)
- No custom crypto

**Minor improvements:**
1. Line 31: Hardcoded iterations=100000 is good, but expose as parameter
2. Add docstring mentioning OWASP PBKDF2 recommendations

---

## Strata API Server

### `/aquifer/strata/server.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 187

**Issues:**
1. **Line 80-85: CORS allows all origins in debug mode**
   ```python
   allow_origins=["*"] if config.debug else [],
   ```
   Correct (empty list = reject all), but confusing
   Better: `allow_origins=["localhost"]` for debug

2. **Line 88-115: Auth middleware skips dashboard**
   ```python
   if request.url.path.startswith(DASHBOARD_PATHS_PREFIX):
       return await call_next(request)
   ```
   Assumes dashboard_routes handles auth
   Risk: If dashboard route forgets check, leak
   Better: Centralize dashboard auth middleware

3. **Line 119-130: No JWT expiry check**
   ```python
   auth = resolve_auth(auth_header, db, jwt_secret)
   if auth is None:
       raise HTTPException(401, "Unauthorized")
   ```
   resolve_auth might return expired token
   Missing: `if auth.is_expired(): raise HTTPException(401, "Token expired")`

4. **Line 150+: No request ID / correlation ID**
   - Logs don't have request ID
   - Hard to trace single request through logs
   - Should add middleware: `request.state.request_id = uuid.uuid4()`

**Fixes:**
```python
@app.middleware("http")
async def correlate_request(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    request.state.start_time = time.time()
    response = await call_next(request)
    duration = time.time() - request.state.start_time
    logger.info(f"request_id={request.state.request_id} "
                f"method={request.method} path={request.url.path} "
                f"status={response.status_code} duration={duration:.3f}s")
    return response
```

---

### `/aquifer/strata/routes/deid_routes.py` — CRITICAL
**Risk Level:** CRITICAL | **Lines:** 180

**Issues:**
1. **Line 51-52: Hardcoded file extensions**
   ```python
   SUPPORTED_EXTENSIONS = {
       ".pdf", ".docx", ".doc", ".txt", ".csv", ".json", ".xml",
       ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp",
   }
   ```
   Duplicated in pipeline.py
   Should be shared constant

2. **Line 62-65: No file type validation**
   ```python
   suffix = Path(file.filename or "unknown.txt").suffix.lower()
   if suffix not in SUPPORTED_EXTENSIONS:
       raise HTTPException(400, ...)
   ```
   But `file.filename` could be `../../../etc/passwd.pdf`
   Should use: `suffix = Path(file.filename).name.suffix`

3. **Line 73-87: Streaming upload checks file size**
   - But checks `file_size > config.max_upload_bytes` AFTER uploading
   - Could waste bandwidth
   Better: Check `Content-Length` header first

4. **Line 100-120: Process file without vault lock check**
   - If vault locked by concurrent operation, will wait indefinitely
   - No timeout
   - Should add timeout + retry:
   ```python
   try:
       vault = vault_mgr.open_vault(..., timeout=30)
   except TimeoutError:
       raise HTTPException(503, "Vault busy, try again later")
   ```

5. **Line 135-139: Error handling catches HTTPException**
   ```python
   except HTTPException:
       raise
   except Exception as e:
       db.update_file_record(file_id, status="failed", error_message=str(e))
       raise HTTPException(500, f"Processing error: {e}")
   ```
   Error message leaks internal details (e.g., "token_id not unique")
   Should sanitize: `error_message=f"Processing error (ID: {request.state.request_id})"`

6. **Line 165: Batch processing serial, not parallel**
   - Each file processed sequentially
   - Better: Use asyncio.gather with semaphore:
   ```python
   semaphore = asyncio.Semaphore(4)  # Max 4 concurrent
   async def process_with_semaphore(file):
       async with semaphore:
           return await deid_file(request, file)
   results = await asyncio.gather(
       *[process_with_semaphore(f) for f in files]
   )
   ```

**Fixes:**
```python
# At top of file
import asyncio
from pathlib import PurePath

MAX_BATCH_CONCURRENT = 4

# In deid_file
try:
    # Check Content-Length before streaming
    content_length = int(request.headers.get("content-length", 0))
    if content_length > config.max_upload_bytes:
        raise HTTPException(413, f"File too large (max: {config.max_upload_bytes})")

    # Stream with timeout
    file_size = 0
    async for chunk in file.file:
        file_size += len(chunk)
        if file_size > config.max_upload_bytes:
            raise HTTPException(413, ...)
        tmp_file.write(chunk)
except TimeoutError:
    raise HTTPException(504, "Upload timeout")
```

---

### `/aquifer/strata/routes/vault_routes.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 243

**Issues:**
1. **Line 91-100: No practice validation**
   ```python
   def _get_sync_manager(request: Request) -> SyncManager:
       auth: AuthContext = request.state.auth
       practice = db.get_practice(auth.practice_id)
       if not practice:
   ```
   If practice deleted between auth and this call, raises KeyError
   Should return 404: `raise HTTPException(404, "Practice not found")`

2. **Line 103-115: Sync manifest response too large**
   ```python
   def sync_manifest(body: SyncManifestRequest, request: Request):
       manifest = sm.get_cloud_manifest()  # All tokens
   ```
   For practice with 100K tokens, response is huge JSON
   Should paginate: `?offset=0&limit=1000`

3. **Line 150+: Token lookup no caching**
   ```python
   def lookup_token(token_id: str, request: Request):
       vault = vault_mgr.get_vault(auth.practice_id)
       token = vault.get_token(token_id)
   ```
   Cache miss for every lookup
   Should add cache with TTL:
   ```python
   cache = request.app.state.token_cache  # {practice_id: {token_id: token}}
   cache_key = (auth.practice_id, token_id)
   if cache_key in cache and cache[cache_key]["expires_at"] > now():
       return cache[cache_key]["token"]
   ```

---

### `/aquifer/strata/config.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 84

**Issues:**
1. **Line 33: `master_key` has default**
   ```python
   master_key: str = ""
   ```
   Empty string is not secure
   Should default to `None` and require explicit set

2. **Line 68-75: Dev secrets hardcoded**
   ```python
   if cfg.debug:
       cfg.master_key = "INSECURE-DEV-MASTER-KEY-REPLACE-IN-PRODUCTION"
   else:
       raise ValueError("AQUIFER_MASTER_KEY must be set in production.")
   ```
   If someone accidentally starts with `AQUIFER_DEBUG=1` in production, uses insecure key
   Better: Require explicit `AQUIFER_ALLOW_INSECURE_DEFAULTS=1` (as in server.py:30)

3. **Line 30-31: Rate limits defined but not used**
   ```python
   rate_limit_deid: int = 60
   rate_limit_default: int = 120
   ```
   No middleware uses these
   Should validate in startup: log warning if defined but not implemented

**Fixes:**
```python
master_key: str = None  # Required in production

@classmethod
def from_env(cls) -> StrataConfig:
    # ...
    if not cfg.master_key:
        if cfg.debug:
            import os
            if not os.getenv("AQUIFER_ALLOW_INSECURE_DEFAULTS"):
                raise ValueError(
                    "AQUIFER_MASTER_KEY required or set "
                    "AQUIFER_ALLOW_INSECURE_DEFAULTS=1 for dev"
                )
            cfg.master_key = "..."
        else:
            raise ValueError(...)
```

---

### `/aquifer/strata/auth.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 246

**Issues:**
1. **Line 27-45: Custom JWT implementation**
   ```python
   def create_jwt(payload: dict, secret: str, algorithm: str = "HS256", expiry_hours: int = 24) -> str:
       # Custom HMAC + base64
   ```
   Not using standard library (PyJWT)
   Risk: Could have subtle bugs (timing attacks, signature verification)
   Should use: `pip install PyJWT`

2. **Line 23-25: `validate_password()` doesn't check strength**
   ```python
   def validate_password(cls, v: str) -> str:
       if len(v) < 8:
           raise ValueError("Password must be at least 8 characters")
       return v
   ```
   No checks for:
   - Uppercase/lowercase mix
   - Numbers
   - Special characters
   - Common passwords (password123, qwerty)
   Should use `zxcvbn` library

3. **Line 156-170: No rate limiting on token creation**
   - User can request unlimited API keys
   - Should limit: max 10 keys per user, max 1 key per minute

4. **Line 204-245: API key hashing**
   ```python
   def hash_api_key(key: str) -> s:
       return hashlib.sha256(key.encode()).hexdigest()
   ```
   Uses SHA-256 without salt
   Risk: Rainbow table attack
   Should use: `hashlib.pbkdf2_hmac('sha256', key.encode(), salt, iterations=100000)`

**Fixes:**
```python
# Use PyJWT
import jwt as pyjwt

def create_jwt(payload: dict, secret: str, algorithm: str = "HS256", expiry_hours: int = 24) -> str:
    payload["exp"] = datetime.utcnow() + timedelta(hours=expiry_hours)
    return pyjwt.encode(payload, secret, algorithm=algorithm)

def decode_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict | None:
    try:
        return pyjwt.decode(token, secret, algorithms=[algorithm])
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidSignatureError:
        return None

# Password validation
def validate_password(cls, v: str) -> str:
    if len(v) < 12:
        raise ValueError("Minimum 12 characters required")
    import zxcvbn
    result = zxcvbn.zxcvbn(v)
    if result["score"] < 3:  # Fair or worse
        raise ValueError(f"Password too weak: {result['feedback']['warning']}")
    return v
```

---

### `/aquifer/strata/database.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 303

**Issues:**
1. **Line 107: `list_files()` returns ALL files**
   ```python
   def list_files(self, practice_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
       rows = self._conn.execute(
           "SELECT * FROM files WHERE practice_id = ? LIMIT ? OFFSET ?",
           (practice_id, limit, offset),
       ).fetchall()
   ```
   Has pagination, but default limit=100 could be large
   Should also validate `limit <= 1000`

2. **Line 100+: Missing indexes**
   - Tables have `practice_id`, `user_id`, `api_key_id` foreign keys
   - But no indexes created on these (only primary keys)
   - N+1 query issue when listing files by practice
   - Should add: `CREATE INDEX idx_files_practice ON files(practice_id)`

3. **Line 130-145: No concurrent insert handling**
   ```python
   def create_api_key(self, ...):
       key_id = str(uuid.uuid4())
       self._conn.execute(
           "INSERT INTO api_keys ...",
           (key_id, ...),
       )
   ```
   If two requests create key simultaneously, both use same UUID
   SQLite allows this (no serial primary key)
   Should use database-generated IDs or explicit lock

4. **Line 160+: No soft delete**
   - Files deleted with `DELETE` not soft delete
   - Audit trail lost
   - Should add: `deleted_at` timestamp column instead of DELETE

---

## Dashboard & Web

### `/aquifer/dashboard/app.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 204

**Issues:**
1. **Line 24-30: Global vault variable**
   ```python
   _vault = None
   def configure(vault_path: Path, password: str, ...):
       global _vault
       _vault = TokenVault(vault_path, password)
       _vault.open()
   ```
   Not thread-safe! FastAPI is async
   If two requests call `_get_vault()` simultaneously:
   - Concurrent reads OK
   - But if one thread modifies vault while another reads, race condition
   Better: Use `request.app.state.vault` (request-scoped)

2. **Line 50-60: No session timeout**
   - No logout endpoint visible
   - Session persists forever
   - Should add timeout: 30 min of inactivity

3. **Line 137-155: File upload path traversal risk**
   ```python
   original_filename = file.filename
   output_path = vault_mgr.aqf_dir(auth.practice_id) / original_filename.with_suffix(".aqf")
   ```
   If `original_filename = "../../../etc/passwd"`, could write outside intended dir
   Should sanitize: `output_path = ... / Path(original_filename).name.with_suffix(".aqf")`

4. **Line 175: No error logging on upload**
   ```python
   except Exception as e:
       return JSONResponse({"error": str(e)}, status_code=500)
   ```
   Error not logged to server logs
   Should: `logger.error(f"Upload failed: {e}", exc_info=True)`

---

### `/aquifer/cli.py` — MEDIUM
**Risk Level:** MEDIUM | **Lines:** 674

**Issues:**
1. **Line 569-640: Claims commands incomplete**
   ```python
   @claims.command()
   def predict(cdt_codes: tuple[str], ...):
       if api_key:
           # Use hosted API
       else:
           # Requires local aquifer-claims (not installed)
           raise SystemExit(1)
   ```
   Feature: Gated behind missing module
   User can call `aquifer claims predict ...` but always fails
   Better: Don't register command if feature unavailable

2. **Line 75-77: No streaming for large downloads**
   ```python
   text = do_rehydrate(Path(α4), vault)
   click.echo(text)
   ```
   Large rehydrated files loaded fully to memory
   Should stream: `click.echo(text, nl=False)` in chunks

3. **Line 99-100: File extensions hardcoded again**
   ```python
   supported = {".pdf", ".docx", ...}
   ```
   Another copy of SUPPORTED_EXTENSIONS
   Create: `aquifer/core.py::SUPPORTED_TYPES`

4. **Line 390+: Sync commands don't validate API key format**
   - API key passed via env var `α1` (unvalidated)
   - If malformed, fails at server with cryptic error
   Should validate locally: `assert len(api_key) > 10`

**Minor:** Use of Greek letters `α1`, `α2`, `α3`, `α4` is clever but confusing
Better: `AQUIFER_API_KEY`, `AQUIFER_SERVER_URL`, `AQUIFER_VAULT_SYNC_CLIENT`, `AQUIFER_AQF_PATH`

---

## Tests

### `/tests/test_strata.py` — GOOD
**Status:** Most critical API paths covered

**Gaps:**
1. No tests for rate limiting (config exists but not implemented)
2. No tests for concurrent batch uploads
3. No tests for vault corruption scenarios
4. No tests for API key revocation (list_api_keys works, but revoke not tested end-to-end)

**Recommendations:**
```python
@pytest.mark.asyncio
async def test_deid_large_file(client, tmp_path):
    """Test de-identification of file near max size."""
    large_file = tmp_path / "large.txt"
    large_file.write_text("PHI: 555-1234 " * 50000)  # ~700KB
    response = client.post(
        "/api/v1/deid",
        files={"file": large_file.open("rb")},
        headers=auth_headers(jwt_token),
    )
    assert response.status_code == 201

@pytest.mark.asyncio
async def test_concurrent_batch_uploads(client, tmp_path):
    """Test 10 concurrent batch uploads."""
    import asyncio
    tasks = [
        deid_batch(client, [file1, file2])
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks)
    assert all(r.status_code == 201 for r in results)
```

---

### `/tests/test_dashboard.py` — MEDIUM
**Status:** Coverage of page loading and basic flows

**Gaps:**
1. No test for session timeout
2. No test for file deletion
3. No test for settings changes
4. No test for concurrent uploads
5. No test for very large file downloads (streaming)

---

### `/tests/test_vault.py` — GOOD
**Status:** Token storage and encryption covered

**Gaps:**
1. No test for vault corruption recovery
2. No test for concurrent read/write
3. No test for batch operation rollback on failure
4. No test for very large token counts (100K+)

---

## Summary Table

| File | Risk | Issue Count | Severity |
|------|------|-------------|----------|
| pipeline.py | HIGH | 4 | Large file memory, broad exceptions, structured data, extraction fallback |
| patterns.py | MED | 2 | ReDoS, MRN specificity |
| ner.py | MED | 3 | Silent failures, timeout, dead code |
| store.py | MED | 5 | Corruption detection, batch rollback, timestamp handling, limit enforcement |
| deid_routes.py | CRITICAL | 6 | Path traversal, no file lock timeout, error message leaks, serial batch processing |
| vault_routes.py | MED | 3 | Practice validation, manifest size, token caching |
| config.py | MED | 3 | Default master key, dev secrets in prod, unused rate limits |
| auth.py | MED | 4 | Custom JWT, weak password validation, API key rate limit, hash without salt |
| database.py | MED | 4 | Missing indexes, concurrent insert, soft delete, limit validation |
| app.py | MED | 4 | Global vault state, no session timeout, path traversal, no error logging |
| cli.py | MED | 4 | Incomplete claims feature, large file streaming, extension duplication, key validation |
| server.py | MED | 4 | CORS in debug, dashboard auth assumption, JWT expiry, no correlation ID |

**Total:** 45 issues identified across 12 critical files
