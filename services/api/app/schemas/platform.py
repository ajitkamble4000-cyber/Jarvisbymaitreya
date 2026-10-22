"""Response models for the platform read APIs: audit, analytics, explore, integrations and the developer console.

Analytics values use the metric shape from docs/api.md: ``{value, unit, source, note}``. A value that
cannot be measured is ``value: null`` with ``source: unavailable``. It is never replaced by a zero.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

MetricSource = Literal["measured", "estimated", "unavailable"]
ExploreStatus = Literal["working", "requires_configuration", "experimental", "unsupported"]
CheckStatus = Literal["ok", "warning", "error"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AuditOutcome = Literal["success", "failure", "denied"]


# -- audit --------------------------------------------------------------------


class AuditItemOut(BaseModel):
    id: int
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    data: dict[str, Any]
    created_at: datetime


class AuditListOut(BaseModel):
    items: list[AuditItemOut]
    total: int


# -- analytics ----------------------------------------------------------------


class Metric(BaseModel):
    value: float | int | list[dict[str, Any]] | None
    unit: str
    source: MetricSource
    note: str | None = None


class TaskMetrics(BaseModel):
    total: Metric
    completed: Metric
    failed: Metric
    cancelled: Metric
    awaiting_approval: Metric
    success_rate: Metric


class LatencyMetrics(BaseModel):
    avg_device_task_seconds: Metric


class ProviderBreakdown(BaseModel):
    provider_id: str
    requests: Metric
    failures: Metric
    avg_latency_ms: Metric
    total_tokens: Metric


class AiMetrics(BaseModel):
    requests: Metric
    failures: Metric
    avg_latency_ms: Metric
    input_tokens: Metric
    output_tokens: Metric
    total_tokens: Metric
    tokens_estimated_share: Metric
    by_provider: list[ProviderBreakdown]


class CostMetrics(BaseModel):
    estimated_usd: Metric


class DeviceMetrics(BaseModel):
    total: Metric
    online: Metric
    offline: Metric
    revoked: Metric


class IntegrationMetrics(BaseModel):
    connected: Metric
    implemented: Metric


class ErrorMetrics(BaseModel):
    task_error_codes: Metric
    ai_error_codes: Metric


class AnalyticsSections(BaseModel):
    tasks: TaskMetrics
    latency: LatencyMetrics
    ai: AiMetrics
    cost: CostMetrics
    devices: DeviceMetrics
    integrations: IntegrationMetrics
    errors: ErrorMetrics


class AnalyticsSummaryOut(BaseModel):
    period_days: int
    generated_at: datetime
    sections: AnalyticsSections


class CallsTodayOut(BaseModel):
    """Chat provider calls made today (UTC), against the daily call limit. ``limit`` is null when none is set."""

    calls: int
    limit: int | None
    resets_at: datetime


# -- explore and tools --------------------------------------------------------


class ExploreItemOut(BaseModel):
    id: str
    title: str
    category: str
    status: ExploreStatus
    description: str
    example_commands: list[str]
    requirements: list[str]
    enabled_for_user: bool


class ExploreOut(BaseModel):
    categories: list[str]
    items: list[ExploreItemOut]


class ToolOut(BaseModel):
    name: str
    title: str
    description: str
    category: str
    risk_level: str
    target: str
    requires_device: bool
    approval_required: bool
    status: str
    example_commands: list[str]
    notes: str
    parameters: dict[str, Any]


class ToolListOut(BaseModel):
    items: list[ToolOut]


# -- integrations -------------------------------------------------------------


class IntegrationOut(BaseModel):
    id: str
    name: str
    category: str
    status: str
    implemented: bool
    requires_configuration: list[str]
    scopes_requested: list[str]
    docs: str


class IntegrationListOut(BaseModel):
    items: list[IntegrationOut]


# -- developer console --------------------------------------------------------


class VersionOut(BaseModel):
    app_version: str
    python_version: str
    environment: str
    protocol_version: int
    background_workers: bool


class LogEntryOut(BaseModel):
    ts: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    task_id: str | None = None
    device_id: str | None = None
    provider_id: str | None = None


class LogsOut(BaseModel):
    items: list[LogEntryOut]


class ConfigCheckOut(BaseModel):
    key: str
    status: CheckStatus
    message: str


class ConfigCheckListOut(BaseModel):
    checks: list[ConfigCheckOut]


__all__ = [
    "AnalyticsSummaryOut",
    "AuditItemOut",
    "AuditListOut",
    "AuditOutcome",
    "CheckStatus",
    "ConfigCheckListOut",
    "ConfigCheckOut",
    "ExploreItemOut",
    "ExploreOut",
    "IntegrationListOut",
    "IntegrationOut",
    "LogEntryOut",
    "LogLevel",
    "LogsOut",
    "Metric",
    "ToolListOut",
    "ToolOut",
    "VersionOut",
]
