"""Fernet symmetric encryption for vault PHI values.

Encryption key derived from master password via PBKDF2.
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def derive_key(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a Fernet key from a password using PBKDF2.

    Args:
        password: Master password.
        salt: Optional salt bytes. Generated if not provided.

    Returns:
        Tuple of (fernet_key, salt).
    """
    if salt is None:
        salt = os.urandom(16)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,  # OWASP recommended minimum
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key, salt


def encrypt_value(value: str, key: bytes) -> str:
    """Encrypt a string value with Fernet.

    Returns base64-encoded ciphertext as a string.
    """
    f = Fernet(key)
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str, key: bytes) -> str:
    """Decrypt a Fernet-encrypted value.

    Returns the original plaintext string.
    """
    f = Fernet(key)
    return f.decrypt(encrypted.encode()).decode()
