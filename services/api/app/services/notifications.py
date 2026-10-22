"""Notification writer. Callers decide whether a notification is wanted; the UI applies display preferences."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.platform import Notification
from app.services.events import EventBus

SEVERITIES = ("info", "success", "warning", "error")


def notify(
    db: AsyncSession,
    events: EventBus,
    *,
    user_id: UUID,
    kind: str,
    severity: str,
    title: str,
    body: str = "",
    related_type: str | None = None,
    related_id: str | None = None,
) -> Notification:
    """Adds a notification to the session (caller commits) and publishes ``notification.created``.

    The publish happens immediately, so callers should commit before expecting the row to be readable.
    Keep ``title``/``body`` free of private content: they can appear in desktop notifications.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown notification severity: {severity}")
    row = Notification(
        user_id=user_id,
        kind=kind,
        severity=severity,
        title=title[:200],
        body=body[:2000],
        related_type=related_type,
        related_id=related_id,
        created_at=utc_now(),
    )
    db.add(row)
    events.publish(user_id, "notification.created", {"kind": kind, "severity": severity})
    return row
