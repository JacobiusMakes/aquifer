# Aquifer Development Roadmap

Aquifer is a patient-owned health identity platform. Patients fill out forms once. Their data follows them to every doctor, dentist, and specialist — securely, with their consent, forever. The platform is built on a HIPAA-compliant de-identification engine that makes this possible without any practice needing to trust any other practice with raw PHI.

Every patient who uses Aquifer becomes an indirect ambassador. When they auto-fill a form at a non-Aquifer practice, the practice sees "Powered by Aquifer" and discovers the platform. This creates a zero-cost acquisition channel where patient adoption drives practice adoption, which drives more patient adoption.

The de-identification engine is the foundation. The killer feature is what it enables: a portable patient identity that travels with the patient, not the practice. Every office that joins makes Aquifer more valuable to every other office. Patients prefer practices that accept their records. That network effect is the moat.

This document describes what has been built, what comes next, and where investment would accelerate the most impactful work.

---

## Phase A — Core Engine and API Server (Complete)

**What was built:**

Aquifer's core is a production-grade HIPAA de-identification pipeline covering all 18 Safe Harbor identifier categories defined in 45 CFR Section 164.514(b)(2). Detection combines regex patterns, spaCy named-entity recognition, and contextual heuristics. The system is designed to prioritize recall over precision: a false positive (over-redaction) is safe; a missed PHI token is a compliance violation.

**Capabilities shipped:**

- 18 Safe Harbor PHI detectors (regex + NER + contextual analysis)
- Multi-format document extraction: PDF, DOCX, TXT, CSV, JSON, XML, and scanned images via OCR
- `.aqf` portable container format with zstd compression and SHA-256 tamper detection
- Encrypted vault (`.aqv`) using Fernet (AES-128-CBC + HMAC-SHA256) with PBKDF2 key derivation at 600,000 iterations
- CLI with `deid`, `inspect`, `rehydrate`, `vault`, `server`, and `dashboard` commands
- Strata API server (FastAPI) with JWT and API key authentication, strict multi-tenant practice isolation
- Web dashboard for QC review of de-identified output
- Docker support with multi-stage production builds
- 613 automated tests with CI across Python 3.11, 3.12, and 3.13
- Apache 2.0 license

**Healthcare impact:**

Every medical and dental practice is currently required to apply enterprise-grade security controls to entire file repositories because PHI is distributed throughout them. Aquifer shifts that burden: de-identified `.aqf` files contain zero PHI under the Privacy Rule and can be stored on any commodity storage without HIPAA compliance requirements. Only the vault needs protection. For a small dental practice, this means the difference between expensive HIPAA-compliant cloud storage for thousands of patient files and a single encrypted database that is a fraction of that size.

---

## Phase B — Patient Data Portability

**Status: Complete**

This is the highest-priority phase. The de-identification engine solves a compliance problem for practices. Patient data portability solves a friction problem for patients — and in doing so, creates a network that makes Aquifer compellingly valuable to every practice that joins.

The current state of healthcare intake is broken. A patient who sees a general dentist, a periodontist, and an orthodontist fills out the same forms three times — demographics, insurance, medical history, medications, allergies. Each practice enters that data manually. Errors accumulate. Time is wasted. Nothing moves between offices unless a staff member faxes it.

Aquifer's vault architecture is already designed for secure, patient-linked record storage. Phase B adds the consent and transfer layer that lets patients authorize their data to move between practices — once, with full audit trails, and with the ability to revoke at any time.

**Shipped:**

- Patient share key (AQ-XXXX-XXXX) for instant check-in at any connected practice — BUILT
- Tap-to-pull: patient-initiated record retrieval at any connected practice with no waiting on the source practice — BUILT
- Dashboard check-in page for front desk patient onboarding — BUILT
- Form scanner and auto-fill for paper intake forms at non-Aquifer practices — BUILT
- Patient data summary and email-to-practice sharing — BUILT

**All planned Phase B work has been completed:**

- Cross-practice consent management: patient authorizes specific practices to receive their records — BUILT
- Secure inter-practice data transfer: vault-to-vault re-encryption so PHI is never transmitted in the clear and never stored outside a compliant vault — BUILT
- Selective scope sharing: patients choose which data categories travel — demographics, insurance, clinical notes, dental history — not all-or-nothing — BUILT
- Transfer audit trail: immutable log of who shared what, when, under which consent record — BUILT
- Patient consent revocation: immediate effect, logged, with downstream practices notified — BUILT

**Healthcare impact:**

The average patient visits two to three distinct practices. Each new-practice visit wastes 15 to 20 minutes on intake paperwork that duplicates information the patient has already provided elsewhere. At scale across millions of annual dental and medical visits, this represents an enormous aggregate cost in patient time, staff data-entry labor, and transcription errors that propagate into billing and clinical records.

