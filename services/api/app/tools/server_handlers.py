"""Registry of server-side tool handlers that plugins and integrations add at startup.

The built-in handlers live in ``TaskEngine.run_server_tool``. Anything not found there is looked up here.
Handlers receive the validated arguments model and must never return credentials or raw provider responses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

ServerHandler = Callable[[AsyncSession, UUID, BaseModel], Awaitable[dict]]

_HANDLERS: dict[str, ServerHandler] = {}


def register_server_handler(name: str, handler: ServerHandler) -> None:
    existing = _HANDLERS.get(name)
    if existing is not None and existing is not handler:
        raise ValueError(f"A server handler named '{name}' is already registered.")
    _HANDLERS[name] = handler


def get_server_handler(name: str) -> ServerHandler | None:
    return _HANDLERS.get(name)
