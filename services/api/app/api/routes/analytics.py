"""Operational analytics computed from measured data only (docs/api.md "Audit, analytics, developer")."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.core.time import utc_now
from app.schemas.platform import AnalyticsSummaryOut, CallsTodayOut
from app.services.analytics_service import build_summary
from app.services.settings_service import load_settings
from app.services.usage import chat_calls_since, utc_day_start

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryOut)
async def analytics_summary(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsSummaryOut:
    return await build_summary(db, user_id=user.id, days=days, hub=request.app.state.device_hub)


@router.get("/calls-today", response_model=CallsTodayOut)
async def calls_today(db: DbSession, user: CurrentUser) -> CallsTodayOut:
    """Chat provider calls made so far today (UTC) and the daily limit they count against."""
    document = await load_settings(db, user.id)
    await db.commit()
    day_start = utc_day_start(utc_now())
    calls = await chat_calls_since(db, user_id=user.id, since=day_start)
    return CallsTodayOut(calls=calls, limit=document.ai.daily_call_limit, resets_at=day_start + timedelta(days=1))
