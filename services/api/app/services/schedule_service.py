"""Scheduled jobs: creation, updates, deletion and reads. Nothing here executes a tool.

Rules enforced here (the worker relies on them):
* The tool exists and its arguments validate against the tool's argument model. Arguments are stored in
  validated form.
* A device, when given, belongs to the user and is not revoked.
* A one-time run must lie in the future at creation, and at re-activation when it is already due.
* ``next_run_at`` is the single cursor the worker reads. While a retry is waiting, the cursor is the retry
  time and a ``retry_wait`` run row holds the slot and attempt number.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.time import utc_now
from app.models.devices import Device
from app.models.identity import User
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.schemas.schedules import ScheduleCreate, ScheduleSpec, ScheduleUpdate
from app.scheduler.timing import (
    ScheduleTimingError,
    ensure_future,
    load_zone,
    next_occurrence,
    next_run_after,
    normalise_weekdays,
    to_utc,
)
from app.tools.args import ToolArgumentError, validate_tool_arguments
from app.tools.base import ToolRegistry
from app.tools.registry import build_tool_registry

MAX_ACTIVE_SCHEDULES = 100
RETRY_WAIT_STATUS = "retry_wait"


def _registry(tools: ToolRegistry | None) -> ToolRegistry:
    return tools if tools is not None else build_tool_registry()


def validate_arguments(tools: ToolRegistry | None, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validated, storable arguments for ``tool_name``. Nothing is executed."""
    spec = _registry(tools).get(tool_name)
    if spec is None:
        raise ValidationAppError("That tool does not exist.", code="invalid_arguments", details={"fields": ["tool_name"]})
    try:
        model = validate_tool_arguments(spec.args_model, arguments)
    except ToolArgumentError as exc:
        if exc.code == "sensitive_content":
            raise ValidationAppError(
                "That looks like a password or key, so the schedule was not saved.", code="sensitive_content"
            ) from None
        raise ValidationAppError(
            "The arguments are not valid for this tool.",
            code="invalid_arguments",
            details={"fields": exc.fields},
        ) from None
    return model.model_dump(mode="json", exclude_none=True)


async def check_device(db: AsyncSession, user_id: uuid.UUID, device_id: uuid.UUID | None) -> uuid.UUID | None:
    if device_id is None:
        return None
    device = await db.scalar(select(Device).where(Device.id == device_id, Device.user_id == user_id))
    if device is None:
        raise NotFoundError("Device not found.", code="device_not_found")
    if device.pairing_status == "revoked" or device.revoked_at is not None:
        raise ConflictError("That device has been revoked.", code="device_revoked")
    return device.id


def resolve_timing(spec: ScheduleSpec, now: datetime) -> dict[str, Any]:
    """Storable timing fields and the first ``next_run_at`` for a schedule specification."""
    try:
        if spec.kind == "once":
            if spec.run_at is None:
                raise ScheduleTimingError("invalid_time", "A one-time schedule needs a run time.")
            run_at = ensure_future(spec.run_at, now)
            return {
                "schedule_kind": "once",
                "run_at": run_at,
                "time_of_day": None,
                "weekdays": [],
                "timezone": spec.timezone,
                "next_run_at": run_at,
            }
        zone = load_zone(spec.timezone)
        if spec.time_of_day is None:
            raise ScheduleTimingError("invalid_time", "A daily or weekly schedule needs a time of day.")
        days = normalise_weekdays(spec.weekdays or []) if spec.kind == "weekly" else []
        first = next_occurrence(
            time_of_day=spec.time_of_day,
            zone=zone,
            weekdays=days if spec.kind == "weekly" else None,
            after=now,
        )
        return {
            "schedule_kind": spec.kind,
            "run_at": None,
            "time_of_day": spec.time_of_day,
            "weekdays": days,
            "timezone": spec.timezone,
            "next_run_at": first,
        }
    except ScheduleTimingError as exc:
        raise ValidationAppError(exc.message, code=exc.code) from None


PREVIEW_RUNS = 5


