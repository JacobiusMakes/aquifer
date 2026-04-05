"""Authentication routes: register, login, API key management, email verification, password management."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator

from aquifer.strata.auth import (
    AuthContext, create_jwt, generate_api_key, generate_practice_vault_key,
    encrypt_vault_key, hash_password, has_api_key_scopes, verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Request/Response Models ---

class RegisterRequest(BaseModel):
    practice_name: str
    email: str
    password: str

    @field_validator("practice_name")
    @classmethod
    def validate_practice_name(cls, v: str) -> str:
        if len(v) < 2 or len(v) > 100:
            raise ValueError("Practice name must be 2-100 characters")
        return v.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address")
        return v.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        # Block common weak passwords
        common = {"password", "123456789", "qwerty", "letmein", "welcome",
                  "admin", "aquifer", "changeme"}
        if v.lower().rstrip("0123456789!@#$%") in common:
            raise ValueError("Password is too common")
        return v


class RegisterResponse(BaseModel):
    practice_id: str
    user_id: str
    email: str
    token: str
    message: str = "Practice registered successfully"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    practice_id: str
    user_id: str
    email: str
    role: str
    tier: str


class CreateApiKeyRequest(BaseModel):
    name: str | None = None
    scopes: str = "deid,files"


class CreateApiKeyResponse(BaseModel):
    id: str
    key: str  # Full key — shown only once
    key_prefix: str
    name: str | None
    scopes: str
    message: str = "Store this key securely — it cannot be retrieved again"


class ApiKeyInfo(BaseModel):
    id: str
    key_prefix: str
    name: str | None
    scopes: str
    is_active: int
    last_used_at: str | None
    created_at: str


def _require_admin_api_key_scope(auth: AuthContext) -> None:
    if auth.role != "admin":
        raise HTTPException(403, "Admin role required")
    if not has_api_key_scopes(auth, "admin"):
        raise HTTPException(403, "API key missing required 'admin' scope")


# --- Endpoints ---

@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest, request: Request):
    """Register a new practice and admin user."""
    app = request.app
    db = app.state.db
    config = app.state.config

    # Check email uniqueness
    if db.get_user_by_email(body.email):
        raise HTTPException(409, "Email already registered")

    # Generate slug from practice name
    slug = re.sub(r"[^a-z0-9]+", "-", body.practice_name.lower()).strip("-")
    if db.get_practice_by_slug(slug):
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    # Create practice with server-managed vault key
    practice_id = str(uuid.uuid4())
    vault_key = generate_practice_vault_key()
    encrypted_key = encrypt_vault_key(vault_key, config.master_key)

    db.create_practice(
        id=practice_id, name=body.practice_name, slug=slug,
        vault_key_encrypted=encrypted_key,
    )

    # Initialize practice storage
    app.state.vault_manager.init_practice(practice_id, vault_key)

    # Create admin user
    user_id = str(uuid.uuid4())
    db.create_user(
        id=user_id, practice_id=practice_id,
        email=body.email, password_hash=hash_password(body.password),
        role="admin",
    )

    # Issue JWT
    token = create_jwt(
        {"sub": user_id, "practice_id": practice_id, "role": "admin"},
        config.jwt_secret, expiry_hours=config.jwt_expiry_hours,
    )

    # Generate email verification token
    verification_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    db.set_verification_token(user_id, verification_token, expires_at)

    # Send verification email if SMTP is configured
    email_config = getattr(app.state, "email_config", None)
    if email_config and email_config.enabled:
        from aquifer.strata.notifications import send_notification
        base_url = str(request.base_url).rstrip("/")
        verify_url = f"{base_url}/api/v1/auth/verify-email?token={verification_token}"
        send_notification(
            email_config,
            to=body.email,
            subject="Aquifer — Verify your email",
            body=(
                f"Welcome to Aquifer!\n\n"
                f"Click the link below to verify your email:\n{verify_url}\n\n"
                f"This link expires in 24 hours.\n\n"
                f"If you didn't create this account, you can ignore this email."
            ),
        )

    db.log_usage(practice_id, "register", user_id=user_id)

    return RegisterResponse(
        practice_id=practice_id, user_id=user_id,
        email=body.email, token=token,
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    """Authenticate and receive a JWT token."""
    db = request.app.state.db
    config = request.app.state.config

    user = db.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled")

    practice = db.get_practice(user["practice_id"])

    token = create_jwt(
        {"sub": user["id"], "practice_id": user["practice_id"], "role": user["role"]},
        config.jwt_secret, expiry_hours=config.jwt_expiry_hours,
    )

    db.log_usage(user["practice_id"], "login", user_id=user["id"])

    return LoginResponse(
        token=token, practice_id=user["practice_id"],
        user_id=user["id"], email=user["email"],
        role=user["role"], tier=practice["tier"],
    )


@router.post("/api-keys", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key_endpoint(body: CreateApiKeyRequest, request: Request):
    """Create a new API key for programmatic access."""
    auth: AuthContext = request.state.auth
    _require_admin_api_key_scope(auth)
    db = request.app.state.db
    config = request.app.state.config

    full_key, key_hash = generate_api_key(hmac_secret=config.jwt_secret)
    key_id = str(uuid.uuid4())

    db.create_api_key(
        id=key_id, practice_id=auth.practice_id, user_id=auth.user_id,
        key_hash=key_hash, key_prefix=full_key[:11],  # "aq_" + 8 chars
        name=body.name, scopes=body.scopes,
    )

    db.log_usage(auth.practice_id, "create_api_key", user_id=auth.user_id)

    return CreateApiKeyResponse(
        id=key_id, key=full_key, key_prefix=full_key[:11],
        name=body.name, scopes=body.scopes,
    )


@router.get("/api-keys", response_model=list[ApiKeyInfo])
async def list_api_keys(request: Request):
    """List all API keys for the current practice."""
    auth: AuthContext = request.state.auth
    _require_admin_api_key_scope(auth)
    db = request.app.state.db
    return db.list_api_keys(auth.practice_id)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an API key."""
    auth: AuthContext = request.state.auth
    _require_admin_api_key_scope(auth)
    db = request.app.state.db

    if not db.revoke_api_key(key_id, auth.practice_id):
        raise HTTPException(404, "API key not found")

    db.log_usage(auth.practice_id, "revoke_api_key", user_id=auth.user_id)


