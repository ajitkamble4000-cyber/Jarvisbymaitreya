"""Developer console: version, redacted logs, configuration checks and task traces. Login required.

Nothing here returns a secret value. Configuration checks report presence and shape only.
"""

from __future__ import annotations

import logging
import platform
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import CurrentUser, DbSession, require_owner
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.logging import redact, ring_buffer
from app.devices.protocol import PROTOCOL_VERSION
from app.models.identity import User
from app.models.ai import ProviderConfig
from app.models.devices import Device
from app.models.tasks import Task, TaskEvent
from app.providers.registry import ProviderRegistry
from app.schemas.platform import (
    CheckStatus,
    ConfigCheckListOut,
    ConfigCheckOut,
    LogEntryOut,
    LogLevel,
    LogsOut,
    VersionOut,
)
from app.schemas.tasks import TaskDetailOut, task_event_out, task_out
from app.services.device_resolver import connection_status, paired_devices

router = APIRouter(prefix="/developer", tags=["developer"])

_MAX_TRACE_EVENTS = 500
_MAX_LOG_LIMIT = 1000

Limit = Annotated[int, Query(ge=1, le=_MAX_LOG_LIMIT)]


@router.get("/version", response_model=VersionOut)
async def version(request: Request, user: CurrentUser) -> VersionOut:
    settings: Settings = request.app.state.settings
    return VersionOut(
        app_version=__version__,
        python_version=platform.python_version(),
        environment=settings.jarvis_env,
        protocol_version=PROTOCOL_VERSION,
        background_workers=settings.run_background_workers,
    )


# The log buffer is process-wide and does not record which user a line belongs to, so only the owner reads it.
@router.get("/logs", response_model=LogsOut)
async def logs(
    _owner: Annotated[User, Depends(require_owner)],
    level: Annotated[LogLevel, Query()] = "INFO",
    limit: Limit = 200,
) -> LogsOut:
    min_level = getattr(logging, level)
    entries = ring_buffer.recent(limit, min_level)
    items = [
        LogEntryOut(
            ts=str(entry["ts"]),
            level=str(entry["level"]),
            logger=str(entry["logger"]),
            # Redacted when stored; redacted again here so a future change cannot leak through this route.
            message=redact(str(entry["message"])),
            request_id=_opt(entry.get("request_id")),
            task_id=_opt(entry.get("task_id")),
            device_id=_opt(entry.get("device_id")),
            provider_id=_opt(entry.get("provider_id")),
        )
        for entry in entries
    ]
    return LogsOut(items=items)


def _opt(value: object) -> str | None:
    return None if value is None else str(value)


def _check(key: str, status: CheckStatus, message: str) -> ConfigCheckOut:
    return ConfigCheckOut(key=key, status=status, message=message)


async def _database_check(db: AsyncSession) -> ConfigCheckOut:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means the database is not reachable
        return _check("database", "error", "The database did not answer SELECT 1.")
    return _check("database", "ok", "The database is reachable.")


async def _pgvector_check(db: AsyncSession) -> ConfigCheckOut:
    try:
        available = await db.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')")
        )
    except Exception:  # noqa: BLE001 - the catalogue query is informational only
        return _check("pgvector", "warning", "Could not query pg_available_extensions.")
    if available:
        return _check("pgvector", "ok", "The pgvector extension is available on this server.")
    return _check("pgvector", "warning", "The pgvector extension is not available. Semantic memory search stays off.")


