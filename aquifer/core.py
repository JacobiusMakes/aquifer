"""Core constants and exceptions shared across Aquifer modules."""

from __future__ import annotations

from enum import Enum

# Supported file extensions for de-identification.
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".csv", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp",
}

# Maps file extension to internal file type identifier.
FILE_TYPE_MAP = {
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


# ---------------------------------------------------------------------------
# Data domain classification
# ---------------------------------------------------------------------------

class DataDomain(str, Enum):
    """Categories of patient health data for scoped sharing.

    When patients consent to share records between practices,
    they (or the practice) select which domains to transfer.
    Different practice types need different data slices.
    """
    # Universal — almost always needed
    DEMOGRAPHICS = "demographics"      # Name, DOB, address, phone, email, emergency contact
    INSURANCE = "insurance"            # Carrier, member ID, group #, policy holder
    MEDICATIONS = "medications"        # Current medications (needed for drug interactions)
    ALLERGIES = "allergies"            # Drug allergies, material allergies (latex, etc.)

    # Specialty-specific
    MEDICAL_HISTORY = "medical_history"  # Conditions, surgeries, hospitalizations
    DENTAL = "dental"                    # Treatment history, perio status, x-rays, CDT codes
    VISION = "vision"                    # Rx, conditions, procedures
    BEHAVIORAL = "behavioral"            # Mental health — extra protected under 42 CFR Part 2
    SURGICAL = "surgical"               # Surgical history, anesthesia records

    # Administrative
    CONSENT_FORMS = "consent_forms"     # Signed consent/HIPAA acknowledgment forms
    REFERRALS = "referrals"            # Referral letters, specialist notes


# Which domains each practice type typically needs
PRACTICE_TYPE_DEFAULTS: dict[str, set[str]] = {
    "dental": {
        DataDomain.DEMOGRAPHICS, DataDomain.INSURANCE,
        DataDomain.MEDICATIONS, DataDomain.ALLERGIES,
        DataDomain.DENTAL, DataDomain.MEDICAL_HISTORY,
        DataDomain.CONSENT_FORMS,
    },
    "medical": {
        DataDomain.DEMOGRAPHICS, DataDomain.INSURANCE,
        DataDomain.MEDICATIONS, DataDomain.ALLERGIES,
        DataDomain.MEDICAL_HISTORY, DataDomain.SURGICAL,
        DataDomain.CONSENT_FORMS,
    },
    "oral_surgery": {
        DataDomain.DEMOGRAPHICS, DataDomain.INSURANCE,
        DataDomain.MEDICATIONS, DataDomain.ALLERGIES,
        DataDomain.DENTAL, DataDomain.MEDICAL_HISTORY,
        DataDomain.SURGICAL, DataDomain.CONSENT_FORMS,
    },
    "orthodontics": {
        DataDomain.DEMOGRAPHICS, DataDomain.INSURANCE,
        DataDomain.MEDICATIONS, DataDomain.ALLERGIES,
        DataDomain.DENTAL, DataDomain.CONSENT_FORMS,
    },
    "specialist": {
        DataDomain.DEMOGRAPHICS, DataDomain.INSURANCE,
        DataDomain.MEDICATIONS, DataDomain.ALLERGIES,
        DataDomain.MEDICAL_HISTORY, DataDomain.REFERRALS,
        DataDomain.CONSENT_FORMS,
    },
}


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class AquiferError(Exception):
    """Base exception for all Aquifer errors."""


class ExtractionError(AquiferError):
    """Failed to extract text from a file."""


class DetectionError(AquiferError):
    """PHI detection failed."""


class VaultError(AquiferError):
    """Vault operation failed."""


class FormatError(AquiferError):
    """Invalid or corrupt .aqf file."""
