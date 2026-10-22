"""Integration catalogue and the OAuth connection routes: connect, callback, connection test and disconnect.

Every route below ``/{provider}`` takes a provider id from the fixed registry. Unknown ids get 404, and an
implemented provider whose client settings are missing gets 409 ``not_configured`` on connect. The callback
is the only route that redirects the browser. Its redirects carry a provider id, a status and a fixed reason
code, never a token or a raw provider error.
"""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import (
    CurrentUser,
    DbSession,
    SettingsDep,
    get_current_session,
    get_current_user,
)
from app.core.config import Settings
from app.core.errors import NotFoundError, RateLimitedError, UnauthorizedError
from app.core.time import utc_now
from app.integrations.catalog import integration_catalogue, integration_status
from app.integrations.oauth.flow import (
    begin_connect,
    check_connection,
    complete_connect,
    disconnect_integration,
)
from app.integrations.oauth.providers import OAuthProvider, get_provider, is_configured
from app.integrations.oauth.tokens import IntegrationError
from app.models.identity import User
from app.models.platform import Integration
from app.schemas.integrations import (
    ConnectionTestOut,
    ConnectOut,
    DisconnectOut,
    IntegrationListOut,
    IntegrationOut,
)
from app.services.audit import record_audit

log = logging.getLogger("jarvis.oauth")

router = APIRouter(prefix="/integrations", tags=["integrations"])

OAUTH_RATE_LIMIT_PER_MINUTE = 20
CALLBACK_RATE_LIMIT_PER_MINUTE = 30

ProviderPath = Annotated[str, Path(min_length=1, max_length=60)]


def _implemented(provider: str) -> OAuthProvider:
    definition = get_provider(provider)
    if definition is None:
        raise NotFoundError("Unknown integration.", code="unknown_integration")
    return definition


def _rate_limit(request: Request, key: str, limit: int) -> None:
    if not request.app.state.rate_limiter.hit(key, limit):
        raise RateLimitedError("Too many requests. Slow down and try again shortly.")


