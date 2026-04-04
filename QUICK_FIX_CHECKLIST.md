# Aquifer: Quick Fix Checklist (Priority Order)

## CRITICAL (Ship-Blocking)

- [ ] **deid_routes.py:62-65** — Path traversal in file upload
  - Fix: Use `Path(filename).name` to strip directories
  - Effort: 10 min
  - Security impact: HIGH

- [ ] **deid_routes.py:73-87** — Check Content-Length before streaming
  - Fix: Add early size check before accepting upload
  - Effort: 15 min
  - Security impact: MEDIUM (DoS)

- [ ] **pipeline.py:62-75** — Handle missing extractors gracefully
  - Fix: Try/except per extractor with fallback to text-only
  - Effort: 20 min
  - Reliability impact: HIGH

- [ ] **config.py:68-75** — Insecure development defaults
  - Fix: Require explicit `AQUIFER_ALLOW_INSECURE_DEFAULTS=1`
  - Effort: 10 min
  - Security impact: HIGH (accidental production use)

- [ ] **strata/server.py:119-130** — Missing JWT expiry check
  - Fix: Call `auth.is_expired()` in auth_middleware
  - Effort: 5 min
  - Security impact: HIGH (token replay)

- [ ] **vault/store.py:44-48** — Vault corruption not detected
  - Fix: Call `validate_schema()` on open
  - Effort: 15 min
  - Reliability impact: CRITICAL

---

## HIGH PRIORITY (Within 1 week)

- [ ] **strata/config.py:30-31** — Rate limiting not implemented
  - Fix: Add `slowapi` middleware to `server.py`
  - Effort: 1-2 hours
  - Impact: DDoS protection

- [ ] **auth.py:204-245** — API key hashing without salt
  - Fix: Use PBKDF2 with salt (like vault key)
  - Effort: 20 min
  - Impact: Rainbow table resistance

- [ ] **auth.py:23-25** — Weak password validation
  - Fix: Install `zxcvbn` and check score >= 3
  - Effort: 30 min
  - Impact: Account security

- [ ] **pipeline.py:124** — Full text extraction to memory
  - Fix: Add streaming extraction for large files
  - Effort: 2-3 hours
  - Impact: Memory/stability under load

- [ ] **patterns.py:88-94** — ReDoS vulnerability in PhoneDetector
  - Fix: Use `regex` library with timeout
  - Effort: 1 hour
  - Impact: Denial of service via malformed input

- [ ] **deid_routes.py:135-139** — Error message leaks details
  - Fix: Sanitize error to request ID only
  - Effort: 15 min
  - Impact: Information disclosure

- [ ] **vault/store.py:91-108** — No batch rollback on error
  - Fix: Add try/except with rollback
  - Effort: 20 min
  - Impact: Data consistency

- [ ] **database.py** — Missing indexes on foreign keys
  - Fix: Add `CREATE INDEX` for practice_id, user_id, etc.
  - Effort: 15 min
  - Impact: Query performance (N+1)

---

## MEDIUM PRIORITY (1-2 weeks)

- [ ] **Test coverage: pipeline.py** — Only 1 test for entire pipeline
  - Add: Large file test, NER failure test, conflict resolution test
  - Effort: 2-3 hours
  - Impact: Reliability confidence

- [ ] **Test coverage: error scenarios** — Missing vault corruption, concurrent access
  - Add: ~10 new test cases
  - Effort: 3-4 hours
  - Impact: Production resilience

- [ ] **dashboard/app.py:24-30** — Global vault variable not thread-safe
  - Fix: Move to `request.app.state.vault`
  - Effort: 30 min
  - Impact: Thread safety under load

- [ ] **ner.py:52-75** — Silent NER model loading failures
  - Fix: Raise PHIDetectionError instead of returning []
  - Effort: 15 min
  - Impact: Visibility into degraded mode

- [ ] **Create core constant file**
  - Move: SUPPORTED_TYPES, FILE_TYPE_MAP to `aquifer/core.py`
  - Removes duplication from pipeline.py, cli.py, deid_routes.py
  - Effort: 30 min

- [ ] **Custom JWT → PyJWT**
  - Replace: `auth.py:create_jwt()` with `PyJWT` library
  - Effort: 1 hour
  - Impact: Standard compliance, security review

- [ ] **API response standardization**
  - Create: `aquifer/strata/responses.py` with unified response wrapper
  - Wrap all responses in `{"data": {...}, "error": null}`
  - Effort: 2-3 hours
  - Impact: API consistency