def preview_runs(spec: Any, now: datetime, count: int = PREVIEW_RUNS) -> list[datetime]:
    """The next ``count`` UTC runs of a schedule spec, worked out with the scheduler's own rules. Nothing is stored.

    A one-time spec in the past has no runs, so the list is empty.
    """
    weekdays = normalise_weekdays(spec.weekdays or []) if spec.kind == "weekly" else None
    runs: list[datetime] = []
    after = now
    try:
        while len(runs) < count:
            following = next_run_after(
                kind=spec.kind,
                after=after,
                timezone_name=spec.timezone,
                time_of_day=spec.time_of_day,
                weekdays=weekdays,
                run_at=spec.run_at,
            )
            if following is None:
                break
            runs.append(following)
            after = following
    except ScheduleTimingError as exc:
        raise ValidationAppError(exc.message, code=exc.code) from None
    return runs


def next_after_now(job: ScheduledJob, now: datetime) -> datetime | None:
    """Next regular run for a stored job, strictly after ``now``. None for a once schedule."""
    if job.schedule_kind == "once":
        return None
    zone = load_zone(job.timezone)
    days = list(job.weekdays or []) if job.schedule_kind == "weekly" else None
    return next_occurrence(
        time_of_day=job.time_of_day or "00:00",
        zone=zone,
        weekdays=days,
        after=now,
    )


async def owned_schedule(
    db: AsyncSession, user_id: uuid.UUID, schedule_id: uuid.UUID, *, lock: bool = False
) -> ScheduledJob:
    query: Select[tuple[ScheduledJob]] = select(ScheduledJob).where(
        ScheduledJob.id == schedule_id, ScheduledJob.user_id == user_id
    )
    if lock:
        query = query.with_for_update()
    job = await db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise NotFoundError("Schedule not found.", code="schedule_not_found")
    return job


async def pending_retry(db: AsyncSession, job_id: uuid.UUID) -> ScheduleRun | None:
    return await db.scalar(
        select(ScheduleRun)
        .where(ScheduleRun.job_id == job_id, ScheduleRun.status == RETRY_WAIT_STATUS)
        .order_by(ScheduleRun.attempt.desc())
        .limit(1)
        .with_for_update()
    )


async def _cancel_pending_retry(db: AsyncSession, job_id: uuid.UUID, now: datetime) -> None:
    retry = await pending_retry(db, job_id)
    if retry is not None:
        retry.status = "cancelled"
        retry.finished_at = now
        retry.error = {"code": "schedule_changed", "message": "The schedule was changed before the retry ran."}


async def create_schedule(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    data: ScheduleCreate,
    tools: ToolRegistry | None = None,
    now: datetime | None = None,
) -> ScheduledJob:
    """Validate and store a new schedule. The caller commits. Raises ``schedule_limit`` at 100 active schedules."""
    moment = to_utc(now) if now is not None else utc_now()
    arguments = validate_arguments(tools, data.tool_name, data.arguments)
    device_id = await check_device(db, user_id, data.device_id)
    timing = resolve_timing(data.schedule, moment)
    retry = data.retry
    # Serialise creation per user so the active-schedule count cannot be raced past the limit.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    active = await db.scalar(
        select(func.count(ScheduledJob.id)).where(ScheduledJob.user_id == user_id, ScheduledJob.ended_at.is_(None))
    )
    if (active or 0) >= MAX_ACTIVE_SCHEDULES:
        raise ConflictError("You already have the maximum number of active schedules.", code="schedule_limit")
    job = ScheduledJob(
        user_id=user_id,
        name=data.name,
        description=data.description,
        tool_name=data.tool_name,
        arguments=arguments,
        device_id=device_id,
        schedule_kind=timing["schedule_kind"],
        run_at=timing["run_at"],
        time_of_day=timing["time_of_day"],
        weekdays=timing["weekdays"],
        timezone=timing["timezone"],
        enabled=True,
        paused=False,
        next_run_at=timing["next_run_at"],
        consecutive_failures=0,
        max_runtime_seconds=data.max_runtime_seconds or 300,
        retry_max_attempts=retry.max_attempts if retry else 1,
        retry_backoff_seconds=retry.backoff_seconds if retry else 300,
        created_at=moment,
        updated_at=moment,
    )
    db.add(job)
    await db.flush()
    return job


