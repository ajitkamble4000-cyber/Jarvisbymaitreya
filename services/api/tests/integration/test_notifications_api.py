"""Notification inbox against real PostgreSQL: unread count, read, read-all, dismiss and isolation."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

from app.models.identity import User
from app.services.notifications import notify
from tests.conftest import create_owner, csrf_headers

BASE = "/api/v1/notifications"


async def _seed(app: Any, user_id: uuid.UUID, titles: list[str]) -> list[str]:
    async with app.state.session_factory() as db:
        rows = [
            notify(db, app.state.events, user_id=user_id, kind="reminder", severity="info", title=title, body="Body")
            for title in titles
        ]
        await db.flush()
        ids = [str(row.id) for row in rows]
        await db.commit()
        return ids


async def test_unread_count_and_listing(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _seed(app, uuid.UUID(owner["id"]), ["One", "Two", "Three"])
    body = (await client.get(BASE)).json()
    assert body["unread_count"] == 3
    assert len(body["items"]) == 3
    assert body["items"][0]["read"] is False
    assert {item["title"] for item in body["items"]} == {"One", "Two", "Three"}


async def test_mark_read_then_read_all(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    ids = await _seed(app, uuid.UUID(owner["id"]), ["A", "B", "C"])

    marked = await client.post(f"{BASE}/{ids[0]}/read", headers=csrf_headers(client))
    assert marked.status_code == 200 and marked.json()["read"] is True
    body = (await client.get(BASE)).json()
    assert body["unread_count"] == 2

    unread_only = (await client.get(f"{BASE}?unread=true")).json()
    assert len(unread_only["items"]) == 2

    read_all = await client.post(f"{BASE}/read-all", headers=csrf_headers(client))
    assert read_all.status_code == 200 and read_all.json() == {"updated": 2}
    assert (await client.get(BASE)).json()["unread_count"] == 0
    assert (await client.get(f"{BASE}?unread=true")).json()["items"] == []


async def test_dismiss_hides_the_row_everywhere(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    ids = await _seed(app, uuid.UUID(owner["id"]), ["Keep", "Dismiss"])
    response = await client.delete(f"{BASE}/{ids[1]}", headers=csrf_headers(client))
    assert response.status_code == 204

    body = (await client.get(BASE)).json()
    assert [item["title"] for item in body["items"]] == ["Keep"]
    assert body["unread_count"] == 1
    assert (await client.post(f"{BASE}/{ids[1]}/read", headers=csrf_headers(client))).status_code == 404
    assert (await client.delete(f"{BASE}/{ids[1]}", headers=csrf_headers(client))).status_code == 204


async def test_read_all_does_not_resurrect_or_count_dismissed_rows(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    ids = await _seed(app, uuid.UUID(owner["id"]), ["Keep", "Gone"])
    await client.delete(f"{BASE}/{ids[1]}", headers=csrf_headers(client))
    assert (await client.post(f"{BASE}/read-all", headers=csrf_headers(client))).json() == {"updated": 1}


async def test_other_users_notifications_are_invisible_and_unchangeable(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    async with app.state.session_factory() as db:
        other = User(email="neighbour@example.com", display_name="N", password_hash="!", role="owner", is_active=True)
        db.add(other)
        await db.flush()
        foreign_id = str(notify(db, app.state.events, user_id=other.id, kind="reminder", severity="info", title="Theirs", body="x").id)
        await db.flush()
        await db.commit()

    body = (await client.get(BASE)).json()
    assert body["items"] == [] and body["unread_count"] == 0
    assert (await client.post(f"{BASE}/{foreign_id}/read", headers=csrf_headers(client))).status_code == 404
    assert (await client.delete(f"{BASE}/{foreign_id}", headers=csrf_headers(client))).status_code == 404
    assert (await client.post(f"{BASE}/not-a-uuid/read", headers=csrf_headers(client))).status_code == 404