def _redirect(settings: Settings, provider_key: str | None, status: str, reason: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if provider_key is not None:
        params["provider"] = provider_key
    params["status"] = status
    if reason is not None:
        params["reason"] = reason
    base = settings.public_base_url.rstrip("/")
    return RedirectResponse(f"{base}/integrations?{urlencode(params)}", status_code=302)


async def _signed_in_user(request: Request, db: DbSession) -> User | None:
    """The session user, or None. The callback needs the session cookie so that a login-CSRF attempt fails."""
    try:
        session_row = await get_current_session(request, db)
        return await get_current_user(request, db, session_row)
    except UnauthorizedError:
        return None


@router.get("", response_model=IntegrationListOut)
async def list_integrations(user: CurrentUser, db: DbSession, settings: SettingsDep) -> IntegrationListOut:
    rows = (await db.scalars(select(Integration).where(Integration.user_id == user.id))).all()
    by_provider = {row.provider: row for row in rows}
    items: list[IntegrationOut] = []
    for spec in integration_catalogue():
        provider = get_provider(spec.id)
        configured = provider is not None and is_configured(settings, provider)
        row = by_provider.get(spec.id)
        items.append(
            IntegrationOut(
                id=spec.id,
                name=spec.name,
                category=spec.category,
                status=integration_status(spec, configured=configured, stored_status=row.status if row else None),
                implemented=spec.implemented,
                requires_configuration=list(spec.requires_configuration),
                scopes_requested=list(spec.scopes_requested),
                permissions=list(row.scopes) if row else [],
                docs=spec.docs,
                account_label=row.account_label if row else None,
                last_checked_at=row.last_checked_at if row else None,
                last_error_code=row.last_error_code if row else None,
                last_error_message=row.last_error_message if row else None,
            )
        )
    return IntegrationListOut(items=items)


@router.post("/{provider}/connect", response_model=ConnectOut)
async def connect(provider: ProviderPath, request: Request, user: CurrentUser, settings: SettingsDep) -> ConnectOut:
    definition = _implemented(provider)
    _rate_limit(request, f"oauth:connect:{user.id}", OAUTH_RATE_LIMIT_PER_MINUTE)
    authorization_url = begin_connect(
        settings,
        request.app.state.secret_box,
        definition,
        user_id=user.id,
        now=utc_now(),
    )
    log.info("oauth connect started provider=%s", definition.key)
    return ConnectOut(authorization_url=authorization_url)


@router.get("/{provider}/callback", response_class=RedirectResponse)
async def callback(
    provider: ProviderPath,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    state: Annotated[str | None, Query(max_length=4096)] = None,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    definition = get_provider(provider)
    if definition is None:
        return _redirect(settings, None, "error", "unknown_integration")
    client_ip = request.client.host if request.client else "unknown"
    if not request.app.state.rate_limiter.hit(f"oauth:callback:{client_ip}", CALLBACK_RATE_LIMIT_PER_MINUTE):
        return _redirect(settings, definition.key, "error", "rate_limited")
    user = await _signed_in_user(request, db)
    if user is None:
        return _redirect(settings, definition.key, "error", "not_signed_in")
    if error is not None:
        # The provider's own error text is never used. The user declined or the provider refused.
        record_audit(
            db,
            action="integration.connect",
            outcome="failure",
            user_id=user.id,
            target_type="integration",
            target_id=definition.key,
            data={"reason": "provider_denied"},
            ip_address=client_ip,
        )
        await db.commit()
        return _redirect(settings, definition.key, "error", "provider_denied")
    try:
        await complete_connect(
            db,
            http=request.app.state.http,
            settings=settings,
            box=request.app.state.secret_box,
            provider=definition,
            user_id=user.id,
            state=state or "",
            code=code,
            now=utc_now(),
        )
    except IntegrationError as exc:
        record_audit(
            db,
            action="integration.connect",
            outcome="failure",
            user_id=user.id,
            target_type="integration",
            target_id=definition.key,
            data={"reason": exc.code},
            ip_address=client_ip,
        )
        await db.commit()
        return _redirect(settings, definition.key, "error", exc.code)
    record_audit(
        db,
        action="integration.connect",
        user_id=user.id,
        target_type="integration",
        target_id=definition.key,
        ip_address=client_ip,
    )
    await db.commit()
    return _redirect(settings, definition.key, "connected")


@router.post("/{provider}/test", response_model=ConnectionTestOut)
async def test_connection(
    provider: ProviderPath,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
) -> ConnectionTestOut:
    definition = _implemented(provider)
    _rate_limit(request, f"oauth:test:{user.id}", OAUTH_RATE_LIMIT_PER_MINUTE)
    result = await check_connection(
        db,
        http=request.app.state.http,
        settings=settings,
        box=request.app.state.secret_box,
        provider=definition,
        user_id=user.id,
        now=utc_now(),
    )
    record_audit(
        db,
        action="integration.test",
        outcome="success" if result.ok else "failure",
        user_id=user.id,
        target_type="integration",
        target_id=definition.key,
        data={"status": result.status},
    )
    await db.commit()
    return ConnectionTestOut(
        ok=result.ok,
        status=result.status,
        account_label=result.account_label,
        message=result.message,
    )


@router.delete("/{provider}/connection", response_model=DisconnectOut)
async def disconnect(provider: ProviderPath, request: Request, user: CurrentUser, db: DbSession) -> DisconnectOut:
    definition = _implemented(provider)
    result = await disconnect_integration(
        db,
        http=request.app.state.http,
        box=request.app.state.secret_box,
        provider=definition,
        user_id=user.id,
        now=utc_now(),
    )
    record_audit(
        db,
        action="integration.disconnect",
        user_id=user.id,
        target_type="integration",
        target_id=definition.key,
        data={"provider_revoked": result.provider_revoked},
    )
    await db.commit()
    return DisconnectOut(status="disconnected", provider_revoked=result.provider_revoked, message=result.message)
