"""Records every provider call so analytics and the AI Models page show measured values."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.ai import UsageRecord
from app.providers.base import TokenUsage

# The operations that count against the daily call limit: each one is a provider call made for a chat reply.
CHAT_OPERATIONS = ("chat", "stream")


def utc_day_start(now: datetime) -> datetime:
    """Midnight UTC at the start of the day that ``now`` falls in. The daily call limit resets at this instant."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def chat_calls_since(db: AsyncSession, *, user_id: UUID, since: datetime) -> int:
    """Provider calls made for chat replies at or after ``since``. A refused turn makes no call, so it is never counted."""
    used = await db.scalar(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.user_id == user_id,
            UsageRecord.created_at >= since,
            UsageRecord.operation.in_(CHAT_OPERATIONS),
        )
    )
    return int(used or 0)


def record_usage(
    db: AsyncSession,
    *,
    user_id: UUID,
    provider_id: str,
    model: str,
    operation: str,
    success: bool,
    latency_ms: int,
    error_code: str | None = None,
    usage: TokenUsage | None = None,
) -> None:
    """Adds a usage row to the session. Token counts are stored only when the provider reported or we estimated them."""
    db.add(
        UsageRecord(
            user_id=user_id,
            created_at=utc_now(),
            provider_id=provider_id,
            model=model[:200],
            operation=operation,
            success=success,
            error_code=error_code,
            latency_ms=max(0, int(latency_ms)),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            tokens_estimated=bool(usage and usage.estimated),
            estimated_cost_usd=None,
        )
    )
