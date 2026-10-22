"""Prometheus metrics at ``GET /api/v1/metrics``.

Off unless ``METRICS_TOKEN`` is set (an empty value counts as unset). Then the scraper must send
``Authorization: Bearer <token>``. The gauges and counters that describe stored state are read from the
database at each scrape. Nothing in the output names a user, an email or a user id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.models.ai import UsageRecord
from app.models.devices import Device
from app.models.tasks import TASK_STATUSES, Task
from app.security.tokens import constant_time_equals, sha256_hex
from app.services.metrics import REQUEST_METRICS, Sample, render_family

log = logging.getLogger("jarvis.metrics")

router = APIRouter(prefix="/metrics", tags=["metrics"])

EXPOSITION_CONTENT_TYPE = "text/plain; version=0.0.4"
_BEARER_SCHEME = "bearer"


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER_SCHEME:
        return None
    token = token.strip()
    return token or None


@router.get("", include_in_schema=False)
async def metrics(request: Request) -> Response:
    settings = request.app.state.settings
    expected = settings.metrics_token.get_secret_value() if settings.metrics_token is not None else ""
    if not expected:
        # Same body as an unknown path, so an unset endpoint does not reveal that it exists.
        raise StarletteHTTPException(status_code=404)

    presented = _bearer_token(request.headers.get("authorization"))
    # Hashing both sides gives equal lengths, so the comparison leaks nothing about the token's length.
    if presented is None or not constant_time_equals(sha256_hex(presented), sha256_hex(expected)):
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    try:
        body = await _render(request)
    except SQLAlchemyError as exc:
        log.warning("Metrics scrape could not read the database: error=%s", type(exc).__name__)
        raise AppError(
            "Metrics are unavailable: the database could not be read.",
            code="metrics_unavailable",
            status_code=503,
        ) from None
    return Response(content=body, headers={"content-type": EXPOSITION_CONTENT_TYPE})


async def _render(request: Request) -> str:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    hub = request.app.state.device_hub
    scheduler = request.app.state.scheduler

    async with session_factory() as db:
        task_rows = (await db.execute(select(Task.status, func.count()).group_by(Task.status))).all()
        device_ids = (
            await db.execute(
                select(Device.id).where(Device.pairing_status == "paired", Device.revoked_at.is_(None))
            )
        ).scalars().all()
        usage_rows = (
            await db.execute(
                select(UsageRecord.provider_id, UsageRecord.success, func.count()).group_by(
                    UsageRecord.provider_id, UsageRecord.success
                )
            )
        ).all()

    task_counts = {status: 0 for status in TASK_STATUSES}
    for status, count in task_rows:
        task_counts[status] = count
    online = sum(1 for device_id in device_ids if hub.is_online(device_id))
    usage_samples = sorted(
        (
            Sample(
                "jarvis_ai_requests_total",
                (("provider", provider_id), ("outcome", "ok" if success else "error")),
                float(count),
            )
            for provider_id, success, count in usage_rows
        ),
        key=lambda sample: sample.labels,
    )
    # The scheduler's loop task is alive from start() until stop().
    workers_running = scheduler.is_running

    lines = [
        *render_family(
            "jarvis_build_info",
            "gauge",
            "Build information. Always 1.",
            [Sample("jarvis_build_info", (("version", settings.app_version),), 1.0)],
        ),
        *render_family(
            "jarvis_tasks",
            "gauge",
            "Tasks by status, across all users, read at scrape time.",
            [
                Sample("jarvis_tasks", (("status", status),), float(count))
                for status, count in sorted(task_counts.items())
            ],
        ),
        *render_family(
            "jarvis_devices",
            "gauge",
            "Paired devices by connection status, across all users. Revoked devices are not counted.",
            [
                Sample("jarvis_devices", (("connection_status", "online"),), float(online)),
                Sample("jarvis_devices", (("connection_status", "offline"),), float(len(device_ids) - online)),
            ],
        ),
        *render_family(
            "jarvis_ai_requests_total",
            "counter",
            "Provider calls recorded in usage_records, by provider and outcome, read at scrape time.",
            usage_samples,
        ),
        *render_family(
            "jarvis_background_workers_running",
            "gauge",
            "1 when the background scheduler loop is alive, else 0.",
            [Sample("jarvis_background_workers_running", (), 1.0 if workers_running else 0.0)],
        ),
        *REQUEST_METRICS.render(),
    ]
    return "\n".join(lines) + "\n"