# --- Email Verification ---

@router.get("/verify-email")
async def verify_email(token: str, request: Request):
    """Verify a user's email address using the token sent during registration."""
    db = request.app.state.db

    user = db.get_user_by_verification_token(token)
    if not user:
        raise HTTPException(400, "Invalid or expired verification token")

    # Check expiry
    if user["verification_token_expires"]:
        try:
            expires = datetime.fromisoformat(str(user["verification_token_expires"]))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                raise HTTPException(400, "Verification token has expired. Please request a new one.")
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid verification token")

    db.verify_user_email(user["id"])

    return {"message": "Email verified successfully. You can now log in.", "verified": True}


@router.post("/resend-verification")
async def resend_verification(request: Request):
    """Resend the email verification link. Requires authentication."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    user = db.get_user(auth.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if user["email_verified"]:
        return {"message": "Email is already verified"}

    verification_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    db.set_verification_token(user["id"], verification_token, expires_at)

    email_config = getattr(request.app.state, "email_config", None)
    if email_config and email_config.enabled:
        from aquifer.strata.notifications import send_notification
        base_url = str(request.base_url).rstrip("/")
        verify_url = f"{base_url}/api/v1/auth/verify-email?token={verification_token}"
        send_notification(
            email_config,
            to=user["email"],
            subject="Aquifer — Verify your email",
            body=(
                f"Click the link below to verify your email:\n{verify_url}\n\n"
                f"This link expires in 24 hours."
            ),
        )

    return {"message": "Verification email sent", "token": verification_token}


# --- Password Management ---

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class RequestResetRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request):
    """Change the current user's password. Requires authentication."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    user = db.get_user(auth.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")

    if body.current_password == body.new_password:
        raise HTTPException(400, "New password must be different from current password")

    db.update_user_password(user["id"], hash_password(body.new_password))

    db.log_audit(
        practice_id=auth.practice_id,
        action="user.password_changed",
        resource_type="user",
        resource_id=auth.user_id,
        user_id=auth.user_id,
    )

    return {"message": "Password changed successfully"}


@router.post("/request-reset")
async def request_password_reset(body: RequestResetRequest, request: Request):
    """Request a password reset. Sends a reset token via email.

    Always returns 200 to prevent email enumeration attacks.
    """
    db = request.app.state.db
    user = db.get_user_by_email(body.email.lower().strip())

    if user:
        reset_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.set_password_reset_token(user["id"], reset_token, expires_at)

        email_config = getattr(request.app.state, "email_config", None)
        if email_config and email_config.enabled:
            from aquifer.strata.notifications import send_notification
            send_notification(
                email_config,
                to=user["email"],
                subject="Aquifer — Password reset",
                body=(
                    f"A password reset was requested for your Aquifer account.\n\n"
                    f"Your reset token: {reset_token}\n\n"
                    f"This token expires in 1 hour.\n\n"
                    f"If you didn't request this, you can ignore this email."
                ),
            )

    # Always return success to prevent email enumeration
    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request):
    """Reset password using a token from request-reset."""
    db = request.app.state.db

    user = db.get_user_by_reset_token(body.token)
    if not user:
        raise HTTPException(400, "Invalid or expired reset token")

    # Check expiry
    if user["password_reset_expires"]:
        try:
            expires = datetime.fromisoformat(str(user["password_reset_expires"]))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                db.clear_reset_token(user["id"])
                raise HTTPException(400, "Reset token has expired. Please request a new one.")
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid reset token")

    db.update_user_password(user["id"], hash_password(body.new_password))
    db.clear_reset_token(user["id"])

    db.log_audit(
        practice_id=user["practice_id"],
        action="user.password_reset",
        resource_type="user",
        resource_id=user["id"],
    )

    return {"message": "Password reset successfully. You can now log in with your new password."}