- [ ] **Add request correlation ID**
  - Middleware: Add `request.state.request_id = uuid.uuid4()`
  - Log: Include in all log statements
  - Effort: 1 hour
  - Impact: Debugging/tracing

- [ ] **Vault key re-keying**
  - Add: `vault.rekey(new_password)` method
  - Allow: User to change vault password
  - Effort: 2 hours
  - Impact: User security control

---

## LOW PRIORITY (Nice-to-have, 2+ weeks)

- [ ] **OCR integration** — Code exists but not used
  - Wire: `detect_ocr()` into pipeline for image fallback
  - Effort: 1-2 hours

- [ ] **Rehydration streaming** — Code exists but not called
  - Endpoint: `/rehydrate-stream` for large files
  - Effort: 1 hour

- [ ] **Dashboard deletion** — Delete button without handler
  - Endpoint: `DELETE /dashboard/files/{file_id}`
  - Effort: 1 hour

- [ ] **Dashboard settings** — Settings page loads but no changes
  - Endpoint: `POST /dashboard/settings` for password change
  - Effort: 1 hour

- [ ] **Load testing** — No stress tests
  - Add: `tests/test_load.py` with concurrent operations
  - Effort: 2-3 hours

- [ ] **Dependency lock file** — No reproducible builds
  - Add: `requirements.lock` or `poetry.lock`
  - Effort: 30 min

---

## Quick Wins (< 15 min each)

- [ ] auth.py: Explicit `iterations=100000` in `derive_key()`
- [ ] vault/store.py: Enforce `limit <= 100` in `get_sync_history()`
- [ ] deid_routes.py: Share `SUPPORTED_EXTENSIONS` from core module
- [ ] cli.py: Validate `api_key` length before sending
- [ ] dashboard/app.py: Sanitize file names with `Path().name`
- [ ] patterns.py: Document ReDoS risk in docstring
- [ ] config.py: Log warning if rate limits defined but not used

---

## Testing Checklist

### New Test Files Needed:
- [ ] `tests/test_error_scenarios.py` — Vault corruption, NER failures, concurrent access
- [ ] `tests/test_performance.py` — Large file handling, sync with 100K+ tokens
- [ ] `tests/test_api_validation.py` — Input validation, rate limiting, auth scope denial
- [ ] `tests/test_security.py` — Path traversal, JWT expiry, API key hashing

### Existing Tests to Enhance:
- [ ] `test_pipeline.py` — Add large file, NER failure, structured data error cases
- [ ] `test_strata.py` — Add concurrent batch uploads, vault lock timeout
- [ ] `test_dashboard.py` — Add session timeout, file deletion, large download
- [ ] `test_vault.py` — Add batch rollback, concurrent read/write, 100K token scaling

---

## Deployment Readiness Checklist

Before shipping to production:

- [ ] All 6 CRITICAL items fixed
- [ ] All 8 HIGH PRIORITY items fixed
- [ ] Rate limiting middleware active
- [ ] Request correlation ID in all logs
- [ ] No hardcoded secrets in code
- [ ] HTTPS required (check uvicorn config)
- [ ] Database backups configured
- [ ] Log aggregation active (e.g., ELK, Splunk)
- [ ] Monitoring/alerting for 5xx errors
- [ ] HIPAA audit trail logged to immutable storage
- [ ] Database encryption at rest (if on shared host)
- [ ] Key management system (KMS) for master key
- [ ] Penetration testing completed
- [ ] HIPAA BAA signed with any third-party services

---

## Files to Review with Team

1. `aquifer/strata/routes/deid_routes.py` — Highest risk code
2. `aquifer/engine/pipeline.py` — Performance/reliability concerns
3. `aquifer/strata/auth.py` — Security implementation review
4. `aquifer/strata/config.py` — Production defaults
5. `aquifer/vault/store.py` — Data consistency
6. `docs/HIPAA_COMPLIANCE.md` — Verify claims are met

---

## Estimated Effort Summary

| Category | Effort | Risk |
|----------|--------|------|
| CRITICAL fixes | 2-3 hours | HIGH |
| HIGH PRIORITY | 8-12 hours | MEDIUM-HIGH |
| MEDIUM PRIORITY | 12-16 hours | MEDIUM |
| LOW PRIORITY | 8-10 hours | LOW |
| **TOTAL** | **30-40 hours** | |

**Recommendation:** Allocate 1-2 weeks (1 engineer) to address CRITICAL + HIGH + MEDIUM categories before production release.
