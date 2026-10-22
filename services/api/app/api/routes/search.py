"""GET /api/v1/search: one search across the signed-in user's conversations, memories and tasks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.services.search_service import MIN_QUERY_CHARS, search_everything

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def global_search(
    db: DbSession,
    user: CurrentUser,
    q: str = Query(min_length=MIN_QUERY_CHARS, max_length=120),
    limit: int = Query(default=5, ge=1, le=10),
) -> dict[str, Any]:
    return await search_everything(db, user_id=user.id, query=q, limit=limit)
