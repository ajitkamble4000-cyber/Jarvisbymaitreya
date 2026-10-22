"""Encrypted storage, refresh, expiry and revocation of OAuth credentials.

A credential lives only inside ``Integration.credential_ciphertext`` as a Fernet token. Nothing here logs,
prints or returns a token. Provider response bodies are parsed and then dropped: only stable error codes and
fixed messages are kept. Every query is filtered by the owning user id.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.integrations.oauth.providers import OAuthProvider, credentials, parse_scopes
from app.models.platform import Integration
from app.security.crypto import SecretBox, SecretDecryptionError

log = logging.getLogger("jarvis.oauth")

REFRESH_MARGIN = timedelta(seconds=60)
_MAX_EXPIRES_IN_SECONDS = 366 * 24 * 3600
_MAX_SCOPE_TEXT = 4000

_STATUS_BY_CODE: dict[str, int] = {
    "not_configured": 409,
    "not_connected": 409,
    "reconnect_required": 409,
    "invalid_arguments": 422,
    "invalid_state": 400,
    "state_expired": 400,
    "invalid_callback": 400,
    "state_user_mismatch": 403,
    "missing_scope": 403,
    "exchange_failed": 502,
    "provider_unavailable": 502,
    "provider_error": 502,
}

# Fixed messages. Provider text is never shown to users or stored.
MESSAGES: dict[str, str] = {
    "not_configured": "This integration is not configured on the server.",
    "not_connected": "This account is not connected. Connect it on the Integrations page first.",
    "reconnect_required": "The connection has expired or was revoked. Connect again.",
    "provider_unavailable": "The provider could not be reached. Try again later.",
    "provider_error": "The provider returned an error. Try again later.",
    "exchange_failed": "The provider did not accept the sign-in. Try connecting again.",
    "invalid_state": "The sign-in request is invalid. Start again.",
    "state_expired": "The sign-in request expired. Start again.",
    "state_user_mismatch": "This sign-in belongs to another account. Start again.",
    "invalid_callback": "The provider response was incomplete. Start again.",
    "missing_scope": "A permission JARVIS needs was not granted. Connect again and allow every permission requested.",
    "invalid_arguments": "The arguments are not valid.",
    "credential_unreadable": "The stored connection cannot be read. Connect again.",
}


class IntegrationError(AppError):
    """A failed integration step. ``code`` is stable and the message is fixed text."""

    status_code = 409
    code = "integration_error"

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(
            message or MESSAGES.get(code, "The integration step failed."),
            code=code,
            status_code=_STATUS_BY_CODE.get(code, 409),
        )


class TokenEndpointError(Exception):
    """Internal. The token endpoint refused or failed. ``kind``: invalid_grant | rejected | unavailable | malformed."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, repr=False)
class Credential:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: str

    def __repr__(self) -> str:
        return "Credential(<redacted>)"


