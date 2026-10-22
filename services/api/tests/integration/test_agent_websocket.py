"""Agent WebSocket hub over a real uvicorn server running in the pytest event loop.

The agent side uses the websockets client library. Close codes, the hello handshake, heartbeats,
signed commands, results, disconnects, heartbeat timeouts and revocation are exercised end to end.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

import app.devices.hub as hub_module
from app.core.time import utc_now
from app.devices.hub import DeviceHub
from app.devices.protocol import build_cancel, build_command
from app.main import create_app
from app.models.devices import Device
from app.models.platform import Notification
from app.security.signing import verify_fields
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"


# ---- helpers ----------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def running_server(application: object) -> AsyncIterator[str]:
    """Serve ``application`` with uvicorn inside the current event loop. Yields the ws:// base URL."""
    config = uvicorn.Config(application, host="127.0.0.1", port=_free_port(), lifespan="off", log_level="warning")  # type: ignore[arg-type]
    server = uvicorn.Server(config)
    serve = asyncio.create_task(server.serve())
    try:
        for _ in range(400):
            if server.started:
                break
            if serve.done():
                serve.result()
                raise RuntimeError("uvicorn exited before it started")
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError("uvicorn did not start")
        yield f"ws://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serve, timeout=10)


class FakeAgent:
    """Stand-in for the desktop agent. Answers pings, queues every other frame, and records the close code."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump = asyncio.create_task(self._run())

    async def _run(self) -> None:
        with suppress(ConnectionClosed):
            async for raw in self.ws:
                message = json.loads(raw)
                if message.get("type") == "ping":
                    await self.ws.send(json.dumps({"type": "pong", "nonce": message["nonce"]}))
                else:
                    self.frames.put_nowait(message)

    async def send(self, message: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(message))

    async def expect(self, kind: str, timeout: float = 5.0) -> dict[str, Any]:
        async def _next() -> dict[str, Any]:
            while True:
                message = await self.frames.get()
                if message.get("type") == kind:
                    return message

        return await asyncio.wait_for(_next(), timeout)

    async def close_code(self, timeout: float = 5.0) -> int | None:
        await asyncio.wait_for(asyncio.shield(self._pump), timeout)
        return self.ws.close_code

    async def aclose(self) -> None:
        with suppress(ConnectionClosed):
            await self.ws.close()
        with suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self._pump), 5)


async def connect_agent(base_url: str, secret: str) -> FakeAgent:
    ws = await connect(
        f"{base_url}/api/v1/agent/ws",
        additional_headers={"Authorization": f"Bearer {secret}"},
        open_timeout=5,
    )
    return FakeAgent(ws)


def hello_frame(**overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "hello",
        "agent_version": "1.0.0",
        "os": "windows",
        "os_version": "10.0.22631",
        "capabilities": ["open_url", "system_info"],
        "resources": {"urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}]},
    }
    frame.update(overrides)
    return frame


async def _wait_for(predicate: Callable[[], bool | Awaitable[bool]], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.02)


async def _device_row(application: object, device_id: uuid.UUID) -> Device:
    async with application.state.session_factory() as db:  # type: ignore[attr-defined]
        row = await db.get(Device, device_id)
    assert row is not None
    return row


async def _notifications(application: object, kind: str) -> list[Notification]:
    async with application.state.session_factory() as db:  # type: ignore[attr-defined]
        return list((await db.scalars(select(Notification).where(Notification.kind == kind))).all())


class RecordingHandler:
    """Stands in for the task engine so the hub's callbacks can be observed."""

    def __init__(self) -> None:
        self.results: list[tuple[uuid.UUID, Any]] = []
        self.connected: list[uuid.UUID] = []
        self.disconnected: list[uuid.UUID] = []

    async def on_agent_result(self, device_id: uuid.UUID, message: Any) -> None:
        self.results.append((device_id, message))

    async def on_agent_progress(self, device_id: uuid.UUID, message: Any) -> None:
        return None

    async def on_device_connected(self, device_id: uuid.UUID) -> None:
        self.connected.append(device_id)

    async def on_device_disconnected(self, device_id: uuid.UUID) -> None:
        self.disconnected.append(device_id)


