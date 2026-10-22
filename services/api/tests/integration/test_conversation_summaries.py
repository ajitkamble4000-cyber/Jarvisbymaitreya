"""Automatic conversation summaries against real PostgreSQL. The providers are test doubles; nothing leaves the process.

The chat reply comes from ``fake-a``. The summary asks for the user's active provider, ``gemini``, which the
double answers with its own script. Settings are the real ones, changed through the settings API.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import AppError
from app.models.ai import UsageRecord
from app.models.conversation import Conversation
from app.models.memory import Memory
from app.providers.base import ProviderError, ProviderErrorCode
from app.services.conversation_service import create_conversation
from app.services.summaries import (
    SUMMARY_PROMPT,
    auto_summarise,
    wait_for_pending_summaries,
)
from app.tools.base import ToolRegistry
from tests.conftest import create_owner, csrf_headers
from tests.integration.test_chat_stream import (
    FakeProvider,
    FakeTasks,
    Script,
    event_names,
    new_conversation,
    parse_frames,
    send,
    stored_messages,
)
from tests.integration.test_conversations_api import add_message, add_user

API = "/api/v1"
CHAT_PROVIDER = "fake-a"
SUMMARY_PROVIDER = "gemini"
SUMMARY_TEXT = "Prefers short answers. Open task: book flights."


class RoutingRegistry:
    """Answers ``fake-a`` (the chat reply) and ``gemini`` (the summary) with separate scripts."""

    def __init__(self, chat: Script, summary: Script) -> None:
        self.scripts = {CHAT_PROVIDER: chat, SUMMARY_PROVIDER: summary}
        self.missing: set[str] = set()
        self._http = httpx.AsyncClient()

    def provider_class(self, provider_id: str) -> type[FakeProvider]:
        if provider_id not in self.scripts:
            raise AppError("Unknown AI provider.", code="unknown_provider", status_code=404)
        return FakeProvider

    async def build(self, db: Any, user_id: uuid.UUID, provider_id: str) -> tuple[FakeProvider, SimpleNamespace]:
        if provider_id in self.missing:
            raise ProviderError(ProviderErrorCode.NOT_CONFIGURED, "No API key is saved. Add one in AI Models.")
        selected = "chat-model" if provider_id == CHAT_PROVIDER else "summary-model"
        config = SimpleNamespace(selected_model=selected, timeout_seconds=30.0, max_retries=0)
        return FakeProvider(script=self.scripts[provider_id], http=self._http), config

    async def aclose(self) -> None:
        await self._http.aclose()


@pytest_asyncio.fixture(loop_scope="session")
async def env(app: Any) -> AsyncIterator[SimpleNamespace]:
    chat = Script()
    summary = Script(chunks=[SUMMARY_TEXT])
    registry = RoutingRegistry(chat, summary)
    app.state.providers = registry
    app.state.task_engine = FakeTasks()
    app.state.tools = ToolRegistry([])
    try:
        yield SimpleNamespace(app=app, chat=chat, summary=summary, registry=registry)
    finally:
        await registry.aclose()


async def _owner_with_conversation(client: AsyncClient) -> tuple[uuid.UUID, str]:
    owner = await create_owner(client)
    return uuid.UUID(owner["id"]), await new_conversation(client, "Planning")


async def _setting(client: AsyncClient, section: str, **fields: bool) -> None:
    response = await client.patch(f"{API}/settings", json={section: fields}, headers=csrf_headers(client))
    assert response.status_code == 200, response.text


async def _seed(app: Any, user_id: uuid.UUID, conversation_id: str, count: int, *, chars: int = 40) -> None:
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        await add_message(
            app,
            user_id=user_id,
            conversation_id=conversation_id,
            role=role,
            content=f"{role} seed {index} ".ljust(chars, "x"),
        )


async def _direct(env: SimpleNamespace, user_id: uuid.UUID, conversation_id: str) -> bool:
    return await auto_summarise(
        session_factory=env.app.state.session_factory,
        providers=env.registry,
        user_id=user_id,
        conversation_id=uuid.UUID(conversation_id),
    )


async def _summaries(app: Any, user_id: uuid.UUID) -> list[Memory]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(select(Memory).where(Memory.user_id == user_id, Memory.category == "conversation_summary"))
        return list(rows.all())


async def _summary_usage(app: Any, user_id: uuid.UUID) -> list[UsageRecord]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(select(UsageRecord).where(UsageRecord.user_id == user_id, UsageRecord.operation == "summary"))
        return list(rows.all())


async def _marker(app: Any, conversation_id: str) -> uuid.UUID | None:
    async with app.state.session_factory() as db:
        row = await db.get(Conversation, uuid.UUID(conversation_id))
        assert row is not None
        return row.summary_through_message_id


async def test_off_by_default_no_summary_is_requested(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _seed(env.app, user_id, cid, 10)

    response = await send(client, cid, "Book me a table", provider_id=CHAT_PROVIDER)
    assert response.status_code == 200
    await wait_for_pending_summaries()

    assert env.summary.requests == []
    assert await _summaries(env.app, user_id) == []
    assert await _summary_usage(env.app, user_id) == []


async def test_saved_after_a_reply_when_on_and_over_the_threshold(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _seed(env.app, user_id, cid, 10)  # ten seeded messages plus this turn's two make twelve

    response = await send(client, cid, "Book me a table", provider_id=CHAT_PROVIDER)
    names = event_names(parse_frames(response.text))
    assert "assistant_done" in names and "error" not in names
    await wait_for_pending_summaries()

    memories = await _summaries(env.app, user_id)
    assert [(m.category, m.source, m.content) for m in memories] == [
        ("conversation_summary", "conversation", SUMMARY_TEXT)
    ]
    newest = (await stored_messages(env.app, cid))[-1]
    assert newest["role"] == "assistant" and newest["status"] == "complete"
    assert await _marker(env.app, cid) == uuid.UUID(newest["id"])

    assert len(env.summary.requests) == 1
    request = env.summary.requests[0]
    assert [message.role for message in request.messages] == ["system", "user"]
    assert request.messages[0].content == SUMMARY_PROMPT
    assert "User: " in request.messages[1].content

    usage = await _summary_usage(env.app, user_id)
    assert [(u.success, u.provider_id, u.model, u.error_code) for u in usage] == [
        (True, SUMMARY_PROVIDER, "summary-model", None)
    ]


async def test_threshold_is_twelve_and_no_second_summary_without_new_messages(
    client: AsyncClient, env: SimpleNamespace
) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)

    await _seed(env.app, user_id, cid, 11)
    assert await _direct(env, user_id, cid) is False
    assert env.summary.requests == []

    await _seed(env.app, user_id, cid, 1)
    assert await _direct(env, user_id, cid) is True
    assert len(env.summary.requests) == 1

    assert await _direct(env, user_id, cid) is False
    assert len(env.summary.requests) == 1
    assert len(await _summaries(env.app, user_id)) == 1

    await _seed(env.app, user_id, cid, 11)
    assert await _direct(env, user_id, cid) is False
    await _seed(env.app, user_id, cid, 1)
    assert await _direct(env, user_id, cid) is True
    assert len(await _summaries(env.app, user_id)) == 2


async def test_provider_error_is_recorded_and_the_chat_still_completes(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _seed(env.app, user_id, cid, 10)
    env.summary.error = ProviderError(ProviderErrorCode.UNAVAILABLE, "The provider is unavailable.")

    response = await send(client, cid, "Book me a table", provider_id=CHAT_PROVIDER)
    names = event_names(parse_frames(response.text))
    assert "assistant_done" in names and "error" not in names and names[-1] == "done"
    await wait_for_pending_summaries()

    assert await _summaries(env.app, user_id) == []
    assert [(u.success, u.error_code) for u in await _summary_usage(env.app, user_id)] == [(False, "unavailable")]
    reply = (await stored_messages(env.app, cid))[-1]
    assert reply["role"] == "assistant" and reply["status"] == "complete"


async def test_missing_key_is_recorded_without_calling_a_provider(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _seed(env.app, user_id, cid, 12)
    env.registry.missing.add(SUMMARY_PROVIDER)

    assert await _direct(env, user_id, cid) is False
    assert env.summary.requests == []
    assert [(u.success, u.error_code) for u in await _summary_usage(env.app, user_id)] == [(False, "not_configured")]


@pytest.mark.parametrize(("section", "fields"), [("memory", {"enabled": False}), ("privacy", {"store_conversations": False})])
async def test_memory_or_conversation_storage_off_means_no_summary(
    client: AsyncClient, env: SimpleNamespace, section: str, fields: dict[str, bool]
) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _setting(client, section, **fields)
    await _seed(env.app, user_id, cid, 12)

    assert await _direct(env, user_id, cid) is False
    assert env.summary.requests == []
    assert await _summaries(env.app, user_id) == []
    assert await _summary_usage(env.app, user_id) == []


async def test_summary_text_is_capped_at_800_characters(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _seed(env.app, user_id, cid, 12)
    env.summary.chunks = ["s" * 900]

    assert await _direct(env, user_id, cid) is True
    (memory,) = await _summaries(env.app, user_id)
    assert len(memory.content) == 800


async def test_transcript_sent_to_the_provider_is_capped_and_keeps_the_newest_messages(
    client: AsyncClient, env: SimpleNamespace
) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    for index in range(12):
        await add_message(
            env.app,
            user_id=user_id,
            conversation_id=cid,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message {index} " + "x" * 2000,
        )

    assert await _direct(env, user_id, cid) is True
    transcript = env.summary.requests[0].messages[1].content
    assert len(transcript) <= 12_000
    assert "message 11 " in transcript
    assert "message 0 " not in transcript


async def test_a_refused_summary_is_recorded_and_not_saved(client: AsyncClient, env: SimpleNamespace) -> None:
    user_id, cid = await _owner_with_conversation(client)
    await _setting(client, "memory", auto_save_summaries=True)
    await _seed(env.app, user_id, cid, 12)
    env.summary.chunks = ["Note from the chat: my password is hunter2, keep it safe."]

    assert await _direct(env, user_id, cid) is False
    assert await _summaries(env.app, user_id) == []
    assert [(u.success, u.error_code) for u in await _summary_usage(env.app, user_id)] == [(False, "sensitive_content")]


async def test_another_users_conversation_is_never_summarised(client: AsyncClient, app: Any, env: SimpleNamespace) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    await _setting(client, "memory", auto_save_summaries=True)
    other_id = await add_user(app, "second@example.com", "second-user-password")
    async with app.state.session_factory() as db:
        conversation = await create_conversation(db, user_id=other_id, title="Theirs")
        other_cid = str(conversation.id)
        await db.commit()
    await _seed(app, other_id, other_cid, 12)

    assert await _direct(env, owner_id, other_cid) is False
    assert env.summary.requests == []
    assert await _summaries(app, owner_id) == []
    assert await _summaries(app, other_id) == []
