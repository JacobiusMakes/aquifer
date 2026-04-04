"""Email notification infrastructure for Strata.

Disabled by default — only active when AQUIFER_SMTP_HOST and AQUIFER_SMTP_USER
are set. No routes send email yet; this module just provides the plumbing.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "noreply@aquifer.health"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> EmailConfig:
        import os
        cfg = cls()
        cfg.smtp_host = os.getenv("AQUIFER_SMTP_HOST", "")
        cfg.smtp_port = int(os.getenv("AQUIFER_SMTP_PORT", "587"))
        cfg.smtp_user = os.getenv("AQUIFER_SMTP_USER", "")
        cfg.smtp_password = os.getenv("AQUIFER_SMTP_PASSWORD", "")
        cfg.from_address = os.getenv("AQUIFER_SMTP_FROM", cfg.from_address)
        cfg.enabled = bool(cfg.smtp_host and cfg.smtp_user)
        return cfg


def send_notification(config: EmailConfig, to: str, subject: str, body: str) -> bool:
    if not config.enabled:
        logger.debug("Email notifications disabled (no SMTP configured)")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = config.from_address
        msg["To"] = to
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False
