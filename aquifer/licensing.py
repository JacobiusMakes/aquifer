"""License key validation and feature gating.

License keys are signed tokens that encode:
- Tier (community, starter, professional, enterprise)
- Practice ID
- Expiration date
- Feature flags

Format: AQ-<TIER>-<PAYLOAD>-<SIGNATURE>

Community tier (no key required) gets:
- De-identification engine (all file types)
- .aqf format read/write
- CLI tool
- Local vault
- 100 files/month

Starter ($99/mo):
- Unlimited local processing
- Claim status tracking

Professional ($299/mo):
- Full claims tracking + denial prediction + appeal drafts
- QC dashboard
- Cloud/hybrid vault

Enterprise ($499+/mo):
- Multi-location
- Cross-practice analytics
- Custom NER training
- API access
"""

from __future__ import annotations

import hashlib
import hmac
import json
import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class Tier(str, Enum):
    COMMUNITY = "community"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


# Features unlocked by tier
TIER_FEATURES: dict[Tier, set[str]] = {
    Tier.COMMUNITY: {
        "deid", "aqf_read", "aqf_write", "vault_local", "cli",
    },
    Tier.STARTER: {
        "deid", "aqf_read", "aqf_write", "vault_local", "cli",
        "claims_tracking", "unlimited_files",
    },
    Tier.PROFESSIONAL: {
        "deid", "aqf_read", "aqf_write", "vault_local", "cli",
        "claims_tracking", "unlimited_files",
        "denial_prediction", "appeal_generation", "qc_dashboard",
        "vault_cloud",
    },
    Tier.ENTERPRISE: {
        "deid", "aqf_read", "aqf_write", "vault_local", "cli",
        "claims_tracking", "unlimited_files",
        "denial_prediction", "appeal_generation", "qc_dashboard",
        "vault_cloud",
        "multi_location", "cross_practice_analytics", "custom_ner",
        "api_access",
    },
}

# Monthly file limits by tier
TIER_FILE_LIMITS: dict[Tier, int | None] = {
    Tier.COMMUNITY: 100,
    Tier.STARTER: None,      # unlimited
    Tier.PROFESSIONAL: None,
    Tier.ENTERPRISE: None,
}


@dataclass
class License:
    """Parsed and validated license."""
    tier: Tier
    practice_id: str
    expires: date
    features: set[str]
    is_valid: bool
    error: Optional[str] = None

    def has_feature(self, feature: str) -> bool:
        return self.is_valid and feature in self.features

    @property
    def file_limit(self) -> int | None:
        return TIER_FILE_LIMITS.get(self.tier)

    @property
    def is_expired(self) -> bool:
        return date.today() > self.expires


# The signing secret would be an env var or HSM-backed in production.
# This default is for development/testing only.
_SIGNING_SECRET = b"aquifer-dev-signing-key-replace-in-production"


def _sign(payload: str) -> str:
    """HMAC-SHA256 sign a payload."""
    sig = hmac.new(_SIGNING_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return sig[:16]  # Truncated for shorter keys


def generate_license_key(
    tier: Tier,
    practice_id: str,
    expires: date,
    signing_secret: bytes | None = None,
) -> str:
    """Generate a signed license key.

    This would run on your license server, not on the client.
    Included here for development and testing.
    """
    secret = signing_secret or _SIGNING_SECRET

    payload = {
        "tier": tier.value,
        "practice_id": practice_id,
        "expires": expires.isoformat(),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")

    sig = hmac.new(secret, payload_json.encode(), hashlib.sha256).hexdigest()[:16]

    tier_prefix = tier.value[:4].upper()
    return f"AQ-{tier_prefix}-{payload_b64}-{sig}"


def validate_license_key(
    key: str,
    signing_secret: bytes | None = None,
) -> License:
    """Validate a license key and return the parsed license.

    Returns a License object with is_valid=False if validation fails.
    """
    secret = signing_secret or _SIGNING_SECRET

    if not key or not key.startswith("AQ-"):
        return License(
            tier=Tier.COMMUNITY, practice_id="", expires=date.min,
            features=TIER_FEATURES[Tier.COMMUNITY],
            is_valid=False, error="Invalid key format",
        )

    parts = key.split("-", 3)
    if len(parts) != 4:
        return License(
            tier=Tier.COMMUNITY, practice_id="", expires=date.min,
            features=TIER_FEATURES[Tier.COMMUNITY],
            is_valid=False, error="Invalid key format",
        )

    _, tier_prefix, payload_b64, provided_sig = parts

    # Decode payload
    try:
        # Add padding back
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
    except Exception:
        return License(
            tier=Tier.COMMUNITY, practice_id="", expires=date.min,
            features=TIER_FEATURES[Tier.COMMUNITY],
            is_valid=False, error="Invalid key payload",
        )

    # Verify signature
    expected_sig = hmac.new(secret, payload_json.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(provided_sig, expected_sig):
        return License(
            tier=Tier.COMMUNITY, practice_id="", expires=date.min,
            features=TIER_FEATURES[Tier.COMMUNITY],
            is_valid=False, error="Invalid key signature",
        )

    # Parse fields
    try:
        tier = Tier(payload["tier"])
        practice_id = payload["practice_id"]
        expires = date.fromisoformat(payload["expires"])
    except (KeyError, ValueError) as e:
        return License(
            tier=Tier.COMMUNITY, practice_id="", expires=date.min,
            features=TIER_FEATURES[Tier.COMMUNITY],
            is_valid=False, error=f"Invalid key data: {e}",
        )

    # Check expiration
    if date.today() > expires:
        return License(
            tier=tier, practice_id=practice_id, expires=expires,
            features=TIER_FEATURES[tier],
            is_valid=False, error="License expired",
        )

    return License(
        tier=tier,
        practice_id=practice_id,
        expires=expires,
        features=TIER_FEATURES[tier],
        is_valid=True,
    )


# --- Local license storage ---

_LICENSE_FILE = Path.home() / ".aquifer" / "license.key"


def activate_license(key: str) -> License:
    """Validate and store a license key locally."""
    license = validate_license_key(key)
    if license.is_valid:
        _LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LICENSE_FILE.write_text(key)
    return license


def get_current_license() -> License:
    """Load the currently activated license, or return community tier."""
    if _LICENSE_FILE.exists():
        key = _LICENSE_FILE.read_text().strip()
        return validate_license_key(key)

    return License(
        tier=Tier.COMMUNITY,
        practice_id="community",
        expires=date(2099, 12, 31),
        features=TIER_FEATURES[Tier.COMMUNITY],
        is_valid=True,
    )


def require_feature(feature: str) -> None:
    """Check that the current license includes a feature. Raises if not."""
    license = get_current_license()
    if not license.has_feature(feature):
        tier_needed = None
        for tier, features in TIER_FEATURES.items():
            if feature in features:
                tier_needed = tier
                break
        raise LicenseError(
            f"Feature '{feature}' requires {tier_needed.value if tier_needed else 'a paid'} "
            f"license. Current tier: {license.tier.value}. "
            f"Upgrade at https://aquifer.health/pricing"
        )


class LicenseError(Exception):
    """Raised when a feature requires a higher license tier."""
    pass
