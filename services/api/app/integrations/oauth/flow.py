"""Authorization-code flow with PKCE (RFC 7636): connect, callback, connection test and disconnect.

The state parameter is a Fernet token made by the server. It carries the user id, the provider, a nonce,
the PKCE verifier and an expiry, so no server-side state table is needed. The verifier never leaves the
server: it travels only inside the encrypted state and is sent in the code exchange.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.oauth.providers import (
    MAX_ACCOUNT_LABEL_CHARS,
    OAuthProvider,
    credentials,
    parse_scopes,
    redirect_uri,
)
from app.integrations.oauth.tokens import (
    Credential,
    IntegrationError,
    TokenEndpointError,
    bearer_get,
    clear_connection,
    credential_from_token_response,
    decode_credential,
    fresh_access_token,
    load_integration,
    mark_row,
    post_token_request,
    response_json,
    response_json_object,
    revoke_at_provider,
    save_connection,
)
from app.models.platform import Integration
from app.security.crypto import SecretBox, SecretDecryptionError

log = logging.getLogger("jarvis.oauth")

PKCE_VERIFIER_BYTES = 48  # 64 characters from the unreserved set, inside the 43 to 128 range of RFC 7636
NONCE_BYTES = 16
MAX_CODE_CHARS = 2048
MAX_STATE_CHARS = 4096

# Codes that a connection test reports as an outcome (ok false) instead of raising.
TEST_OUTCOME_CODES = frozenset({"reconnect_required", "provider_unavailable", "provider_error"})


class StatePayload(BaseModel):
    """Decrypted state. Field names are short to keep the state small. Only the server reads them."""

    model_config = ConfigDict(extra="forbid")

    u: uuid.UUID
    p: str = Field(min_length=1, max_length=60)
    n: str = Field(min_length=8, max_length=128)
    v: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$", repr=False)
    exp: int


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    status: str
    account_label: str | None
    message: str


@dataclass(frozen=True)
class DisconnectResult:
    status: str
    provider_revoked: bool
    message: str


def new_code_verifier() -> str:
    return secrets.token_urlsafe(PKCE_VERIFIER_BYTES)


def code_challenge(verifier: str) -> str:
    """S256 challenge: base64url of SHA-256(verifier), without padding (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_state(
    box: SecretBox,
    *,
    user_id: uuid.UUID,
    provider_key: str,
    verifier: str,
    ttl_seconds: int,
    now: datetime,
) -> str:
    document = {
        "u": str(user_id),
        "p": provider_key,
        "n": secrets.token_urlsafe(NONCE_BYTES),
        "v": verifier,
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return box.encrypt(json.dumps(document, separators=(",", ":"), sort_keys=True))


def open_state(
    box: SecretBox,
    token: str,
    *,
    provider_key: str,
    user_id: uuid.UUID,
    now: datetime,
) -> StatePayload:
    """Check the state in this order: decryption, expiry, provider, then the signed-in user."""
    if not token or len(token) > MAX_STATE_CHARS:
        raise IntegrationError("invalid_state")
    try:
        payload = StatePayload.model_validate_json(box.decrypt(token))
    except (SecretDecryptionError, ValueError):
        # Decryption and parse errors can echo decrypted content, so they are not chained.
        raise IntegrationError("invalid_state") from None
    if now.timestamp() >= payload.exp:
        raise IntegrationError("state_expired")
    if payload.p != provider_key:
        raise IntegrationError("invalid_state")
    if payload.u != user_id:
        raise IntegrationError("state_user_mismatch")
    return payload


def build_authorization_url(
    settings: Settings, provider: OAuthProvider, *, state: str, challenge: str
) -> str:
    creds = credentials(settings, provider)
    if creds is None:
        raise IntegrationError("not_configured")
    client_id, _ = creds
    # Notion takes no scope parameter, so the parameter is omitted when a provider lists no scopes.
    scope_params: list[tuple[str, str]] = [("scope", " ".join(provider.scopes))] if provider.scopes else []
    params: list[tuple[str, str]] = [
        ("client_id", client_id),
        ("redirect_uri", redirect_uri(settings, provider)),
        ("response_type", "code"),
        *scope_params,
        ("state", state),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        *provider.authorize_params,
    ]
    return f"{provider.authorize_url}?{urlencode(params, quote_via=quote)}"


def begin_connect(
    settings: Settings,
    box: SecretBox,
    provider: OAuthProvider,
    *,
    user_id: uuid.UUID,
    now: datetime,
) -> str:
    """Return the provider's authorization URL. The browser navigates there; the verifier is not returned."""
    if credentials(settings, provider) is None:
        raise IntegrationError("not_configured")
    verifier = new_code_verifier()
    state = issue_state(
        box,
        user_id=user_id,
        provider_key=provider.key,
        verifier=verifier,
        ttl_seconds=settings.oauth_state_ttl_seconds,
        now=now,
    )
    return build_authorization_url(settings, provider, state=state, challenge=code_challenge(verifier))


def basic_auth_value(client_id: str, client_secret: str) -> str:
    """The value of an HTTP Basic Authorization header for the client credentials."""
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")


async def _post_json_basic_token_request(
    http: httpx.AsyncClient,
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    payload: dict[str, str],
) -> dict[str, Any]:
    """Token request for a provider that takes a JSON body and Basic client credentials (Notion).

    Failures map to the same TokenEndpointError kinds as ``tokens.post_token_request``. The exception is raised
    without a chained cause, so no request detail reaches the caller.
    """
    headers = {"Accept": "application/json", "Authorization": basic_auth_value(client_id, client_secret)}
    try:
        response = await http.post(provider.token_url, json=payload, headers=headers)
    except httpx.HTTPError:
        raise TokenEndpointError("unavailable") from None
    if response.status_code >= 500:
        raise TokenEndpointError("unavailable")
    body = response_json_object(response)
    if body is None:
        raise TokenEndpointError("rejected" if response.status_code >= 400 else "malformed")
    if response.status_code >= 400 or body.get("object") == "error":
        raise TokenEndpointError("invalid_grant" if body.get("code") == "invalid_grant" else "rejected")
    token = body.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise TokenEndpointError("malformed")
    return body


async def exchange_code(
    http: httpx.AsyncClient,
    settings: Settings,
    provider: OAuthProvider,
    *,
    code: str,
    verifier: str,
    now: datetime,
) -> tuple[Credential, dict[str, Any]]:
    """Exchange the code. Returns the credential and the token response body (Notion's workspace name is there)."""
    creds = credentials(settings, provider)
    if creds is None:
        raise IntegrationError("not_configured")
    client_id, client_secret = creds
    redirect = redirect_uri(settings, provider)
    try:
        if provider.token_request == "json_basic":
            # Notion does not document PKCE. The verifier is sent anyway, and Notion is not verified to check it.
            body = await _post_json_basic_token_request(
                http,
                provider,
                client_id=client_id,
                client_secret=client_secret,
                payload={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect,
                    "code_verifier": verifier,
                },
            )
        else:
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": verifier,
            }
            body = await post_token_request(http, provider, form)
    except TokenEndpointError as exc:
        if exc.kind == "unavailable":
            raise IntegrationError("provider_unavailable") from None
        raise IntegrationError("exchange_failed") from None
    return credential_from_token_response(body, now=now), body


def account_label_from_body(provider: OAuthProvider, body: dict[str, Any] | None) -> str:
    """The account label (email for Google, login for GitHub), validated. Anything else is a provider error."""
    value = body.get(provider.account_field) if body else None
    if not isinstance(value, str):
        raise IntegrationError("provider_error")
    label = value.strip()
    if not label or len(label) > MAX_ACCOUNT_LABEL_CHARS:
        raise IntegrationError("provider_error")
    return label


async def complete_connect(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    settings: Settings,
    box: SecretBox,
    provider: OAuthProvider,
    user_id: uuid.UUID,
    state: str,
    code: str | None,
    now: datetime,
) -> Integration:
    """Callback step: verify the state, exchange the code, read the account label and store the connection.

    Nothing is stored unless every step succeeds. The caller commits.
    """
    if credentials(settings, provider) is None:
        raise IntegrationError("not_configured")
    payload = open_state(box, state, provider_key=provider.key, user_id=user_id, now=now)
    if not code or len(code) > MAX_CODE_CHARS:
        raise IntegrationError("invalid_callback")
    credential, token_body = await exchange_code(http, settings, provider, code=code, verifier=payload.v, now=now)
    if provider.account_from_token:
        label = account_label_from_body(provider, token_body)
    else:
        response = await bearer_get(http, provider.account_url, credential.access_token)
        if response.status_code == 429 or response.status_code >= 500:
            raise IntegrationError("provider_unavailable")
        if response.status_code >= 400:
            raise IntegrationError("provider_error")
        label = account_label_from_body(provider, response_json_object(response))
    granted = set(parse_scopes(credential.scope))
    if any(scope not in granted for scope in provider.required_scopes):
        # Nothing is stored, so the grant just issued would be orphaned. Revoke it when the provider allows.
        await revoke_at_provider(http, provider, credential.refresh_token or credential.access_token)
        raise IntegrationError("missing_scope")
    previous = await _stored_credential(db, box, user_id, provider)
    row = await save_connection(
        db,
        box=box,
        user_id=user_id,
        provider=provider,
        credential=credential,
        label=label,
        now=now,
    )
    if previous is not None:
        # The stored credential is replaced, so JARVIS could not revoke the old grant later. Revoke it now.
        old_token = previous.refresh_token or previous.access_token
        if old_token != (credential.refresh_token or credential.access_token):
            await revoke_at_provider(http, provider, old_token)
    return row


async def _stored_credential(
    db: AsyncSession, box: SecretBox, user_id: uuid.UUID, provider: OAuthProvider
) -> Credential | None:
    row = await load_integration(db, user_id, provider.key)
    if row is None or row.credential_ciphertext is None:
        return None
    try:
        return decode_credential(box, row.credential_ciphertext)
    except IntegrationError:
        return None


async def check_connection(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    settings: Settings,
    box: SecretBox,
    provider: OAuthProvider,
    user_id: uuid.UUID,
    now: datetime,
) -> ConnectionTest:
    """Refresh if needed, make the provider's cheap read and record the outcome. Failures are results, not errors."""
    row = await load_integration(db, user_id, provider.key)
    if row is None or row.credential_ciphertext is None or row.status in ("not_connected", "disconnected"):
        raise IntegrationError("not_connected")
    if credentials(settings, provider) is None:
        raise IntegrationError("not_configured")
    try:
        token = await fresh_access_token(
            db, http=http, settings=settings, box=box, user_id=user_id, provider=provider, now=now
        )
        body = await provider_call_json(
            db, http=http, provider=provider, user_id=user_id, access_token=token, url=provider.account_url, now=now
        )
        # A token-labelled provider (Notion) keeps the workspace name stored at connect time.
        label = row.account_label if provider.account_from_token else account_label_from_body(provider, body)
    except IntegrationError as exc:
        if exc.code not in TEST_OUTCOME_CODES:
            raise
        if exc.code == "provider_error":
            mark_row(row, status="error", code="provider_error", now=now)
        return ConnectionTest(ok=False, status=row.status, account_label=row.account_label, message=exc.message)
    if not provider.account_from_token:
        row.account_label = label
    mark_row(row, status="connected", code=None, now=now)
    await db.flush()
    return ConnectionTest(
        ok=True,
        status="connected",
        account_label=label,
        message="The provider accepted the connection.",
    )


async def _mark_connection(
    db: AsyncSession, provider: OAuthProvider, user_id: uuid.UUID, *, status: str, code: str, now: datetime
) -> None:
    row = await load_integration(db, user_id, provider.key)
    if row is not None:
        mark_row(row, status=status, code=code, now=now)
        await db.flush()


async def provider_call_json(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    provider: OAuthProvider,
    user_id: uuid.UUID,
    access_token: str,
    url: str,
    method: str = "GET",
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    now: datetime,
) -> Any:
    """Call a provider API with the user's token and return its JSON value.

    Sends the provider's API headers (Notion's version header). Outcomes match ``tokens.provider_json``: a rejected
    token marks the connection for reconnecting, and every failure marks the row before it raises.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", **dict(provider.api_headers)}
    try:
        response = await http.request(method, url, params=params, json=json_body, headers=headers)
    except httpx.HTTPError:
        await _mark_connection(db, provider, user_id, status="error", code="provider_unavailable", now=now)
        raise IntegrationError("provider_unavailable") from None
    if response.status_code == 401:
        await _mark_connection(db, provider, user_id, status="expired", code="reconnect_required", now=now)
        raise IntegrationError("reconnect_required")
    if response.status_code == 429 or response.status_code >= 500:
        await _mark_connection(db, provider, user_id, status="error", code="provider_unavailable", now=now)
        raise IntegrationError("provider_unavailable")
    body = response_json(response)
    if response.status_code >= 400 or body is None:
        await _mark_connection(db, provider, user_id, status="error", code="provider_error", now=now)
        raise IntegrationError("provider_error")
    return body


async def disconnect_integration(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    box: SecretBox,
    provider: OAuthProvider,
    user_id: uuid.UUID,
    now: datetime,
) -> DisconnectResult:
    """Revoke at the provider when it supports that, then delete the local credential. The caller commits."""
    row = await load_integration(db, user_id, provider.key)
    if row is None or row.credential_ciphertext is None:
        if row is not None:
            clear_connection(row, now=now)
            await db.flush()
        return DisconnectResult("disconnected", False, "Nothing was connected.")

    revoked = False
    try:
        credential = decode_credential(box, row.credential_ciphertext)
    except IntegrationError:
        credential = None
    if credential is not None and provider.revoke_url is not None:
        # Revoking the refresh token revokes the whole grant. Fall back to the access token when there is none.
        revoked = await revoke_at_provider(http, provider, credential.refresh_token or credential.access_token)

    clear_connection(row, now=now)
    await db.flush()
    if provider.revoke_url is None:
        message = provider.no_revoke_message or "The stored token was deleted. The provider has no revoke endpoint."
    elif revoked:
        message = "Access was revoked at the provider and the stored token was deleted."
    else:
        message = (
            "The stored token was deleted. The provider did not confirm revocation, so remove JARVIS "
            "from your Google account security settings if the grant still exists."
        )
    log.info("oauth connection disconnected provider=%s revoked=%s", provider.key, revoked)
    return DisconnectResult("disconnected", revoked, message)