async def _pair_with_owner(owner: AsyncClient, application: object) -> dict[str, Any]:
    """Issue a code through the owner's session, then pair as the agent (no cookies)."""
    issued = await owner.post(f"{API}/devices/pairing-codes", headers=csrf_headers(owner))
    assert issued.status_code == 201, issued.text
    transport = ASGITransport(app=application)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as agent:
        response = await agent.post(
            f"{API}/agent/pair",
            json={
                "code": issued.json()["code"],
                "device_name": "Laptop",
                "device_type": "laptop",
                "os": "windows",
                "capabilities": ["open_url", "system_info"],
                "resources": {},
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"device_id": uuid.UUID(body["device_id"]), "secret": body["device_secret"]}


# ---- fixtures ---------------------------------------------------------------------------

@pytest_asyncio.fixture(loop_scope="session")
async def paired(app: object, client: AsyncClient) -> dict[str, Any]:
    await create_owner(client)
    return await _pair_with_owner(client, app)


@pytest_asyncio.fixture(loop_scope="session")
async def handler(app: object) -> RecordingHandler:
    recorder = RecordingHandler()
    app.state.device_hub.bind(recorder)  # type: ignore[attr-defined]
    return recorder


@pytest_asyncio.fixture(loop_scope="session")
async def live(app: object, paired: dict[str, Any], handler: RecordingHandler) -> AsyncIterator[str]:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    await hub.start()
    try:
        async with running_server(app) as base_url:
            yield base_url
    finally:
        await hub.stop()


# ---- handshake --------------------------------------------------------------------------

async def test_unknown_secret_is_closed_with_4401(live: str) -> None:
    agent = await connect_agent(live, "not-a-real-secret")
    assert await agent.close_code() == 4401
    await agent.aclose()


async def test_revoked_device_cannot_reconnect_with_its_old_secret(app: object, client: AsyncClient, paired: dict, live: str) -> None:
    """Revocation erases the stored hash, so the old secret no longer maps to any device (4401, not 4403)."""
    response = await client.post(f"{API}/devices/{paired['device_id']}/revoke", json={}, headers=csrf_headers(client))
    assert response.status_code == 200
    agent = await connect_agent(live, paired["secret"])
    assert await agent.close_code() == 4401
    await agent.aclose()


async def test_revoked_device_that_still_holds_a_credential_is_closed_with_4403(app: object, paired: dict, live: str) -> None:
    """Defence in depth: a device recorded as revoked is refused with 4403 even if its hash were still present."""
    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        await db.execute(
            update(Device)
            .where(Device.id == paired["device_id"])
            .values(pairing_status="revoked", revoked_at=utc_now())
        )
        await db.commit()
    agent = await connect_agent(live, paired["secret"])
    assert await agent.close_code() == 4403
    await agent.aclose()


async def test_hello_is_acknowledged_and_the_device_comes_online(app: object, client: AsyncClient, paired: dict, live: str) -> None:
    device_id = paired["device_id"]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    ack = await agent.expect("hello_ack")
    assert ack["device_id"] == str(device_id)
    assert ack["heartbeat_interval_seconds"] == 15
    assert ack["protocol_version"] == 1
    assert app.state.device_hub.is_online(device_id)  # type: ignore[attr-defined]

    listed = (await client.get(f"{API}/devices")).json()["items"][0]
    assert listed["connection_status"] == "online"
    assert listed["capabilities"] == ["open_url", "system_info"]
    assert listed["resources"]["urls"] == [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}]
    await agent.aclose()


async def test_missing_hello_is_closed_with_4408(app: object, paired: dict, live: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub_module, "HELLO_TIMEOUT_SECONDS", 0.3)
    agent = await connect_agent(live, paired["secret"])
    assert await agent.close_code() == 4408


async def test_first_frame_must_be_hello(app: object, paired: dict, live: str) -> None:
    agent = await connect_agent(live, paired["secret"])
    await agent.send({"type": "heartbeat", "running_command_ids": [], "sent_at": "2026-10-19T04:51:37Z"})
    assert await agent.close_code() == 1008


