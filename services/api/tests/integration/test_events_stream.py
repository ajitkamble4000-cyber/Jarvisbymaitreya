"""Server-sent events: the frame generator (unit level) and the /events/stream route (real ASGI, cookie auth).

httpx's ASGI transport buffers a response body until the app finishes, so an open-ended stream cannot be
read through it. The route tests therefore drive the ASGI app directly: they read the first chunks from
the send channel with timeouts and then simulate a client disconnect.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.routes.events import event_frames
from app.services.events import EventBus
from tests.conftest import create_owner

STREAM_PATH = "/api/v1/events/stream"
WAIT = 5.0


async def _never_disconnected() -> bool:
    return False


async def _always_disconnected() -> bool:
    return True


def _parse_frame(frame: bytes) -> tuple[dict[str, str], Any]:
    """Splits one SSE frame into its fields. Returns ({"id", "event"}, decoded data)."""
    fields: dict[str, str] = {}
    data_lines: list[str] = []
    for line in frame.decode("utf-8").split("\n"):
        if not line:
            continue
        name, _, value = line.partition(": ")
        if name == "data":
            data_lines.append(value)
        else:
            fields[name] = value
    data = json.loads("\n".join(data_lines)) if data_lines else None
    return fields, data


async def _next_frame(frames: AsyncIterator[bytes]) -> bytes:
    return await asyncio.wait_for(frames.__anext__(), WAIT)


# -- generator -----------------------------------------------------------------


async def test_frames_deliver_an_allowed_event_for_the_user() -> None:
    bus = EventBus()
    user = uuid.uuid4()
    frames = event_frames(bus, user, 60.0, _never_disconnected)

    assert await _next_frame(frames) == b": connected\n\n"
    bus.publish(user, "task.updated", {"task_id": "t-1", "status": "completed"})
    fields, data = _parse_frame(await _next_frame(frames))

    assert fields["event"] == "task.updated"
    assert fields["id"].isdigit()
    assert data == {"task_id": "t-1", "status": "completed"}
    await frames.aclose()


async def test_frames_drop_event_types_that_are_not_on_the_allowlist() -> None:
    bus = EventBus()
    user = uuid.uuid4()
    frames = event_frames(bus, user, 60.0, _never_disconnected)

    await _next_frame(frames)
    bus.publish(user, "internal.debug", {"detail": "not for the browser"})
    bus.publish(user, "chat.message", {"conversation_id": "c-1", "message_id": "m-1"})
    fields, data = _parse_frame(await _next_frame(frames))

    assert fields["event"] == "chat.message"
    assert data == {"conversation_id": "c-1", "message_id": "m-1"}
    await frames.aclose()


async def test_another_users_events_are_never_delivered() -> None:
    bus = EventBus()
    user, neighbour = uuid.uuid4(), uuid.uuid4()
    frames = event_frames(bus, user, 60.0, _never_disconnected)

    await _next_frame(frames)
    bus.publish(neighbour, "notification.created", {"kind": "task", "severity": "info"})
    bus.publish(user, "device.updated", {"device_id": "d-1", "connection_status": "online"})
    fields, data = _parse_frame(await _next_frame(frames))

    assert fields["event"] == "device.updated"
    assert data == {"device_id": "d-1", "connection_status": "online"}
    await frames.aclose()


async def test_heartbeat_arrives_on_the_configured_interval() -> None:
    bus = EventBus()
    user = uuid.uuid4()
    frames = event_frames(bus, user, 0.05, _never_disconnected)

    await _next_frame(frames)
    beats = [_parse_frame(await _next_frame(frames)) for _ in range(2)]

    for fields, data in beats:
        assert fields == {"event": "heartbeat"}
        assert data["time"].endswith("Z")
    await frames.aclose()


async def test_generator_stops_on_disconnect_and_unsubscribes() -> None:
    bus = EventBus()
    user = uuid.uuid4()
    frames = event_frames(bus, user, 60.0, _always_disconnected)

    assert await _next_frame(frames) == b": connected\n\n"
    with pytest.raises(StopAsyncIteration):
        await _next_frame(frames)
    assert bus.subscriber_count(user) == 0


async def test_generator_rejects_a_non_positive_heartbeat() -> None:
    frames = event_frames(EventBus(), uuid.uuid4(), 0.0, _never_disconnected)
    with pytest.raises(ValueError):
        await frames.__anext__()


# -- HTTP route ----------------------------------------------------------------


async def _open_stream(app: Any, token: str) -> tuple[asyncio.Task[None], asyncio.Queue[dict[str, Any]], asyncio.Event]:
    """Runs the ASGI app for one streaming GET. Returns the task, the messages it sent and a disconnect switch."""
    outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    disconnect = asyncio.Event()
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        await outbox.put(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": STREAM_PATH,
        "raw_path": STREAM_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"cookie", f"jarvis_session={token}".encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    return task, outbox, disconnect


async def _next_body(outbox: asyncio.Queue[dict[str, Any]]) -> bytes:
    while True:
        message = await asyncio.wait_for(outbox.get(), WAIT)
        if message["type"] == "http.response.body" and message.get("body"):
            return bytes(message["body"])


async def test_stream_route_returns_event_stream_and_forwards_the_users_events(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    token = client.cookies.get("jarvis_session")
    assert token

    task, outbox, disconnect = await _open_stream(app, token)
    start = await asyncio.wait_for(outbox.get(), WAIT)
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"

    assert await _next_body(outbox) == b": connected\n\n"

    app.state.events.publish(uuid.uuid4(), "task.updated", {"task_id": "not-mine", "status": "running"})
    app.state.events.publish(user_id, "approval.requested", {"task_id": "mine", "title": "Launch Chrome"})
    fields, data = _parse_frame(await _next_body(outbox))
    assert fields["event"] == "approval.requested"
    assert data == {"task_id": "mine", "title": "Launch Chrome"}

    disconnect.set()
    await asyncio.wait_for(task, WAIT)
    assert app.state.events.subscriber_count(user_id) == 0


async def test_stream_route_requires_a_session(client: AsyncClient) -> None:
    response = await client.get(STREAM_PATH)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_an_open_stream_ends_when_its_session_is_revoked(client: AsyncClient, app: Any) -> None:
    from sqlalchemy import update

    from app.core.time import utc_now
    from app.models.identity import UserSession
    from app.security.tokens import sha256_hex

    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    token = client.cookies.get("jarvis_session")
    assert token

    task, outbox, _disconnect = await _open_stream(app, token)
    assert (await asyncio.wait_for(outbox.get(), WAIT))["type"] == "http.response.start"
    assert await _next_body(outbox) == b": connected\n\n"

    # Sign out: the session row is revoked while the stream is open.
    async with app.state.session_factory() as db:
        await db.execute(
            update(UserSession).where(UserSession.token_hash == sha256_hex(token)).values(revoked_at=utc_now())
        )
        await db.commit()
    app.state.events.publish(user_id, "approval.requested", {"task_id": "after-sign-out", "title": "Secret"})

    await asyncio.wait_for(task, WAIT)
    bodies: list[bytes] = []
    while not outbox.empty():
        message = outbox.get_nowait()
        if message["type"] == "http.response.body" and message.get("body"):
            bodies.append(bytes(message["body"]))
    assert not any(b"after-sign-out" in body for body in bodies)
