"""Streaming chat against real PostgreSQL. Providers and the task engine are test doubles; nothing leaves the process.

The doubles are installed on ``app.state`` (providers, task engine, tools) and the chat module's settings
loader is replaced, because the settings service is not part of this workstream.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import select

from app.core.errors import AppError
from app.core.time import utc_now
from app.models.conversation import Message
from app.models.ai import UsageRecord
from app.models.memory import Memory
from app.models.tasks import Task
from app.providers.base import (
    AIProvider,
    GenerationRequest,
    GenerationResult,
    HealthCheckResult,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
    StreamEvent,
    TokenUsage,
    ToolCall,
)
from app.schemas.conversations import ChatMessageIn
from app.services import chat as chat_module
from app.services.chat import ChatService, post_task_outcome_message
from app.tools.base import ExecutionTarget, RiskLevel, ToolRegistry, ToolSpec
from tests.conftest import csrf_headers, create_owner

API = "/api/v1"
FAKE_A = "fake-a"
FAKE_B = "fake-b"
DISPLAY = {FAKE_A: "Fake A", FAKE_B: "Fake B"}


class _LaunchArgs(BaseModel):
    device: str
    app: str


LAUNCH_SPEC = ToolSpec(
    name="launch_app",
    title="Launch app",
    description="Open an application on a paired device.",
    args_model=_LaunchArgs,
    risk=RiskLevel.MEDIUM,
    target=ExecutionTarget.DEVICE,
    category="apps",
    device_action="launch_app",
    required_capability="launch_app",
)


# -- provider double ------------------------------------------------------------


@dataclass
class Script:
    """What one fake provider does on its next call."""

    chunks: list[str] = field(default_factory=lambda: ["Hello", ", ", "world."])
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: ProviderError | None = None  # raised before any output
    error_after_first_chunk: ProviderError | None = None
    report_usage: bool = True  # False: the provider reports no usage, so the service estimates it
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(input_tokens=11, output_tokens=4, total_tokens=15))
    gate: asyncio.Event | None = None  # streaming waits on this before it yields anything
    hold_after_first: asyncio.Event | None = None  # streaming waits on this after the first chunk (never set in tests)
    started: asyncio.Event | None = None  # set as soon as a call begins
    requests: list[GenerationRequest] = field(default_factory=list)


class FakeProvider(AIProvider):
    provider_id: ClassVar[str] = "fake"
    display_name: ClassVar[str] = "Fake"

    def __init__(self, *, script: Script, http: httpx.AsyncClient) -> None:
        super().__init__(api_key="fake-key-never-logged", base_url="http://fake.invalid", http=http)
        self.script = script

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True, usage_reporting=True, model_listing=False)

    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        self.script.requests.append(request)
        if self.script.error is not None:
            raise self.script.error
        return GenerationResult(
            model=request.model,
            text="".join(self.script.chunks),
            tool_calls=list(self.script.tool_calls),
            usage=self.script.usage if self.script.report_usage else None,
            latency_ms=1,
        )

    async def stream_response(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        self.script.requests.append(request)
        if self.script.started is not None:
            self.script.started.set()
        if self.script.gate is not None:
            await self.script.gate.wait()
        if self.script.error is not None:
            raise self.script.error
        for index, chunk in enumerate(self.script.chunks):
            yield StreamEvent(type="text_delta", text=chunk)
            if index == 0 and self.script.error_after_first_chunk is not None:
                raise self.script.error_after_first_chunk
            if index == 0 and self.script.hold_after_first is not None:
                await self.script.hold_after_first.wait()
        for call in self.script.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=call)
        if self.script.report_usage:
            yield StreamEvent(type="usage", usage=self.script.usage)
        yield StreamEvent(type="done", finish_reason="stop")

    async def health_check(self) -> HealthCheckResult:
        return HealthCheckResult(ok=True, latency_ms=1, message="ok")

    async def list_models(self) -> list[Any]:
        return []


def _provider_class(provider_id: str) -> type[FakeProvider]:
    return type(f"Fake_{provider_id.replace('-', '_')}", (FakeProvider,), {"provider_id": provider_id, "display_name": DISPLAY[provider_id]})


class FakeRegistry:
    """Stands in for ProviderRegistry: ``build`` returns a fake adapter and a config with the chosen model."""

    def __init__(self, scripts: dict[str, Script], selected: dict[str, str | None]) -> None:
        self.scripts = scripts
        self.selected = dict(selected)
        self.missing_key: set[str] = set()
        self._classes = {pid: _provider_class(pid) for pid in DISPLAY}
        self._http = httpx.AsyncClient()

    def provider_class(self, provider_id: str) -> type[FakeProvider]:
        cls = self._classes.get(provider_id)
        if cls is None:
            raise AppError("Unknown AI provider.", code="unknown_provider", status_code=404)
        return cls

    async def build(self, db: Any, user_id: uuid.UUID, provider_id: str) -> tuple[FakeProvider, SimpleNamespace]:
        cls = self.provider_class(provider_id)
        if provider_id in self.missing_key:
            raise ProviderError(ProviderErrorCode.NOT_CONFIGURED, f"No API key is saved for {cls.display_name}. Add one in AI Models.")
        config = SimpleNamespace(selected_model=self.selected.get(provider_id), timeout_seconds=30.0, max_retries=0)
        return cls(script=self.scripts[provider_id], http=self._http), config

    async def aclose(self) -> None:
        await self._http.aclose()


# -- task engine double ---------------------------------------------------------------


class FakeTasks:
    """Stands in for TaskEngine. Records proposals; creates a real (unsaved-until-commit) Task row."""

    def __init__(self) -> None:
        self.proposals: list[dict[str, Any]] = []
        self.refusal: AppError | None = None
        self.refuse_apps: set[str] = set()  # proposals for these apps are refused, others are accepted
        self.status = "awaiting_approval"
        self.title = "Launch Google Chrome on Laptop"
        self.cancelled: list[uuid.UUID] = []

    async def propose(
        self,
        db: Any,
        *,
        user_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        source: str,
        conversation_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        title: str | None = None,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> Task:
        self.proposals.append(
            {"tool_name": tool_name, "arguments": dict(arguments), "source": source, "conversation_id": conversation_id}
        )
        if self.refusal is not None:
            raise self.refusal
        if arguments.get("app") in self.refuse_apps:
            raise AppError("No paired device matches that name.", code="device_not_found", status_code=404)
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=conversation_id,
            source=source,
            title=self.title,
            description=description,
            tool_name=tool_name,
            arguments=dict(arguments),
            risk_level="medium",
            status=self.status,
            required_permissions=["launch_app"],
            attempt=0,
            max_attempts=1,
            device_id=device_id,
        )
        db.add(task)
        return task

    async def cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        self.cancelled.append(task_id)


# -- settings double -------------------------------------------------------------------


def make_doc(
    *,
    active: str = FAKE_A,
    fallback: str | None = None,
    fallback_enabled: bool = False,
    stream: bool = True,
    include_memory: bool = False,
    store: bool = True,
    system_instructions: str = "",
    disabled_tools: tuple[str, ...] = (),
    daily_call_limit: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        ai=SimpleNamespace(
            active_provider=active,
            fallback_provider=fallback,
            fallback_enabled=fallback_enabled,
            temperature=0.5,
            max_output_tokens=512,
            system_instructions=system_instructions,
            stream_responses=stream,
            include_memory=include_memory,
            daily_call_limit=daily_call_limit,
        ),
        privacy=SimpleNamespace(store_conversations=store),
        automation=SimpleNamespace(emergency_stopped=False, disabled_tools=list(disabled_tools)),
    )


# -- environment ---------------------------------------------------------------------


@dataclass
class ChatEnv:
    app: Any
    scripts: dict[str, Script]
    registry: FakeRegistry
    tasks: FakeTasks
    doc: SimpleNamespace


@pytest_asyncio.fixture(loop_scope="session")
async def chat_env(app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ChatEnv]:
    scripts = {FAKE_A: Script(), FAKE_B: Script(chunks=["Backup ", "reply."])}
    registry = FakeRegistry(scripts, selected={FAKE_A: "model-a", FAKE_B: "model-b"})
    tasks = FakeTasks()
    app.state.providers = registry
    app.state.task_engine = tasks
    app.state.tools = ToolRegistry([LAUNCH_SPEC])
    env = ChatEnv(app=app, scripts=scripts, registry=registry, tasks=tasks, doc=make_doc())

    async def fake_load_settings(db: Any, user_id: uuid.UUID) -> SimpleNamespace:
        return env.doc

    monkeypatch.setattr(chat_module, "_load_settings", fake_load_settings)
    yield env
    await registry.aclose()


# -- helpers ---------------------------------------------------------------------------


def parse_frames(text: str) -> list[tuple[str | None, dict[str, Any] | None]]:
    """Returns (event name, decoded data) for each frame. Comment-only frames (keep-alives) are skipped."""
    frames: list[tuple[str | None, dict[str, Any] | None]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if event is None and not data_lines:
            continue
        frames.append((event, json.loads("\n".join(data_lines)) if data_lines else None))
    return frames


def event_names(frames: list[tuple[str | None, dict[str, Any] | None]]) -> list[str]:
    return [name for name, _ in frames if name is not None]


def frame_data(frames: list[tuple[str | None, dict[str, Any] | None]], name: str) -> list[dict[str, Any]]:
    return [data for event, data in frames if event == name and data is not None]


def text_of(frames: list[tuple[str | None, dict[str, Any] | None]]) -> str:
    return "".join(str(data["text"]) for event, data in frames if event == "text_delta" and data is not None)


async def new_conversation(client: AsyncClient, title: str | None = None) -> str:
    body: dict[str, Any] = {} if title is None else {"title": title}
    response = await client.post(f"{API}/conversations", json=body, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def send(client: AsyncClient, conversation_id: str, content: str, **extra: Any) -> httpx.Response:
    return await client.post(
        f"{API}/conversations/{conversation_id}/messages",
        json={"content": content, **extra},
        headers=csrf_headers(client),
    )


async def stored_messages(app: Any, conversation_id: str) -> list[dict[str, Any]]:
    async with app.state.session_factory() as db:
        rows = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == uuid.UUID(conversation_id))
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "role": row.role,
                "status": row.status,
                "content": row.content,
                "provider_id": row.provider_id,
                "model": row.model,
                "task_id": str(row.task_id) if row.task_id else None,
                "meta": dict(row.meta or {}),
            }
            for row in rows
        ]


async def owner_id(client: AsyncClient) -> uuid.UUID:
    user = await create_owner(client)
    return uuid.UUID(user["id"])


# -- tests: happy path, prompt and persistence -----------------------------------------------


async def test_send_streams_events_in_order_and_persists_both_messages(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)

    response = await send(client, cid, "Hi there, plan my week please")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    frames = parse_frames(response.text)
    assert event_names(frames) == [
        "user_message",
        "assistant_start",
        "text_delta",
        "text_delta",
        "text_delta",
        "assistant_done",
        "done",
    ]
    assert text_of(frames) == "Hello, world."
    start = frame_data(frames, "assistant_start")[0]
    assert start["provider_id"] == FAKE_A and start["model"] == "model-a"

    messages = await stored_messages(chat_env.app, cid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert all(m["status"] == "complete" for m in messages)
    assert messages[1]["content"] == "Hello, world."
    assert messages[1]["provider_id"] == FAKE_A and messages[1]["model"] == "model-a"
    assert messages[1]["meta"]["usage"]["estimated"] is False

    conversation = (await client.get(f"{API}/conversations/{cid}")).json()["conversation"]
    assert conversation["title"] == "Hi there, plan my week please"


async def test_provider_gets_untrusted_data_preamble_user_instructions_and_tool_catalogue(
    chat_env: ChatEnv, client: AsyncClient
) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(system_instructions="Answer in British English.")

    response = await send(client, cid, "Open Chrome")

    assert response.status_code == 200
    (request,) = chat_env.scripts[FAKE_A].requests
    system = request.messages[0]
    assert system.role == "system"
    assert "untrusted data" in system.content
    assert "Answer in British English." in system.content
    assert system.content.index("untrusted data") < system.content.index("Answer in British English.")
    assert request.messages[-1].role == "user" and request.messages[-1].content == "Open Chrome"
    assert [tool.name for tool in request.tools] == ["launch_app"]
    assert request.model == "model-a"
    assert request.temperature == 0.5


async def test_saved_memory_appears_in_a_labelled_block_when_memory_is_included(chat_env: ChatEnv, client: AsyncClient) -> None:
    user_id = await owner_id(client)
    async with chat_env.app.state.session_factory() as db:
        db.add(Memory(user_id=user_id, category="fact", content="Lives in Leeds", source="manual", created_at=utc_now(), updated_at=utc_now()))
        await db.commit()
    cid = await new_conversation(client)
    chat_env.doc = make_doc(include_memory=True)

    await send(client, cid, "Where do I live?")

    system = chat_env.scripts[FAKE_A].requests[0].messages[0].content
    assert "Saved memories (user-controlled, may be outdated)" in system
    assert "- Lives in Leeds" in system


async def test_custom_model_and_provider_in_the_body_are_used(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)

    response = await send(client, cid, "Hello", provider_id=FAKE_B, model="custom-model")

    frames = parse_frames(response.text)
    assert frame_data(frames, "assistant_start")[0]["provider_id"] == FAKE_B
    assert chat_env.scripts[FAKE_B].requests[0].model == "custom-model"
    assert chat_env.scripts[FAKE_A].requests == []


async def test_stream_responses_false_uses_a_single_generate_call(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(stream=False)

    response = await send(client, cid, "Hello")

    frames = parse_frames(response.text)
    deltas = frame_data(frames, "text_delta")
    assert len(deltas) == 1 and deltas[0]["text"] == "Hello, world."
    assert event_names(frames)[-2:] == ["assistant_done", "done"]
    assert chat_env.scripts[FAKE_A].requests[0].model == "model-a"
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["meta"]["usage"]["estimated"] is False


async def test_missing_usage_is_estimated_and_marked_as_estimated(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].report_usage = False
    chat_env.scripts[FAKE_A].chunks = ["Ok"]

    response = await send(client, cid, "Hello")

    assert response.status_code == 200
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    usage = assistant["meta"]["usage"]
    assert usage["estimated"] is True
    assert usage["input_tokens"] >= 1
    assert usage["output_tokens"] == 0  # "Ok" is two characters, so the estimate is 2 // 4 = 0


# -- tests: tool proposals -----------------------------------------------------------------


async def test_tool_call_becomes_a_proposal_and_the_reply_is_composed_from_the_task(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].chunks = []
    chat_env.scripts[FAKE_A].tool_calls = [
        ToolCall(id="call-1", name="launch_app", arguments={"device": "laptop", "app": "chrome"})
    ]

    response = await send(client, cid, "Open Chrome on my laptop")

    frames = parse_frames(response.text)
    assert event_names(frames) == ["user_message", "assistant_start", "tool_proposed", "text_delta", "assistant_done", "done"]
    proposed = frame_data(frames, "tool_proposed")[0]
    assert proposed["decision"] == "awaiting_approval"
    assert proposed["task"]["status"] == "awaiting_approval"
    assert proposed["task"]["actions"]["approve"] is True
    assert text_of(frames) == "I have proposed this action: Launch Google Chrome on Laptop. It needs your approval before it runs."
    assert chat_env.tasks.proposals == [
        {
            "tool_name": "launch_app",
            "arguments": {"device": "laptop", "app": "chrome"},
            "source": "chat",
            "conversation_id": uuid.UUID(cid),
        }
    ]
    assert len(chat_env.scripts[FAKE_A].requests) == 1, "the model is not called again after a proposal"

    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["task_id"] == proposed["task"]["id"]
    assert assistant["meta"]["tool_calls"][0]["decision"] == "awaiting_approval"
    assert assistant["content"] == "I have proposed this action: Launch Google Chrome on Laptop. It needs your approval before it runs."


async def test_model_text_before_a_tool_call_is_kept_and_followed_by_the_composed_status(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].chunks = ["Sure."]
    chat_env.scripts[FAKE_A].tool_calls = [ToolCall(id="c", name="launch_app", arguments={"device": "laptop", "app": "chrome"})]

    response = await send(client, cid, "Open Chrome")

    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["content"].startswith("Sure.\n\nI have proposed this action:")
    assert text_of(parse_frames(response.text)).startswith("Sure.\n\n")


@pytest.mark.parametrize(
    ("refusal", "decision", "wording"),
    [
        (AppError("No paired device matches that name.", code="device_not_found", status_code=404), "rejected", "I did not start this action: No paired device matches that name."),
        (
            AppError(
                "More than one device matches.",
                code="needs_device_selection",
                status_code=409,
                details={"candidates": [{"id": "d1", "name": "Laptop", "connection_status": "online"}, {"id": "d2", "name": "Desk PC", "connection_status": "offline"}]},
            ),
            "needs_device_selection",
            "Pick one of these devices: Laptop, Desk PC.",
        ),
    ],
)
async def test_refused_proposals_are_reported_with_the_real_reason(
    chat_env: ChatEnv, client: AsyncClient, refusal: AppError, decision: str, wording: str
) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.tasks.refusal = refusal
    chat_env.scripts[FAKE_A].chunks = []
    chat_env.scripts[FAKE_A].tool_calls = [ToolCall(id="c", name="launch_app", arguments={"device": "x", "app": "chrome"})]

    response = await send(client, cid, "Open Chrome")

    frames = parse_frames(response.text)
    proposed = frame_data(frames, "tool_proposed")[0]
    assert proposed["decision"] == decision
    assert proposed["task"] is None
    if decision == "needs_device_selection":
        assert [c["name"] for c in proposed["candidates"]] == ["Laptop", "Desk PC"]
    assert wording in text_of(frames)
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["status"] == "complete"
    assert assistant["task_id"] is None


async def test_mixed_proposals_link_the_first_accepted_task_to_the_message(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.tasks.refuse_apps = {"notepad"}
    chat_env.scripts[FAKE_A].chunks = []
    chat_env.scripts[FAKE_A].tool_calls = [
        ToolCall(id="refused", name="launch_app", arguments={"device": "laptop", "app": "notepad"}),
        ToolCall(id="accepted", name="launch_app", arguments={"device": "laptop", "app": "chrome"}),
    ]

    response = await send(client, cid, "Open two apps")

    frames = parse_frames(response.text)
    proposals = frame_data(frames, "tool_proposed")
    assert [p["decision"] for p in proposals] == ["rejected", "awaiting_approval"]
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["task_id"] == proposals[1]["task"]["id"]
    assert len(assistant["meta"]["tool_calls"]) == 2


async def test_tool_round_limit_of_zero_refuses_proposals_without_creating_tasks(
    chat_env: ChatEnv, client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    monkeypatch.setattr(app.state, "settings", app.state.settings.model_copy(update={"max_tool_rounds_per_message": 0}))
    chat_env.scripts[FAKE_A].chunks = []
    chat_env.scripts[FAKE_A].tool_calls = [ToolCall(id="c", name="launch_app", arguments={"device": "laptop", "app": "chrome"})]

    response = await send(client, cid, "Open Chrome")

    frames = parse_frames(response.text)
    assert frame_data(frames, "tool_proposed")[0]["decision"] == "rejected"
    assert chat_env.tasks.proposals == []


# -- tests: errors and fallback --------------------------------------------------------------


async def test_provider_auth_failure_yields_an_error_and_an_error_message(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].error = ProviderError(ProviderErrorCode.AUTH_FAILED, "The API key was rejected.", status_code=401)

    response = await send(client, cid, "Hello")

    frames = parse_frames(response.text)
    assert event_names(frames) == ["user_message", "assistant_start", "error", "done"]
    error = frame_data(frames, "error")[0]
    assert error["code"] == "auth_failed"
    assert error["message"] == "The API key was rejected."
    messages = await stored_messages(chat_env.app, cid)
    assert messages[-1]["status"] == "error"
    assert messages[-1]["content"] == "The API key was rejected."
    assert messages[-1]["meta"]["error"]["code"] == "auth_failed"
    assert "assistant_done" not in event_names(frames)


async def test_fallback_runs_once_when_enabled_and_is_recorded_on_the_message(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(fallback=FAKE_B, fallback_enabled=True)
    chat_env.scripts[FAKE_A].error = ProviderError(ProviderErrorCode.UNAVAILABLE, "The service is unavailable.", retryable=True)

    response = await send(client, cid, "Hello")

    frames = parse_frames(response.text)
    assert event_names(frames) == ["user_message", "assistant_start", "text_delta", "text_delta", "assistant_done", "done"]
    start = frame_data(frames, "assistant_start")[0]
    assert start["provider_id"] == FAKE_B
    assert start["fallback"]["provider_id"] == FAKE_B
    assert start["fallback"]["from_provider_id"] == FAKE_A
    assert start["fallback"]["reason"] == "unavailable"
    assert "fallback" in start["fallback"]["note"].lower()
    assert text_of(frames) == "Backup reply."
    assert len(chat_env.scripts[FAKE_A].requests) == 1
    assert len(chat_env.scripts[FAKE_B].requests) == 1
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["provider_id"] == FAKE_B and assistant["model"] == "model-b"
    assert assistant["status"] == "complete"
    assert assistant["meta"]["fallback_used"] == {"provider_id": FAKE_B, "from_provider_id": FAKE_A, "reason": "unavailable"}


async def test_fallback_never_runs_when_it_is_disabled(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(fallback=FAKE_B, fallback_enabled=False)
    chat_env.scripts[FAKE_A].error = ProviderError(ProviderErrorCode.UNAVAILABLE, "The service is unavailable.", retryable=True)

    response = await send(client, cid, "Hello")

    assert frame_data(parse_frames(response.text), "error")[0]["code"] == "unavailable"
    assert chat_env.scripts[FAKE_B].requests == []
    assert "fallback_used" not in (await stored_messages(chat_env.app, cid))[-1]["meta"]


async def test_non_retryable_errors_never_trigger_the_fallback(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(fallback=FAKE_B, fallback_enabled=True)
    chat_env.scripts[FAKE_A].error = ProviderError(ProviderErrorCode.AUTH_FAILED, "The API key was rejected.", retryable=False)

    await send(client, cid, "Hello")

    assert chat_env.scripts[FAKE_B].requests == []


async def test_a_failure_after_output_is_not_replayed_on_the_fallback(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(fallback=FAKE_B, fallback_enabled=True)
    chat_env.scripts[FAKE_A].chunks = ["Partly"]
    chat_env.scripts[FAKE_A].error_after_first_chunk = ProviderError(ProviderErrorCode.UNAVAILABLE, "Connection dropped.", retryable=True)

    response = await send(client, cid, "Hello")

    assert frame_data(parse_frames(response.text), "error")[0]["code"] == "unavailable"
    assert chat_env.scripts[FAKE_B].requests == []
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["status"] == "error"
    assert assistant["content"].startswith("Partly")


async def test_missing_model_emits_model_not_selected_and_stores_an_error_message(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.registry.selected[FAKE_A] = None

    response = await send(client, cid, "Hello")

    frames = parse_frames(response.text)
    error = frame_data(frames, "error")[0]
    assert error["code"] == "model_not_selected"
    assert "Choose a model" in error["message"]
    assert chat_env.scripts[FAKE_A].requests == []
    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["status"] == "error"
    assert assistant["model"] is None


async def test_missing_api_key_is_reported_as_not_configured(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.registry.missing_key.add(FAKE_A)

    response = await send(client, cid, "Hello")

    assert frame_data(parse_frames(response.text), "error")[0]["code"] == "not_configured"


# -- tests: storage, cancellation, outcomes ---------------------------------------------------


async def test_conversation_storage_off_keeps_messages_only_for_the_stream(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(store=False)

    response = await send(client, cid, "A private question")

    frames = parse_frames(response.text)
    assert frame_data(frames, "user_message")[0]["message"]["meta"] == {"not_stored": True}
    assert event_names(frames)[-2:] == ["assistant_done", "done"]
    assert await stored_messages(chat_env.app, cid) == []
    conversation = (await client.get(f"{API}/conversations/{cid}")).json()["conversation"]
    assert conversation["title"] == "New conversation"


async def test_disconnect_mid_reply_saves_the_partial_text_as_cancelled(chat_env: ChatEnv, client: AsyncClient) -> None:
    user = await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].chunks = ["Par", "tial", " text"]
    chat = ChatService(
        settings=chat_env.app.state.settings,
        session_factory=chat_env.app.state.session_factory,
        providers=chat_env.registry,
        tasks=chat_env.tasks,
        events=None,
        tools=chat_env.app.state.tools,
    )

    async with chat_env.app.state.session_factory() as db:
        stream = await chat.start_send(
            db,
            user_id=uuid.UUID(user["id"]),
            conversation_id=uuid.UUID(cid),
            body=ChatMessageIn(content="Tell me a story"),
        )
    seen: list[str] = []
    async for event in stream:
        seen.append(event.event or "")
        if event.event == "text_delta":
            break
    await stream.aclose()

    assert seen[-1] == "text_delta"
    messages = await stored_messages(chat_env.app, cid)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["status"] == "cancelled"
    assert messages[-1]["content"] == "Par"
    assert not chat._guard.is_active(uuid.UUID(cid)), "the conversation must accept a new reply after a disconnect"


async def test_cancelled_request_saves_the_partial_reply_as_cancelled(chat_env: ChatEnv, client: AsyncClient) -> None:
    """A dropped connection cancels the request task. The provider is stuck mid-reply at that point."""
    user = await create_owner(client)
    cid = await new_conversation(client)
    chat_env.scripts[FAKE_A].chunks = ["Par", "tial"]
    chat_env.scripts[FAKE_A].hold_after_first = asyncio.Event()  # never set: the reply is cut off here
    chat = ChatService(
        settings=chat_env.app.state.settings,
        session_factory=chat_env.app.state.session_factory,
        providers=chat_env.registry,
        tasks=chat_env.tasks,
        events=None,
        tools=chat_env.app.state.tools,
    )
    async with chat_env.app.state.session_factory() as db:
        stream = await chat.start_send(
            db,
            user_id=uuid.UUID(user["id"]),
            conversation_id=uuid.UUID(cid),
            body=ChatMessageIn(content="Tell me a story"),
        )
    got_text = asyncio.Event()

    async def consume() -> None:
        async for event in stream:
            if event.event == "text_delta":
                got_text.set()

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(got_text.wait(), timeout=5)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assistant = (await stored_messages(chat_env.app, cid))[-1]
    assert assistant["status"] == "cancelled"
    assert assistant["content"] == "Par"
    assert not chat._guard.is_active(uuid.UUID(cid))


async def test_every_model_call_is_recorded_for_usage(chat_env: ChatEnv, client: AsyncClient, app: Any) -> None:
    user_id = await owner_id(client)
    cid = await new_conversation(client)

    await send(client, cid, "streamed call")
    chat_env.doc = make_doc(stream=False)
    await send(client, cid, "single call")
    chat_env.doc = make_doc(fallback=FAKE_B, fallback_enabled=True)
    chat_env.scripts[FAKE_A].error = ProviderError(ProviderErrorCode.UNAVAILABLE, "The service is unavailable.", retryable=True)
    await send(client, cid, "fallback call")

    async with app.state.session_factory() as db:
        rows = (
            await db.scalars(select(UsageRecord).where(UsageRecord.user_id == user_id).order_by(UsageRecord.created_at))
        ).all()
    summary = [(row.provider_id, row.operation, row.success, row.error_code) for row in rows]
    assert summary == [
        (FAKE_A, "stream", True, None),
        (FAKE_A, "chat", True, None),
        (FAKE_A, "stream", False, "unavailable"),
        (FAKE_B, "stream", True, None),
    ]
    assert rows[0].input_tokens == 11 and rows[0].tokens_estimated is False


async def test_generate_path_tool_calls_are_proposed_too(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(stream=False)
    chat_env.scripts[FAKE_A].chunks = []
    chat_env.scripts[FAKE_A].tool_calls = [ToolCall(id="g", name="launch_app", arguments={"device": "laptop", "app": "chrome"})]

    response = await send(client, cid, "Open Chrome")

    frames = parse_frames(response.text)
    assert frame_data(frames, "tool_proposed")[0]["decision"] == "awaiting_approval"
    assert chat_env.tasks.proposals[0]["arguments"] == {"device": "laptop", "app": "chrome"}


async def test_concurrent_send_in_the_same_conversation_returns_409(chat_env: ChatEnv, client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    gate = asyncio.Event()
    started = asyncio.Event()
    chat_env.scripts[FAKE_A].gate = gate
    chat_env.scripts[FAKE_A].started = started

    first = asyncio.create_task(send(client, cid, "first"))
    await asyncio.wait_for(started.wait(), timeout=5)
    second = await send(client, cid, "second")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "generation_in_progress"
    gate.set()
    first_response = await first
    assert first_response.status_code == 200
    assert "assistant_done" in event_names(parse_frames(first_response.text))


async def test_send_validation_rules(chat_env: ChatEnv, client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    cid = await new_conversation(client)
    too_long = "a" * (app.state.settings.max_message_chars + 1)

    blank = await send(client, cid, "   ")
    long = await send(client, cid, too_long)
    unknown = await send(client, cid, "hi", provider_id="nope")
    missing = await send(client, str(uuid.uuid4()), "hi")

    assert blank.status_code == 422 and blank.json()["error"]["code"] == "validation_error"
    assert long.status_code == 422 and long.json()["error"]["code"] == "validation_error"
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_provider"
    assert missing.status_code == 404
    assert await stored_messages(app, cid) == []


async def test_task_outcome_is_appended_once_from_the_task_fields(chat_env: ChatEnv, client: AsyncClient) -> None:
    user_id = await owner_id(client)
    cid = await new_conversation(client)
    conversation_uuid = uuid.UUID(cid)

    async with chat_env.app.state.session_factory() as db:
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=conversation_uuid,
            source="chat",
            title="Launch Google Chrome on Laptop",
            description="",
            tool_name="launch_app",
            arguments={"device": "laptop", "app": "chrome"},
            risk_level="medium",
            status="completed",
            result={"summary": "Chrome opened on your laptop."},
            required_permissions=["launch_app"],
            attempt=1,
            max_attempts=1,
        )
        db.add(task)
        await db.flush()
        first = await post_task_outcome_message(db, task)
        await db.commit()
    async with chat_env.app.state.session_factory() as db:
        second = await post_task_outcome_message(db, task)
        await db.commit()

    assert first is not None and second is not None
    assert first.id == second.id
    assert first.content == "Done: Launch Google Chrome on Laptop. Chrome opened on your laptop."
    messages = await stored_messages(chat_env.app, cid)
    assert [m["role"] for m in messages] == ["tool", "assistant"]
    assert messages[0]["content"] == "Chrome opened on your laptop."
    assert messages[1]["task_id"] == str(task.id)


async def test_failed_task_outcome_states_the_real_error(chat_env: ChatEnv, client: AsyncClient) -> None:
    user_id = await owner_id(client)
    cid = await new_conversation(client)

    async with chat_env.app.state.session_factory() as db:
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=uuid.UUID(cid),
            source="chat",
            title="Launch Google Chrome on Laptop",
            description="",
            tool_name="launch_app",
            arguments={},
            risk_level="medium",
            status="failed",
            error={"code": "not_installed", "message": "Google Chrome is not installed on this laptop."},
            required_permissions=[],
            attempt=1,
            max_attempts=1,
        )
        db.add(task)
        await db.flush()
        reply = await post_task_outcome_message(db, task)
        await db.commit()

    assert reply is not None
    assert reply.content == "It did not work: Launch Google Chrome on Laptop. Google Chrome is not installed on this laptop."


async def test_task_outcome_is_not_posted_when_conversations_are_not_stored(chat_env: ChatEnv, client: AsyncClient) -> None:
    user_id = await owner_id(client)
    cid = await new_conversation(client)
    chat_env.doc = make_doc(store=False)

    async with chat_env.app.state.session_factory() as db:
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=uuid.UUID(cid),
            source="chat",
            title="Launch Google Chrome",
            description="",
            tool_name="launch_app",
            arguments={},
            risk_level="medium",
            status="completed",
            result={"summary": "Done."},
            required_permissions=[],
            attempt=1,
            max_attempts=1,
        )
        db.add(task)
        await db.flush()
        assert await post_task_outcome_message(db, task) is None
        await db.commit()
    assert await stored_messages(chat_env.app, cid) == []


async def test_task_still_running_is_not_reported_as_an_outcome(chat_env: ChatEnv, client: AsyncClient) -> None:
    user_id = await owner_id(client)
    cid = await new_conversation(client)

    async with chat_env.app.state.session_factory() as db:
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=uuid.UUID(cid),
            source="chat",
            title="Launch Google Chrome",
            description="",
            tool_name="launch_app",
            arguments={},
            risk_level="medium",
            status="running",
            required_permissions=[],
            attempt=1,
            max_attempts=1,
        )
        db.add(task)
        await db.flush()
        assert await post_task_outcome_message(db, task) is None
        await db.commit()
