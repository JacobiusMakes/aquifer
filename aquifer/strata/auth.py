"""Authentication and authorization for the Strata API.

Supports two auth methods:
1. JWT bearer tokens (for browser/dashboard sessions)
2. API keys (for programmatic access: "Bearer aq_..." header)

Vault encryption keys are server-managed per practice:
- A random Fernet key is generated per practice at registration
- It's encrypted with the server master key and stored in the DB
- Decrypted on-demand to open practice vaults
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from aquifer.strata.database import StrataDB


# --- Password Hashing (PBKDF2, no bcrypt dependency) ---

def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 + random salt."""
    salt = secrets.token_bytes(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32,
        salt=salt, iterations=600_000,
    )
    derived = kdf.derive(password.encode())
    # Store as: iterations$salt_hex$hash_hex
    return f"600000${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash."""
    try:
        iterations_s, salt_hex, hash_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32,
            salt=salt, iterations=int(iterations_s),
        )
        kdf.verify(password.encode(), bytes.fromhex(hash_hex))
        return True
    except Exception:
        return False


# --- JWT Tokens ---

def create_jwt(payload: dict, secret: str, algorithm: str = "HS256", expiry_hours: int = 24) -> str:
    """Create a minimal JWT token (no pyjwt dependency)."""
    header = {"alg": algorithm, "typ": "JWT"}
    payload = {**payload, "exp": int(time.time()) + expiry_hours * 3600}

    def _b64(data: bytes) -> str:
        return urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = _b64(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}"

    if algorithm == "HS256":
        sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    return f"{signing_input}.{_b64(sig)}"


def decode_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict | None:
    """Decode and verify a JWT token. Returns None if invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        def _unb64(s: str) -> bytes:
            padding = 4 - len(s) % 4
            if padding != 4:
                s += "=" * padding
            return urlsafe_b64decode(s)

        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}"

        # Verify signature
        expected_sig = hmac.new(
            secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        actual_sig = _unb64(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload = json.loads(_unb64(payload_b64))

        # Check expiration
        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception:
        return None


# --- API Keys ---

def generate_api_key() -> tuple[str, str]:
    """Generate an API key. Returns (full_key, sha256_hash).

    Key format: aq_<40 random chars>
    The full key is shown once to the user; only the hash is stored.
    """
    raw = secrets.token_urlsafe(30)  # ~40 chars
    full_key = f"aq_{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_hash


def hash_api_key(key: str) -> str:
    """Hash an API key for lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


# --- Vault Key Management ---

def generate_practice_vault_key() -> str:
    """Generate a random Fernet key for a practice vault."""
    return Fernet.generate_key().decode()


def encrypt_vault_key(vault_key: str, master_key: str) -> str:
    """Encrypt a practice vault key with the server master key."""
    # Derive a Fernet key from the master key string
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32,
        salt=b"aquifer-strata-vault-key-encryption",
        iterations=100_000,
    )
    derived = urlsafe_b64encode(kdf.derive(master_key.encode()))
    f = Fernet(derived)
    return f.encrypt(vault_key.encode()).decode()


def decrypt_vault_key(encrypted_vault_key: str, master_key: str) -> str:
    """Decrypt a practice vault key using the server master key."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32,
        salt=b"aquifer-strata-vault-key-encryption",
        iterations=100_000,
    )
    derived = urlsafe_b64encode(kdf.derive(master_key.encode()))
    f = Fernet(derived)
    return f.decrypt(encrypted_vault_key.encode()).decode()


# --- Auth Context ---

@dataclass
class AuthContext:
    """Resolved authentication context for a request."""
    practice_id: str
    user_id: str
    email: str
    role: str
    tier: str
    scopes: set[str]  # For API keys; JWT gets all scopes
    auth_method: str  # "jwt" or "api_key"


def has_api_key_scopes(auth: AuthContext, *required_scopes: str) -> bool:
    """Return True when an API key auth context includes every required scope.

    JWT-backed sessions are treated as first-party dashboard access and always pass.
    """
    if auth.auth_method != "api_key":
        return True
    return all(scope in auth.scopes for scope in required_scopes)


def resolve_auth(
    authorization: str | None,
    db: StrataDB,
    jwt_secret: str,
) -> AuthContext | None:
    """Resolve an Authorization header to an AuthContext.

    Accepts:
      - "Bearer <jwt_token>" (JWT)
      - "Bearer aq_<key>" (API key)
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]  # Strip "Bearer "

    # API key path
    if token.startswith("aq_"):
        key_hash = hash_api_key(token)
        api_key = db.get_api_key_by_hash(key_hash)
        if not api_key:
            return None
        user = db.get_user(api_key["user_id"])
        practice = db.get_practice(api_key["practice_id"])
        if not user or not practice or not user["is_active"]:
            return None
        return AuthContext(
            practice_id=practice["id"],
            user_id=user["id"],
            email=user["email"],
            role=user["role"],
            tier=practice["tier"],
            scopes={scope.strip() for scope in api_key["scopes"].split(",") if scope.strip()},
            auth_method="api_key",
        )

    # JWT path
    payload = decode_jwt(token, jwt_secret)
    if not payload:
        return None
    user = db.get_user(payload.get("sub", ""))
    if not user or not user["is_active"]:
        return None
    practice = db.get_practice(user["practice_id"])
    if not practice:
        return None
    return AuthContext(
        practice_id=practice["id"],
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        tier=practice["tier"],
        scopes={"deid", "files", "vault", "admin"},  # JWT gets full access
        auth_method="jwt",
    )