def _apply_schedule(job: ScheduledJob, timing: dict[str, Any]) -> None:
    job.schedule_kind = timing["schedule_kind"]
    job.run_at = timing["run_at"]
    job.time_of_day = timing["time_of_day"]
    job.weekdays = timing["weekdays"]
    job.timezone = timing["timezone"]
    job.next_run_at = timing["next_run_at"]


async def _reactivate(db: AsyncSession, job: ScheduledJob, now: datetime) -> None:
    """Recompute the cursor when a job becomes active again, so a stale slot cannot fire as a catch-up."""
    if await pending_retry(db, job.id) is not None:
        return  # the waiting retry keeps its time
    if job.schedule_kind == "once":
        if job.run_at is not None and job.run_at <= now:
            raise ValidationAppError(
                "That one-time run time has already passed. Create a new schedule.", code="schedule_in_past"
            )
        job.next_run_at = job.run_at
        return
    job.next_run_at = next_after_now(job, now)


async def update_schedule(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
    tools: ToolRegistry | None = None,
    now: datetime | None = None,
) -> ScheduledJob:
    """Apply a partial update. The caller commits. Raises ``schedule_completed`` for finished schedules."""
    moment = now or utc_now()
    job = await owned_schedule(db, user_id, schedule_id, lock=True)
    fields = data.model_fields_set
    reactivating = (data.enabled is True and not job.enabled) or (data.paused is False and job.paused)
    if job.ended_at is not None and ("schedule" in fields or reactivating):
        raise ConflictError("This schedule has finished. Create a new schedule to run again.", code="schedule_completed")
    was_active = job.enabled and not job.paused

    if "name" in fields and data.name is not None:
        job.name = data.name
    if "description" in fields and data.description is not None:
        job.description = data.description
    if "arguments" in fields and data.arguments is not None:
        job.arguments = validate_arguments(tools, job.tool_name, data.arguments)
    if "device_id" in fields:
        job.device_id = await check_device(db, user_id, data.device_id)

    schedule_changed = data.schedule is not None
    if data.schedule is not None:
        _apply_schedule(job, resolve_timing(data.schedule, moment))
        await _cancel_pending_retry(db, job.id, moment)
    if "enabled" in fields and data.enabled is not None:
        job.enabled = data.enabled
    if "paused" in fields and data.paused is not None:
        job.paused = data.paused

    now_active = job.enabled and not job.paused
    if now_active and not was_active and not schedule_changed and job.ended_at is None:
        await _reactivate(db, job, moment)
    job.updated_at = moment
    await db.flush()
    return job


async def delete_schedule(db: AsyncSession, *, user_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    job = await owned_schedule(db, user_id, schedule_id)
    await db.delete(job)
    await db.flush()


async def list_schedules(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[ScheduledJob], int]:
    """Ordered by next run (nulls last), then creation time."""
    base = select(ScheduledJob).where(ScheduledJob.user_id == user_id)
    total = await db.scalar(select(func.count(ScheduledJob.id)).where(ScheduledJob.user_id == user_id))
    rows = await db.scalars(
        base.order_by(ScheduledJob.next_run_at.asc().nulls_last(), ScheduledJob.created_at, ScheduledJob.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.all()), int(total or 0)


async def list_runs(
    db: AsyncSession, *, user_id: uuid.UUID, schedule_id: uuid.UUID, limit: int
) -> Sequence[ScheduleRun]:
    job = await owned_schedule(db, user_id, schedule_id)
    rows = await db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.job_id == job.id, ScheduleRun.user_id == user_id)
        .order_by(ScheduleRun.scheduled_for.desc(), ScheduleRun.attempt.desc())
        .limit(limit)
    )
    return list(rows.all())


def retry_delay(job: ScheduledJob, now: datetime) -> datetime:
    return now + timedelta(seconds=job.retry_backoff_seconds)


__all__ = [
    "MAX_ACTIVE_SCHEDULES",
    "RETRY_WAIT_STATUS",
    "check_device",
    "create_schedule",
    "delete_schedule",
    "list_runs",
    "list_schedules",
    "next_after_now",
    "owned_schedule",
    "pending_retry",
    "resolve_timing",
    "retry_delay",
    "update_schedule",
    "validate_arguments",
]
