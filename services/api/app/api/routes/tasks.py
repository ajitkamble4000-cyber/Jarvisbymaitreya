"""Task list, details, approvals, cancellation, retry and the emergency stop (docs/api.md "Tasks")."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.devices.hub import DeviceHub
from app.models.devices import Device
from app.models.tasks import Task, TaskEvent
from app.schemas.tasks import (
    DenyRequest,
    EmergencyResumeOut,
    EmergencyStopOut,
    EmergencyStopRequest,
    TaskCounts,
    TaskCreate,
    TaskDetailOut,
    TaskListOut,
    TaskOut,
    TaskStatusLiteral,
    task_event_out,
    task_out,
)
from app.services.device_resolver import connection_status
from app.services.task_export import FILENAME as EXPORT_FILENAME
from app.services.task_export import tasks_to_csv
from app.services.task_engine import TaskEngine

router = APIRouter(prefix="/tasks", tags=["tasks"])

_MAX_LIST = 200
_MAX_EVENTS = 200


def _engine(request: Request) -> TaskEngine:
    engine: TaskEngine = request.app.state.task_engine
    return engine


def _hub(request: Request) -> DeviceHub:
    hub: DeviceHub = request.app.state.device_hub
    return hub


def _parse_task_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Task not found.", code="task_not_found") from exc


async def _outs(request: Request, db: AsyncSession, user_id: uuid.UUID, tasks: Sequence[Task]) -> list[TaskOut]:
    """Build API shapes with one device lookup. Devices are read only within the owner's scope."""
    hub = _hub(request)
    device_ids = {task.device_id for task in tasks if task.device_id is not None}
    devices: dict[uuid.UUID, Device] = {}
    if device_ids:
        rows = await db.scalars(select(Device).where(Device.id.in_(device_ids), Device.user_id == user_id))
        devices = {device.id: device for device in rows.all()}
    outs: list[TaskOut] = []
    for task in tasks:
        device = devices.get(task.device_id) if task.device_id is not None else None
        status = connection_status(device, hub) if device is not None else ""
        outs.append(task_out(task, device, status))
    return outs


async def _out(request: Request, db: AsyncSession, user_id: uuid.UUID, task: Task) -> TaskOut:
    return (await _outs(request, db, user_id, [task]))[0]


async def _owned(db: AsyncSession, user_id: uuid.UUID, raw_id: str) -> Task:
    task = await db.scalar(select(Task).where(Task.id == _parse_task_id(raw_id), Task.user_id == user_id))
    if task is None:
        raise NotFoundError("Task not found.", code="task_not_found")
    return task


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _where(user_id: uuid.UUID, status: str | None, q: str | None) -> ColumnElement[bool]:
    """The owner's rows, narrowed by status and by a text search over title and tool name."""
    conditions = [Task.user_id == user_id]
    if status is not None:
        conditions.append(Task.status == status)
    text = (q or "").strip()
    if text:
        pattern = f"%{_escape_like(text)}%"
        conditions.append(Task.title.ilike(pattern, escape="\\") | Task.tool_name.ilike(pattern, escape="\\"))
    where = conditions[0]
    for extra in conditions[1:]:
        where = where & extra
    return where


@router.get("/export.csv")
async def export_tasks_csv(
    db: DbSession,
    user: CurrentUser,
    status: TaskStatusLiteral | None = None,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=500, ge=1, le=2000),
) -> Response:
    """The owner's tasks as CSV, newest first, with the same filters as the list. Declared before ``/{task_id}``."""
    rows = (
        await db.scalars(
            select(Task)
            .where(_where(user.id, status, q))
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(limit)
        )
    ).all()
    return Response(
        content=tasks_to_csv(list(rows)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"', "Cache-Control": "no-store"},
    )


@router.get("", response_model=TaskListOut)
async def list_tasks(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: TaskStatusLiteral | None = None,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=_MAX_LIST),
    offset: int = Query(default=0, ge=0),
) -> TaskListOut:
    where = _where(user.id, status, q)

    total = await db.scalar(select(func.count(Task.id)).where(where)) or 0
    rows = (
        await db.scalars(
            select(Task).where(where).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    count_rows = (
        await db.execute(select(Task.status, func.count(Task.id)).where(Task.user_id == user.id).group_by(Task.status))
    ).all()
    counts = {name: 0 for name in TaskCounts.model_fields}
    for task_status, value in count_rows:
        if task_status in counts:
            counts[task_status] = int(value)
    return TaskListOut(
        items=await _outs(request, db, user.id, list(rows)),
        total=int(total),
        counts=TaskCounts(**counts),
    )


@router.get("/{task_id}", response_model=TaskDetailOut)
async def get_task(task_id: str, request: Request, db: DbSession, user: CurrentUser) -> TaskDetailOut:
    task = await _owned(db, user.id, task_id)
    events = (
        await db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.user_id == user.id)
            .order_by(TaskEvent.id)
            .limit(_MAX_EVENTS)
        )
    ).all()
    return TaskDetailOut(task=await _out(request, db, user.id, task), events=[task_event_out(e) for e in events])


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreate, request: Request, db: DbSession, user: CurrentUser) -> TaskOut:
    """Manual task. Policy applies exactly as it does for chat proposals."""
    engine = _engine(request)
    task = await engine.propose(
        db,
        user_id=user.id,
        tool_name=body.tool_name,
        arguments=body.arguments,
        source="manual",
        device_id=body.device_id,
        title=body.title,
        description=body.description,
    )
    await db.commit()
    return await _out(request, db, user.id, task)


@router.post("/emergency-stop", response_model=EmergencyStopOut)
async def emergency_stop(body: EmergencyStopRequest, request: Request, user: CurrentUser) -> EmergencyStopOut:
    result = await _engine(request).emergency_stop(user.id)
    return EmergencyStopOut(**result)


@router.post("/emergency-resume", response_model=EmergencyResumeOut)
async def emergency_resume(request: Request, user: CurrentUser) -> EmergencyResumeOut:
    result = await _engine(request).emergency_resume(user.id)
    return EmergencyResumeOut(**result)


@router.post("/{task_id}/approve", response_model=TaskOut)
async def approve_task(task_id: str, request: Request, db: DbSession, user: CurrentUser) -> TaskOut:
    task = await _engine(request).approve(user.id, _parse_task_id(task_id))
    return await _out(request, db, user.id, task)


@router.post("/{task_id}/deny", response_model=TaskOut)
async def deny_task(
    task_id: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    body: DenyRequest | None = None,
) -> TaskOut:
    task = await _engine(request).deny(user.id, _parse_task_id(task_id), body.reason if body else None)
    return await _out(request, db, user.id, task)


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: str, request: Request, db: DbSession, user: CurrentUser) -> TaskOut:
    task = await _engine(request).cancel(user.id, _parse_task_id(task_id))
    return await _out(request, db, user.id, task)


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: str, request: Request, db: DbSession, user: CurrentUser) -> TaskOut:
    task = await _engine(request).retry(user.id, _parse_task_id(task_id))
    return await _out(request, db, user.id, task)

