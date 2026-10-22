"""POST /api/v1/conversations/clear: count first, delete only what is old and not pinned, owner only, audited."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.core.time import utc_now
from app.models.conversation import Conversation, Message
from app.services.conversation_service import new_message
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"


async def _conversation(client: AsyncClient, title: str) -> str:
    response = await client.post(f"{API}/conversations", json={"title": title}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _age(app: Any, conversation_id: str, days: int) -> None:
    async with app.state.session_factory() as db:
        await db.execute(
            update(Conversation).where(Conversation.id == uuid.UUID(conversation_id)).values(updated_at=utc_now() - timedelta(days=days))
        )
        await db.commit()


async def _messages_in(app: Any, conversation_id: str) -> int:
    async with app.state.session_factory() as db:
        return int(await db.scalar(select(func.count(Message.id)).where(Message.conversation_id == uuid.UUID(conversation_id))) or 0)


async def test_dry_run_counts_without_deleting(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    old = await _conversation(client, "Old")
    await _age(app, old, 120)

    response = await client.post(f"{API}/conversations/clear", json={"days": 90}, headers=csrf_headers(client))

    assert response.status_code == 200, response.text
    assert response.json() == {"matched": 1, "deleted": 0, "dry_run": True}
    assert (await client.get(f"{API}/conversations/{old}")).status_code == 200


async def test_clearing_deletes_old_unpinned_conversations_with_their_messages(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    old = await _conversation(client, "Old")
    recent = await _conversation(client, "Recent")
    pinned_old = await _conversation(client, "Pinned and old")
    await _age(app, old, 120)
    await _age(app, pinned_old, 120)
    await client.patch(f"{API}/conversations/{pinned_old}", json={"pinned": True}, headers=csrf_headers(client))
    async with app.state.session_factory() as db:
        db.add(new_message(user_id=uuid.UUID(owner["id"]), conversation_id=uuid.UUID(old), role="user", content="Old message"))
        await db.commit()

    response = await client.post(
        f"{API}/conversations/clear", json={"days": 90, "dry_run": False}, headers=csrf_headers(client)
    )

    assert response.json() == {"matched": 1, "deleted": 1, "dry_run": False}
    assert (await client.get(f"{API}/conversations/{old}")).status_code == 404
    assert await _messages_in(app, old) == 0
    assert (await client.get(f"{API}/conversations/{recent}")).status_code == 200
    assert (await client.get(f"{API}/conversations/{pinned_old}")).status_code == 200


async def test_archived_old_conversations_are_cleared_too(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    archived = await _conversation(client, "Archived")
    await client.patch(f"{API}/conversations/{archived}", json={"archived": True}, headers=csrf_headers(client))
    await _age(app, archived, 200)  # archiving itself counts as activity, so age the row after it

    response = await client.post(f"{API}/conversations/clear", json={"days": 90, "dry_run": False}, headers=csrf_headers(client))
    assert response.json()["deleted"] == 1


async def test_days_and_flags_are_validated(client: AsyncClient) -> None:
    await create_owner(client)
    for body in ({"days": 0}, {"days": 3651}, {"days": "30"}, {"days": 30, "dry_run": "no"}, {}):
        response = await client.post(f"{API}/conversations/clear", json=body, headers=csrf_headers(client))
        assert response.status_code == 422, (body, response.text)


async def test_clearing_is_audited_without_the_titles(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    old = await _conversation(client, "A private title")
    await _age(app, old, 120)
    await client.post(f"{API}/conversations/clear", json={"days": 90, "dry_run": False}, headers=csrf_headers(client))
    trail = (await client.get(f"{API}/audit", params={"action": "conversations.cleared"})).json()
    assert trail["total"] == 1
    assert "A private title" not in str(trail)


async def test_a_member_clears_only_their_own_conversations(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    owner_conversation = await _conversation(client, "Owner old")
    await _age(app, owner_conversation, 120)
    created = await client.post(
        f"{API}/users",
        json={"email": "clearer@example.com", "display_name": "Clearer", "password": "clearer-password-one", "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as member:
        await member.get(f"{API}/auth/status")
        login = await member.post(
            f"{API}/auth/login", json={"email": "clearer@example.com", "password": "clearer-password-one"}, headers=csrf_headers(member)
        )
        assert login.status_code == 200, login.text
        response = await member.post(f"{API}/conversations/clear", json={"days": 90, "dry_run": False}, headers=csrf_headers(member))
    assert response.json()["deleted"] == 0