Aquifer's portability layer eliminates this redundancy. A patient completes intake once, at their first participating practice. Every subsequent practice that joins the network receives verified, structured data with patient consent — no forms, no manual entry, no fax.

**Network effect:**

Every practice that joins Aquifer makes the network more valuable to every other practice. A patient whose dentist uses Aquifer will seek out a periodontist who also uses Aquifer, because that means no intake forms. Practices that accept Aquifer records attract patients who have already experienced the convenience. This dynamic creates a defensible competitive position that compounds with adoption: the network is the moat.

**Funding impact:**

This is the feature that transforms Aquifer from a point solution (de-identification for a single practice) into a platform (patient health data portability network). The technical foundation — vaults, encryption, multi-tenant isolation — is already built. Phase B is the consent and transfer layer on top of it.

Grant and venture funding would accelerate practice onboarding and build the network to critical mass. The value of the network is superlinear in the number of connected practices; early investment in adoption compounds.

---

## Phase C — Patient Health Data and Mobile

**Timeline: 4-8 weeks**

Phase C extends the patient identity with richer health data — both imported from existing sources and entered directly — and delivers the patient-facing experience that makes Aquifer useful outside a practice setting.

**Shipped:**

- FHIR R4 (MyChart/Epic) health data import — BUILT (parser + API endpoints)
- Apple Health import (HealthKit XML) — BUILT (parser + upload endpoint)
- Manual structured health data entry — BUILT (API endpoint)
- Health records retrieval with OTP-gated decryption — BUILT
- QR code check-in: practice generates QR, patient scans and enters share key, records flow automatically — BUILT

**Planned work:**

- Patient mobile PWA (progressive web app)

**Healthcare impact:**

Patients increasingly manage their health data across multiple apps and portals — Apple Health, MyChart, insurance portals. Aquifer's import layer unifies these sources into a single portable identity that the patient controls. A patient arriving at a new specialist with a complete, structured health history imported from their existing records reduces the intake burden to near zero without requiring any coordination between practices.

---

## Phase D — Dashboard Hardening and Operational Readiness

**Timeline: 4-6 weeks**

Phase A delivered a working system. Phase D makes it a system practices can depend on daily.

**Planned work:**

- WebSocket-based progress updates for batch de-identification jobs so users have real-time feedback during large file runs
- Email verification for new user registration, blocking unverified accounts from vault access
- Rate limiting middleware (infrastructure is in place; enforcement logic is not yet implemented)
- Audit logging: every rehydration event — who restored which file, at what time, from which IP — written to an immutable log
- Password strength validation at registration and password-change endpoints
- Improved error messages throughout the UI, replacing internal exception text with actionable user guidance

**Healthcare impact:**

Audit logging is not a convenience feature for healthcare settings — it is a prerequisite for demonstrating compliance in the event of a breach investigation or OCR audit. Practices need to show exactly who accessed PHI-adjacent data and when. Without this, even technically sound de-identification is difficult to defend operationally.

Rate limiting protects practices against credential stuffing and unauthorized bulk rehydration attempts.

**Funding impact:**

Grant funding would allow these features to be developed by a dedicated engineer rather than fit into available hours. The audit logging component in particular involves schema design decisions that affect every subsequent phase; rushing it risks costly rework. Funded time would allow proper design, thorough testing, and documentation that compliance consultants can cite.

---

## Phase E — Sync Protocol

**Timeline: 6-8 weeks**

Phase E introduces bidirectional synchronization between local vault instances and the cloud Strata server. The vault schema (v2) already includes the manifest and history tables needed for sync; Phase E implements the transfer protocol on top of them.

**Planned work:**

- Local-to-cloud vault synchronization over the Strata API
- Delta compression for sync transfers — only changed tokens are transmitted, not full vault exports
- Conflict resolution with a last-write-wins default and a manual override interface for administrators
- Offline-first operation: the local vault remains fully functional with no network connection; sync occurs automatically on reconnect
- Sync manifest and history tracking integrated into the existing vault schema v2 structure

**Healthcare impact:**

Dental and medical practices frequently operate in environments with unreliable internet connectivity — in-office outages, between-building fiber cuts, temporary clinics, or rural settings. An offline-first architecture means PHI de-identification never stops working because the network is down. When connectivity is restored, the system reconciles state without intervention.

For practices with multiple locations sharing a patient population, vault sync enables consistent token resolution across sites without centralizing PHI storage. Each location retains its own encrypted vault; the protocol synchronizes only the token mappings, never raw PHI.

**Funding impact:**

The sync protocol requires careful design to ensure that conflict resolution never silently discards token mappings — a lost mapping means a permanently unrehydratable file. Funded development time would allow a formal protocol specification, adversarial testing of edge cases (simultaneous writes, partial syncs, interrupted transfers), and a staged rollout with pilot practices before general availability.

