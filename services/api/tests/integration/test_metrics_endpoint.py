"""Metrics endpoint against real PostgreSQL: token rules, content, scrape-time gauges, and no user data in output."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from app.core.time import utc_now
from app.models.ai import UsageRecord
from app.models.devices import Device
from app.models.identity import User
from app.models.tasks import Task
from tests.conftest import create_owner

METRICS = "/api/v1/metrics"
TOKEN = "scrape-token-0123456789abcdefghijklmnop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _enable(app: Any, monkeypatch: pytest.MonkeyPatch, token: str = TOKEN) -> None:
    monkeypatch.setattr(app.state.settings, "metrics_token", SecretStr(token))


async def _other_user(app: Any, email: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name="Other", password_hash="!", role="owner", is_active=True)
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
    return user_id


def _sample(body: str, prefix: str) -> float:
    for line in body.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


# -- availability and token ----------------------------------------------------


async def test_unset_token_answers_404_exactly_like_an_unknown_path(client: AsyncClient, app: Any) -> None:
    assert app.state.settings.metrics_token is None
    missing = await client.get(METRICS)
    unknown = await client.get("/api/v1/no-such-route")

    assert missing.status_code == unknown.status_code == 404
    assert missing.json()["error"] == unknown.json()["error"]


async def test_empty_token_counts_as_unset(client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable(app, monkeypatch, token="")
    response = await client.get(METRICS, headers={"Authorization": "Bearer "})
    assert response.status_code == 404


async def test_missing_or_wrong_token_is_401_with_no_body(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    attempts = [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": "Bearer "},
    ]
    for headers in attempts:
        response = await client.get(METRICS, headers=headers)
        assert response.status_code == 401, headers
        assert response.content == b""
        assert response.headers["www-authenticate"] == "Bearer"


async def test_right_token_returns_exposition_text_and_ignores_origin(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    # GET is not an unsafe method, so neither the origin check nor the CSRF check applies to the scrape.
    response = await client.get(METRICS, headers={**AUTH, "Origin": "https://elsewhere.example"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4"
    body = response.text
    assert "# TYPE jarvis_build_info gauge" in body
    assert "# TYPE jarvis_tasks gauge" in body
    assert "# TYPE jarvis_devices gauge" in body
    assert "# TYPE jarvis_ai_requests_total counter" in body
    assert "# TYPE jarvis_background_workers_running gauge" in body
    assert "# TYPE jarvis_http_request_duration_seconds histogram" in body


# -- gauges read at scrape time ------------------------------------------------


async def test_tasks_gauge_matches_the_seeded_tasks(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    neighbour_id = await _other_user(app, "neighbour@example.com")
    async with app.state.session_factory() as db:
        for status, user_id in (
            ("completed", owner_id),
            ("completed", owner_id),
            ("queued", neighbour_id),
            ("failed", owner_id),
        ):
            db.add(Task(user_id=user_id, title="Check", tool_name="clock.now", status=status))
        await db.commit()

    lines = (await client.get(METRICS, headers=AUTH)).text.splitlines()
    assert 'jarvis_tasks{status="completed"} 2' in lines
    assert 'jarvis_tasks{status="queued"} 1' in lines
    assert 'jarvis_tasks{status="failed"} 1' in lines
    assert 'jarvis_tasks{status="running"} 0' in lines
    assert 'jarvis_tasks{status="cancelled"} 0' in lines


async def test_devices_gauge_uses_the_hub_and_skips_revoked_devices(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    online_id = uuid.uuid4()
    async with app.state.session_factory() as db:
        db.add(Device(id=online_id, user_id=owner_id, name="Laptop", registered_at=utc_now(), pairing_status="paired"))
        db.add(Device(user_id=owner_id, name="Desk", registered_at=utc_now(), pairing_status="paired"))
        db.add(
            Device(
                user_id=owner_id,
                name="Old",
                registered_at=utc_now(),
                pairing_status="revoked",
                revoked_at=utc_now(),
            )
        )
        await db.commit()
    monkeypatch.setattr(app.state.device_hub, "is_online", lambda device_id: device_id == online_id)

    lines = (await client.get(METRICS, headers=AUTH)).text.splitlines()
    assert 'jarvis_devices{connection_status="online"} 1' in lines
    assert 'jarvis_devices{connection_status="offline"} 1' in lines


async def test_ai_requests_are_counted_by_provider_and_outcome(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        for success in (True, True, False):
            db.add(
                UsageRecord(
                    user_id=owner_id,
                    created_at=utc_now(),
                    provider_id="gemini",
                    operation="chat",
                    success=success,
                )
            )
        await db.commit()

    lines = (await client.get(METRICS, headers=AUTH)).text.splitlines()
    assert 'jarvis_ai_requests_total{provider="gemini",outcome="ok"} 2' in lines
    assert 'jarvis_ai_requests_total{provider="gemini",outcome="error"} 1' in lines


async def test_workers_gauge_follows_the_scheduler_loop_task(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    # Tests run without background workers, so the scheduler has no loop task.
    idle = (await client.get(METRICS, headers=AUTH)).text.splitlines()
    assert "jarvis_background_workers_running 0" in idle

    loop_task = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(app.state.scheduler, "_loop_task", loop_task)
    try:
        running = (await client.get(METRICS, headers=AUTH)).text.splitlines()
    finally:
        loop_task.cancel()
    assert "jarvis_background_workers_running 1" in running


# -- privacy and labels --------------------------------------------------------


async def test_output_names_no_user_email_or_id_and_not_the_token(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    owner = await create_owner(client, email="owner.private@example.com")
    neighbour_id = await _other_user(app, "neighbour.private@example.com")
    async with app.state.session_factory() as db:
        db.add(Task(user_id=uuid.UUID(owner["id"]), title="Private", tool_name="clock.now"))
        db.add(Task(user_id=neighbour_id, title="Private", tool_name="clock.now"))
        await db.commit()

    body = (await client.get(METRICS, headers=AUTH)).text
    for forbidden in ("owner.private", "neighbour.private", "@", owner["id"], str(neighbour_id), TOKEN):
        assert forbidden not in body


async def test_request_route_is_the_template_and_never_carries_ids_or_query(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    task_id = uuid.uuid4()
    # Unauthenticated, so the route matches and the request is answered with 401.
    response = await client.get(f"/api/v1/tasks/{task_id}?user=owner.private@example.com")
    assert response.status_code == 401

    body = (await client.get(METRICS, headers=AUTH)).text
    assert 'route="/api/v1/tasks/{id}",status="401"' in body
    assert str(task_id) not in body
    assert "owner.private" not in body


async def test_request_refused_before_routing_is_labelled_unmatched(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    response = await client.post("/api/v1/tasks", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403

    body = (await client.get(METRICS, headers=AUTH)).text
    assert 'route="unmatched",status="403"' in body
    assert "evil.example" not in body


async def test_scrapes_are_not_counted_and_api_counters_increase(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    series = 'jarvis_http_requests_total{method="GET",route="/api/v1/health",status="200"}'
    count_series = 'jarvis_http_request_duration_seconds_count{method="GET",route="/api/v1/health"}'
    before = (await client.get(METRICS, headers=AUTH)).text
    await client.get("/api/v1/health")
    await client.get("/api/v1/health")
    await client.get(METRICS)  # a 401 scrape is not recorded either
    after = (await client.get(METRICS, headers=AUTH)).text

    assert _sample(after, series) - _sample(before, series) == 2
    assert _sample(after, count_series) - _sample(before, count_series) == 2
    assert 'route="/api/v1/metrics"' not in after


# -- honest failure ------------------------------------------------------------


class _UnreadableDatabase:
    async def __aenter__(self) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused for role jarvis"))

    async def __aexit__(self, *exc_info: object) -> None:
        return None


async def test_database_failure_is_503_without_database_details(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _enable(app, monkeypatch)
    monkeypatch.setattr(app.state, "session_factory", lambda: _UnreadableDatabase())

    response = await client.get(METRICS, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "metrics_unavailable"
    assert "role jarvis" not in response.text
