"""GET /api/v1/analytics/calls-today: today's chat provider calls against the daily limit, counted per user from usage records."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.core.time import utc_now
from app.models.ai import UsageRecord
from app.services.usage import utc_day_start
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"


async def _usage(app: Any, user_id: str, operation: str, created_at: datetime, count: int = 1) -> None:
    async with app.state.session_factory() as db:
        for _ in range(count):
            db.add(
                UsageRecord(
                    user_id=uuid.UUID(user_id),
                    created_at=created_at,
                    provider_id="fake-a",
                    model="model-a",
                    operation=operation,
                    success=True,
                    latency_ms=10,
                )
            )
        await db.commit()


async def test_a_new_owner_has_no_calls_and_no_limit_and_the_count_resets_at_midnight_utc(client: AsyncClient) -> None:
    await create_owner(client)

    response = await client.get(f"{API}/analytics/calls-today")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calls"] == 0
    assert body["limit"] is None
    assert datetime.fromisoformat(body["resets_at"]) == utc_day_start(utc_now()) + timedelta(days=1)


async def test_counts_only_today_chat_and_stream_calls(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    now = utc_now()
    yesterday = utc_day_start(now) - timedelta(minutes=1)
    await _usage(app, owner["id"], "chat", now, count=2)
    await _usage(app, owner["id"], "stream", now)
    await _usage(app, owner["id"], "summary", now)
    await _usage(app, owner["id"], "chat", yesterday)

    body = (await client.get(f"{API}/analytics/calls-today")).json()

    assert body["calls"] == 3


async def test_reports_the_daily_limit_the_owner_has_set(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    saved = await client.patch(f"{API}/settings", json={"ai": {"daily_call_limit": 25}}, headers=csrf_headers(client))
    assert saved.status_code == 200, saved.text
    await _usage(app, owner["id"], "chat", utc_now(), count=3)

    body = (await client.get(f"{API}/analytics/calls-today")).json()

    assert body["calls"] == 3
    assert body["limit"] == 25


async def test_each_user_sees_only_their_own_calls(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _usage(app, owner["id"], "chat", utc_now(), count=2)
    created = await client.post(
        f"{API}/users",
        json={"email": "counter@example.com", "display_name": "Counter", "password": "counter-password-one", "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    await _usage(app, created.json()["id"], "chat", utc_now(), count=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as member:
        await member.get(f"{API}/auth/status")
        login = await member.post(
            f"{API}/auth/login", json={"email": "counter@example.com", "password": "counter-password-one"}, headers=csrf_headers(member)
        )
        assert login.status_code == 200, login.text
        member_body = (await member.get(f"{API}/analytics/calls-today")).json()

    assert member_body["calls"] == 1
    assert (await client.get(f"{API}/analytics/calls-today")).json()["calls"] == 2


async def test_requires_sign_in(client: AsyncClient) -> None:
    await create_owner(client)
    client.cookies.clear()

    response = await client.get(f"{API}/analytics/calls-today")

    assert response.status_code == 401
