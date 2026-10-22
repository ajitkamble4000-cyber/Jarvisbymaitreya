"""GET /api/v1/audit and /audit/export.csv: since and until keep the events in a time range, with both ends inclusive."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta
from typing import Any

from httpx import AsyncClient

from app.core.time import utc_now
from app.models.platform import AuditEvent
from tests.conftest import create_owner

API = "/api/v1"
ACTION = "test.range"


async def _events(app: Any, user_id: str, times: list[datetime]) -> None:
    async with app.state.session_factory() as db:
        for index, when in enumerate(times):
            db.add(
                AuditEvent(
                    user_id=uuid.UUID(user_id),
                    actor_type="user",
                    actor_id=user_id,
                    action=f"{ACTION}.{index}",
                    outcome="success",
                    data={},
                    created_at=when,
                )
            )
        await db.commit()


async def test_since_and_until_narrow_the_list_and_both_ends_are_inclusive(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    now = utc_now()
    await _events(app, owner["id"], [now - timedelta(days=10), now - timedelta(days=3), now])

    async def total(**params: str) -> int:
        return int((await client.get(f"{API}/audit", params={"action": ACTION, **params})).json()["total"])

    assert await total() == 3
    assert await total(since=(now - timedelta(days=5)).isoformat()) == 2
    # The event exactly at the boundary is included on both sides.
    assert await total(until=(now - timedelta(days=3)).isoformat()) == 2
    assert await total(since=(now - timedelta(days=3)).isoformat(), until=(now - timedelta(days=3)).isoformat()) == 1
    assert await total(since=(now - timedelta(days=5)).isoformat(), until=(now - timedelta(days=3)).isoformat()) == 1


async def test_the_csv_export_covers_the_same_range(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    now = utc_now()
    await _events(app, owner["id"], [now - timedelta(days=10), now - timedelta(days=3), now])

    response = await client.get(f"{API}/audit/export.csv", params={"action": ACTION, "since": (now - timedelta(days=5)).isoformat()})

    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) - 1 == 2


async def test_a_time_without_a_zone_is_read_as_utc(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    now = utc_now()
    await _events(app, owner["id"], [now - timedelta(days=10), now])

    naive_start = (now - timedelta(days=5)).replace(tzinfo=None).isoformat()
    body = (await client.get(f"{API}/audit", params={"action": ACTION, "since": naive_start})).json()

    assert body["total"] == 1


async def test_a_start_after_the_end_is_refused(client: AsyncClient) -> None:
    await create_owner(client)
    now = utc_now()

    response = await client.get(f"{API}/audit", params={"since": now.isoformat(), "until": (now - timedelta(days=1)).isoformat()})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


async def test_a_time_that_is_not_a_time_is_refused(client: AsyncClient) -> None:
    await create_owner(client)

    response = await client.get(f"{API}/audit", params={"since": "last tuesday"})

    assert response.status_code == 422
