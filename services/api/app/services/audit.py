"""Audit trail writer. Callers add the event to their own transaction."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.platform import AuditEvent

_SENSITIVE_MARKERS = ("password", "secret", "token", "key", "cookie", "credential", "code")


def sanitize_audit_data(data: dict[str, Any] | None) -> dict[str, Any]:
    """Drop any field whose name suggests a credential. Values are never logged raw."""
    if not data:
        return {}
    clean: dict[str, Any] = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            continue
        if isinstance(value, dict):
            clean[key] = sanitize_audit_data(value)
        else:
            clean[key] = value
    return clean


def record_audit(
    db: AsyncSession,
    *,
    action: str,
    outcome: str = "success",
    user_id: UUID | None = None,
    actor_type: str = "user",
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    data: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            ip_address=ip_address,
            data=sanitize_audit_data(data),
            created_at=utc_now(),
        )
    )
