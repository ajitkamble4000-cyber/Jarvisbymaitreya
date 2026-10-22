"""OAuth integrations against PostgreSQL with a scripted provider transport.

No request leaves the process: Google and GitHub are replaced by an httpx.MockTransport that records every
call. Tests cover connect, callback, refresh, the connection test, disconnect, the two read-only tools and the
rule that a token never appears in a response, a redirect or a log line.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.time import utc_now
from app.database.base import Base
from app.integrations.oauth.flow import code_challenge, issue_state
from app.integrations.oauth.providers import GITHUB, GOOGLE_CALENDAR
from app.integrations.oauth.tokens import (
    Credential,
    decode_credential,
    load_integration,
    save_connection,
)
from app.integrations.tools import CALENDAR_TOOL, GITHUB_TOOL, integration_tool_specs
from app.main import create_app
from app.models.identity import User
from app.models.platform import AuditEvent
from app.security.passwords import hash_password
from app.services.task_states import ProposalRefusedError
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

ACCESS = "ya29.access-secret-value-001"
REFRESH = "1//refresh-secret-value-002"
ROTATED = "ya29.rotated-access-secret-003"
GITHUB_TOKEN = "gho_github-secret-token-004"
GOOGLE_SCOPE = "openid email https://www.googleapis.com/auth/calendar.readonly"
SECRETS = (ACCESS, REFRESH, ROTATED, GITHUB_TOKEN)
CALLBACK = "/api/v1/integrations/{provider}/callback"


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode("utf-8")).items()}


def _bearer(request: httpx.Request) -> str:
    header = request.headers.get("authorization", "")
    return header.removeprefix("Bearer ").strip()


class FakeProviders:
    """Scripted Google and GitHub. Every request is recorded so tests can assert on what was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.exchange_mode = "ok"  # ok | invalid_grant | network | server_error
        self.refresh_mode = "ok"  # ok | invalid_grant
        self.account_status = 200
        self.revoke_status = 200
        self.repo_count = 25
        self.valid_tokens = {ACCESS, ROTATED, GITHUB_TOKEN}
        self.calendar_items: list[dict[str, Any]] = [
            {
                "id": "e1",
                "status": "confirmed",
                "summary": "Design review",
                "location": "Room 4",
                "start": {"dateTime": "2026-10-21T09:00:00+00:00"},
                "end": {"dateTime": "2026-10-21T10:00:00+00:00"},
            },
            {
                "id": "e2",
                "status": "cancelled",
                "summary": "Cancelled meeting",
                "start": {"dateTime": "2026-10-21T11:00:00+00:00"},
                "end": {"dateTime": "2026-10-21T12:00:00+00:00"},
            },
            {
                "id": "e3",
                "status": "confirmed",
                "summary": "L" * 250,
                "start": {"date": "2026-10-22"},
                "end": {"date": "2026-10-23"},
            },
            {
                "id": "e4",
                "status": "confirmed",
                "start": {"dateTime": "2026-10-23T08:00:00+00:00"},
                "end": {"dateTime": "2026-10-23T08:30:00+00:00"},
            },
        ]
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def sent_to(self, fragment: str) -> list[httpx.Request]:
        return [request for request in self.requests if fragment in str(request.url)]

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if (host, path) == ("oauth2.googleapis.com", "/token"):
            return self._google_token(request)
        if (host, path) == ("oauth2.googleapis.com", "/revoke"):
            return httpx.Response(self.revoke_status, json={})
        if (host, path) == ("github.com", "/login/oauth/access_token"):
            return self._github_token(request)
        if (host, path) == ("openidconnect.googleapis.com", "/v1/userinfo"):
            return self._bearer(request, {"email": "ada@example.com"}, status=self.account_status)
        if (host, path) == ("api.github.com", "/user"):
            return self._bearer(request, {"login": "ada-dev"}, status=self.account_status)
        if (host, path) == ("www.googleapis.com", "/calendar/v3/calendars/primary/events"):
            return self._bearer(request, {"items": self.calendar_items})
        if (host, path) == ("api.github.com", "/user/repos"):
            repos = [
                {
                    "name": f"repo-{index}",
                    "html_url": f"https://github.com/ada-dev/repo-{index}",
                    "updated_at": "2026-10-19T10:00:00Z",
                    "description": "d" * 300 if index == 0 else None,
                    "private": False,
                }
                for index in range(self.repo_count)
            ]
            return self._bearer(request, repos)
        return httpx.Response(404, json={"error": "unexpected_request"})

    def _google_token(self, request: httpx.Request) -> httpx.Response:
        form = _form(request)
        if self.exchange_mode == "network" and form.get("grant_type") == "authorization_code":
            raise httpx.ConnectError("connection refused", request=request)
        if form.get("grant_type") == "authorization_code":
            if self.exchange_mode == "invalid_grant":
                return httpx.Response(400, json={"error": "invalid_grant"})
            if self.exchange_mode == "server_error":
                return httpx.Response(503, text="upstream unavailable")
            return httpx.Response(
                200,
                json={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600, "scope": GOOGLE_SCOPE, "token_type": "Bearer"},
            )
        if form.get("grant_type") == "refresh_token":
            if self.refresh_mode == "invalid_grant":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": ROTATED, "expires_in": 3600, "scope": GOOGLE_SCOPE, "token_type": "Bearer"})
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _github_token(self, request: httpx.Request) -> httpx.Response:
        if self.exchange_mode == "invalid_grant":
            return httpx.Response(200, json={"error": "bad_verification_code", "error_description": "expired"})
        return httpx.Response(200, json={"access_token": GITHUB_TOKEN, "scope": "read:user", "token_type": "bearer"})

    def _bearer(self, request: httpx.Request, body: Any, *, status: int = 200) -> httpx.Response:
        if _bearer(request) not in self.valid_tokens:
            return httpx.Response(401, json={"error": "invalid_token"})
        if status != 200:
            return httpx.Response(status, json={"error": "upstream"})
        return httpx.Response(200, json=body)


