"""Strata server configuration.

All settings can be overridden via environment variables prefixed with AQUIFER_.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from aquifer.strata.notifications import EmailConfig


@dataclass
class StrataConfig:
    """Server-side configuration for the Strata API."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8443
    debug: bool = False

    # Data storage root — all practice vaults and .aqf files live here
    data_dir: Path = Path("./strata_data")

    # Database (server metadata: users, practices, API keys, usage)
    db_path: Path = Path("./strata_data/strata.db")

    # Master encryption key for server-managed vault keys.
    # In production: load from HSM, KMS, or env var. NEVER hardcode.
    master_key: str = ""

    # Previous master key — set during key rotation so existing vaults can still
    # be decrypted and transparently re-encrypted with the new key.
    previous_master_key: str = ""

    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # Rate limits (requests per minute)
    rate_limit_deid: int = 60
    rate_limit_default: int = 120

    # File limits
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB
    max_batch_size: int = 50

    # Processing
    use_ner: bool = True

    # Email notifications
    email: EmailConfig = field(default_factory=EmailConfig)

    @classmethod
    def from_env(cls) -> StrataConfig:
        """Load config from environment variables."""
        cfg = cls()
        cfg.host = os.getenv("AQUIFER_HOST", cfg.host)
        cfg.port = int(os.getenv("AQUIFER_PORT", str(cfg.port)))
        cfg.debug = os.getenv("AQUIFER_DEBUG", "").lower() in ("1", "true", "yes")
        cfg.data_dir = Path(os.getenv("AQUIFER_DATA_DIR", str(cfg.data_dir)))
        cfg.db_path = Path(os.getenv("AQUIFER_DB_PATH", str(cfg.data_dir / "strata.db")))
        cfg.master_key = os.getenv("AQUIFER_MASTER_KEY", "")
        cfg.previous_master_key = os.getenv("AQUIFER_PREVIOUS_MASTER_KEY", "")
        cfg.jwt_secret = os.getenv("AQUIFER_JWT_SECRET", "")
        cfg.jwt_expiry_hours = int(os.getenv("AQUIFER_JWT_EXPIRY_HOURS", str(cfg.jwt_expiry_hours)))
        cfg.use_ner = os.getenv("AQUIFER_USE_NER", "true").lower() in ("1", "true", "yes")
        cfg.max_upload_bytes = int(os.getenv("AQUIFER_MAX_UPLOAD_BYTES", str(cfg.max_upload_bytes)))
        cfg.email = EmailConfig.from_env()

        # Require explicit secrets — never auto-generate insecure defaults
        allow_insecure = os.getenv(
            "AQUIFER_ALLOW_INSECURE_DEFAULTS", ""
        ).lower() in ("1", "true", "yes")

        if not cfg.master_key:
            if cfg.debug and allow_insecure:
                import sys
                cfg.master_key = "INSECURE-DEV-MASTER-KEY-REPLACE-IN-PRODUCTION"
                print(
                    "\n  WARNING: Using INSECURE development master key.\n"
                    "  Set AQUIFER_MASTER_KEY for production.\n"
                    "  Generate one: python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n",
                    file=sys.stderr,
                )
            else:
                raise ValueError(
                    "AQUIFER_MASTER_KEY must be set. "
                    "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\" "
                    "For local development only: set AQUIFER_DEBUG=1 and AQUIFER_ALLOW_INSECURE_DEFAULTS=1"
                )
        if not cfg.jwt_secret:
            if cfg.debug and allow_insecure:
                import sys
                cfg.jwt_secret = "INSECURE-DEV-JWT-SECRET-REPLACE-IN-PRODUCTION"
                print(
                    "  WARNING: Using INSECURE development JWT secret.\n"
                    "  Set AQUIFER_JWT_SECRET for production.\n",
                    file=sys.stderr,
                )
            else:
                raise ValueError(
                    "AQUIFER_JWT_SECRET must be set. "
                    "For local development only: set AQUIFER_DEBUG=1 and AQUIFER_ALLOW_INSECURE_DEFAULTS=1"
                )

        return cfg

    def ensure_dirs(self) -> None:
        """Create required directories."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "practices").mkdir(exist_ok=True)
