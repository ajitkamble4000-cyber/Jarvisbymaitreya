"""PUT .../messages/{id}/feedback: thumbs up or down on finished assistant replies, owner only, cleared with null."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.models.conversation import Message
from app.services.conversation_service import new_message
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"


async def _conversation(client: AsyncClient, title: str) -> str:
    response = await client.post(f"{API}/conversations", json={"title": title}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _insert(app: Any, *, user_id: str, conversation_id: str, role: str, status: str = "complete") -> str:
    async with app.state.session_factory() as db:
        row = new_message(
            user_id=uuid.UUID(user_id),
            conversation_id=uuid.UUID(conversation_id),
            role=role,
            content="Text",
            status=status,
            meta={"latency_ms": 12},
        )
        db.add(row)
        await db.commit()
        return str(row.id)


async def _stored_meta(app: Any, message_id: str) -> dict[str, Any]:
    async with app.state.session_factory() as db:
        row = await db.get(Message, uuid.UUID(message_id))
        assert row is not None
        return dict(row.meta)


def _feedback_url(conversation_id: str, message_id: str) -> str:
    return f"{API}/conversations/{conversation_id}/messages/{message_id}/feedback"


async def test_owner_rates_overwrites_and_clears_a_reply(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    cid = await _conversation(client, "Feedback")
    mid = await _insert(app, user_id=owner["id"], conversation_id=cid, role="assistant")

    up = await client.put(_feedback_url(cid, mid), json={"rating": "up"}, headers=csrf_headers(client))
    assert up.status_code == 200, up.text
    assert up.json()["meta"]["feedback"]["rating"] == "up"
    assert up.json()["meta"]["latency_ms"] == 12  # the rest of the meta is kept

    down = await client.put(_feedback_url(cid, mid), json={"rating": "down"}, headers=csrf_headers(client))
    assert down.json()["meta"]["feedback"]["rating"] == "down"

    cleared = await client.put(_feedback_url(cid, mid), json={"rating": None}, headers=csrf_headers(client))
    assert cleared.status_code == 200
    assert "feedback" not in cleared.json()["meta"]
    assert "feedback" not in await _stored_meta(app, mid)


async def test_only_finished_assistant_replies_can_be_rated(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    cid = await _conversation(client, "Feedback rules")
    user_row = await _insert(app, user_id=owner["id"], conversation_id=cid, role="user")
    streaming = await _insert(app, user_id=owner["id"], conversation_id=cid, role="assistant", status="streaming")

    for message_id in (user_row, streaming):
        refused = await client.put(_feedback_url(cid, message_id), json={"rating": "up"}, headers=csrf_headers(client))
        assert refused.status_code == 409, message_id
        assert refused.json()["error"]["code"] == "feedback_not_allowed"


async def test_invalid_bodies_and_unknown_messages_are_refused(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    cid = await _conversation(client, "Feedback input")
    mid = await _insert(app, user_id=owner["id"], conversation_id=cid, role="assistant")
    for body in ({"rating": "meh"}, {"rating": 1}, {}, {"rating": "up", "extra": True}):
        response = await client.put(_feedback_url(cid, mid), json=body, headers=csrf_headers(client))
        assert response.status_code == 422, (body, response.text)

    missing = await client.put(_feedback_url(cid, str(uuid.uuid4())), json={"rating": "up"}, headers=csrf_headers(client))
    assert missing.status_code == 404


async def test_members_cannot_rate_the_owners_replies(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    cid = await _conversation(client, "Owner only")
    mid = await _insert(app, user_id=owner["id"], conversation_id=cid, role="assistant")
    created = await client.post(
        f"{API}/users",
        json={"email": "rater@example.com", "display_name": "Rater", "password": "rater-password-one", "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as member:
        await member.get(f"{API}/auth/status")
        login = await member.post(
            f"{API}/auth/login", json={"email": "rater@example.com", "password": "rater-password-one"}, headers=csrf_headers(member)
        )
        assert login.status_code == 200, login.text
        response = await member.put(_feedback_url(cid, mid), json={"rating": "up"}, headers=csrf_headers(member))

    assert response.status_code == 404
    assert "feedback" not in await _stored_meta(app, mid)
