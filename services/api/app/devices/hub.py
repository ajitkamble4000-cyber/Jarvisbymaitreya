"""Device hub: owns the agent WebSocket connections, heartbeats, and command dispatch.

Constructor and method signatures are a contract shared with ``services.task_engine``
(see docs/architecture.md). The hub is in-process: it holds the connection registry for this
API instance. Several API instances would need a shared registry before agents could be spread
across them.

Handshake and close codes follow docs/desktop-protocol.md. Authentication failures are answered
after the upgrade with the protocol close code (4401/4403), so the agent can read the code; a
rejection before the upgrade would reach the agent only as an HTTP 403.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from fastapi import WebSocket
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.time import utc_now
from app.database.session import session_scope
from app.devices.permissions import merge_default_permissions
from app.devices.protocol import (
    CANCEL_ACTION,
    AgentHeartbeat,
    AgentHello,
    AgentMessage,
    AgentPong,
    AgentProgress,
    AgentResult,
    CancelMessage,
    CommandMessage,
    HelloAck,
    PingMessage,
    ServerMessageError,
    agent_message_adapter,
    utc_iso,
)
from app.models.devices import Device
from app.schemas.devices import filter_capabilities, sanitise_resources
from app.security.crypto import SecretBox
from app.security.signing import verify_fields
from app.security.tokens import new_token, sha256_hex
from app.services.events import EventBus
from app.services.notifications import notify

log = logging.getLogger("jarvis.devices")

HELLO_TIMEOUT_SECONDS = 10.0
HEARTBEAT_INTERVAL_SECONDS = 15
LAST_SEEN_WRITE_INTERVAL_SECONDS = 15.0
HEARTBEAT_CHECK_INTERVAL_SECONDS = 5.0
MAX_BAD_FRAMES = 5
MAX_FRAMES_PER_SECOND = 50
MAX_FRAME_CHARS = 262_144
MAX_BEARER_CHARS = 512
DEFAULT_PING_TIMEOUT_SECONDS = 5.0

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1008
CLOSE_UNAUTHORISED = 4401
CLOSE_REVOKED = 4403
CLOSE_HELLO_TIMEOUT = 4408
CLOSE_SUPERSEDED = 4409

# Disconnect reasons that must not produce a "device disconnected" notification.
_SILENT_DISCONNECTS = frozenset({"revoked", "shutdown"})


class DeviceEventHandler(Protocol):
    """Implemented by TaskEngine. Called from the hub; exceptions are logged, never propagated."""

    async def on_agent_result(self, device_id: UUID, message: AgentResult) -> None: ...

    async def on_agent_progress(self, device_id: UUID, message: AgentProgress) -> None: ...

    async def on_device_connected(self, device_id: UUID) -> None: ...

    async def on_device_disconnected(self, device_id: UUID) -> None: ...


class FrameRateLimiter:
    """Sliding window: at most ``max_per_second`` accepted frames in any one-second window."""

    def __init__(
        self,
        max_per_second: int = MAX_FRAMES_PER_SECOND,
        *,
        window_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_per_second
        self._window = window_seconds
        self._clock = clock
        self._stamps: deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        cutoff = now - self._window
        while self._stamps and self._stamps[0] <= cutoff:
            self._stamps.popleft()
        if len(self._stamps) >= self._max:
            return False
        self._stamps.append(now)
        return True


@dataclass(eq=False)
class _Connection:
    device_id: UUID
    user_id: UUID
    websocket: WebSocket
    # The secret the agent authenticated with. Held in memory only, to verify command signatures.
    secret: str = field(repr=False)
    frame_limiter: FrameRateLimiter = field(default_factory=FrameRateLimiter)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    registered: bool = False
    finished: bool = False
    close_sent: bool = False
    close_reason: str | None = None
    last_heartbeat_at: float = 0.0
    last_seen_written_at: float | None = None
    running_command_ids: tuple[str, ...] = ()

    async def send_text(self, payload: str) -> bool:
        """Serialised send. Returns False instead of raising when the socket is gone."""
        async with self.send_lock:
            if self.close_sent:
                return False
            try:
                await self.websocket.send_text(payload)
            except Exception:  # the socket may already be closed by the peer; report, never raise
                log.debug("Agent send failed.", extra={"device_id": str(self.device_id)})
                return False
            return True

    async def close(self, code: int, reason: str) -> None:
        async with self.send_lock:
            if self.close_sent:
                return
            self.close_sent = True
            try:
                await self.websocket.close(code=code, reason=reason[:120])
            except Exception:  # already closed; the receive loop will observe the disconnect
                log.debug("Agent close failed.", extra={"device_id": str(self.device_id)})


@dataclass(frozen=True)
class _AuthenticatedDevice:
    device_id: UUID
    user_id: UUID
    revoked: bool
    secret: str = field(repr=False)


def _command_is_signed(secret: str, message: CommandMessage) -> bool:
    return verify_fields(
        secret,
        {
            "id": message.id,
            "action": message.action,
            "issued_at": message.issued_at,
            "expires_at": message.expires_at,
            "nonce": message.nonce,
            "args_json": message.args_json,
        },
        message.signature,
    )


def _cancel_is_signed(secret: str, message: CancelMessage) -> bool:
    return verify_fields(
        secret,
        {
            "id": message.command_id,
            "action": CANCEL_ACTION,
            "issued_at": message.issued_at,
            "expires_at": message.expires_at,
            "nonce": message.nonce,
            "args_json": "{}",
        },
        message.signature,
    )


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = value.strip()
    if not token or len(token) > MAX_BEARER_CHARS:
        return None
    return token


async def _close_unregistered(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):  # the peer may have gone away during the handshake
        await websocket.close(code=code, reason=reason)


async def _receive_text(websocket: WebSocket) -> str | None:
    """Next frame as text, "" for a non-text frame, or None once the client has disconnected."""
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        return None
    text = message.get("text")
    return text if isinstance(text, str) else ""


def _parse_agent_frame(text: str) -> AgentMessage | None:
    if not text or len(text) > MAX_FRAME_CHARS:
        return None
    try:
        return agent_message_adapter.validate_json(text)
    except ValidationError:
        return None


class DeviceHub:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        secret_box: SecretBox,
        events: EventBus,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._secret_box = secret_box
        self._events = events
        self._handler: DeviceEventHandler | None = None
        self._connections: dict[UUID, _Connection] = {}
        # Ping waiters keyed by nonce; the value also records the connection that must answer.
        self._pending_pings: dict[str, tuple[_Connection, asyncio.Future[bool]]] = {}
        self._monitor_task: asyncio.Task[None] | None = None

    # ---- lifecycle ------------------------------------------------------------------

    def bind(self, handler: DeviceEventHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="jarvis-device-heartbeat-monitor")

    async def stop(self) -> None:
        task, self._monitor_task = self._monitor_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for conn in list(self._connections.values()):
            if conn.close_reason is None:
                conn.close_reason = "shutdown"
            await conn.close(CLOSE_GOING_AWAY, "server_shutdown")
            await self._finish_connection(conn)

    # ---- queries and commands -------------------------------------------------------

    def is_online(self, device_id: UUID) -> bool:
        return device_id in self._connections

    def running_command_ids(self, device_id: UUID) -> tuple[str, ...]:
        conn = self._connections.get(device_id)
        return conn.running_command_ids if conn is not None else ()

    async def send_command(self, device_id: UUID, message: CommandMessage) -> bool:
        """Send a signed command. Refuses (False) when offline or when the signature does not match the device secret."""
        conn = self._connections.get(device_id)
        if conn is None:
            return False
        if not _command_is_signed(conn.secret, message):
            log.error("Refusing to send a command that is not signed with the device secret.", extra={"device_id": str(device_id)})
            return False
        return await conn.send_text(message.model_dump_json())

    async def send_cancel(self, device_id: UUID, message: CancelMessage) -> bool:
        conn = self._connections.get(device_id)
        if conn is None:
            return False
        if not _cancel_is_signed(conn.secret, message):
            log.error("Refusing to send a cancel that is not signed with the device secret.", extra={"device_id": str(device_id)})
            return False
        return await conn.send_text(message.model_dump_json())

    async def ping(self, device_id: UUID, timeout_seconds: float = DEFAULT_PING_TIMEOUT_SECONDS) -> int | None:
        """Round-trip latency in milliseconds, or None when the device is offline, silent or unreachable."""
        conn = self._connections.get(device_id)
        if conn is None:
            return None
        nonce = new_token(16)
        waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_pings[nonce] = (conn, waiter)
        try:
            started = time.perf_counter()
            payload = PingMessage(nonce=nonce, sent_at=utc_iso()).model_dump_json()
            if not await conn.send_text(payload):
                return None
            answered = await asyncio.wait_for(waiter, timeout=timeout_seconds)
            if not answered:
                return None
            return max(0, round((time.perf_counter() - started) * 1000))
        except TimeoutError:
            return None
        finally:
            self._pending_pings.pop(nonce, None)

    async def disconnect(self, device_id: UUID, reason: str) -> None:
        """Close the device's socket. Revocation closes with 4403, anything else with 1000 and the reason."""
        conn = self._connections.get(device_id)
        if conn is None:
            return
        conn.close_reason = reason
        if reason == "revoked":
            await conn.close(CLOSE_REVOKED, "revoked")
        else:
            await conn.close(CLOSE_NORMAL, reason)
        await self._finish_connection(conn)

    # ---- WebSocket entry point ------------------------------------------------------

    async def handle_connection(self, websocket: WebSocket) -> None:
        authed = await self._authenticate(websocket.headers.get("authorization"))
        await websocket.accept()
        if authed is None:
            await _close_unregistered(websocket, CLOSE_UNAUTHORISED, "unauthorised")
            return
        if authed.revoked:
            await _close_unregistered(websocket, CLOSE_REVOKED, "revoked")
            return

        conn = _Connection(
            device_id=authed.device_id,
            user_id=authed.user_id,
            websocket=websocket,
            secret=authed.secret,
        )
        try:
            hello = await self._receive_hello(conn)
            if hello is None:
                return
            if not await self._register(conn, hello):
                return
            await self._run(conn)
        finally:
            await self._finish_connection(conn)

    async def _authenticate(self, header: str | None) -> _AuthenticatedDevice | None:
        token = _bearer_token(header)
        if token is None:
            return None
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(Device.id, Device.user_id, Device.pairing_status, Device.revoked_at).where(
                        Device.credential_hash == sha256_hex(token)
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        revoked = row.pairing_status == "revoked" or row.revoked_at is not None
        return _AuthenticatedDevice(device_id=row.id, user_id=row.user_id, revoked=revoked, secret=token)

    async def _receive_hello(self, conn: _Connection) -> AgentHello | None:
        try:
            text = await asyncio.wait_for(_receive_text(conn.websocket), timeout=HELLO_TIMEOUT_SECONDS)
        except TimeoutError:
            conn.close_reason = "hello_timeout"
            await conn.close(CLOSE_HELLO_TIMEOUT, "hello_timeout")
            return None
        if text is None:
            conn.close_reason = "client_closed"
            return None
        message = _parse_agent_frame(text)
        if not isinstance(message, AgentHello):
            await self._reject(conn, "invalid_hello", "The first frame must be a valid hello.")
            return None
        return message

    async def _register(self, conn: _Connection, hello: AgentHello) -> bool:
        if not await self._store_hello(conn, hello):
            await self._close_revoked(conn)
            return False

        previous = self._connections.get(conn.device_id)
        self._connections[conn.device_id] = conn
        conn.registered = True
        conn.last_heartbeat_at = time.monotonic()
        if previous is not None and previous is not conn:
            previous.close_reason = "superseded"
            await previous.close(CLOSE_SUPERSEDED, "superseded")
            await self._finish_connection(previous)

        # Revocation may have committed between the check above and the insert. Re-check now that the
        # connection is visible to disconnect(), so either this check or the revoke's disconnect closes it.
        if not await self._device_is_active(conn.device_id, conn.user_id):
            await self._close_revoked(conn)
            return False

        ack = HelloAck(
            device_id=str(conn.device_id),
            server_time=utc_iso(),
            heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        )
        if not await conn.send_text(ack.model_dump_json()):
            log.warning("Could not send hello_ack.", extra={"device_id": str(conn.device_id)})
        self._events.publish(conn.user_id, "device.updated", {"device_id": str(conn.device_id), "connection_status": "online"})
        await self._call_handler("on_device_connected", conn.device_id)
        return True

    async def _store_hello(self, conn: _Connection, hello: AgentHello) -> bool:
        """Persist what the agent reported. Returns False when the device is no longer paired."""
        capabilities = filter_capabilities(hello.capabilities)
        resources = sanitise_resources(hello.resources)
        now = utc_now()
        async with session_scope(self._session_factory) as db:
            device = await db.scalar(select(Device).where(Device.id == conn.device_id, Device.user_id == conn.user_id))
            if device is None or device.pairing_status != "paired" or device.revoked_at is not None:
                return False
            if device.capabilities != capabilities:
                device.capabilities = capabilities
            permissions = merge_default_permissions(device.permissions or {}, capabilities)
            if permissions != (device.permissions or {}):
                device.permissions = permissions
            if device.resources != resources:
                device.resources = resources
            device.os = hello.os
            device.os_version = hello.os_version
            device.agent_version = hello.agent_version
            device.last_connected_at = now
            device.last_seen_at = now
            return True

    async def _run(self, conn: _Connection) -> None:
        bad_frames = 0
        while not conn.close_sent:
            text = await _receive_text(conn.websocket)
            if text is None:
                if conn.close_reason is None:
                    conn.close_reason = "client_closed"
                return
            if not conn.frame_limiter.allow():
                continue  # over the per-second budget: drop without answering

            message = _parse_agent_frame(text)
            if message is None or isinstance(message, AgentHello):
                bad_frames += 1
                await conn.send_text(ServerMessageError(code="invalid_frame", message="Frame could not be parsed.").model_dump_json())
                if bad_frames >= MAX_BAD_FRAMES:
                    conn.close_reason = "protocol_error"
                    await conn.close(CLOSE_PROTOCOL_ERROR, "protocol_error")
                    return
                continue

            bad_frames = 0
            await self._dispatch(conn, message)

    async def _dispatch(self, conn: _Connection, message: AgentMessage) -> None:
        if isinstance(message, AgentHeartbeat):
            await self._on_heartbeat(conn, message)
        elif isinstance(message, AgentProgress):
            await self._call_handler("on_agent_progress", conn.device_id, message)
        elif isinstance(message, AgentResult):
            await self._call_handler("on_agent_result", conn.device_id, message)
        elif isinstance(message, AgentPong):
            self._resolve_pong(conn, message.nonce)

    async def _on_heartbeat(self, conn: _Connection, message: AgentHeartbeat) -> None:
        now = time.monotonic()
        conn.last_heartbeat_at = now
        conn.running_command_ids = tuple(message.running_command_ids)
        if conn.last_seen_written_at is None or now - conn.last_seen_written_at >= LAST_SEEN_WRITE_INTERVAL_SECONDS:
            conn.last_seen_written_at = now
            await self._touch_last_seen(conn)

    async def _touch_last_seen(self, conn: _Connection) -> None:
        try:
            async with session_scope(self._session_factory) as db:
                await db.execute(
                    update(Device)
                    .where(Device.id == conn.device_id, Device.user_id == conn.user_id)
                    .values(last_seen_at=utc_now())
                )
        except Exception:  # a failed liveness write must not drop the agent connection
            log.warning("Could not record device heartbeat.", extra={"device_id": str(conn.device_id)})

    def _resolve_pong(self, conn: _Connection, nonce: str) -> None:
        entry = self._pending_pings.get(nonce)
        if entry is None:
            return
        owner, waiter = entry
        if owner is conn and not waiter.done():
            waiter.set_result(True)

    # ---- disconnect path (shared by socket close, heartbeat timeout, revocation, shutdown) ----

    async def _finish_connection(self, conn: _Connection) -> None:
        if conn.finished:
            return
        conn.finished = True
        for _nonce, (owner, waiter) in list(self._pending_pings.items()):
            if owner is conn and not waiter.done():
                waiter.set_result(False)
        if not conn.registered:
            return
        if self._connections.get(conn.device_id) is not conn:
            return  # superseded by a newer connection, which now owns the device
        del self._connections[conn.device_id]
        conn.running_command_ids = ()

        reason = conn.close_reason
        status = "revoked" if reason == "revoked" else "offline"
        await self._call_handler("on_device_disconnected", conn.device_id)
        self._events.publish(conn.user_id, "device.updated", {"device_id": str(conn.device_id), "connection_status": status})
        if reason in _SILENT_DISCONNECTS:
            return
        await self._notify_disconnected(conn)

    async def _notify_disconnected(self, conn: _Connection) -> None:
        try:
            async with session_scope(self._session_factory) as db:
                device = await db.scalar(select(Device).where(Device.id == conn.device_id, Device.user_id == conn.user_id))
                if device is None or device.pairing_status == "revoked" or device.revoked_at is not None:
                    return
                notify(
                    db,
                    self._events,
                    user_id=conn.user_id,
                    kind="device_disconnected",
                    severity="warning",
                    title="Device disconnected",
                    body="A paired device is no longer connected.",
                    related_type="device",
                    related_id=str(conn.device_id),
                )
        except Exception:  # the disconnect itself has already happened; a missing notice is not fatal
            log.warning("Could not record device disconnection notice.", extra={"device_id": str(conn.device_id)})

    # ---- helpers --------------------------------------------------------------------

    async def _reject(self, conn: _Connection, code: str, message: str) -> None:
        await conn.send_text(ServerMessageError(code=code, message=message).model_dump_json())
        conn.close_reason = "protocol_error"
        await conn.close(CLOSE_PROTOCOL_ERROR, "protocol_error")

    async def _close_revoked(self, conn: _Connection) -> None:
        conn.close_reason = "revoked"
        await conn.close(CLOSE_REVOKED, "revoked")

    async def _device_is_active(self, device_id: UUID, user_id: UUID) -> bool:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(Device.pairing_status, Device.revoked_at).where(Device.id == device_id, Device.user_id == user_id)
                )
            ).one_or_none()
        return row is not None and row.pairing_status == "paired" and row.revoked_at is None

    async def _call_handler(self, name: str, *args: object) -> None:
        handler = self._handler
        if handler is None:
            return
        method = getattr(handler, name, None)
        if method is None:
            log.error("Device event handler is missing %s.", name)
            return
        try:
            await method(*args)
        except Exception:  # a task-engine failure must not tear down the agent connection
            log.exception("Device event handler %s failed.", name)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)
            try:
                await self._check_heartbeats()
            except Exception:
                log.exception("Heartbeat monitor iteration failed.")

    async def _check_heartbeats(self) -> None:
        timeout = self._settings.device_heartbeat_timeout_seconds
        now = time.monotonic()
        stale = [conn for conn in self._connections.values() if now - conn.last_heartbeat_at > timeout]
        for conn in stale:
            conn.close_reason = "heartbeat_timeout"
            await conn.close(CLOSE_GOING_AWAY, "heartbeat_timeout")
            await self._finish_connection(conn)