async def test_a_second_connection_replaces_the_first_with_4409(app: object, paired: dict, live: str) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    first = await connect_agent(live, paired["secret"])
    await first.send(hello_frame())
    await first.expect("hello_ack")

    second = await connect_agent(live, paired["secret"])
    await second.send(hello_frame())
    await second.expect("hello_ack")

    assert await first.close_code() == 4409
    assert hub.is_online(paired["device_id"])
    assert await _notifications(app, "device_disconnected") == []
    await second.aclose()


# ---- frames, heartbeats, commands -------------------------------------------------------

async def test_heartbeat_refreshes_last_seen_and_remembers_running_commands(app: object, paired: dict, live: str) -> None:
    device_id = paired["device_id"]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")

    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        await db.execute(
            update(Device).where(Device.id == device_id).values(last_seen_at=datetime(2020, 1, 1, tzinfo=UTC))
        )
        await db.commit()

    sent_at = utc_now()
    await agent.send({"type": "heartbeat", "running_command_ids": ["cmd-1"], "sent_at": "2026-10-19T04:52:00Z"})

    async def seen_updated() -> bool:
        row = await _device_row(app, device_id)
        return row.last_seen_at is not None and row.last_seen_at >= sent_at

    await _wait_for(seen_updated)
    assert app.state.device_hub.running_command_ids(device_id) == ("cmd-1",)  # type: ignore[attr-defined]
    await agent.aclose()


async def test_signed_command_reaches_the_agent_and_forgeries_are_refused(app: object, paired: dict, live: str) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    device_id = paired["device_id"]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")

    command = build_command(paired["secret"], action="open_url", args={"url_id": "youtube"}, ttl_seconds=60)
    assert await hub.send_command(device_id, command) is True
    frame = await agent.expect("command")
    fields = {
        "id": frame["id"],
        "action": frame["action"],
        "issued_at": frame["issued_at"],
        "expires_at": frame["expires_at"],
        "nonce": frame["nonce"],
        "args_json": frame["args_json"],
    }
    assert verify_fields(paired["secret"], fields, frame["signature"])
    assert json.loads(frame["args_json"]) == {"url_id": "youtube"}
    assert not verify_fields(paired["secret"], {**fields, "args_json": '{"url_id":"other"}'}, frame["signature"])

    forged = build_command("some-other-secret", action="open_url", args={"url_id": "youtube"}, ttl_seconds=60)
    assert await hub.send_command(device_id, forged) is False

    cancel = build_cancel(paired["secret"], command_id=frame["id"])
    assert await hub.send_cancel(device_id, cancel) is True
    cancelled = await agent.expect("cancel")
    assert cancelled["command_id"] == frame["id"]
    assert await hub.send_cancel(device_id, build_cancel("other-secret", command_id=frame["id"])) is False

    with pytest.raises(TimeoutError):
        await agent.expect("command", timeout=0.3)  # the forged command was never sent
    await agent.aclose()


async def test_commands_to_an_offline_device_return_false(app: object, paired: dict) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    command = build_command(paired["secret"], action="open_url", args={"url_id": "youtube"}, ttl_seconds=60)
    assert await hub.send_command(paired["device_id"], command) is False
    assert await hub.send_command(uuid.uuid4(), command) is False


async def test_result_from_the_agent_reaches_the_task_handler(app: object, paired: dict, live: str, handler: RecordingHandler) -> None:
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")
    await agent.send(
        {
            "type": "result",
            "command_id": "9b7e0000-0000-4000-8000-000000000001",
            "status": "succeeded",
            "output": {"verified": False, "summary": "Opened YouTube."},
            "error": None,
            "started_at": None,
            "finished_at": "2026-10-19T04:52:00Z",
        }
    )
    await _wait_for(lambda: len(handler.results) == 1)
    device_id, message = handler.results[0]
    assert device_id == paired["device_id"]
    assert message.command_id == "9b7e0000-0000-4000-8000-000000000001"
    assert message.status == "succeeded"
    await agent.aclose()


async def test_five_bad_frames_close_with_1008(app: object, paired: dict, live: str) -> None:
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")
    for _ in range(4):
        await agent.ws.send("{not json")
    await agent.send({"type": "run_shell", "cmd": "dir"})
    for _ in range(5):
        error = await agent.expect("error")
        assert error["code"] == "invalid_frame"
    assert await agent.close_code() == 1008


# ---- disconnects, timeouts and revocation -----------------------------------------------

