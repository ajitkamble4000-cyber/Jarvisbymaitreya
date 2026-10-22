"""Notification inbox: list with unread count, mark read, read all, dismiss.

The list hides rows whose kind the user turned off in ``notifications`` settings. Hiding is not deleting:
the rows stay and reappear when the setting is turned back on. ``notify()`` writes every kind regardless.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response
from sqlalchemy import ColumnElement, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.core.time import utc_now
from app.models.platform import Notification
from app.schemas.notifications import NotificationListOut, NotificationOut, ReadAllOut, notification_out
from app.schemas.settings import NotificationSettings
from app.services.settings_service import load_settings

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Kinds with a preference. Each name is a field of NotificationSettings. Other kinds (reminder, late_result,
# and anything not listed) are always shown.
_EXACT_KIND_SETTINGS: dict[str, str] = {
    "task_completed": "on_task_completed",
    "task_failed": "on_task_failed",
    "approval_required": "on_approval_required",
    "device_disconnected": "on_device_disconnected",
    "integration_error": "on_integration_error",
}
_PREFIX_KIND_SETTINGS: dict[str, str] = {
    "schedule": "on_schedule_event",
    "security": "on_security_event",
    "auth": "on_security_event",
}


class NotificationInboxOut(NotificationListOut):
    """The list response. ``hidden_by_preferences`` counts this user's undismissed rows the preferences hide."""

    hidden_by_preferences: int


def _hidden_by_preferences(prefs: NotificationSettings) -> ColumnElement[bool] | None:
    """SQL condition that matches the rows the preferences hide, or None when no kind is turned off."""
    mapped = {*_EXACT_KIND_SETTINGS.values(), *_PREFIX_KIND_SETTINGS.values()}
    disabled = {setting for setting in mapped if not getattr(prefs, setting)}
    exact = [kind for kind, setting in _EXACT_KIND_SETTINGS.items() if setting in disabled]
    terms: list[ColumnElement[bool]] = []
    if exact:
        terms.append(Notification.kind.in_(exact))
    terms.extend(
        Notification.kind.startswith(prefix) for prefix, setting in _PREFIX_KIND_SETTINGS.items() if setting in disabled
    )
    return or_(*terms) if terms else None


def _parse_notification_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Notification not found.", code="notification_not_found") from exc


async def _owned_visible(db: AsyncSession, user_id: uuid.UUID, raw_id: str) -> Notification:
    row = await db.scalar(
        select(Notification).where(
            Notification.id == _parse_notification_id(raw_id),
            Notification.user_id == user_id,
            Notification.dismissed_at.is_(None),
        )
    )
    if row is None:
        raise NotFoundError("Notification not found.", code="notification_not_found")
    return row


@router.get("", response_model=NotificationInboxOut)
async def list_notifications(
    db: DbSession,
    user: CurrentUser,
    unread: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> NotificationInboxOut:
    prefs = (await load_settings(db, user.id)).notifications
    hidden = _hidden_by_preferences(prefs)
    owned = [Notification.user_id == user.id, Notification.dismissed_at.is_(None)]
    visible = owned if hidden is None else [*owned, not_(hidden)]
    stmt = select(Notification).where(*visible)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id).limit(limit))).all()
    unread_count = await db.scalar(
        select(func.count(Notification.id)).where(*visible, Notification.read_at.is_(None))
    )
    hidden_count = 0
    if hidden is not None:
        hidden_count = int(
            await db.scalar(select(func.count(Notification.id)).where(*owned, hidden)) or 0
        )
    return NotificationInboxOut(
        items=[notification_out(row) for row in rows],
        unread_count=int(unread_count or 0),
        hidden_by_preferences=hidden_count,
    )


@router.post("/read-all", response_model=ReadAllOut)
async def read_all(db: DbSession, user: CurrentUser) -> ReadAllOut:
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.dismissed_at.is_(None),
            Notification.read_at.is_(None),
        )
        .values(read_at=utc_now())
    )
    await db.commit()
    return ReadAllOut(updated=int(result.rowcount or 0))


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(notification_id: str, db: DbSession, user: CurrentUser) -> NotificationOut:
    row = await _owned_visible(db, user.id, notification_id)
    if row.read_at is None:
        row.read_at = utc_now()
        await db.commit()
    return notification_out(row)


@router.delete("/{notification_id}", status_code=204)
async def dismiss(notification_id: str, db: DbSession, user: CurrentUser) -> Response:
    """Dismiss hides the row from the inbox. Dismissing twice is harmless."""
    row = await db.scalar(
        select(Notification).where(
            Notification.id == _parse_notification_id(notification_id),
            Notification.user_id == user.id,
        )
    )
    if row is None:
        raise NotFoundError("Notification not found.", code="notification_not_found")
    if row.dismissed_at is None:
        row.dismissed_at = utc_now()
        await db.commit()
    return Response(status_code=204)
