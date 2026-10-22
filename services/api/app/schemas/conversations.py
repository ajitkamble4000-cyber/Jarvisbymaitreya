"""Request and response models for conversations, messages and the chat stream (see docs/api.md)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.models.conversation import Conversation, Message
from app.models.tasks import Task

MESSAGE_ROLES = ("user", "assistant", "system", "tool")
MESSAGE_STATUSES = ("complete", "streaming", "error", "cancelled")
# Upper bound on the request body. The real limit is settings-driven (max_message_chars) and checked in the service.
_BODY_CHAR_GUARD = 32_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(_Strict):
    title: str | None = Field(default=None, max_length=200)


class ClearConversationsIn(_Strict):
    """Clears conversations last updated more than ``days`` ago. Pinned ones are never cleared."""

    days: StrictInt = Field(ge=1, le=3650)
    dry_run: StrictBool = True


class ClearConversationsOut(BaseModel):
    matched: int
    deleted: int
    dry_run: bool


class ConversationPatch(_Strict):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    pinned: StrictBool | None = None


class ChatMessageIn(_Strict):
    content: str = Field(max_length=_BODY_CHAR_GUARD)
    provider_id: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)


class MessageFeedback(_Strict):
    """``up`` or ``down``. ``null`` clears the rating."""

    rating: Literal["up", "down"] | None


class MessageEdit(_Strict):
    content: str = Field(max_length=_BODY_CHAR_GUARD)


class ConversationOut(BaseModel):
    id: str
    title: str
    archived: bool
    pinned: bool
    provider_id: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None

    @classmethod
    def from_row(cls, row: Conversation) -> ConversationOut:
        return cls(
            id=str(row.id),
            title=row.title,
            archived=row.archived,
            pinned=row.pinned,
            provider_id=row.provider_id,
            model=row.model,
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_message_at=row.last_message_at,
        )


class ConversationListOut(BaseModel):
    items: list[ConversationOut]
    total: int


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    status: Literal["complete", "streaming", "error", "cancelled"]
    provider_id: str | None
    model: str | None
    task_id: str | None
    meta: dict[str, Any]
    created_at: datetime
    edited_at: datetime | None

    @classmethod
    def from_row(cls, row: Message) -> MessageOut:
        return cls(
            id=str(row.id),
            conversation_id=str(row.conversation_id),
            role=row.role,  # type: ignore[arg-type]  - validated by the database writer
            content=row.content,
            status=row.status,  # type: ignore[arg-type]
            provider_id=row.provider_id,
            model=row.model,
            task_id=str(row.task_id) if row.task_id else None,
            meta=dict(row.meta or {}),
            created_at=row.created_at,
            edited_at=row.edited_at,
        )


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class TaskDeviceOut(BaseModel):
    id: str
    name: str
    connection_status: Literal["online", "offline", "revoked"]


class TaskActionsOut(BaseModel):
    approve: bool
    deny: bool
    cancel: bool
    retry: bool


class TaskOut(BaseModel):
    """Mirror of TaskOut in docs/api.md, as embedded in ``tool_proposed``."""

    id: str
    title: str
    description: str
    source: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: str
    status: str
    device: TaskDeviceOut | None
    conversation_id: str | None
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
    actions: TaskActionsOut


def task_out(task: Task, device: TaskDeviceOut | None = None) -> TaskOut:
    status = task.status
    return TaskOut(
        id=str(task.id),
        title=task.title,
        description=task.description or "",
        source=task.source,
        tool_name=task.tool_name,
        arguments=dict(task.arguments or {}),
        risk_level=task.risk_level,
        status=status,
        device=device,
        conversation_id=str(task.conversation_id) if task.conversation_id else None,
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
        actions=TaskActionsOut(
            approve=status == "awaiting_approval",
            deny=status == "awaiting_approval",
            cancel=status in ("queued", "running", "awaiting_approval"),
            retry=status in ("failed", "cancelled"),
        ),
    )


def device_out(device_id: uuid.UUID, name: str, *, revoked: bool, online: bool | None) -> TaskDeviceOut:
    """Connection status is derived: revoked beats online, and an unknown hub reads as offline."""
    if revoked:
        status: Literal["online", "offline", "revoked"] = "revoked"
    elif online:
        status = "online"
    else:
        status = "offline"
    return TaskDeviceOut(id=str(device_id), name=name, connection_status=status)


def candidates_out(raw: Any) -> list[dict[str, Any]]:
    """Normalises ``needs_device_selection`` candidates to ``{id, name, connection_status}``."""
    if not isinstance(raw, list):
        return []
    clean: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping) and "id" in item and "name" in item:
            clean.append(
                {
                    "id": str(item["id"]),
                    "name": str(item["name"]),
                    "connection_status": str(item.get("connection_status", "offline")),
                }
            )
    return clean
