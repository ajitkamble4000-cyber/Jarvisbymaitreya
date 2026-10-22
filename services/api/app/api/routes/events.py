"""Server-sent events stream for live UI updates.

Events are signals, not data: a client that receives one re-reads the matching REST resource. Only the
types listed in docs/api.md are forwarded. Anything else published on the bus is dropped here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import CurrentSession, DbSession, SettingsDep
from app.core.time import to_iso, utc_now
from app.models.identity import User, UserSession
from app.services.events import EventBus, ServerEvent
from app.services.sse import SSE_HEADERS, dumps_payload, validate_event_name

router = APIRouter(prefix="/events", tags=["events"])

STREAM_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.updated",
        "approval.requested",
        "device.updated",
        "schedule.updated",
        "chat.message",
        "notification.created",
    }
)
HEARTBEAT_EVENT = "heartbeat"
# Upper bound on how long the stream waits before checking for a client disconnect.
_DISCONNECT_POLL_SECONDS = 1.0
_CONNECTED_FRAME = b": connected\n\n"


async def session_is_active(
    session_factory: async_sessionmaker,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """True while the session that opened the stream is still signed in and its account is active."""
    async with session_factory() as db:
        row = await db.scalar(
            select(UserSession.id)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > utc_now(),
                User.is_active.is_(True),
            )
        )
    return row is not None


def _event_frame(event: ServerEvent) -> bytes:
    name = validate_event_name(event.type)
    return f"id: {event.id}\nevent: {name}\ndata: {dumps_payload(event.data)}\n\n".encode()


def _heartbeat_frame() -> bytes:
    payload = dumps_payload({"time": to_iso(utc_now())})
    return f"event: {HEARTBEAT_EVENT}\ndata: {payload}\n\n".encode()


async def event_frames(
    events: EventBus,
    user_id: uuid.UUID,
    heartbeat_seconds: float,
    is_disconnected: Callable[[], Awaitable[bool]],
    is_signed_in: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[bytes]:
    """Encoded SSE frames for one user's events, with a heartbeat every ``heartbeat_seconds``.

    The first frame is a comment sent as soon as the subscription exists, so the client sees headers
    and a body without waiting for the first heartbeat. The generator ends when ``is_disconnected``
    reports True, and it always unsubscribes on exit.

    ``is_signed_in`` is checked before every frame. A stream opened by a session that has since been
    signed out, revoked or expired ends at once and sends nothing more.
    """
    if heartbeat_seconds <= 0:
        raise ValueError("Heartbeat interval must be positive.")
    loop = asyncio.get_running_loop()
    async with events.subscribe(user_id) as queue:
        yield _CONNECTED_FRAME
        next_heartbeat = loop.time() + heartbeat_seconds
        while True:
            if await is_disconnected():
                return
            now = loop.time()
            if now >= next_heartbeat:
                if is_signed_in is not None and not await is_signed_in():
                    return
                yield _heartbeat_frame()
                next_heartbeat = now + heartbeat_seconds
                continue
            wait = min(next_heartbeat - now, _DISCONNECT_POLL_SECONDS)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=wait)
            except TimeoutError:
                continue
            if event.type in STREAM_EVENT_TYPES:
                if is_signed_in is not None and not await is_signed_in():
                    return
                yield _event_frame(event)


@router.get("/stream")
async def stream(request: Request, db: DbSession, session: CurrentSession, settings: SettingsDep) -> StreamingResponse:
    # Read the ids first: rollback expires loaded rows, and the stream must not touch the request's session.
    user_id = session.user_id
    session_id = session.id
    # The stream is open-ended. Release the request's database connection before it starts.
    await db.rollback()
    session_factory = request.app.state.session_factory

    async def signed_in() -> bool:
        return await session_is_active(session_factory, session_id, user_id)

    frames = event_frames(
        request.app.state.events,
        user_id,
        settings.sse_heartbeat_seconds,
        request.is_disconnected,
        signed_in,
    )
    return StreamingResponse(frames, media_type="text/event-stream", headers=SSE_HEADERS)
