"""Practice management routes: info, usage, settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aquifer.strata.auth import AuthContext
from aquifer.licensing import Tier, TIER_FEATURES, TIER_FILE_LIMITS

router = APIRouter(prefix="/practice", tags=["practice"])


class PracticeInfoResponse(BaseModel):
    id: str
    name: str
    slug: str
    tier: str
    features: list[str]
    file_limit: int | None
    created_at: str


class UsageResponse(BaseModel):
    period_days: int
    total_actions: int
    total_bytes: int
    unique_files: int
    by_action: dict[str, int]
    file_count: int
    file_limit: int | None
    usage_pct: float | None


class HealthResponse(BaseModel):
    status: str
    version: str
    vault_ok: bool


@router.get("", response_model=PracticeInfoResponse)
async def get_practice(request: Request):
    """Get current practice info."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    practice = db.get_practice(auth.practice_id)
    if not practice:
        raise HTTPException(404, "Practice not found")

    tier = Tier(practice["tier"]) if practice["tier"] in [t.value for t in Tier] else Tier.COMMUNITY
    features = sorted(TIER_FEATURES.get(tier, set()))
    file_limit = TIER_FILE_LIMITS.get(tier)

    return PracticeInfoResponse(
        id=practice["id"],
        name=practice["name"],
        slug=practice["slug"],
        tier=practice["tier"],
        features=features,
        file_limit=file_limit,
        created_at=practice["created_at"],
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage(request: Request, days: int = 30):
    """Get usage statistics for the current billing period."""
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    practice = db.get_practice(auth.practice_id)
    tier = Tier(practice["tier"]) if practice["tier"] in [t.value for t in Tier] else Tier.COMMUNITY
    file_limit = TIER_FILE_LIMITS.get(tier)

    stats = db.get_usage_stats(auth.practice_id, days=days)
    file_count = db.count_files(auth.practice_id)

    usage_pct = None
    if file_limit:
        usage_pct = round((file_count / file_limit) * 100, 1)

    return UsageResponse(
        **stats,
        file_count=file_count,
        file_limit=file_limit,
        usage_pct=usage_pct,
    )
