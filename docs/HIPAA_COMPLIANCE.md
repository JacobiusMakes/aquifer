# Aquifer HIPAA Compliance Documentation

## De-Identification Method

Aquifer implements the **Safe Harbor Method** defined in 45 CFR Section 164.514(b)(2) of the HIPAA Privacy Rule.

## The 18 Safe Harbor Identifiers

Aquifer's detection engine identifies and removes all 18 categories:

| # | Identifier | Aquifer Detection |
|---|-----------|-------------------|
| 1 | Names | Regex + NER + contextual patterns |
| 2 | Geographic subdivisions < state | Address regex + NER (GPE) |
| 3 | Dates (except year) + ages >89 | Multi-format date regex |
| 4 | Telephone numbers | Phone pattern regex |
| 5 | Fax numbers | Fax-context phone regex |
| 6 | Email addresses | Email regex |
| 7 | Social Security numbers | SSN pattern regex |
| 8 | Medical record numbers | MRN pattern regex |
| 9 | Health plan beneficiary numbers | Account/member ID regex |
| 10 | Account numbers | Account pattern regex |
| 11 | Certificate/license numbers | License pattern regex |
| 12 | Vehicle identifiers | Pattern matching |
| 13 | Device identifiers | Pattern matching |
| 14 | Web URLs | URL regex |
| 15 | IP addresses | IPv4 regex |
| 16 | Biometric identifiers | NER + contextual |
| 17 | Full-face photographs | Image PHI region detection |
| 18 | Other unique identifiers | Catch-all patterns + NER |

## Token Non-Derivation (Section 164.514(c))

Aquifer's re-identification tokens comply with Section 164.514(c):

- **Random generation**: All tokens are UUIDv4 generated via Python's `uuid.uuid4()` using cryptographically secure random sources
- **Zero derivation**: Tokens have no mathematical relationship to the source PHI. They are not hashes, encodings, or transformations of the original data
- **Non-sequential**: Token ordering reveals nothing about the original data

## Vault Security

The token-to-PHI mapping vault is the only component containing PHI-adjacent data:

- **Encryption at rest**: Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
- **Key derivation**: PBKDF2-HMAC-SHA256 with 600,000 iterations (OWASP recommendation)
- **Random salt**: 16-byte cryptographically random salt per vault
- **Access control**: Password required for any vault operation

## De-Identified Output

AQF files produced by Aquifer contain zero PHI. Per the Privacy Rule at Section 164.514(a):

> Health information that does not identify an individual and with respect to which there is no reasonable basis to believe that the information can be used to identify an individual is not individually identifiable health information.

AQF files meet this standard and therefore fall outside the Privacy Rule's protections. They can be stored on any medium without HIPAA-grade security requirements.

## Detection Philosophy

Aquifer prioritizes **recall over precision**. A false positive (over-redaction) is safe but inconvenient. A false negative (missed PHI) is a compliance violation. The system defaults to aggressive detection with a human-in-the-loop QC dashboard for edge cases.
