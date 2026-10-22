"""Pinned conversations: PATCH pinned, pinned-first order in the list, and the unchanged default."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update

from app.core.time import utc_now
from app.models.conversation import Conversation
from tests.conftest import create_owner, csrf_headers

API = "/api/v1"


async def _conversation(client: AsyncClient, title: str) -> str:
    response = await client.post(f"{API}/conversations", json={"title": title}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _age(app: Any, conversation_id: str, days: int) -> None:
    """Makes a conversation older, so the list order is decided by the pin and not by creation order."""
    async with app.state.session_factory() as db:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == uuid.UUID(conversation_id))
            .values(updated_at=utc_now() - timedelta(days=days))
        )
        await db.commit()


async def test_new_conversations_are_unpinned_and_pinning_round_trips(client: AsyncClient) -> None:
    await create_owner(client)
    cid = await _conversation(client, "Plans")
    assert (await client.get(f"{API}/conversations/{cid}")).json()["conversation"]["pinned"] is False

    pinned = await client.patch(f"{API}/conversations/{cid}", json={"pinned": True}, headers=csrf_headers(client))
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["pinned"] is True

    unchanged = await client.patch(f"{API}/conversations/{cid}", json={"title": "Plans 2027"}, headers=csrf_headers(client))
    assert unchanged.json()["pinned"] is True  # a patch without the key keeps the pin

    unpinned = await client.patch(f"{API}/conversations/{cid}", json={"pinned": False}, headers=csrf_headers(client))
    assert unpinned.json()["pinned"] is False


async def test_pinned_conversations_list_first_even_when_older(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    old_pinned = await _conversation(client, "Old but pinned")
    recent = await _conversation(client, "Recent")
    await _age(app, old_pinned, days=30)
    await client.patch(f"{API}/conversations/{old_pinned}", json={"pinned": True}, headers=csrf_headers(client))

    items = (await client.get(f"{API}/conversations")).json()["items"]

    assert [item["id"] for item in items][:2] == [old_pinned, recent]
    assert items[0]["pinned"] is True and items[1]["pinned"] is False


async def test_pinned_flag_must_be_a_boolean(client: AsyncClient) -> None:
    await create_owner(client)
    cid = await _conversation(client, "Strict")
    for value in ("yes", 1, []):
        response = await client.patch(f"{API}/conversations/{cid}", json={"pinned": value}, headers=csrf_headers(client))
        assert response.status_code == 422, (value, response.text)
