"""Liveness and readiness probes. No authentication; they reveal no user data."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.time import to_iso, utc_now

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
async def health(request: Request, db: DbSession) -> dict[str, object]:
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - any failure means the database is unavailable
        database = "error"
    settings = request.app.state.settings
    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": settings.app_version,
        "database": database,
        "background_workers": settings.run_background_workers,
        "time": to_iso(utc_now()),
    }