@pytest.fixture(scope="session")
def oauth_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "google_oauth_client_id": "google-client-id.apps.example",
            "google_oauth_client_secret": "google-client-secret-value",
            "github_oauth_client_id": "github-client-id",
            "github_oauth_client_secret": "github-client-secret-value",
        }
    )


@pytest_asyncio.fixture(loop_scope="session")
async def oauth_app(oauth_settings: Settings, schema: None) -> AsyncIterator[Any]:
    application = create_app(oauth_settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield application


@pytest_asyncio.fixture(loop_scope="session")
async def oauth_client(oauth_app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=oauth_app),
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    ) as http:
        yield http


@pytest.fixture
def fake(oauth_app: Any, oauth_settings: Settings) -> FakeProviders:
    """Route both the HTTP routes (app.state.http) and the tools (integration_tool_specs) to the fake."""
    providers = FakeProviders()
    oauth_app.state.http = providers.client
    integration_tool_specs(oauth_settings, providers.client)
    return providers


def _state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


def _verifier_of(app: Any, state: str) -> str:
    return json.loads(app.state.secret_box.decrypt(state))["v"]


async def _connect(client: AsyncClient, provider: str) -> str:
    response = await client.post(f"/api/v1/integrations/{provider}/connect", headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    return response.json()["authorization_url"]


async def _callback(client: AsyncClient, provider: str, state: str, code: str | None = "auth-code-1") -> Any:
    params: dict[str, str] = {"state": state}
    if code is not None:
        params["code"] = code
    return await client.get(CALLBACK.format(provider=provider), params=params, follow_redirects=False)


async def _connected_google(client: AsyncClient, app: Any) -> None:
    url = await _connect(client, "google_calendar")
    response = await _callback(client, "google_calendar", _state_of(url))
    assert response.status_code == 302
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=google_calendar&status=connected"


async def _seed(
    app: Any,
    user_id: uuid.UUID,
    provider_key: str,
    *,
    access: str = ACCESS,
    refresh: str | None = REFRESH,
    expires_in: timedelta | None = timedelta(hours=1),
    scope: str = GOOGLE_SCOPE,
) -> None:
    provider = GOOGLE_CALENDAR if provider_key == "google_calendar" else GITHUB
    now = utc_now()
    expires_at = now + expires_in if expires_in is not None else None
    async with app.state.session_factory() as db:
        await save_connection(
            db,
            box=app.state.secret_box,
            user_id=user_id,
            provider=provider,
            credential=Credential(access, refresh, expires_at, scope),
            label="ada@example.com",
            now=now,
        )
        await db.commit()


async def _stored(app: Any, user_id: uuid.UUID, provider_key: str) -> Any:
    async with app.state.session_factory() as db:
        return await load_integration(db, user_id, provider_key)


async def _propose(app: Any, user_id: uuid.UUID, tool: str, arguments: dict[str, Any]) -> Any:
    async with app.state.session_factory() as db:
        task = await app.state.task_engine.propose(
            db, user_id=user_id, tool_name=tool, arguments=arguments, source="chat"
        )
        await db.commit()
        return task


async def _add_user(app: Any, email: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name="Second", password_hash=hash_password("correct-horse-battery"), is_active=True)
        db.add(user)
        await db.commit()
        return user.id


# -- catalogue and configuration (default settings: no client ids) ----------------------------


async def test_catalogue_implements_only_google_calendar_and_github(client: AsyncClient) -> None:
    await create_owner(client)
    items = {item["id"]: item for item in (await client.get("/api/v1/integrations")).json()["items"]}
    assert items["google_calendar"]["implemented"] is True
    assert items["github"]["implemented"] is True
    assert items["google_calendar"]["status"] == "requires_configuration"
    assert items["github"]["status"] == "requires_configuration"
    for planned in ("discord", "slack"):
        assert items[planned]["implemented"] is False
        assert items[planned]["status"] == "planned"
    assert items["google_calendar"]["permissions"] == []
    assert "https://www.googleapis.com/auth/calendar.readonly" in items["google_calendar"]["scopes_requested"]


async def test_connect_without_client_settings_returns_409_not_configured(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.post("/api/v1/integrations/github/connect", headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_configured"


async def test_unknown_or_planned_provider_is_not_found(client: AsyncClient) -> None:
    await create_owner(client)
    for method, path in (("post", "/api/v1/integrations/discord/connect"), ("post", "/api/v1/integrations/discord/test"), ("delete", "/api/v1/integrations/discord/connection")):
        response = await getattr(client, method)(path, headers=csrf_headers(client))
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "unknown_integration"


async def test_connect_needs_a_session_and_the_csrf_token(client: AsyncClient) -> None:
    anonymous = await client.post("/api/v1/integrations/github/connect", headers=csrf_headers(client))
    assert anonymous.status_code == 401
    await create_owner(client)
    without_csrf = await client.post("/api/v1/integrations/github/connect")
    assert without_csrf.status_code == 403
    assert without_csrf.json()["error"]["code"] == "csrf_failed"


# -- connect, callback ----------------------------------------------------------------------------


async def test_connect_then_callback_stores_an_encrypted_connection(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    url = await _connect(oauth_client, "google_calendar")

    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "accounts.google.com"
    assert query["redirect_uri"] == [f"{TEST_ORIGIN}/api/v1/integrations/google_calendar/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    state = _state_of(url)
    verifier = _verifier_of(oauth_app, state)
    assert code_challenge(verifier) == query["code_challenge"][0]
    assert verifier not in url

    response = await _callback(oauth_client, "google_calendar", state)
    assert response.status_code == 302
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=google_calendar&status=connected"

    exchange = fake.sent_to("oauth2.googleapis.com/token")[0]
    form = _form(exchange)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "auth-code-1"
    assert form["code_verifier"] == verifier
    assert form["redirect_uri"] == f"{TEST_ORIGIN}/api/v1/integrations/google_calendar/callback"

    listing = {item["id"]: item for item in (await oauth_client.get("/api/v1/integrations")).json()["items"]}
    google = listing["google_calendar"]
    assert google["status"] == "connected"
    assert google["account_label"] == "ada@example.com"
    assert google["permissions"] == GOOGLE_SCOPE.split()

    row = await _stored(oauth_app, uuid.UUID(owner["id"]), "google_calendar")
    assert row is not None and row.status == "connected"
    assert row.credential_ciphertext is not None
    for secret in SECRETS:
        assert secret not in row.credential_ciphertext
    stored = decode_credential(oauth_app.state.secret_box, row.credential_ciphertext)
    assert stored.access_token == ACCESS and stored.refresh_token == REFRESH and stored.expires_at is not None

    async with oauth_app.state.session_factory() as db:
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "integration.connect"))).all()
    assert [event.outcome for event in audit] == ["success"]


async def test_forged_state_redirects_with_a_reason_code(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    await create_owner(oauth_client)
    response = await _callback(oauth_client, "google_calendar", "forged-state-value")
    assert response.status_code == 302
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=google_calendar&status=error&reason=invalid_state"
    assert fake.requests == []


async def test_state_issued_for_another_user_is_refused(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    await create_owner(oauth_client)
    other_user = await _add_user(oauth_app, "bob@example.com")
    foreign_state = issue_state(
        oauth_app.state.secret_box,
        user_id=other_user,
        provider_key="github",
        verifier="v" * 43,
        ttl_seconds=600,
        now=utc_now(),
    )
    response = await _callback(oauth_client, "github", foreign_state)
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=github&status=error&reason=state_user_mismatch"
    assert await _stored(oauth_app, other_user, "github") is None
    assert fake.requests == []


async def test_callback_without_a_session_is_refused(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    await create_owner(oauth_client)
    url = await _connect(oauth_client, "github")
    oauth_client.cookies.clear()
    response = await _callback(oauth_client, "github", _state_of(url))
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=github&status=error&reason=not_signed_in"
    assert fake.requests == []


async def test_provider_denial_is_reported_without_the_provider_text(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    await create_owner(oauth_client)
    url = await _connect(oauth_client, "github")
    response = await oauth_client.get(
        CALLBACK.format(provider="github"),
        params={"state": _state_of(url), "error": "access_denied"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=github&status=error&reason=provider_denied"
    assert "access_denied" not in response.headers["location"]
    async with oauth_app.state.session_factory() as db:
        events = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "integration.connect"))).all()
    assert [(event.outcome, event.data) for event in events] == [("failure", {"reason": "provider_denied"})]


@pytest.mark.parametrize(
    ("mode", "reason"),
    [("invalid_grant", "exchange_failed"), ("network", "provider_unavailable"), ("server_error", "provider_unavailable")],
)
async def test_failed_code_exchange_stores_nothing(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders, mode: str, reason: str) -> None:
    owner = await create_owner(oauth_client)
    fake.exchange_mode = mode
    url = await _connect(oauth_client, "google_calendar")
    response = await _callback(oauth_client, "google_calendar", _state_of(url))
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=google_calendar&status=error&reason={reason}"
    assert await _stored(oauth_app, uuid.UUID(owner["id"]), "google_calendar") is None


async def test_callback_for_an_unknown_provider_redirects_without_a_provider(oauth_client: AsyncClient) -> None:
    await create_owner(oauth_client)
    response = await _callback(oauth_client, "discord", "anything")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?status=error&reason=unknown_integration"


async def test_connect_is_rate_limited_per_user(oauth_client: AsyncClient) -> None:
    await create_owner(oauth_client)
    for _ in range(20):
        assert (await oauth_client.post("/api/v1/integrations/github/connect", headers=csrf_headers(oauth_client))).status_code == 200
    limited = await oauth_client.post("/api/v1/integrations/github/connect", headers=csrf_headers(oauth_client))
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


# -- refresh, connection test, disconnect ---------------------------------------------------------


async def test_expiring_token_is_refreshed_before_the_provider_read(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "google_calendar", expires_in=timedelta(seconds=30))

    response = await oauth_client.post("/api/v1/integrations/google_calendar/test", headers=csrf_headers(oauth_client))
    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "connected", "account_label": "ada@example.com", "message": "The provider accepted the connection."}

    refresh = fake.sent_to("oauth2.googleapis.com/token")
    assert len(refresh) == 1 and _form(refresh[0])["grant_type"] == "refresh_token"
    assert _form(refresh[0])["refresh_token"] == REFRESH
    read = fake.sent_to("openidconnect.googleapis.com/v1/userinfo")
    assert len(read) == 1 and _bearer(read[0]) == ROTATED

    row = await _stored(oauth_app, uuid.UUID(owner["id"]), "google_calendar")
    rotated = decode_credential(oauth_app.state.secret_box, row.credential_ciphertext)
    assert rotated.access_token == ROTATED and rotated.refresh_token == REFRESH
    assert rotated.expires_at is not None and rotated.expires_at > utc_now() + timedelta(minutes=30)


async def test_revoked_refresh_token_marks_the_connection_expired(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "google_calendar", expires_in=timedelta(seconds=10))
    fake.refresh_mode = "invalid_grant"

    response = await oauth_client.post("/api/v1/integrations/google_calendar/test", headers=csrf_headers(oauth_client))
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False and body["status"] == "expired"
    assert fake.sent_to("openidconnect.googleapis.com/v1/userinfo") == []

    listing = {item["id"]: item for item in (await oauth_client.get("/api/v1/integrations")).json()["items"]}
    assert listing["google_calendar"]["status"] == "expired"
    assert listing["google_calendar"]["last_error_code"] == "reconnect_required"


async def test_expiring_token_without_refresh_token_needs_reconnect(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "google_calendar", refresh=None, expires_in=timedelta(seconds=5))
    body = (await oauth_client.post("/api/v1/integrations/google_calendar/test", headers=csrf_headers(oauth_client))).json()
    assert body["ok"] is False and body["status"] == "expired"
    assert fake.sent_to("oauth2.googleapis.com/token") == []


async def test_connection_test_reports_provider_failure_as_error(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "github", refresh=None, expires_in=None, scope="read:user")
    fake.account_status = 500
    body = (await oauth_client.post("/api/v1/integrations/github/test", headers=csrf_headers(oauth_client))).json()
    assert body["ok"] is False and body["status"] == "error"
    listing = {item["id"]: item for item in (await oauth_client.get("/api/v1/integrations")).json()["items"]}
    assert listing["github"]["last_error_code"] == "provider_unavailable"


async def test_connection_test_success_for_github_without_expiry(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "github", refresh=None, expires_in=None, scope="read:user")
    body = (await oauth_client.post("/api/v1/integrations/github/test", headers=csrf_headers(oauth_client))).json()
    assert body == {"ok": True, "status": "connected", "account_label": "ada-dev", "message": "The provider accepted the connection."}
    assert fake.sent_to("oauth2.googleapis.com/token") == []


async def test_connection_test_without_a_connection_is_refused(oauth_client: AsyncClient) -> None:
    await create_owner(oauth_client)
    response = await oauth_client.post("/api/v1/integrations/github/test", headers=csrf_headers(oauth_client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_connected"


async def test_disconnect_google_revokes_the_refresh_token_and_clears_the_credential(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    await _seed(oauth_app, user_id, "google_calendar")

    response = await oauth_client.delete("/api/v1/integrations/google_calendar/connection", headers=csrf_headers(oauth_client))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disconnected" and body["provider_revoked"] is True
    revoke = fake.sent_to("oauth2.googleapis.com/revoke")
    assert len(revoke) == 1 and _form(revoke[0])["token"] == REFRESH

    row = await _stored(oauth_app, user_id, "google_calendar")
    assert row.status == "disconnected" and row.credential_ciphertext is None and row.scopes == []
    async with oauth_app.state.session_factory() as db:
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "integration.disconnect"))).all()
    assert len(audit) == 1 and audit[0].data == {"provider_revoked": True}


async def test_disconnect_github_deletes_locally_and_says_so(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    await _seed(oauth_app, user_id, "github", refresh=None, expires_in=None, scope="read:user")

    body = (await oauth_client.delete("/api/v1/integrations/github/connection", headers=csrf_headers(oauth_client))).json()
    assert body["status"] == "disconnected" and body["provider_revoked"] is False
    assert "GitHub Settings" in body["message"] and "stays active" in body["message"]
    assert not any("revoke" in str(request.url) for request in fake.requests)
    row = await _stored(oauth_app, user_id, "github")
    assert row.credential_ciphertext is None


async def test_disconnect_when_nothing_is_connected_is_idempotent(oauth_client: AsyncClient) -> None:
    await create_owner(oauth_client)
    body = (await oauth_client.delete("/api/v1/integrations/github/connection", headers=csrf_headers(oauth_client))).json()
    assert body == {"status": "disconnected", "provider_revoked": False, "message": "Nothing was connected."}


# -- read-only tools ------------------------------------------------------------------------------


async def test_calendar_tool_returns_mocked_events_without_locations(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    await _seed(oauth_app, user_id, "google_calendar")

    task = await _propose(oauth_app, user_id, CALENDAR_TOOL, {"days": 3, "max_results": 10})
    assert task.status == "completed", task.error
    output = task.result["output"]
    assert output["source"] == "google_calendar" and output["count"] == 3
    assert [event["summary"] for event in output["events"]][0] == "Design review"
    assert len(output["events"][1]["summary"]) == 200
    assert output["events"][2] == {"start": "2026-10-23T08:00:00+00:00", "end": "2026-10-23T08:30:00+00:00", "summary": "(no title)"}
    assert "Room 4" not in json.dumps(output) and "Cancelled meeting" not in json.dumps(output)
    assert ACCESS not in json.dumps(task.result)

    query = parse_qs(urlsplit(str(fake.sent_to("calendars/primary/events")[0].url)).query)
    assert query["singleEvents"] == ["true"] and query["orderBy"] == ["startTime"] and query["maxResults"] == ["10"]
    assert "timeMin" in query and "timeMax" in query


async def test_github_tool_returns_repositories_up_to_the_limit(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    await _seed(oauth_app, user_id, "github", refresh=None, expires_in=None, scope="read:user")

    task = await _propose(oauth_app, user_id, GITHUB_TOOL, {"max_results": 3})
    assert task.status == "completed", task.error
    output = task.result["output"]
    assert output["count"] == 3 and len(output["repositories"]) == 3
    first = output["repositories"][0]
    assert first["html_url"] == "https://github.com/ada-dev/repo-0"
    assert len(first["description"]) == 200
    assert set(first) == {"name", "html_url", "updated_at", "description"}
    query = parse_qs(urlsplit(str(fake.sent_to("api.github.com/user/repos")[0].url)).query)
    assert query == {"visibility": ["public"], "per_page": ["20"], "sort": ["updated"]}


async def test_tools_refuse_when_not_connected(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    calendar = await _propose(oauth_app, user_id, CALENDAR_TOOL, {})
    repos = await _propose(oauth_app, user_id, GITHUB_TOOL, {})
    assert calendar.status == "failed" and calendar.error["code"] == "not_connected"
    assert repos.status == "failed" and repos.error["code"] == "not_connected"
    assert fake.requests == []


async def test_tools_refuse_when_the_provider_is_not_configured(oauth_app: Any, oauth_settings: Settings, fake: FakeProviders) -> None:
    unconfigured = oauth_settings.model_copy(update={"google_oauth_client_id": None, "google_oauth_client_secret": None})
    specs = {spec.name: spec for spec in integration_tool_specs(unconfigured, fake.client)}
    assert specs[CALENDAR_TOOL].status == "requires_configuration"
    assert specs[GITHUB_TOOL].status == "working"
    user_id = await _add_user(oauth_app, "carol@example.com")
    task = await _propose(oauth_app, user_id, CALENDAR_TOOL, {})
    assert task.status == "failed" and task.error["code"] == "not_configured"
    assert fake.requests == []
    integration_tool_specs(oauth_settings, fake.client)


async def test_tool_results_are_isolated_per_user(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    await _seed(oauth_app, uuid.UUID(owner["id"]), "github", refresh=None, expires_in=None, scope="read:user")
    other = await _add_user(oauth_app, "dana@example.com")
    task = await _propose(oauth_app, other, GITHUB_TOOL, {})
    assert task.status == "failed" and task.error["code"] == "not_connected"
    assert await _stored(oauth_app, other, "github") is None


async def test_tool_that_meets_a_revoked_token_fails_and_marks_reconnect(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    await _seed(oauth_app, user_id, "google_calendar", expires_in=timedelta(seconds=10))
    fake.refresh_mode = "invalid_grant"
    task = await _propose(oauth_app, user_id, CALENDAR_TOOL, {})
    assert task.status == "failed" and task.error["code"] == "reconnect_required"
    row = await _stored(oauth_app, user_id, "google_calendar")
    assert row.status == "expired" and row.last_error_code == "reconnect_required"


async def test_tool_arguments_outside_the_range_are_refused_before_any_call(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders) -> None:
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    with pytest.raises(ProposalRefusedError) as exc:
        await _propose(oauth_app, user_id, CALENDAR_TOOL, {"days": 15})
    assert exc.value.code == "invalid_arguments"
    with pytest.raises(ProposalRefusedError):
        await _propose(oauth_app, user_id, GITHUB_TOOL, {"max_results": 0})
    assert fake.requests == []


# -- secrets -------------------------------------------------------------------------------------


async def test_tokens_and_verifiers_never_reach_responses_redirects_or_logs(oauth_client: AsyncClient, oauth_app: Any, fake: FakeProviders, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    owner = await create_owner(oauth_client)
    user_id = uuid.UUID(owner["id"])
    seen: list[str] = []

    connect = await oauth_client.post("/api/v1/integrations/google_calendar/connect", headers=csrf_headers(oauth_client))
    seen.append(connect.text)
    verifier = _verifier_of(oauth_app, _state_of(connect.json()["authorization_url"]))
    callback = await _callback(oauth_client, "google_calendar", _state_of(connect.json()["authorization_url"]))
    seen.extend([callback.text, callback.headers["location"]])
    seen.append((await oauth_client.get("/api/v1/integrations")).text)
    seen.append((await oauth_client.post("/api/v1/integrations/google_calendar/test", headers=csrf_headers(oauth_client))).text)
    fake.refresh_mode = "ok"
    seen.append((await oauth_client.post("/api/v1/integrations/google_calendar/test", headers=csrf_headers(oauth_client))).text)
    seen.append(json.dumps((await _propose(oauth_app, user_id, CALENDAR_TOOL, {})).result or {}))
    seen.append((await oauth_client.delete("/api/v1/integrations/google_calendar/connection", headers=csrf_headers(oauth_client))).text)

    everything = "\n".join(seen) + "\n" + caplog.text
    for secret in (*SECRETS, verifier):
        assert secret not in everything
    assert "Bearer" not in caplog.text
    # The capture is real: the flow's own log lines are in it, so the absence of tokens is meaningful.
    assert "oauth connect started provider=google_calendar" in caplog.text
    assert "oauth connection disconnected provider=google_calendar" in caplog.text
