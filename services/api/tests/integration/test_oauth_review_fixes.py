"""Regression tests for the OAuth review findings: a disconnect during a refresh, a partial scope grant, and a
reconnect that replaces a grant. The fixtures and helpers come from test_integrations_oauth."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest

from app.core.time import utc_now
from app.integrations.oauth.flow import check_connection, disconnect_integration
from app.integrations.oauth.providers import GOOGLE_CALENDAR
from app.integrations.oauth.tokens import IntegrationError
from tests.conftest import create_owner  # noqa: F401
from tests.integration.test_integrations_oauth import (  # noqa: F401
    ACCESS,
    GOOGLE_SCOPE,
    _add_user,
    _callback,
    _connect,
    _seed,
    _state_of,
    _stored,
    fake,
    oauth_app,
    oauth_client,
    oauth_settings,
)

REVOKE_FRAGMENT = "oauth2.googleapis.com/revoke"


def _token_request(request: httpx.Request) -> bool:
    return request.url.host == "oauth2.googleapis.com" and request.url.path == "/token" and b"refresh_token" in request.content


async def test_disconnect_during_refresh_is_not_undone(oauth_app, oauth_settings, fake) -> None:
    user_id = await _add_user(oauth_app, "race@example.com")
    # The access token expires inside the 60 second refresh margin, so a refresh runs.
    await _seed(oauth_app, user_id, "google_calendar", expires_in=timedelta(seconds=30))
    inner = fake.client
    box = oauth_app.state.secret_box
    hook_ran: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if _token_request(request):
            # The user clicks Disconnect while the refresh request is in flight.
            async with oauth_app.state.session_factory() as other:
                result = await disconnect_integration(
                    other, http=inner, box=box, provider=GOOGLE_CALENDAR, user_id=user_id, now=utc_now()
                )
                await other.commit()
            hook_ran.append(result.message)
        return await inner.send(request)

    racing = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with oauth_app.state.session_factory() as db:
        with pytest.raises(IntegrationError) as caught:
            await check_connection(
                db, http=racing, settings=oauth_settings, box=box, provider=GOOGLE_CALENDAR, user_id=user_id, now=utc_now()
            )
        await db.commit()

    assert hook_ran, "the disconnect did not run inside the refresh"
    assert caught.value.code == "not_connected"
    row = await _stored(oauth_app, user_id, "google_calendar")
    assert row.status == "disconnected"
    assert row.credential_ciphertext is None, "a credential was written back after the disconnect"


async def test_refresh_refused_after_a_disconnect_keeps_the_disconnect(oauth_app, oauth_settings, fake) -> None:
    user_id = await _add_user(oauth_app, "race2@example.com")
    await _seed(oauth_app, user_id, "google_calendar", expires_in=timedelta(seconds=30))
    inner = fake.client
    box = oauth_app.state.secret_box

    async def handler(request: httpx.Request) -> httpx.Response:
        if _token_request(request):
            async with oauth_app.state.session_factory() as other:
                await disconnect_integration(
                    other, http=inner, box=box, provider=GOOGLE_CALENDAR, user_id=user_id, now=utc_now()
                )
                await other.commit()
            # The provider refuses the refresh because the grant was revoked.
            fake.refresh_mode = "invalid_grant"
        return await inner.send(request)

    racing = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        async with oauth_app.state.session_factory() as db:
            with pytest.raises(IntegrationError) as caught:
                await check_connection(
                    db, http=racing, settings=oauth_settings, box=box, provider=GOOGLE_CALENDAR, user_id=user_id, now=utc_now()
                )
            await db.commit()
    finally:
        fake.refresh_mode = "ok"

    assert caught.value.code == "not_connected"
    row = await _stored(oauth_app, user_id, "google_calendar")
    assert row.status == "disconnected", "a refused refresh must not turn a disconnect into expired"
    assert row.last_error_code is None
    assert row.credential_ciphertext is None


async def test_callback_without_the_calendar_scope_stores_nothing(oauth_client, oauth_app, fake) -> None:
    owner = await create_owner(oauth_client)
    url = await _connect(oauth_client, "google_calendar")

    def openid_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS,
                "refresh_token": "1//partial-grant",
                "expires_in": 3600,
                "scope": "openid email",
                "token_type": "Bearer",
            },
        )

    fake._google_token = openid_only  # the fake dispatches Google token requests through this attribute
    response = await _callback(oauth_client, "google_calendar", _state_of(url))

    assert "reason=missing_scope" in response.headers["location"]
    assert "status=connected" not in response.headers["location"]
    row = await _stored(oauth_app, uuid.UUID(owner["id"]), "google_calendar")
    assert row is None or row.credential_ciphertext is None
    # The grant that was just issued is revoked at the provider, because nothing was stored for it.
    revokes = fake.sent_to(REVOKE_FRAGMENT)
    assert any(b"partial-grant" in request.content for request in revokes)


async def test_reconnect_revokes_the_superseded_grant(oauth_client, oauth_app, fake) -> None:
    await create_owner(oauth_client)
    refresh_values = iter(["1//first-grant-token", "1//second-grant-token"])

    def issue(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS,
                "refresh_token": next(refresh_values),
                "expires_in": 3600,
                "scope": GOOGLE_SCOPE,
                "token_type": "Bearer",
            },
        )

    fake._google_token = issue
    for _ in range(2):
        url = await _connect(oauth_client, "google_calendar")
        await _callback(oauth_client, "google_calendar", _state_of(url))

    revokes = fake.sent_to(REVOKE_FRAGMENT)
    # The first grant was replaced by the second connect, so JARVIS must revoke it now.
    assert any(b"first-grant-token" in request.content for request in revokes)
    # The grant that is still stored must not be revoked.
    assert not any(b"second-grant-token" in request.content for request in revokes)
