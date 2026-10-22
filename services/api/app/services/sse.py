"""Server-Sent Events frame encoding for the chat stream.

Frames follow the event-stream format: an optional ``event:`` line, one ``data:`` line per
line of payload, and a blank line that ends the frame. A line starting with a colon is a
comment; the chat stream uses ``: keep-alive`` comments while the provider is quiet.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

# Fixed on purpose: keep-alives are not a user setting.
KEEP_ALIVE_SECONDS = 15.0
KEEP_ALIVE_COMMENT = "keep-alive"

SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_DONE = object()


def validate_event_name(name: str) -> str:
    """Event names must be a single short token so they can never inject extra lines."""
    if not _EVENT_NAME_RE.match(name):
        raise ValueError("Invalid SSE event name.")
    return name


def dumps_payload(payload: Mapping[str, Any]) -> str:
    """JSON with no raw newlines (they are escaped), so one payload fits on one ``data:`` line."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    """One frame. ``data`` may span several lines; each line gets its own ``data:`` prefix."""

    event: str | None = None
    data: str | None = None
    comment: str | None = None

    def encode(self) -> bytes:
        parts: list[str] = []
        if self.comment is not None:
            parts.extend(f": {line}\n" for line in _LINE_BREAK_RE.split(self.comment))
        if self.event is not None:
            parts.append(f"event: {validate_event_name(self.event)}\n")
        if self.data is not None:
            parts.extend(f"data: {line}\n" for line in _LINE_BREAK_RE.split(self.data))
        parts.append("\n")
        return "".join(parts).encode("utf-8")


def json_event(name: str, payload: Mapping[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(event=validate_event_name(name), data=dumps_payload(payload))


def keep_alive() -> ServerSentEvent:
    return ServerSentEvent(comment=KEEP_ALIVE_COMMENT)


async def encode_stream(events: AsyncIterator[ServerSentEvent]) -> AsyncIterator[bytes]:
    """Turns frames into bytes for ``StreamingResponse``. Closing this closes ``events``."""
    try:
        async for event in events:
            yield event.encode()
    finally:
        await events.aclose()


async def _next_or_done(source: AsyncIterator[ServerSentEvent]) -> Any:
    try:
        return await source.__anext__()
    except StopAsyncIteration:
        return _DONE


async def with_keepalive(
    source: AsyncIterator[ServerSentEvent],
    *,
    interval: float = KEEP_ALIVE_SECONDS,
) -> AsyncIterator[ServerSentEvent]:
    """Yields ``source`` unchanged, inserting a keep-alive comment whenever it stays quiet for ``interval`` seconds.

    The pending read is never abandoned: when the consumer goes away, the pending read is cancelled
    so the source sees ``CancelledError`` at its own await point and can record what it has.
    """
    if interval <= 0:
        raise ValueError("Keep-alive interval must be positive.")
    pending: asyncio.Future[Any] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(_next_or_done(source))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield keep_alive()
                continue
            finished, pending = pending, None
            item = finished.result()
            if item is _DONE:
                return
            yield item
    finally:
        if pending is not None:
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the source's own error is already handled there
                pass
        await source.aclose()
