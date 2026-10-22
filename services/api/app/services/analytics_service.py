"""Operational analytics for one user, computed from stored rows only.

Every number comes from a query over tasks, devices, usage records or integrations. When a value
cannot be measured (no finished tasks, no latency samples, no reported tokens) it is ``None`` with
``source: unavailable``. Counts that really are zero stay ``0``. Nothing is estimated from a model.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.integrations.catalog import implemented_count
from app.models.ai import UsageRecord
from app.models.devices import Device
from app.models.platform import Integration
from app.models.tasks import Task
from app.schemas.platform import (
    AiMetrics,
    AnalyticsSections,
    AnalyticsSummaryOut,
    CostMetrics,
    DeviceMetrics,
    ErrorMetrics,
    IntegrationMetrics,
    LatencyMetrics,
    Metric,
    ProviderBreakdown,
    TaskMetrics,
)
from app.services.device_resolver import OnlineChecker, connection_status

_TOP_ERROR_CODES = 10
_NO_PRICING_NOTE = "No pricing table is configured"
_UNMEASURED_TOKENS_NOTE = "No provider reported token usage in this period"
_ESTIMATED_TOKENS_NOTE = "Some counts are estimated locally because the provider did not report usage"


def measured(value: float | int | list[dict[str, Any]], unit: str, note: str | None = None) -> Metric:
    return Metric(value=value, unit=unit, source="measured", note=note)


def unavailable(unit: str, note: str) -> Metric:
    return Metric(value=None, unit=unit, source="unavailable", note=note)


@dataclass
class _UsageAggregate:
    """Totals over a set of usage rows. Sums stay ``None`` when no row carried a measured value."""

    requests: int = 0
    failures: int = 0
    latency_sum: int = 0
    latency_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_total_tokens: int | None = None
    estimated_rows: int = 0

    @classmethod
    def from_row(cls, row: Any) -> _UsageAggregate:
        return cls(
            requests=int(row.requests or 0),
            failures=int(row.failures or 0),
            latency_sum=int(row.latency_sum or 0),
            latency_count=int(row.latency_count or 0),
            input_tokens=_opt_int(row.input_tokens),
            output_tokens=_opt_int(row.output_tokens),
            total_tokens=_opt_int(row.total_tokens),
            estimated_total_tokens=_opt_int(row.estimated_total_tokens),
            estimated_rows=int(row.estimated_rows or 0),
        )

    @property
    def avg_latency_ms(self) -> float | None:
        if self.latency_count == 0:
            return None
        return round(self.latency_sum / self.latency_count, 1)

    @property
    def estimated_share(self) -> float | None:
        if not self.total_tokens:
            return None
        return round((self.estimated_total_tokens or 0) / self.total_tokens, 4)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _token_metric(value: int | None, aggregate: _UsageAggregate) -> Metric:
    if value is None:
        return unavailable("tokens", _UNMEASURED_TOKENS_NOTE)
    if aggregate.estimated_rows > 0:
        return Metric(value=value, unit="tokens", source="estimated", note=_ESTIMATED_TOKENS_NOTE)
    return measured(value, "tokens")


def _usage_columns() -> list[Any]:
    measured_latency = UsageRecord.latency_ms > 0
    estimated = UsageRecord.tokens_estimated.is_(True)
    return [
        func.count().label("requests"),
        func.count().filter(UsageRecord.success.is_(False)).label("failures"),
        func.sum(UsageRecord.latency_ms).filter(measured_latency).label("latency_sum"),
        func.count().filter(measured_latency).label("latency_count"),
        func.sum(UsageRecord.input_tokens).label("input_tokens"),
        func.sum(UsageRecord.output_tokens).label("output_tokens"),
        func.sum(UsageRecord.total_tokens).label("total_tokens"),
        func.sum(UsageRecord.total_tokens).filter(estimated).label("estimated_total_tokens"),
        func.count().filter(estimated, UsageRecord.total_tokens.is_not(None)).label("estimated_rows"),
    ]


async def _task_metrics(db: AsyncSession, user_id: uuid.UUID, since: datetime) -> TaskMetrics:
    rows = await db.execute(
        select(Task.status, func.count())
        .where(Task.user_id == user_id, Task.created_at >= since)
        .group_by(Task.status)
    )
    counts: Counter[str] = Counter({status: int(total) for status, total in rows.all()})
    completed, failed = counts["completed"], counts["failed"]
    finished = completed + failed
    if finished:
        success_rate = measured(round(completed / finished, 4), "ratio", "completed / (completed + failed)")
    else:
        success_rate = unavailable("ratio", "No finished tasks in this period")
    scope_note = "Tasks created in this period"
    return TaskMetrics(
        total=measured(sum(counts.values()), "tasks", scope_note),
        completed=measured(completed, "tasks", scope_note),
        failed=measured(failed, "tasks", scope_note),
        cancelled=measured(counts["cancelled"], "tasks", scope_note),
        awaiting_approval=measured(counts["awaiting_approval"], "tasks", scope_note),
        success_rate=success_rate,
    )


async def _latency_metrics(db: AsyncSession, user_id: uuid.UUID, since: datetime) -> LatencyMetrics:
    duration = func.extract("epoch", Task.finished_at - Task.started_at)
    row = (
        await db.execute(
            select(func.avg(duration), func.count())
            .where(
                Task.user_id == user_id,
                Task.created_at >= since,
                Task.device_id.is_not(None),
                Task.started_at.is_not(None),
                Task.finished_at.is_not(None),
                Task.finished_at >= Task.started_at,
            )
        )
    ).one()
    average, samples = row[0], int(row[1] or 0)
    if samples == 0 or average is None:
        return LatencyMetrics(
            avg_device_task_seconds=unavailable("seconds", "No device task has both a start and a finish time")
        )
    return LatencyMetrics(
        avg_device_task_seconds=measured(
            round(float(average), 2), "seconds", f"Average over {samples} device task(s), from started_at to finished_at"
        )
    )


async def _ai_metrics(db: AsyncSession, user_id: uuid.UUID, since: datetime) -> AiMetrics:
    scope = (UsageRecord.user_id == user_id, UsageRecord.created_at >= since)
    total_row = (await db.execute(select(*_usage_columns()).where(*scope))).one()
    totals = _UsageAggregate.from_row(total_row)

    provider_rows = await db.execute(
        select(UsageRecord.provider_id, *_usage_columns())
        .where(*scope)
        .group_by(UsageRecord.provider_id)
        .order_by(UsageRecord.provider_id)
    )
    by_provider: list[ProviderBreakdown] = []
    for row in provider_rows.all():
        bucket = _UsageAggregate.from_row(row)
        by_provider.append(
            ProviderBreakdown(
                provider_id=str(row.provider_id),
                requests=measured(bucket.requests, "requests"),
                failures=measured(bucket.failures, "requests"),
                avg_latency_ms=_latency_metric(bucket),
                total_tokens=_token_metric(bucket.total_tokens, bucket),
            )
        )

    requests_note = "All provider calls recorded in this period: chat, streaming, connection tests and model listing"
    return AiMetrics(
        requests=measured(totals.requests, "requests", requests_note),
        failures=measured(totals.failures, "requests"),
        avg_latency_ms=_latency_metric(totals),
        input_tokens=_token_metric(totals.input_tokens, totals),
        output_tokens=_token_metric(totals.output_tokens, totals),
        total_tokens=_token_metric(totals.total_tokens, totals),
        tokens_estimated_share=_share_metric(totals),
        by_provider=by_provider,
    )


def _latency_metric(bucket: _UsageAggregate) -> Metric:
    average = bucket.avg_latency_ms
    if average is None:
        return unavailable("ms", "No provider call has a measured latency in this period")
    return measured(average, "ms", "Average over calls with a measured latency (greater than zero)")


def _share_metric(bucket: _UsageAggregate) -> Metric:
    share = bucket.estimated_share
    if share is None:
        return unavailable("ratio", _UNMEASURED_TOKENS_NOTE)
    return measured(share, "ratio", "Estimated total tokens divided by total tokens")


async def _device_metrics(db: AsyncSession, user_id: uuid.UUID, hub: OnlineChecker) -> DeviceMetrics:
    devices = (await db.scalars(select(Device).where(Device.user_id == user_id))).all()
    statuses = Counter(connection_status(device, hub) for device in devices)
    return DeviceMetrics(
        total=measured(len(devices), "devices", "All devices registered to this account"),
        online=measured(statuses["online"], "devices", "Live connection state at the time of the request"),
        offline=measured(statuses["offline"], "devices", "Live connection state at the time of the request"),
        revoked=measured(statuses["revoked"], "devices"),
    )


async def _integration_metrics(db: AsyncSession, user_id: uuid.UUID) -> IntegrationMetrics:
    connected = await db.scalar(
        select(func.count()).select_from(Integration).where(Integration.user_id == user_id, Integration.status == "connected")
    )
    return IntegrationMetrics(
        connected=measured(int(connected or 0), "integrations", "Integrations with a connected status"),
        implemented=measured(
            implemented_count(), "integrations", "No integration is implemented yet, so none can be connected"
        ),
    )


async def _error_metrics(db: AsyncSession, user_id: uuid.UUID, since: datetime) -> ErrorMetrics:
    code = Task.error["code"].as_string()
    task_rows = await db.execute(
        select(code.label("code"), func.count().label("count"))
        .where(
            Task.user_id == user_id,
            Task.created_at >= since,
            Task.error.is_not(None),
            code.is_not(None),
        )
        .group_by(code)
        .order_by(func.count().desc(), code)
        .limit(_TOP_ERROR_CODES)
    )
    ai_rows = await db.execute(
        select(UsageRecord.error_code.label("code"), func.count().label("count"))
        .where(
            UsageRecord.user_id == user_id,
            UsageRecord.created_at >= since,
            UsageRecord.success.is_(False),
            UsageRecord.error_code.is_not(None),
        )
        .group_by(UsageRecord.error_code)
        .order_by(func.count().desc(), UsageRecord.error_code)
        .limit(_TOP_ERROR_CODES)
    )
    return ErrorMetrics(
        task_error_codes=measured(
            [{"code": str(row.code), "count": int(row.count)} for row in task_rows.all()],
            "count",
            "Task failures by error.code, most frequent first",
        ),
        ai_error_codes=measured(
            [{"code": str(row.code), "count": int(row.count)} for row in ai_rows.all()],
            "count",
            "Failed provider calls by error code, most frequent first",
        ),
    )


async def build_summary(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    days: int,
    hub: OnlineChecker,
    now: datetime | None = None,
) -> AnalyticsSummaryOut:
    """Summary for the last ``days`` days, scoped to ``user_id``. Read-only: nothing is written."""
    current = now or utc_now()
    since = current - timedelta(days=days)
    sections = AnalyticsSections(
        tasks=await _task_metrics(db, user_id, since),
        latency=await _latency_metrics(db, user_id, since),
        ai=await _ai_metrics(db, user_id, since),
        cost=CostMetrics(estimated_usd=unavailable("usd", _NO_PRICING_NOTE)),
        devices=await _device_metrics(db, user_id, hub),
        integrations=await _integration_metrics(db, user_id),
        errors=await _error_metrics(db, user_id, since),
    )
    return AnalyticsSummaryOut(period_days=days, generated_at=current, sections=sections)
