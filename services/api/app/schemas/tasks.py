"""Request and response models for the tasks API (docs/api.md "Tasks" and "Tasks: emergency stop")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.devices import Device
from app.models.tasks import Task, TaskEvent

TaskStatusLiteral = Literal["queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]
TASK_STATUS_VALUES: tuple[str, ...] = ("queued", "running", "awaiting_approval", "completed", "failed", "cancelled")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(_Strict):
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    device_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class DenyRequest(_Strict):
    reason: str | None = Field(default=None, max_length=200)


class EmergencyStopRequest(_Strict):
    confirm: Literal["STOP"]


class DeviceRef(BaseModel):
    id: uuid.UUID
    name: str
    connection_status: str


class TaskActions(BaseModel):
    approve: bool
    deny: bool
    cancel: bool
    retry: bool


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    source: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: str
    status: str
    device: DeviceRef | None
    conversation_id: uuid.UUID | None
    required_permissions: list[str]
    current_step: str | None
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    attempt: int
    max_attempts: int
    approval_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    actions: TaskActions


class TaskEventOut(BaseModel):
    id: int
    type: str
    message: str
    data: dict[str, Any]
    created_at: datetime


class TaskDetailOut(BaseModel):
    task: TaskOut
    events: list[TaskEventOut]


class TaskCounts(BaseModel):
    queued: int = 0
    running: int = 0
    awaiting_approval: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    counts: TaskCounts


class EmergencyStopOut(BaseModel):
    stopped: bool
    cancelled: int


class EmergencyResumeOut(BaseModel):
    stopped: bool


def task_actions(status: str) -> TaskActions:
    return TaskActions(
        approve=status == "awaiting_approval",
        deny=status == "awaiting_approval",
        cancel=status in ("queued", "awaiting_approval", "running"),
        retry=status in ("failed", "cancelled"),
    )


def device_ref(device: Device | None, connection: str) -> DeviceRef | None:
    if device is None:
        return None
    return DeviceRef(id=device.id, name=device.name, connection_status=connection)


def task_out(task: Task, device: Device | None, connection: str) -> TaskOut:
    """Public shape of a task. ``connection`` is the device's status: online, offline or revoked."""
    return TaskOut(
        id=task.id,
        title=task.title,
        description=task.description,
        source=task.source,
        tool_name=task.tool_name,
        arguments=dict(task.arguments or {}),
        risk_level=task.risk_level,
        status=task.status,
        device=device_ref(device, connection),
        conversation_id=task.conversation_id,
        required_permissions=list(task.required_permissions or []),
        current_step=task.current_step,
        progress=task.progress,
        result=task.result,
        error=task.error,
        attempt=task.attempt,
        max_attempts=task.max_attempts,
        approval_expires_at=task.approval_expires_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        actions=task_actions(task.status),
    )


def task_event_out(event: TaskEvent) -> TaskEventOut:
    return TaskEventOut(
        id=event.id,
        type=event.type,
        message=event.message,
        data=dict(event.data or {}),
        created_at=event.created_at,
    )


__all__ = [
    "DenyRequest",
    "EmergencyResumeOut",
    "EmergencyStopOut",
    "EmergencyStopRequest",
    "TASK_STATUS_VALUES",
    "TaskCounts",
    "TaskCreate",
    "TaskDetailOut",
    "TaskEventOut",
    "TaskListOut",
    "TaskOut",
    "TaskStatusLiteral",
    "device_ref",
    "task_actions",
    "task_event_out",
    "task_out",
]
