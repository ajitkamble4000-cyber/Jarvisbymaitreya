"""In-process publish/subscribe bus that feeds the Server-Sent Events stream.

Events are scoped to a user: a subscriber only ever receives events for its own user.
Each subscriber has a bounded queue; when it is full the oldest event is dropped so a
slow browser tab cannot exhaust memory. Clients recover by re-reading the REST resource.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.time import utc_now

_QUEUE_SIZE = 256


@dataclass(frozen=True)
class ServerEvent:
    id: int
    type: str
    data: dict[str, Any]
    created_at: str = field(default_factory=lambda: utc_now().isoformat())


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[ServerEvent]]] = defaultdict(set)
        self._ids = itertools.count(1)

    def publish(self, user_id: UUID, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = ServerEvent(id=next(self._ids), type=event_type, data=data or {})
        for queue in list(self._subscribers.get(user_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, user_id: UUID) -> AsyncIterator[asyncio.Queue[ServerEvent]]:
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers[user_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[user_id].discard(queue)
            if not self._subscribers[user_id]:
                self._subscribers.pop(user_id, None)

    def subscriber_count(self, user_id: UUID) -> int:
        return len(self._subscribers.get(user_id, ()))
