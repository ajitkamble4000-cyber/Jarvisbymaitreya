"""Persistent tasks and their execution event timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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

# Task status values. Transitions are enforced by the task engine.
TASK_STATUSES = ("queued", "running", "awaiting_approval", "completed", "failed", "cancelled")
TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled")


class Task(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_status_created", "user_id", "status", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_tasks_user_idempotency"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    # chat | schedule | manual | api
    source: Mapped[str] = mapped_column(String(20), default="chat", nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    required_permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(200))
    # Only populated when the device reports measurable progress: {"value": 0..1, "step": "..."}.
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {"code": "...", "message": "...", "details": {...}}
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    command_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When a sent command counts as timed out: dispatch time plus the user's automation.default_task_timeout_seconds.
    # NULL for tasks that were never sent, and for rows dispatched before this column existed (the sweep then uses started_at).
    run_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))


class TaskEvent(Base):
    """Append-only execution timeline for a task."""

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_id", "task_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
