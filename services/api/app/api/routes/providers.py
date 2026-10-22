"""AI providers: configuration, credentials, connection tests, live models and usage statistics.

Routes commit explicitly. Usage rows written by a failed upstream call are committed before the
error is raised, so failures are counted in the usage statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError, ConflictError, ValidationAppError
from app.core.time import utc_now
from app.models.ai import ProviderConfig, UsageRecord
from app.providers.base import AIProvider, ProviderError, ProviderErrorCode
from app.providers.registry import ProviderRegistry
from app.schemas.providers import (
    CredentialIn,
    ModelListOut,
    ModelOut,
    ProviderCapabilitiesOut,
    ProviderLastErrorOut,
    ProviderListOut,
    ProviderOut,
    ProviderTestOut,
    ProviderUpdate,
    UsageErrorOut,
    UsageOut,
    UsageRowOut,
    UsageTotalsOut,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/providers", tags=["providers"])

_COST_NOTE = "No pricing table is configured, so the cost of these calls is not estimated."


def _registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


RegistryDep = Annotated[ProviderRegistry, Depends(_registry)]


def _provider_out(provider_cls: type[AIProvider], config: ProviderConfig, registry: ProviderRegistry) -> ProviderOut:
    capabilities = registry.capabilities_for(provider_cls)
    last_error = (
        ProviderLastErrorOut(code=config.last_error_code, message=config.last_error_message or "")
        if config.last_error_code
        else None
    )
    return ProviderOut(
        id=provider_cls.provider_id,
        display_name=provider_cls.display_name,
        enabled=config.enabled,
        configured=bool(config.credential_ciphertext),
        credential_hint=config.credential_hint,
        selected_model=config.selected_model,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        status=config.status,
        last_tested_at=config.last_tested_at,
        last_latency_ms=config.last_latency_ms,
        last_error=last_error,
        capabilities=ProviderCapabilitiesOut(
            streaming=capabilities.streaming,
            tool_calling=capabilities.tool_calling,
            multimodal_input=capabilities.multimodal_input,
            usage_reporting=capabilities.usage_reporting,
            model_listing=capabilities.model_listing,
        ),
    )


def _upstream_error(exc: ProviderError) -> AppError:
    """Maps a provider failure to an HTTP error. Missing credentials are a 409; upstream failures are a 502."""
    if exc.code is ProviderErrorCode.NOT_CONFIGURED:
        return ConflictError(exc.message, code="not_configured")
    return AppError(exc.message, code=exc.code.value, status_code=502, details={"retryable": exc.retryable})


# -- configuration ------------------------------------------------------------


@router.get("", response_model=ProviderListOut)
async def list_providers(user: CurrentUser, db: DbSession, registry: RegistryDep) -> ProviderListOut:
    rows = await registry.ensure_configs(db, user.id)
    await db.commit()
    return ProviderListOut(
        items=[_provider_out(provider_cls, rows[provider_cls.provider_id], registry) for provider_cls in registry.catalog()]
    )


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
) -> ProviderOut:
    provider_cls = registry.provider_class(provider_id)
    config = await registry.get_config(db, user.id, provider_id)
    configured = bool(config.credential_ciphertext)

    if body.enabled is True and not configured:
        raise ConflictError("Save an API key before enabling this provider.", code="not_configured")

    if "selected_model" in body.model_fields_set and body.selected_model is not None and configured:
        # A model that is being set must come from the live list. An unconfigured provider stores it unchecked.
        try:
            models = await registry.list_models(db, user.id, provider_id)
        except ProviderError as exc:
            await db.commit()
            raise _upstream_error(exc) from exc
        await db.commit()
        if body.selected_model not in {model.id for model in models}:
            raise ValidationAppError("Choose a model from the live model list.", code="model_not_listed")

    changed: list[str] = []
    if "selected_model" in body.model_fields_set and body.selected_model != config.selected_model:
        config.selected_model = body.selected_model
        changed.append("selected_model")
    if body.enabled is not None and body.enabled != config.enabled:
        config.enabled = body.enabled
        changed.append("enabled")
    if body.timeout_seconds is not None and body.timeout_seconds != config.timeout_seconds:
        config.timeout_seconds = body.timeout_seconds
        changed.append("timeout_seconds")
    if body.max_retries is not None and body.max_retries != config.max_retries:
        config.max_retries = body.max_retries
        changed.append("max_retries")

    if changed:
        record_audit(
            db,
            action="provider.updated",
            user_id=user.id,
            target_type="provider",
            target_id=provider_id,
            data={"provider_id": provider_id, "changed": changed},
        )
    await db.commit()
    return _provider_out(provider_cls, config, registry)


@router.put("/{provider_id}/credential", response_model=ProviderOut)
async def set_credential(
    provider_id: str,
    body: CredentialIn,
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
) -> ProviderOut:
    provider_cls = registry.provider_class(provider_id)
    config = await registry.set_credential(db, user.id, provider_id, body.api_key)
    await db.commit()
    return _provider_out(provider_cls, config, registry)


@router.delete("/{provider_id}/credential", response_model=ProviderOut)
async def clear_credential(
    provider_id: str,
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
) -> ProviderOut:
    provider_cls = registry.provider_class(provider_id)
    config = await registry.clear_credential(db, user.id, provider_id)
    await db.commit()
    return _provider_out(provider_cls, config, registry)


# -- calls --------------------------------------------------------------------


@router.post("/{provider_id}/test", response_model=ProviderTestOut)
async def test_provider(
    provider_id: str,
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
) -> ProviderTestOut:
    provider_cls = registry.provider_class(provider_id)
    outcome, config = await registry.test_connection(db, user.id, provider_id)
    await db.commit()
    return ProviderTestOut(
        ok=outcome.ok,
        latency_ms=outcome.latency_ms,
        message=outcome.message,
        error_code=outcome.error_code,
        account_label=outcome.account_label,
        provider=_provider_out(provider_cls, config, registry),
    )


@router.get("/{provider_id}/models", response_model=ModelListOut)
async def list_models(
    provider_id: str,
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
) -> ModelListOut:
    registry.provider_class(provider_id)
    try:
        models = await registry.list_models(db, user.id, provider_id)
    except ProviderError as exc:
        await db.commit()
        raise _upstream_error(exc) from exc
    await db.commit()
    return ModelListOut(items=[ModelOut(**model.model_dump()) for model in models])


# -- usage --------------------------------------------------------------------


@dataclass
class _Bucket:
    """Running totals for a set of usage rows. Unmeasured values stay None rather than becoming zero."""

    requests: int = 0
    successes: int = 0
    failures: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tokens_estimated: bool = False
    latency_sum: int = 0
    latency_count: int = 0

    def add(self, row: Any) -> None:
        self.requests += int(row.requests)
        self.successes += int(row.successes)
        self.failures += int(row.failures)
        self.input_tokens = _sum_optional(self.input_tokens, row.input_tokens)
        self.output_tokens = _sum_optional(self.output_tokens, row.output_tokens)
        self.total_tokens = _sum_optional(self.total_tokens, row.total_tokens)
        self.tokens_estimated = self.tokens_estimated or bool(row.tokens_estimated)
        self.latency_sum += int(row.latency_sum or 0)
        self.latency_count += int(row.latency_count or 0)

    @property
    def avg_latency_ms(self) -> float | None:
        if self.latency_count == 0:
            return None
        return round(self.latency_sum / self.latency_count, 1)


def _sum_optional(current: int | None, value: Any) -> int | None:
    """Adds a measured SQL aggregate to a running total. NULL (nothing measured) leaves the total unchanged."""
    if value is None:
        return current
    if current is None:
        return int(value)
    return current + int(value)


def _row_out(
    provider_id: str,
    model: str | None,
    bucket: _Bucket,
    display_names: dict[str, str],
) -> UsageRowOut:
    return UsageRowOut(
        provider_id=provider_id,
        display_name=display_names.get(provider_id),
        model=model,
        requests=bucket.requests,
        successes=bucket.successes,
        failures=bucket.failures,
        input_tokens=bucket.input_tokens,
        output_tokens=bucket.output_tokens,
        total_tokens=bucket.total_tokens,
        tokens_estimated=bucket.tokens_estimated,
        avg_latency_ms=bucket.avg_latency_ms,
    )


@router.get("/usage", response_model=UsageOut)
async def usage_statistics(
    user: CurrentUser,
    db: DbSession,
    registry: RegistryDep,
    days: int = Query(default=30, ge=1, le=365),
) -> UsageOut:
    """Aggregates this user's usage records only. Averages use measured latencies (greater than zero) only."""
    now = utc_now()
    since = now - timedelta(days=days)
    scope = (UsageRecord.user_id == user.id, UsageRecord.created_at >= since)
    measured = UsageRecord.latency_ms > 0

    model_rows = (
        await db.execute(
            select(
                UsageRecord.provider_id.label("provider_id"),
                UsageRecord.model.label("model"),
                func.count().label("requests"),
                func.count().filter(UsageRecord.success.is_(True)).label("successes"),
                func.count().filter(UsageRecord.success.is_(False)).label("failures"),
                func.sum(UsageRecord.input_tokens).label("input_tokens"),
                func.sum(UsageRecord.output_tokens).label("output_tokens"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.bool_or(UsageRecord.tokens_estimated)
                .filter(UsageRecord.total_tokens.is_not(None))
                .label("tokens_estimated"),
                func.sum(UsageRecord.latency_ms).filter(measured).label("latency_sum"),
                func.count().filter(measured).label("latency_count"),
            )
            .where(*scope)
            .group_by(UsageRecord.provider_id, UsageRecord.model)
        )
    ).all()

    error_rows = (
        await db.execute(
            select(UsageRecord.error_code, func.count().label("count"))
            .where(
                *scope,
                UsageRecord.success.is_(False),
                UsageRecord.error_code.is_not(None),
            )
            .group_by(UsageRecord.error_code)
            .order_by(func.count().desc(), UsageRecord.error_code)
        )
    ).all()

    display_names = {provider_cls.provider_id: provider_cls.display_name for provider_cls in registry.catalog()}
    totals = _Bucket()
    by_provider: dict[str, _Bucket] = {}
    by_model: dict[tuple[str, str], _Bucket] = {}
    for row in model_rows:
        totals.add(row)
        by_provider.setdefault(row.provider_id, _Bucket()).add(row)
        by_model.setdefault((row.provider_id, row.model), _Bucket()).add(row)

    provider_items = sorted(by_provider.items(), key=lambda item: (-item[1].requests, item[0]))
    model_items = sorted(by_model.items(), key=lambda item: (-item[1].requests, item[0]))
    return UsageOut(
        period_days=days,
        generated_at=now,
        totals=UsageTotalsOut(
            requests=totals.requests,
            successes=totals.successes,
            failures=totals.failures,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            total_tokens=totals.total_tokens,
            tokens_estimated=totals.tokens_estimated,
        ),
        avg_latency_ms=totals.avg_latency_ms,
        by_provider=[_row_out(pid, None, bucket, display_names) for pid, bucket in provider_items],
        by_model=[_row_out(pid, model, bucket, display_names) for (pid, model), bucket in model_items],
        error_frequency=[UsageErrorOut(code=row.error_code, count=int(row.count)) for row in error_rows],
        cost_note=_COST_NOTE,
    )