class _StoredCredential(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    expires_at: datetime | None = None
    scope: str = Field(default="", max_length=_MAX_SCOPE_TEXT)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def encode_credential(box: SecretBox, credential: Credential) -> str:
    """Encrypt ``{access_token, refresh_token?, expires_at?, scope}``. The result is the only stored copy."""
    document: dict[str, Any] = {"access_token": credential.access_token, "scope": credential.scope}
    if credential.refresh_token:
        document["refresh_token"] = credential.refresh_token
    if credential.expires_at is not None:
        document["expires_at"] = _aware(credential.expires_at).isoformat()
    return box.encrypt(json.dumps(document, separators=(",", ":"), sort_keys=True))


def decode_credential(box: SecretBox, ciphertext: str) -> Credential:
    try:
        stored = _StoredCredential.model_validate_json(box.decrypt(ciphertext))
    except (SecretDecryptionError, ValueError):
        # The original error text can contain decrypted content, so it is dropped on purpose.
        raise IntegrationError("reconnect_required", MESSAGES["credential_unreadable"]) from None
    return Credential(
        access_token=stored.access_token,
        refresh_token=stored.refresh_token,
        expires_at=stored.expires_at,
        scope=stored.scope,
    )


async def load_integration(db: AsyncSession, user_id: uuid.UUID, provider_key: str) -> Integration | None:
    return await db.scalar(
        select(Integration).where(Integration.user_id == user_id, Integration.provider == provider_key)
    )


def mark_row(row: Integration, *, status: str, code: str | None, now: datetime) -> None:
    """Set the status and the last error. ``code`` is one of the stable codes; its fixed message is stored."""
    row.status = status
    row.last_checked_at = now
    row.last_error_code = code
    row.last_error_message = MESSAGES.get(code) if code else None


def _status_values(*, status: str, code: str | None, now: datetime) -> dict[str, Any]:
    """Column values equivalent to ``mark_row``, for a conditional UPDATE."""
    return {
        "status": status,
        "last_checked_at": now,
        "last_error_code": code,
        "last_error_message": MESSAGES.get(code) if code else None,
    }


async def _write_if_unchanged(db: AsyncSession, row: Integration, observed: str, values: dict[str, Any]) -> bool:
    """Apply ``values`` only while the stored credential is still ``observed``. False when another session changed it.

    A provider call can take seconds. A disconnect or reconnect that commits meanwhile must not be overwritten by
    the stale copy this session loaded before the call. ``row`` is reloaded in both cases.
    """
    result = await db.execute(
        update(Integration)
        .where(Integration.id == row.id, Integration.credential_ciphertext == observed)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(row)
    return result.rowcount == 1


async def save_connection(
    db: AsyncSession,
    *,
    box: SecretBox,
    user_id: uuid.UUID,
    provider: OAuthProvider,
    credential: Credential,
    label: str,
    now: datetime,
) -> Integration:
    row = await load_integration(db, user_id, provider.key)
    if row is None:
        row = Integration(user_id=user_id, provider=provider.key)
        db.add(row)
    row.credential_ciphertext = encode_credential(box, credential)
    row.account_label = label
    row.scopes = parse_scopes(credential.scope)
    mark_row(row, status="connected", code=None, now=now)
    await db.flush()
    return row


def clear_connection(row: Integration, *, now: datetime) -> None:
    """Forget the credential, the granted scopes and the account. The row stays, with status disconnected."""
    row.credential_ciphertext = None
    row.scopes = []
    row.account_label = None
    mark_row(row, status="disconnected", code=None, now=now)


def response_json(response: httpx.Response) -> Any | None:
    """Parsed JSON body, or None when the body is not JSON. Lists and objects are both allowed."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body


def response_json_object(response: httpx.Response) -> dict[str, Any] | None:
    body = response_json(response)
    return body if isinstance(body, dict) else None


async def post_token_request(
    http: httpx.AsyncClient, provider: OAuthProvider, form: dict[str, str]
) -> dict[str, Any]:
    """POST to the provider's token endpoint and return the JSON object. Failures become TokenEndpointError."""
    try:
        response = await http.post(provider.token_url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        raise TokenEndpointError("unavailable") from None
    if response.status_code >= 500:
        raise TokenEndpointError("unavailable")
    body = response_json_object(response)
    if body is None:
        raise TokenEndpointError("rejected" if response.status_code >= 400 else "malformed")
    # GitHub reports bad codes with HTTP 200 and an "error" member, so the body is checked too.
    if response.status_code >= 400 or "error" in body:
        raise TokenEndpointError("invalid_grant" if body.get("error") == "invalid_grant" else "rejected")
    token = body.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise TokenEndpointError("malformed")
    return body


def credential_from_token_response(
    body: dict[str, Any], *, now: datetime, previous: Credential | None = None
) -> Credential:
    """Build a credential from a token response. A refresh response may omit the refresh token or the scope."""
    access = str(body["access_token"]).strip()
    refresh_value = body.get("refresh_token")
    if isinstance(refresh_value, str) and refresh_value.strip():
        refresh: str | None = refresh_value.strip()
    else:
        refresh = previous.refresh_token if previous else None
    expires_at: datetime | None = None
    expires_in = body.get("expires_in")
    if isinstance(expires_in, int) and not isinstance(expires_in, bool) and 0 < expires_in <= _MAX_EXPIRES_IN_SECONDS:
        expires_at = now + timedelta(seconds=expires_in)
    scope_value = body.get("scope")
    if isinstance(scope_value, str):
        scope = scope_value[:_MAX_SCOPE_TEXT]
    else:
        scope = previous.scope if previous else ""
    return Credential(access_token=access, refresh_token=refresh, expires_at=expires_at, scope=scope)


def needs_refresh(credential: Credential, now: datetime) -> bool:
    return credential.expires_at is not None and _aware(credential.expires_at) - now <= REFRESH_MARGIN


async def fresh_access_token(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    settings: Settings,
    box: SecretBox,
    user_id: uuid.UUID,
    provider: OAuthProvider,
    now: datetime | None = None,
) -> str:
    """Return an access token that is valid for at least the refresh margin, refreshing it first when needed.

    Status changes are only flushed here. The caller commits them, including when this raises.
    """
    now = now or utc_now()
    creds = credentials(settings, provider)
    if creds is None:
        raise IntegrationError("not_configured")
    row = await load_integration(db, user_id, provider.key)
    if row is None or row.credential_ciphertext is None or row.status in ("not_connected", "disconnected"):
        raise IntegrationError("not_connected")
    if row.status == "expired":
        raise IntegrationError("reconnect_required")
    try:
        credential = decode_credential(box, row.credential_ciphertext)
    except IntegrationError:
        mark_row(row, status="expired", code="reconnect_required", now=now)
        await db.flush()
        raise
    if not needs_refresh(credential, now):
        return credential.access_token
    if not credential.refresh_token:
        mark_row(row, status="expired", code="reconnect_required", now=now)
        await db.flush()
        raise IntegrationError("reconnect_required")

    observed = row.credential_ciphertext
    client_id, client_secret = creds
    form = {
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        body = await post_token_request(http, provider, form)
    except TokenEndpointError as exc:
        if exc.kind == "invalid_grant":
            code = "reconnect_required"
            status = "expired"
        else:
            code = "provider_unavailable" if exc.kind == "unavailable" else "provider_error"
            status = "error"
        if not await _write_if_unchanged(db, row, observed, _status_values(status=status, code=code, now=now)):
            # Disconnected or reconnected while the refresh was in flight: the failure must not change that state.
            raise IntegrationError("not_connected") from None
        await db.flush()
        raise IntegrationError(code) from None

    refreshed = credential_from_token_response(body, now=now, previous=credential)
    values: dict[str, Any] = {
        "credential_ciphertext": encode_credential(box, refreshed),
        "scopes": parse_scopes(refreshed.scope),
        **_status_values(status="connected", code=None, now=now),
    }
    if not await _write_if_unchanged(db, row, observed, values):
        if row.credential_ciphertext is None:
            # The user disconnected while the refresh was in flight. Do not keep the grant that was just issued.
            await revoke_at_provider(http, provider, refreshed.refresh_token or refreshed.access_token)
        raise IntegrationError("not_connected")
    await db.flush()
    log.info("oauth token refreshed provider=%s", provider.key)
    return refreshed.access_token


async def bearer_get(
    http: httpx.AsyncClient, url: str, access_token: str, *, params: dict[str, str] | None = None
) -> httpx.Response:
    try:
        return await http.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    except httpx.HTTPError:
        raise IntegrationError("provider_unavailable") from None


async def provider_json(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    provider: OAuthProvider,
    user_id: uuid.UUID,
    access_token: str,
    url: str,
    params: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Any:
    """GET a provider JSON document (object or array) with the user's token.

    A rejected token marks the connection for reconnecting. Every failure marks the row, then raises.
    """
    now = now or utc_now()
    try:
        response = await bearer_get(http, url, access_token, params=params)
    except IntegrationError:
        await _mark_loaded(db, provider, user_id, status="error", code="provider_unavailable", now=now)
        raise
    if response.status_code == 401:
        await _mark_loaded(db, provider, user_id, status="expired", code="reconnect_required", now=now)
        raise IntegrationError("reconnect_required")
    if response.status_code == 429 or response.status_code >= 500:
        await _mark_loaded(db, provider, user_id, status="error", code="provider_unavailable", now=now)
        raise IntegrationError("provider_unavailable")
    body = response_json(response)
    if response.status_code >= 400 or body is None:
        await _mark_loaded(db, provider, user_id, status="error", code="provider_error", now=now)
        raise IntegrationError("provider_error")
    return body


async def _mark_loaded(
    db: AsyncSession, provider: OAuthProvider, user_id: uuid.UUID, *, status: str, code: str, now: datetime
) -> None:
    row = await load_integration(db, user_id, provider.key)
    if row is not None:
        mark_row(row, status=status, code=code, now=now)
        await db.flush()


async def revoke_at_provider(http: httpx.AsyncClient, provider: OAuthProvider, token: str) -> bool:
    """True only when the provider confirmed the revocation. Providers without a revoke endpoint return False."""
    if provider.revoke_url is None:
        return False
    try:
        response = await http.post(provider.revoke_url, data={"token": token}, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return False
    return response.status_code == 200
