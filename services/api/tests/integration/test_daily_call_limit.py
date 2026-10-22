"""ai.daily_call_limit: the turn is refused before any provider call once today's calls reach the limit."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from app.core.time import utc_now
from app.models.ai import UsageRecord
from tests.conftest import create_owner, csrf_headers
from tests.integration.test_chat_stream import (  # chat_env is a fixture imported for its side effects
    FAKE_A,
    ChatEnv,
    chat_env,  # noqa: F401
    event_names,
    frame_data,
    make_doc,
    new_conversation,
    parse_frames,
    send,
)

API = "/api/v1"


async def _usage(app: Any, user_id: uuid.UUID, count: int, *, operation: str = "chat", created_at: Any = None) -> None:
    async with app.state.session_factory() as db:
        for _ in range(count):
            db.add(
                UsageRecord(
                    user_id=user_id,
                    created_at=created_at or utc_now(),
                    provider_id=FAKE_A,
                    model="model-a",
                    operation=operation,
                    success=True,
                    latency_ms=10,
                )
            )
        await db.commit()


async def test_a_reply_is_refused_before_any_provider_call_once_the_limit_is_reached(chat_env: ChatEnv, client: AsyncClient) -> None:
    owner = await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(daily_call_limit=2)
    await _usage(chat_env.app, uuid.UUID(owner["id"]), 2)

    response = await send(client, cid, "Hello")

    frames = parse_frames(response.text)
    error = frame_data(frames, "error")[0]
    assert error["code"] == "daily_call_limit_reached"
    assert "2 AI calls" in error["message"]
    assert chat_env.scripts[FAKE_A].requests == []
    # Same frames as any other failed turn: no text reached the reader.
    assert event_names(frames) == ["user_message", "assistant_start", "error", "done"]
    assert "assistant_done" not in event_names(frames)


async def test_one_warning_is_raised_per_day_however_many_turns_are_refused(chat_env: ChatEnv, client: AsyncClient) -> None:
    owner = await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(daily_call_limit=1)
    await _usage(chat_env.app, uuid.UUID(owner["id"]), 1)

    await send(client, cid, "First")
    await send(client, cid, "Second")

    items = (await client.get(f"{API}/notifications")).json()["items"]
    limit_items = [item for item in items if item["kind"] == "usage_limit"]
    assert len(limit_items) == 1
    assert limit_items[0]["severity"] == "warning"


async def test_calls_from_earlier_days_and_other_operations_do_not_count(chat_env: ChatEnv, client: AsyncClient) -> None:
    owner = await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(daily_call_limit=1)
    user_id = uuid.UUID(owner["id"])
    await _usage(chat_env.app, user_id, 3, created_at=utc_now() - timedelta(days=1))
    await _usage(chat_env.app, user_id, 3, operation="test")

    response = await send(client, cid, "Hello")

    assert frame_data(parse_frames(response.text), "error") == []
    assert len(chat_env.scripts[FAKE_A].requests) == 1


async def test_no_limit_means_no_refusal(chat_env: ChatEnv, client: AsyncClient) -> None:
    owner = await create_owner(client)
    cid = await new_conversation(client)
    await _usage(chat_env.app, uuid.UUID(owner["id"]), 50)

    response = await send(client, cid, "Hello")

    assert frame_data(parse_frames(response.text), "error") == []


async def test_the_limit_is_saved_and_bad_values_are_refused(client: AsyncClient) -> None:
    await create_owner(client)
    saved = await client.patch(f"{API}/settings", json={"ai": {"daily_call_limit": 25}}, headers=csrf_headers(client))
    assert saved.status_code == 200, saved.text
    assert saved.json()["ai"]["daily_call_limit"] == 25
    for value in (0, 10001, True, "10", 2.5):
        response = await client.patch(f"{API}/settings", json={"ai": {"daily_call_limit": value}}, headers=csrf_headers(client))
        assert response.status_code == 422, (value, response.text)
