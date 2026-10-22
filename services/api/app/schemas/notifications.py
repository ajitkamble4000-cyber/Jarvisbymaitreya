"""Request and response models for the notifications API (docs/api.md "Notifications")."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.platform import Notification


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    severity: str
    title: str
    body: str
    related_type: str | None
    related_id: str | None
    read: bool
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class ReadAllOut(BaseModel):
    updated: int


def notification_out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        kind=row.kind,
        severity=row.severity,
        title=row.title,
        body=row.body,
        related_type=row.related_type,
        related_id=row.related_id,
        read=row.read_at is not None,
        created_at=row.created_at,
    )
