"""GET /api/v1/search: conversations, memories and tasks in one call. Owner scoped, literal matching, expiry respected."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.core.time import utc_now
from app.models.memory import Memory
from app.services.conversation_service import new_message
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"


async def _seed(client: AsyncClient, app: Any, owner_id: str) -> str:
    created = await client.post(f"{API}/conversations", json={"title": "Holiday plans"}, headers=csrf_headers(client))
    cid = str(created.json()["id"])
    async with app.state.session_factory() as db:
        db.add(new_message(user_id=uuid.UUID(owner_id), conversation_id=uuid.UUID(cid), role="user", content="Pack the sunscreen"))
        await db.commit()
    await client.post(f"{API}/memories", json={"category": "preference", "content": "Prefers morning meetings"}, headers=csrf_headers(client))
    await client.post(f"{API}/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client))
    return cid


async def test_one_call_finds_conversations_by_title_and_message_memories_and_tasks(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    cid = await _seed(client, app, owner["id"])

    by_title = (await client.get(f"{API}/search", params={"q": "holiday"})).json()
    assert [(item["id"], item["match"]) for item in by_title["conversations"]] == [(cid, "title")]

    by_message = (await client.get(f"{API}/search", params={"q": "sunscreen"})).json()
    assert [(item["id"], item["match"]) for item in by_message["conversations"]] == [(cid, "message")]

    memories = (await client.get(f"{API}/search", params={"q": "morning"})).json()["memories"]
    assert len(memories) == 1 and memories[0]["category"] == "preference"

    tasks = (await client.get(f"{API}/search", params={"q": "devices"})).json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["tool_name"] == "list_devices"


async def test_wildcards_are_matched_literally(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _seed(client, app, owner["id"])
    for query in ("%%", "__", "h%"):
        body = (await client.get(f"{API}/search", params={"q": query})).json()
        assert body["conversations"] == [] and body["memories"] == [] and body["tasks"] == [], query


async def test_short_and_long_queries_are_refused(client: AsyncClient) -> None:
    await create_owner(client)
    assert (await client.get(f"{API}/search", params={"q": "a"})).status_code == 422
    assert (await client.get(f"{API}/search", params={"q": "x" * 121})).status_code == 422


async def test_expired_memories_are_not_returned(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    async with app.state.session_factory() as db:
        db.add(
            Memory(
                user_id=uuid.UUID(owner["id"]),
                category="fact",
                content="Old passport number reminder",
                source="manual",
                expires_at=utc_now() - timedelta(days=1),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        await db.commit()
    assert (await client.get(f"{API}/search", params={"q": "passport"})).json()["memories"] == []


async def test_a_member_searches_only_their_own_data(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _seed(client, app, owner["id"])
    created = await client.post(
        f"{API}/users",
        json={"email": "searcher@example.com", "display_name": "Searcher", "password": "searcher-password-one", "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as member:
        await member.get(f"{API}/auth/status")
        login = await member.post(
            f"{API}/auth/login", json={"email": "searcher@example.com", "password": "searcher-password-one"}, headers=csrf_headers(member)
        )
        assert login.status_code == 200, login.text
        for query in ("holiday", "sunscreen", "morning", "devices"):
            body = (await member.get(f"{API}/search", params={"q": query})).json()
            assert body["conversations"] == [] and body["memories"] == [] and body["tasks"] == [], query
