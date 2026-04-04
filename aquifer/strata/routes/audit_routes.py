"""Audit log routes — HIPAA-compliant event history for a practice."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aquifer.strata.auth import AuthContext

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    practice_id: str
    user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: str | None
    ip_address: str | None
    created_at: str


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    limit: int
    offset: int


@router.get("", response_model=AuditLogResponse)
async def get_audit_log(request: Request, limit: int = 100, offset: int = 0):
    """Return the audit log for the authenticated practice. Admin only."""
    auth: AuthContext = request.state.auth
    if auth.role != "admin":
        raise HTTPException(403, "Admin role required to access audit log")
    if limit > 500:
        limit = 500
    db = request.app.state.db
    entries = db.get_audit_log(auth.practice_id, limit=limit, offset=offset)
    return AuditLogResponse(entries=entries, limit=limit, offset=offset)
