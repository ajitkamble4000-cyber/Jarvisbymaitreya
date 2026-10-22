"""Request and response models for scheduled jobs (docs/api.md "Schedules").

Request models reject unknown fields. Their validators check shape and time zone only. Anything that needs
the clock or the tool registry (past run times, tool arguments, device ownership) is checked in
schedule_service.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.models.scheduling import ScheduledJob, ScheduleRun
from app.scheduler.timing import TIME_OF_DAY_PATTERN, ScheduleTimingError, load_zone, normalise_weekdays

STATE_COMPLETED = "completed"
STATE_PAUSED = "paused"
STATE_DISABLED = "disabled"
STATE_FAILING = "failing"
STATE_SCHEDULED = "scheduled"
FAILING_THRESHOLD = 2
MAX_ACTIVE_NAME = 200


def _nonblank(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


Name = Annotated[str, Field(min_length=1, max_length=MAX_ACTIVE_NAME), AfterValidator(_nonblank)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleSpec(_Strict):
    kind: Literal["once", "daily", "weekly"]
    run_at: datetime | None = None
    time_of_day: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @model_validator(mode="after")
    def _shape(self) -> ScheduleSpec:
        try:
            load_zone(self.timezone)
            if self.kind == "once":
                if self.run_at is None:
                    raise ValueError("a one-time schedule needs run_at")
                if self.run_at.tzinfo is None or self.run_at.utcoffset() is None:
                    raise ValueError("run_at must include a UTC offset")
                if self.time_of_day is not None or self.weekdays is not None:
                    raise ValueError("a one-time schedule takes only run_at")
            else:
                if self.run_at is not None:
                    raise ValueError("run_at is only for one-time schedules")
                if self.time_of_day is None:
                    raise ValueError("a daily or weekly schedule needs time_of_day")
                if self.kind == "daily" and self.weekdays is not None:
                    raise ValueError("a daily schedule takes no weekdays")
                if self.kind == "weekly":
                    if self.weekdays is None:
                        raise ValueError("a weekly schedule needs weekdays")
                    normalise_weekdays(self.weekdays)
        except ScheduleTimingError as exc:
            raise ValueError(exc.message) from None
        return self


class RetrySpec(_Strict):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: int = Field(default=300, ge=0, le=86400)


class ScheduleCreate(_Strict):
    name: Name
    description: str = Field(default="", max_length=2000)
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    device_id: uuid.UUID | None = None
    schedule: ScheduleSpec
    retry: RetrySpec | None = None
    max_runtime_seconds: Annotated[int, Field(ge=30, le=3600)] | None = None


_NOT_NULL_ON_UPDATE = ("name", "description", "enabled", "paused", "schedule", "arguments")


class ScheduleUpdate(_Strict):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=2000)
    enabled: StrictBool | None = None
    paused: StrictBool | None = None
    schedule: ScheduleSpec | None = None
    arguments: dict[str, Any] | None = None
    device_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _explicit_nulls(self) -> ScheduleUpdate:
        for field in _NOT_NULL_ON_UPDATE:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class SchedulePreviewOut(BaseModel):
    """The next UTC runs of a spec. Nothing is saved by the preview."""

    runs: list[datetime]


class ScheduleSpecOut(BaseModel):
    kind: Literal["once", "daily", "weekly"]
    run_at: datetime | None
    time_of_day: str | None
    weekdays: list[int] | None
    timezone: str


class RetryOut(BaseModel):
    max_attempts: int
    backoff_seconds: int


class ScheduleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    tool_name: str
    arguments: dict[str, Any]
    device_id: uuid.UUID | None
    schedule: ScheduleSpecOut
    enabled: bool
    paused: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    consecutive_failures: int
    retry: RetryOut
    max_runtime_seconds: int
    ended_at: datetime | None
    state: Literal["scheduled", "paused", "disabled", "completed", "failing"]


class ScheduleListOut(BaseModel):
    items: list[ScheduleOut]
    total: int


class ScheduleRunOut(BaseModel):
    id: uuid.UUID
    scheduled_for: datetime
    status: str
    attempt: int
    task_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    error: dict[str, Any] | None


class ScheduleRunListOut(BaseModel):
    items: list[ScheduleRunOut]


def schedule_state(job: ScheduledJob) -> str:
    """Derived state, checked in this order: completed, paused, disabled, failing, scheduled."""
    if job.ended_at is not None:
        return STATE_COMPLETED
    if job.paused:
        return STATE_PAUSED
    if not job.enabled:
        return STATE_DISABLED
    if job.consecutive_failures >= FAILING_THRESHOLD:
        return STATE_FAILING
    return STATE_SCHEDULED


def schedule_out(job: ScheduledJob) -> ScheduleOut:
    weekdays = list(job.weekdays or []) if job.schedule_kind == "weekly" else None
    return ScheduleOut(
        id=job.id,
        name=job.name,
        description=job.description,
        tool_name=job.tool_name,
        arguments=dict(job.arguments or {}),
        device_id=job.device_id,
        schedule=ScheduleSpecOut(
            kind=job.schedule_kind,  # type: ignore[arg-type]
            run_at=job.run_at if job.schedule_kind == "once" else None,
            time_of_day=job.time_of_day if job.schedule_kind != "once" else None,
            weekdays=weekdays,
            timezone=job.timezone,
        ),
        enabled=job.enabled,
        paused=job.paused,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        last_status=job.last_status,
        consecutive_failures=job.consecutive_failures,
        retry=RetryOut(max_attempts=job.retry_max_attempts, backoff_seconds=job.retry_backoff_seconds),
        max_runtime_seconds=job.max_runtime_seconds,
        ended_at=job.ended_at,
        state=schedule_state(job),  # type: ignore[arg-type]
    )


def run_out(run: ScheduleRun) -> ScheduleRunOut:
    return ScheduleRunOut(
        id=run.id,
        scheduled_for=run.scheduled_for,
        status=run.status,
        attempt=run.attempt,
        task_id=run.task_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=dict(run.error) if run.error is not None else None,
    )


__all__ = [
    "FAILING_THRESHOLD",
    "RetryOut",
    "RetrySpec",
    "STATE_COMPLETED",
    "STATE_DISABLED",
    "STATE_FAILING",
    "STATE_PAUSED",
    "STATE_SCHEDULED",
    "ScheduleCreate",
    "ScheduleListOut",
    "ScheduleOut",
    "ScheduleRunListOut",
    "ScheduleRunOut",
    "ScheduleSpec",
    "ScheduleSpecOut",
    "ScheduleUpdate",
    "run_out",
    "schedule_out",
    "schedule_state",
]
