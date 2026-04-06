"""Cross-practice analytics API routes.

All endpoints require authentication. The network-wide snapshot is
available to any authenticated practice. Practice-specific benchmarks
only return data for the authenticated practice.

Privacy: all responses are aggregate-only, k-anonymity enforced,
no PHI values exposed, no practice identifiers in network data.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aquifer.analytics.engine import AnalyticsEngine
from aquifer.strata.auth import AuthContext, has_api_key_scopes

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _require_scope(auth: AuthContext, scope: str) -> None:
    if not has_api_key_scopes(auth, scope):
        raise HTTPException(403, f"API key missing required '{scope}' scope")


@router.get("/snapshot")
async def network_snapshot(request: Request):
    """Get a point-in-time analytics snapshot across all participating practices.

    Returns aggregate statistics with k-anonymity guarantees. No PHI or
    practice-identifying data is included.
    """
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    engine = AnalyticsEngine(db)
    snapshot = engine.generate_snapshot()

    db.log_audit(
        practice_id=auth.practice_id,
        action="analytics.snapshot",
        resource_type="analytics",
        user_id=auth.user_id,
    )

    return snapshot.to_dict()


@router.get("/benchmarks")
async def practice_benchmarks(request: Request):
    """Compare this practice against network averages.

    Shows how your file volume, patient count, and data mix compare
    to the network — without revealing any other practice's data.
    """
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    engine = AnalyticsEngine(db)
    benchmarks = engine.get_practice_benchmarks(auth.practice_id)

    db.log_audit(
        practice_id=auth.practice_id,
        action="analytics.benchmarks",
        resource_type="analytics",
        user_id=auth.user_id,
    )

    return benchmarks


@router.get("/trends")
async def network_trends(request: Request, months: int = 12):
    """Get monthly trend data for the network.

    Shows processing volume, transfer activity, and network growth
    over the specified period.
    """
    auth: AuthContext = request.state.auth
    db = request.app.state.db

    if months < 1 or months > 36:
        raise HTTPException(400, "Months must be between 1 and 36")

    engine = AnalyticsEngine(db)
    trends = engine.get_trend_data(months=months)

    db.log_audit(
        practice_id=auth.practice_id,
        action="analytics.trends",
        resource_type="analytics",
        user_id=auth.user_id,
    )

    return trends