---

## Phase F — Scale and Integration

**Timeline: 8-12 weeks**

Phase F moves Aquifer from a tool that works for individual practices to infrastructure that integrates into existing healthcare workflows and scales to multi-site organizations.

**Planned work:**

- PostgreSQL migration for the Strata server database, replacing SQLite for production deployments with concurrent write workloads
- Token deduplication across files: when the same PHI value appears in multiple documents, a single token handles all occurrences, reducing vault size and enabling consistent anonymization across a patient record corpus
- Cross-practice analytics: aggregated, de-identified statistics across consenting practices (procedure volumes, seasonal patterns, demographic distributions) with zero PHI exposure
- FHIR bridge for EHR integration, allowing Aquifer to consume and produce FHIR R4 resources with PHI stripped to the Safe Harbor standard
- Async task queue for large batch processing, decoupling file upload from processing and enabling reliable handling of multi-thousand-file jobs
- Multi-region deployment support with data residency controls

**Healthcare impact:**

The FHIR bridge is the highest-impact item in this phase. Most practice management systems speak FHIR or can export to it. A standards-compliant bridge means Aquifer can be inserted into existing workflows rather than requiring practices to change how they handle records. De-identified FHIR resources can feed research datasets, quality improvement programs, and insurance analytics without building a separate extraction pipeline.

Cross-practice analytics address a structural problem in small practice healthcare: individual offices lack the patient volume to draw statistically significant conclusions from their own data. Aggregated de-identified analytics across multiple participating practices can surface trends — treatment outcomes, referral patterns, scheduling efficiency — that no single office could detect alone.

**Funding impact:**

The PostgreSQL migration and async task queue are preconditions for any serious enterprise or health system deployment. Without them, Aquifer is limited to practices with low concurrent usage. Funding would enable load testing at realistic scale, proper connection pooling design, and deployment tooling (Helm charts, Terraform modules) that lowers the barrier for IT departments to adopt the system.

The FHIR bridge requires deep familiarity with FHIR R4 and the specific profiles used by major EHR vendors. Funded time for this work would include engagement with dental practice management vendors to validate the integration against real-world data shapes.

---

## Phase G — Future Vision

These items are on a longer horizon, contingent on the foundation established in Phases B through F.

**Integration with major practice management systems:** Pre-built connectors for Dentrix, Eaglesoft, Open Dental, and other widely used dental PMS platforms, allowing Aquifer to read and write patient records natively without requiring practices to change their workflows.

**Cross-specialty referral networks:** When a general dentist refers a patient to an oral surgeon or orthodontist, the relevant clinical history travels with the referral — automatically, with patient consent, through the Aquifer network. Referring practices can confirm that the specialist received complete and accurate records.

**Patient health passport:** A patient-controlled, encrypted summary of their dental and medical history that they own and can present to any provider, regardless of whether that provider is on the Aquifer network. The passport is generated from the patient's vault records and can be exported as a signed, tamper-evident document.

**Real-time streaming de-identification:** A low-latency processing mode for systems that generate a continuous stream of clinical documentation — dictation transcription, real-time EHR entry, imaging annotations. Rather than processing complete files, the engine would de-identify text as it is produced.

**Browser extension for webmail PHI detection:** A client-side tool that detects PHI in the browser before it is sent — catching the common case of staff emailing patient information through consumer webmail without recognizing it as a HIPAA issue. Detection would run locally with no data leaving the machine.

---

## Summary

| Phase | Status | Timeline | Primary Deliverable |
|-------|--------|----------|---------------------|
| A | Complete | -- | Core engine, API server, CLI, Docker |
| B | Complete | -- | Patient share key, tap-to-pull, form scanner, consent, transfer, check-in |
| C | In progress | 4-8 weeks | Apple Health import, FHIR import, QR check-in (done); patient mobile PWA (planned) |
| D | Complete | -- | Audit logging, rate limiting, email verification, async batch jobs, WebSocket progress |
| E | Complete | -- | Local-cloud vault sync, manifest diff, bidirectional sync, auto-sync, offline-first |
| F | Planned | 8-12 weeks | PostgreSQL, FHIR bridge, cross-practice analytics |
| G | Future | -- | PMS integrations, referral networks, health passport |

Aquifer's Apache 2.0 license and open architecture are deliberate choices. Healthcare compliance tooling that is opaque or proprietary is tooling that cannot be audited, trusted, or extended by the practices that depend on it. The goal is infrastructure that small practices can rely on without needing to understand cryptography, and that compliance consultants can inspect and endorse with confidence.

The portability layer built in Phase B does not change that philosophy. Patient data moves between vaults — encrypted, consented, audited. No practice sees another practice's raw records. No intermediary holds the keys. The network is federated by design.
