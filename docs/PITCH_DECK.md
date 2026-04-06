# Aquifer — Pitch Deck

---

## SLIDE 1: Title

**Aquifer**
Patient data portability for healthcare.

Fill out forms once. Your data follows you — forever.

*aquifer.health*

---

## SLIDE 2: The Problem

**Every patient visit starts the same way: clipboard, pen, 15 minutes of paperwork.**

- The average patient sees 2-3 distinct practices
- Each visit: same name, same DOB, same insurance, same allergies — rewritten from scratch
- Staff re-enters it manually. Errors propagate into billing and clinical records.
- Nothing moves between offices unless someone faxes it.

**Millions of visits per year. Billions of redundant keystrokes.**

The problem isn't technology — it's that no one has built the network.

---

## SLIDE 3: The Solution

**Aquifer gives patients a portable health identity.**

1. Patient visits Practice A. Aquifer de-identifies and encrypts their intake data.
2. Patient gets a share key: **AQ-XXXX-XXXX**
3. Patient visits Practice B. Scans a QR code. Records flow automatically.

No forms. No fax. No manual entry. No PHI on the wire.

Practice B gets verified, structured data — in seconds, with full audit trail and patient consent.

---

## SLIDE 4: How It Works

**Three layers, one platform:**

**Layer 1: De-Identification Engine**
- 18 Safe Harbor PHI categories (regex + NER + contextual analysis)
- PDF, DOCX, images, FHIR — every format a practice uses
- Encrypted vault stores token mappings (AES-256, PBKDF2)

**Layer 2: Patient Portability**
- Share key system for instant check-in
- Consent management with scoped sharing (dental, insurance, medications...)
- Vault-to-vault transfer — PHI is re-encrypted server-side, never plaintext

**Layer 3: Network Intelligence**
- Cross-practice analytics with k-anonymity privacy guarantees
- FHIR R4 bridge for EHR integration (Epic, Cerner, Open Dental)
- Benchmarks: how does your practice compare to the network?

---

## SLIDE 5: The Network Effect

**Every practice that joins makes Aquifer more valuable to every other practice.**

Patient at Practice A wants to see a specialist.
The specialist uses Aquifer? Zero intake friction.
The specialist doesn't? Patient fills out forms — and the specialist sees "Powered by Aquifer" at the bottom.

**Patients prefer practices that accept their records.**

That preference drives adoption. Adoption drives the network. The network is the moat.

This is not a feature. It's a flywheel.

---

## SLIDE 6: Market

**$4.7B** — US healthcare data management market (2025)

**Target: dental + medical small practices (1-10 providers)**
- 200,000+ dental practices in the US alone
- 85% are small/independent — no enterprise IT team
- Average practice spends $3,200/year on HIPAA compliance tooling

**Wedge: dental first.**
- Highest intake friction (patients see dentist + specialist + orthodontist)
- Smallest IT sophistication (most receptive to plug-and-play)
- Natural referral networks (GP dentist → periodontist → oral surgeon)

---

## SLIDE 7: What's Built

**This is not a prototype. This is a product.**

| Capability | Status |
|-----------|--------|
| De-ID engine (18 Safe Harbor categories) | Complete |
| Patient share keys + QR check-in | Complete |
| Consent management + vault-to-vault transfer | Complete |
| FHIR R4 bridge (EHR integration) | Complete |
| Cross-practice analytics (k-anonymity) | Complete |
| PostgreSQL + SQLite dual backend | Complete |
| Background job processing + WebSocket progress | Complete |
| Email verification + password management | Complete |
| Apple Health + FHIR + manual data import | Complete |
| 650+ automated tests, CI pipeline | Complete |
| Docker deployment | Complete |

**Phases A through F: shipped.** Apache 2.0 license. Production-ready.

---

## SLIDE 8: Business Model

**Community (Free forever)**
The full product. De-identification, portability, vault, API, dashboard.
Free builds the network. The network is the product.

**Professional ($299/mo per practice)**
Claims intelligence: denial prediction, appeal generation, advanced analytics.
For practices that want to optimize revenue, not just manage compliance.

**Enterprise (Custom)**
SSO/SAML, dedicated infrastructure, SLA, custom integrations, white-label.
For DSOs and health systems with 10+ locations.

**Unit economics:** $299/mo x 1,000 practices = $3.6M ARR.
Community tier drives adoption. Professional tier drives revenue.

---

## SLIDE 9: Competitive Landscape

| | Aquifer | Phreesia | PatientPop | Manual (Fax/Paper) |
|--|---------|----------|------------|-------------------|
| Patient-owned data | Yes | No | No | No |
| Cross-practice portability | Yes | No | No | No |
| HIPAA de-identification engine | Yes | No | No | N/A |
| Network effect | Yes | No | No | No |
| Free tier | Yes | No | No | Yes |
| Open source (auditable) | Yes | No | No | N/A |

**No one else is building the portability network.** Existing players digitize forms *within* a practice. Aquifer moves data *between* practices.

---

## SLIDE 10: The Ask

**Seeking: $500K (NIH SBIR Phase I or Seed)**

**What it funds:**
- Pilot deployment with 10-20 dental practices (6 months)
- Mobile PWA for patient self-service
- Practice management system integrations (Dentrix, Eaglesoft, Open Dental)
- Compliance certification (SOC 2 Type II)

**What it proves:**
- Patient adoption rate at pilot practices
- Time-to-intake reduction (target: 15 min → 30 seconds)
- Practice-to-practice transfer volume
- Network growth rate (does the flywheel spin?)

**Milestone: 50 connected practices within 12 months of pilot launch.**

---

## SLIDE 11: Why Now

1. **21st Century Cures Act** mandates patient data access and interoperability
2. **FHIR R4** is now the standard — EHRs are required to support it
3. **Patient expectations** have shifted — they expect digital, instant, mobile
4. **Small practices** are being left behind by enterprise-focused solutions
5. **The infrastructure is ready** — encrypted vaults, consent protocols, and PHI detection are solved problems. The missing piece was the network layer.

Aquifer is that layer.

---

## SLIDE 12: Contact

**Aquifer**
*Patient data portability for healthcare.*

aquifer.health
github.com/JacobiusMakes/aquifer

Apache 2.0 | HIPAA-compliant by design | 650+ tests | Production-ready

---

*Notes for presenter:*
- Demo flow: register practice → register patient → get share key → QR scan at second practice → instant check-in
- Key stat to emphasize: 15 minutes of paperwork → 30 seconds
- The free tier is strategic, not charitable — it's how the network grows
- "The network is the moat" is the one line they should remember