async def test_disconnect_notifies_the_handler_and_records_a_warning(app: object, paired: dict, live: str, handler: RecordingHandler) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")
    assert paired["device_id"] in handler.connected

    await agent.aclose()
    await _wait_for(lambda: paired["device_id"] in handler.disconnected)
    assert not hub.is_online(paired["device_id"])

    # The recording handler is notified before the real handler writes the row, so wait for the row itself.
    async def _written() -> bool:
        return len(await _notifications(app, "device_disconnected")) == 1

    await _wait_for(_written)
    notices = await _notifications(app, "device_disconnected")
    assert len(notices) == 1
    assert notices[0].severity == "warning"
    assert notices[0].related_id == str(paired["device_id"])
    assert paired["secret"] not in notices[0].body


async def test_heartbeat_timeout_closes_with_1001_and_marks_the_device_offline(
    app: object,
    settings: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs its own app with a one-second heartbeat window so the real monitor loop fires quickly.

    The ``app`` fixture is requested only so the database is truncated before this test creates its owner.
    """
    monkeypatch.setattr(hub_module, "HEARTBEAT_CHECK_INTERVAL_SECONDS", 0.1)
    short = create_app(settings.model_copy(update={"device_heartbeat_timeout_seconds": 1}))  # type: ignore[attr-defined]
    async with short.router.lifespan_context(short):
        async with AsyncClient(
            transport=ASGITransport(app=short),
            base_url="http://testserver",
            headers={"Origin": TEST_ORIGIN},
        ) as owner:
            await create_owner(owner)
            device = await _pair_with_owner(owner, short)

        hub: DeviceHub = short.state.device_hub
        recorder = RecordingHandler()
        hub.bind(recorder)
        await hub.start()
        try:
            async with running_server(short) as base_url:
                agent = await connect_agent(base_url, device["secret"])
                await agent.send(hello_frame())
                await agent.expect("hello_ack")
                assert await agent.close_code(timeout=5) == 1001
        finally:
            await hub.stop()

        assert not hub.is_online(device["device_id"])
        assert device["device_id"] in recorder.disconnected
        assert len(await _notifications(short, "device_disconnected")) == 1
        row = await _device_row(short, device["device_id"])
        assert row.pairing_status == "paired"


async def test_revoking_a_connected_device_closes_with_4403_and_does_not_notify(
    app: object,
    client: AsyncClient,
    paired: dict,
    live: str,
) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")

    response = await client.post(
        f"{API}/devices/{paired['device_id']}/revoke",
        json={"reason": "retired"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    assert await agent.close_code() == 4403
    assert not hub.is_online(paired["device_id"])
    assert await _notifications(app, "device_disconnected") == []

    listed = (await client.get(f"{API}/devices")).json()["items"][0]
    assert listed["connection_status"] == "revoked"
    await agent.aclose()


async def test_ping_measures_round_trip_latency(app: object, paired: dict, live: str) -> None:
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    agent = await connect_agent(live, paired["secret"])
    await agent.send(hello_frame())
    await agent.expect("hello_ack")

    latency = await hub.ping(paired["device_id"], timeout_seconds=2.0)
    assert isinstance(latency, int) and latency >= 0
    await agent.aclose()
    assert await hub.ping(paired["device_id"], timeout_seconds=0.5) is None


async def test_ping_of_a_device_that_never_answers_returns_none(app: object, paired: dict, live: str) -> None:
    """A connected agent that ignores pings is reported as not answering rather than hanging the request."""
    hub: DeviceHub = app.state.device_hub  # type: ignore[attr-defined]
    ws = await connect(
        f"{live}/api/v1/agent/ws",
        additional_headers={"Authorization": f"Bearer {paired['secret']}"},
        open_timeout=5,
    )
    try:
        await ws.send(json.dumps(hello_frame()))
        ack = json.loads(await ws.recv())
        assert ack["type"] == "hello_ack"
        assert await hub.ping(paired["device_id"], timeout_seconds=0.3) is None
    finally:
        with suppress(ConnectionClosed):
            await ws.close()


async def test_secret_hash_matches_the_stored_credential(app: object, paired: dict) -> None:
    row = await _device_row(app, paired["device_id"])
    assert row.credential_hash == hashlib.sha256(paired["secret"].encode()).hexdigest()