def settings_checks(settings: Settings) -> list[ConfigCheckOut]:
    checks: list[ConfigCheckOut] = []
    if settings.encryption_key:
        checks.append(_check("encryption_key", "ok", "An ENCRYPTION_KEY is configured."))
    elif settings.is_production:
        checks.append(_check("encryption_key", "error", "ENCRYPTION_KEY is not set. Production refuses to start without it."))
    else:
        checks.append(_check("encryption_key", "warning", "No ENCRYPTION_KEY is set. A local key file in DATA_DIR is used."))

    if settings.is_production:
        if settings.cookie_secure:
            checks.append(_check("cookie_secure", "ok", "Session cookies are marked Secure."))
        else:
            checks.append(_check("cookie_secure", "error", "COOKIE_SECURE must be true in production."))
        if settings.public_base_url.startswith("https://"):
            checks.append(_check("public_base_url", "ok", "PUBLIC_BASE_URL uses https."))
        else:
            checks.append(_check("public_base_url", "error", "PUBLIC_BASE_URL must use https in production."))
    else:
        checks.append(_check("cookie_secure", "ok", "Not enforced outside production."))
        checks.append(_check("public_base_url", "ok", "Not enforced outside production."))

    if "*" in settings.allowed_origins:
        checks.append(_check("allowed_origins", "error", "ALLOWED_ORIGINS contains a wildcard. Wildcards are not permitted."))
    else:
        checks.append(_check("allowed_origins", "ok", "ALLOWED_ORIGINS contains no wildcard."))

    if settings.run_background_workers:
        checks.append(_check("background_workers", "ok", "Background workers are enabled."))
    else:
        checks.append(
            _check(
                "background_workers",
                "warning",
                "Background workers are disabled. Scheduled tasks and device heartbeats will not run.",
            )
        )
    return checks


async def _provider_checks(db: AsyncSession, user_id: uuid.UUID, providers: ProviderRegistry) -> list[ConfigCheckOut]:
    rows = await db.scalars(select(ProviderConfig).where(ProviderConfig.user_id == user_id))
    stored = {row.provider_id: row for row in rows.all()}
    checks: list[ConfigCheckOut] = []
    for provider_cls in providers.catalog():
        provider_id = str(provider_cls.provider_id)
        row = stored.get(provider_id)
        configured = row is not None and row.credential_ciphertext is not None
        key = f"provider.{provider_id}.credential"
        if configured:
            checks.append(_check(key, "ok", f"{provider_cls.display_name}: a credential is stored."))
        else:
            checks.append(_check(key, "warning", f"{provider_cls.display_name}: no credential is stored for this account."))
    return checks


@router.get("/config-check", response_model=ConfigCheckListOut)
async def config_check(request: Request, db: DbSession, owner: Annotated[User, Depends(require_owner)]) -> ConfigCheckListOut:
    settings: Settings = request.app.state.settings
    checks: list[ConfigCheckOut] = [await _database_check(db), await _pgvector_check(db)]
    checks.extend(settings_checks(settings))
    checks.extend(await _provider_checks(db, owner.id, request.app.state.providers))
    devices = await paired_devices(db, owner.id)
    if devices:
        checks.append(_check("paired_device", "ok", "At least one device is paired."))
    else:
        checks.append(_check("paired_device", "warning", "No device is paired yet."))
    return ConfigCheckListOut(checks=checks)


@router.get("/traces/{task_id}", response_model=TaskDetailOut)
async def trace(task_id: str, request: Request, db: DbSession, user: CurrentUser) -> TaskDetailOut:
    task = await db.scalar(select(Task).where(Task.id == _parse_task_id(task_id), Task.user_id == user.id))
    if task is None:
        raise NotFoundError("Task not found.", code="task_not_found")

    device: Device | None = None
    connection = ""
    if task.device_id is not None:
        device = await db.scalar(select(Device).where(Device.id == task.device_id, Device.user_id == user.id))
        if device is not None:
            connection = connection_status(device, request.app.state.device_hub)

    events = (
        await db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.user_id == user.id)
            .order_by(TaskEvent.id)
            .limit(_MAX_TRACE_EVENTS)
        )
    ).all()
    return TaskDetailOut(
        task=task_out(task, device, connection),
        events=[task_event_out(event) for event in events],
    )


def _parse_task_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("Task not found.", code="task_not_found") from exc
