"""FastAPI dependencies: settings, database session, authenticated user and shared services."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.time import utc_now
from app.database.session import get_db
from app.models.identity import User, UserSession
from app.security.tokens import sha256_hex

SESSION_COOKIE = "jarvis_session"
CSRF_COOKIE = "jarvis_csrf"
CSRF_HEADER = "x-csrf-token"

# Only write last_seen_at when it is at least this stale, to avoid a write on every request.
_LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def _get_db_dep(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in get_db(request):
        yield session


DbSession = Annotated[AsyncSession, Depends(_get_db_dep)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_session(request: Request, db: DbSession) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise UnauthorizedError("Sign in to continue.", code="not_authenticated")

    row = await db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    now = utc_now()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise UnauthorizedError("Your session has expired. Sign in again.", code="session_expired")

    if now - row.last_seen_at >= _LAST_SEEN_RESOLUTION:
        await db.execute(update(UserSession).where(UserSession.id == row.id).values(last_seen_at=now))
        await db.commit()
    return row


async def get_current_user(
    request: Request,
    db: DbSession,
    session: Annotated[UserSession, Depends(get_current_session)],
) -> User:
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Account is not available.", code="account_unavailable")
    request.state.user_id = user.id
    return user


CurrentSession = Annotated[UserSession, Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_owner(user: CurrentUser) -> User:
    """Allows only the owner account. A signed-in member gets 403 ``forbidden``."""
    if user.role != "owner":
        raise ForbiddenError("Only the owner can do that.", code="forbidden")
    return user


def get_service(request: Request, name: str) -> object:
    """Fetch a process-wide service from ``app.state``. Raises AttributeError if it was not wired."""
    return getattr(request.app.state, name)


def parse_uuid(value: str) -> uuid.UUID:
    from app.core.errors import NotFoundError

    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("Not found.") from exc
