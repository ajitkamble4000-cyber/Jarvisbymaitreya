"""Scheduled jobs: create, list, update (pause, resume, disable), delete and run history (docs/api.md "Schedules")."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.core.time import utc_now
from app.schemas.schedules import (
    ScheduleSpec,
    SchedulePreviewOut,
    ScheduleCreate,
    ScheduleListOut,
    ScheduleOut,
    ScheduleRunListOut,
    ScheduleUpdate,
    run_out,
    schedule_out,
)
from app.services.events import EventBus
from app.services.schedule_service import (
    preview_runs,
    create_schedule,
    delete_schedule,
    list_runs,
    list_schedules,
    update_schedule,
)
from app.tools.base import ToolRegistry

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _tools(request: Request) -> ToolRegistry:
    tools: ToolRegistry = request.app.state.tools
    return tools


def _events(request: Request) -> EventBus:
    events: EventBus = request.app.state.events
    return events


def _parse_schedule_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Schedule not found.", code="schedule_not_found") from exc


@router.get("", response_model=ScheduleListOut)
async def read_schedules(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ScheduleListOut:
    jobs, total = await list_schedules(db, user_id=user.id, limit=limit, offset=offset)
    return ScheduleListOut(items=[schedule_out(job) for job in jobs], total=total)


@router.post("/preview", response_model=SchedulePreviewOut)
async def preview_schedule(body: ScheduleSpec, user: CurrentUser) -> SchedulePreviewOut:
    """The next runs of a schedule, using the scheduler's rules. Declared before the ``/{schedule_id}`` routes."""
    return SchedulePreviewOut(runs=preview_runs(body, utc_now()))


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_job(body: ScheduleCreate, request: Request, user: CurrentUser, db: DbSession) -> ScheduleOut:
    job = await create_schedule(db, user_id=user.id, data=body, tools=_tools(request))
    await db.commit()
    _events(request).publish(user.id, "schedule.updated", {"schedule_id": str(job.id)})
    return schedule_out(job)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_job(
    schedule_id: str, body: ScheduleUpdate, request: Request, user: CurrentUser, db: DbSession
) -> ScheduleOut:
    job = await update_schedule(
        db,
        user_id=user.id,
        schedule_id=_parse_schedule_id(schedule_id),
        data=body,
        tools=_tools(request),
    )
    await db.commit()
    _events(request).publish(user.id, "schedule.updated", {"schedule_id": str(job.id)})
    return schedule_out(job)


@router.delete("/{schedule_id}", status_code=204)
async def remove_job(schedule_id: str, request: Request, user: CurrentUser, db: DbSession) -> Response:
    parsed = _parse_schedule_id(schedule_id)
    await delete_schedule(db, user_id=user.id, schedule_id=parsed)
    await db.commit()
    _events(request).publish(user.id, "schedule.updated", {"schedule_id": str(parsed)})
    return Response(status_code=204)


@router.get("/{schedule_id}/runs", response_model=ScheduleRunListOut)
async def read_runs(
    schedule_id: str,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ScheduleRunListOut:
    runs = await list_runs(db, user_id=user.id, schedule_id=_parse_schedule_id(schedule_id), limit=limit)
    return ScheduleRunListOut(items=[run_out(run) for run in runs])
