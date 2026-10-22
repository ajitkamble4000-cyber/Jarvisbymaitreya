"""Time helpers. All persisted timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime | None) -> str | None:
    """Serialise a datetime as RFC 3339 with a trailing ``Z`` (UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
