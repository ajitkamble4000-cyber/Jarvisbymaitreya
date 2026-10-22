"""Scheduled jobs (one-time, daily, weekly) and their run history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin


class ScheduledJob(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_due", "enabled", "paused", "next_run_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="SET NULL"))
    schedule_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # once | daily | weekly
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # once: UTC instant
    time_of_day: Mapped[str | None] = mapped_column(String(5))  # daily/weekly: "HH:MM" in `timezone`
    weekdays: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)  # 0=Mon .. 6=Sun
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    retry_max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduleRun(Base, UUIDMixin):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        # Duplicate-run prevention: one row per (job, slot, attempt).
        UniqueConstraint("job_id", "scheduled_for", "attempt", name="uq_schedule_runs_job_slot_attempt"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # queued | running | awaiting_approval | succeeded | failed | cancelled | skipped
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
