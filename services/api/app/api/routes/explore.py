"""Capability discovery (/explore) and the tool registry (/tools). Discovery never authorises anything."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.schemas.platform import ExploreOut, ToolListOut
from app.services.explore_service import build_explore, explore_response, load_context, tool_out
from app.services.settings_service import load_settings

router = APIRouter(tags=["explore"])


@router.get("/tools", response_model=ToolListOut)
async def list_tools(request: Request, user: CurrentUser) -> ToolListOut:
    registry = request.app.state.tools
    return ToolListOut(items=[tool_out(spec) for spec in registry.all()])


@router.get("/explore", response_model=ExploreOut)
async def explore(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    category: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> ExploreOut:
    settings = request.app.state.settings
    document = await load_settings(db, user.id)
    await db.commit()
    ctx = await load_context(
        db,
        user.id,
        background_workers=settings.run_background_workers,
        disabled_tools=document.automation.disabled_tools,
    )
    items = build_explore(request.app.state.tools, ctx)
    return explore_response(items, category=category, query=q)
