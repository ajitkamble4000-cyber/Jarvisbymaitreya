"""Audit and security event log. Each user sees only the events recorded for their own account."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import ValidationAppError
from app.models.platform import AuditEvent
from app.schemas.platform import AuditItemOut, AuditListOut, AuditOutcome
from app.services.audit import sanitize_audit_data
from app.services.csv_export import to_csv

router = APIRouter(prefix="/audit", tags=["audit"])

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


# The CSV leaves out the IP address and the event data: both can identify a device or hold detail that is not needed
# to read the trail.
AUDIT_CSV_COLUMNS: tuple[str, ...] = ("created_at", "actor_type", "actor_id", "action", "target_type", "target_id", "outcome")


def _time_range(since: datetime | None, until: datetime | None) -> tuple[datetime | None, datetime | None]:
    """Both ends are inclusive. A time without a zone is read as UTC. The start must not be after the end."""
    since = since.replace(tzinfo=UTC) if since is not None and since.tzinfo is None else since
    until = until.replace(tzinfo=UTC) if until is not None and until.tzinfo is None else until
    if since is not None and until is not None and since > until:
        raise ValidationAppError("The start time is after the end time.", code="invalid_range")
    return since, until


def _conditions(
    user_id: object,
    action: str | None,
    outcome: AuditOutcome | None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list:
    conditions = [AuditEvent.user_id == user_id]
    if action:
        # Prefix match. autoescape keeps % and _ in the filter as literal characters.
        conditions.append(AuditEvent.action.startswith(action, autoescape=True))
    if outcome is not None:
        conditions.append(AuditEvent.outcome == outcome)
    if since is not None:
        conditions.append(AuditEvent.created_at >= since)
    if until is not None:
        conditions.append(AuditEvent.created_at <= until)
    return conditions


@router.get("/export.csv")
async def export_audit_csv(
    db: DbSession,
    user: CurrentUser,
    action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> Response:
    start, end = _time_range(since, until)
    rows = await db.scalars(
        select(AuditEvent)
        .where(*_conditions(user.id, action, outcome, start, end))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    body = to_csv(
        AUDIT_CSV_COLUMNS,
        [
            (
                row.created_at.isoformat(),
                row.actor_type,
                row.actor_id,
                row.action,
                row.target_type,
                row.target_id,
                row.outcome,
            )
            for row in rows.all()
        ],
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="jarvis-audit.csv"', "Cache-Control": "no-store"},
    )


@router.get("", response_model=AuditListOut)
async def list_audit(
    db: DbSession,
    user: CurrentUser,
    action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AuditListOut:
    start, end = _time_range(since, until)
    conditions = _conditions(user.id, action, outcome, start, end)

    total = await db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
    rows = await db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        AuditItemOut(
            id=row.id,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            outcome=row.outcome,
            data=sanitize_audit_data(row.data),
            created_at=row.created_at,
        )
        for row in rows.all()
    ]
    return AuditListOut(items=items, total=int(total or 0))
