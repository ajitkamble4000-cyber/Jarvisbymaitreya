"""Gmail, Google Drive and Notion over PostgreSQL with a scripted provider transport.

No request leaves the process. Every provider call goes to an httpx.MockTransport that records the request, so
tests can assert on what was sent: the Drive query, the metadata-only Gmail requests, the Notion JSON body and
Basic auth. Each test starts from an empty database.
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

from app.core.config import Settings
from app.core.time import utc_now
from app.database.base import Base
from app.integrations.oauth.flow import basic_auth_value
from app.integrations.oauth.providers import GMAIL, GOOGLE_DRIVE, NOTION
from app.integrations.oauth.tokens import (
    Credential,
    decode_credential,
    load_integration,
    save_connection,
)
from app.integrations.tools import (
    DRIVE_TOOL,
    GMAIL_TOOL,
    NOTION_TOOL,
    drive_name_query,
    integration_tool_specs,
)
from app.main import create_app
from app.models.identity import User
from app.security.passwords import hash_password
from app.services.task_states import ProposalRefusedError
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

GMAIL_ACCESS = "ya29.gmail-access-secret-101"
GMAIL_REFRESH = "1//gmail-refresh-secret-102"
DRIVE_ACCESS = "ya29.drive-access-secret-103"
DRIVE_REFRESH = "1//drive-refresh-secret-104"
NOTION_ACCESS = "ntn_notion-access-secret-105"
PARTIAL_ACCESS = "ya29.partial-grant-secret-106"
PARTIAL_REFRESH = "1//partial-refresh-secret-107"
GOOGLE_CLIENT_SECRET = "google-client-secret-value"
NOTION_CLIENT_ID = "notion-client-id"
NOTION_CLIENT_SECRET = "notion-client-secret-value"
SECRETS = (GMAIL_ACCESS, GMAIL_REFRESH, DRIVE_ACCESS, DRIVE_REFRESH, NOTION_ACCESS, PARTIAL_ACCESS, PARTIAL_REFRESH)
GMAIL_SCOPE = "openid email https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "openid email https://www.googleapis.com/auth/drive.metadata.readonly"
CALLBACK = "/api/v1/integrations/{provider}/callback"
BODY_TEXT = "BODY-TEXT-THAT-MUST-NOT-LEAK"


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode("utf-8")).items()}


def _bearer(request: httpx.Request) -> str:
    return request.headers.get("authorization", "").removeprefix("Bearer ").strip()


def _message(message_id: str) -> dict[str, Any]:
    subjects = {"m-1": "Quarterly numbers " + "Q" * 230, "m-2": "Lunch?"}
    return {
        "id": message_id,
        "threadId": "t-" + message_id,
        "snippet": BODY_TEXT,
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {"size": 40, "data": BODY_TEXT},
            "headers": [
                {"name": "From", "value": "Grace <grace@example.com>" if message_id == "m-1" else "Linus <l@example.com>"},
                {"name": "Subject", "value": subjects[message_id]},
                {"name": "Date", "value": "Tue, 20 Oct 2026 08:00:00 +0000"},
                {"name": "To", "value": "ada@example.com"},
            ],
        },
    }


class MoreProviders:
    """Scripted Google and Notion. ``valid_tokens`` controls which bearer tokens the APIs accept."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.valid_tokens = {GMAIL_ACCESS, DRIVE_ACCESS, NOTION_ACCESS, PARTIAL_ACCESS}
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def sent_to(self, fragment: str) -> list[httpx.Request]:
        return [request for request in self.requests if fragment in str(request.url)]

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if (host, path) == ("oauth2.googleapis.com", "/token"):
            return self._google_token(request)
        if (host, path) == ("oauth2.googleapis.com", "/revoke"):
            return httpx.Response(200, json={})
        if (host, path) == ("openidconnect.googleapis.com", "/v1/userinfo"):
            return self._bearer(request, {"email": "ada@example.com"})
        if (host, path) == ("gmail.googleapis.com", "/gmail/v1/users/me/messages"):
            return self._bearer(request, {"messages": [{"id": "m-1", "threadId": "t-m-1"}, {"id": "m-2", "threadId": "t-m-2"}]})
        if host == "gmail.googleapis.com" and path.startswith("/gmail/v1/users/me/messages/"):
            return self._bearer(request, _message(path.rsplit("/", 1)[-1]))
        if (host, path) == ("www.googleapis.com", "/drive/v3/files"):
            return self._bearer(request, _drive_listing())
        if (host, path) == ("api.notion.com", "/v1/oauth/token"):
            return self._notion_token(request)
        if (host, path) == ("api.notion.com", "/v1/users/me"):
            return self._bearer(request, {"object": "user", "type": "bot"})
        if (host, path) == ("api.notion.com", "/v1/search"):
            return self._notion_search(request)
        return httpx.Response(404, json={"error": "unexpected_request"})

    def _google_token(self, request: httpx.Request) -> httpx.Response:
        form = _form(request)
        if form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        grants = {
            "gmail-code": (GMAIL_ACCESS, GMAIL_REFRESH, GMAIL_SCOPE),
            "drive-code": (DRIVE_ACCESS, DRIVE_REFRESH, DRIVE_SCOPE),
            # Google can issue a token without every permission the user was asked for.
            "partial-code": (PARTIAL_ACCESS, PARTIAL_REFRESH, "openid email"),
        }
        grant = grants.get(form.get("code", ""))
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        access, refresh, scope = grant
        return httpx.Response(
            200,
            json={"access_token": access, "refresh_token": refresh, "expires_in": 3600, "scope": scope, "token_type": "Bearer"},
        )

    def _notion_token(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != basic_auth_value(NOTION_CLIENT_ID, NOTION_CLIENT_SECRET):
            return httpx.Response(401, json={"object": "error", "status": 401, "code": "unauthorized"})
        body = json.loads(request.content)
        if body.get("code") != "notion-code":
            return httpx.Response(400, json={"object": "error", "status": 400, "code": "invalid_grant", "message": "x"})
        return httpx.Response(
            200,
            json={"access_token": NOTION_ACCESS, "token_type": "bearer", "bot_id": "bot-1", "workspace_name": "Ada's Workspace"},
        )

    def _notion_search(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("notion-version") != "2022-06-28":
            return httpx.Response(400, json={"object": "error", "code": "validation_error"})
        results = [
            {
                "object": "page",
                "id": "p-1",
                "url": "https://www.notion.so/Roadmap-p1",
                "properties": {"Name": {"type": "title", "title": [{"plain_text": "Roadmap 2027"}]}},
            },
            {"object": "database", "id": "d-1", "url": "https://www.notion.so/Tasks-d1", "title": [{"plain_text": "Tasks"}]},
            {"object": "page", "id": "p-2", "url": "http://insecure.example/page", "properties": {}},
            {"object": "block", "id": "b-1", "url": "https://www.notion.so/block-b1"},
        ]
        return self._bearer(request, {"object": "list", "results": results})

    def _bearer(self, request: httpx.Request, body: Any) -> httpx.Response:
        if _bearer(request) not in self.valid_tokens:
            return httpx.Response(401, json={"error": {"code": 401, "message": "invalid token"}})
        return httpx.Response(200, json=body)


def _drive_listing() -> dict[str, Any]:
    return {
        "files": [
            {
                "id": "f-1",
                "name": "Budget 2027",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-10-19T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/spreadsheets/d/f-1/edit",
            },
            {
                "id": "f-2",
                "name": "n" * 300,
                "mimeType": "text/plain",
                "modifiedTime": "2026-10-18T10:00:00.000Z",
                "webViewLink": "javascript:alert(1)",
            },
        ]
    }


@pytest.fixture(scope="session")
def more_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "google_oauth_client_id": "google-client-id.apps.example",
            "google_oauth_client_secret": GOOGLE_CLIENT_SECRET,
            "notion_oauth_client_id": NOTION_CLIENT_ID,
            "notion_oauth_client_secret": NOTION_CLIENT_SECRET,
        }
    )


@pytest_asyncio.fixture(loop_scope="session")
async def more_app(more_settings: Settings, schema: None) -> AsyncIterator[Any]:
    application = create_app(more_settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield application


@pytest_asyncio.fixture(loop_scope="session")
async def more_client(more_app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=more_app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}
    ) as http:
        yield http


@pytest.fixture
def fake(more_app: Any, more_settings: Settings) -> MoreProviders:
    """Route the HTTP routes and the tools to the scripted providers."""
    providers = MoreProviders()
    more_app.state.http = providers.client
    integration_tool_specs(more_settings, providers.client)
    return providers


def _state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


async def _connect(client: AsyncClient, provider: str) -> str:
    response = await client.post(f"/api/v1/integrations/{provider}/connect", headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    return response.json()["authorization_url"]


async def _callback(client: AsyncClient, provider: str, state: str, code: str) -> Any:
    return await client.get(CALLBACK.format(provider=provider), params={"state": state, "code": code}, follow_redirects=False)


async def _connect_and_callback(client: AsyncClient, provider: str, code: str) -> Any:
    url = await _connect(client, provider)
    return await _callback(client, provider, _state_of(url), code)


async def _seed(
    app: Any,
    user_id: uuid.UUID,
    provider_key: str,
    *,
    access: str,
    refresh: str | None,
    scope: str,
    label: str,
) -> None:
    provider = {"gmail": GMAIL, "google_drive": GOOGLE_DRIVE, "notion": NOTION}[provider_key]
    now = utc_now()
    expires_at = now + timedelta(hours=1) if provider is not NOTION else None
    async with app.state.session_factory() as db:
        await save_connection(
            db,
            box=app.state.secret_box,
            user_id=user_id,
            provider=provider,
            credential=Credential(access, refresh, expires_at, scope),
            label=label,
            now=now,
        )
        await db.commit()


async def _seed_gmail(app: Any, user_id: uuid.UUID) -> None:
    await _seed(app, user_id, "gmail", access=GMAIL_ACCESS, refresh=GMAIL_REFRESH, scope=GMAIL_SCOPE, label="ada@example.com")


async def _seed_drive(app: Any, user_id: uuid.UUID) -> None:
    await _seed(app, user_id, "google_drive", access=DRIVE_ACCESS, refresh=DRIVE_REFRESH, scope=DRIVE_SCOPE, label="ada@example.com")


async def _seed_notion(app: Any, user_id: uuid.UUID) -> None:
    await _seed(app, user_id, "notion", access=NOTION_ACCESS, refresh=None, scope="", label="Ada's Workspace")


async def _stored(app: Any, user_id: uuid.UUID, provider_key: str) -> Any:
    async with app.state.session_factory() as db:
        return await load_integration(db, user_id, provider_key)


async def _propose(app: Any, user_id: uuid.UUID, tool: str, arguments: dict[str, Any]) -> Any:
    async with app.state.session_factory() as db:
        task = await app.state.task_engine.propose(db, user_id=user_id, tool_name=tool, arguments=arguments, source="chat")
        await db.commit()
        return task


async def _listing(client: AsyncClient) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in (await client.get("/api/v1/integrations")).json()["items"]}


# -- catalogue and connection -----------------------------------------------------------------


async def test_catalogue_implements_the_three_providers_and_keeps_discord_and_slack_planned(more_client: AsyncClient) -> None:
    await create_owner(more_client)
    items = await _listing(more_client)
    for key in ("gmail", "google_drive", "notion"):
        assert items[key]["implemented"] is True, key
        assert items[key]["status"] == "not_connected", key
        assert items[key]["permissions"] == []
    assert items["discord"]["status"] == "planned" and items["slack"]["status"] == "planned"
    assert items["gmail"]["scopes_requested"] == list(GMAIL.scopes)
    assert items["google_drive"]["scopes_requested"] == list(GOOGLE_DRIVE.scopes)


async def test_gmail_connect_then_callback_stores_an_encrypted_connection(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    url = await _connect(more_client, "gmail")
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "accounts.google.com"
    assert query["scope"] == [GMAIL_SCOPE]
    assert query["redirect_uri"] == [f"{TEST_ORIGIN}/api/v1/integrations/gmail/callback"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]

    response = await _callback(more_client, "gmail", _state_of(url), "gmail-code")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=gmail&status=connected"
    exchange = _form(fake.sent_to("oauth2.googleapis.com/token")[0])
    assert exchange["client_secret"] == GOOGLE_CLIENT_SECRET and exchange["grant_type"] == "authorization_code"

    gmail = (await _listing(more_client))["gmail"]
    assert gmail["status"] == "connected"
    assert gmail["account_label"] == "ada@example.com"
    assert gmail["permissions"] == GMAIL_SCOPE.split()
    row = await _stored(more_app, uuid.UUID(owner["id"]), "gmail")
    assert row is not None and row.credential_ciphertext is not None
    for secret in SECRETS:
        assert secret not in row.credential_ciphertext
    assert decode_credential(more_app.state.secret_box, row.credential_ciphertext).refresh_token == GMAIL_REFRESH


async def test_drive_connect_then_callback_uses_the_drive_scope_only(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    response = await _connect_and_callback(more_client, "google_drive", "drive-code")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=google_drive&status=connected"
    drive = (await _listing(more_client))["google_drive"]
    assert drive["status"] == "connected" and drive["permissions"] == DRIVE_SCOPE.split()
    assert drive["account_label"] == "ada@example.com"
    assert "https://www.googleapis.com/auth/drive.metadata.readonly" in drive["scopes_requested"]
    assert await _stored(more_app, uuid.UUID(owner["id"]), "google_drive") is not None


async def test_gmail_callback_without_the_read_scope_is_refused_and_the_grant_is_revoked(
    more_client: AsyncClient, more_app: Any, fake: MoreProviders
) -> None:
    owner = await create_owner(more_client)
    response = await _connect_and_callback(more_client, "gmail", "partial-code")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=gmail&status=error&reason=missing_scope"
    assert await _stored(more_app, uuid.UUID(owner["id"]), "gmail") is None
    revoked = [_form(request)["token"] for request in fake.sent_to("oauth2.googleapis.com/revoke")]
    assert revoked == [PARTIAL_REFRESH]


async def test_notion_connect_uses_owner_user_and_stores_the_workspace_name(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    url = await _connect(more_client, "notion")
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "api.notion.com"
    assert query["owner"] == ["user"] and query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"] and "code_challenge" in query
    assert "scope" not in query

    response = await _callback(more_client, "notion", _state_of(url), "notion-code")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=notion&status=connected"

    exchange = fake.sent_to("api.notion.com/v1/oauth/token")[0]
    assert exchange.headers["authorization"] == basic_auth_value(NOTION_CLIENT_ID, NOTION_CLIENT_SECRET)
    body = json.loads(exchange.content)
    assert set(body) == {"grant_type", "code", "redirect_uri", "code_verifier"}
    assert body["redirect_uri"] == f"{TEST_ORIGIN}/api/v1/integrations/notion/callback"
    assert NOTION_CLIENT_SECRET not in exchange.content.decode("utf-8")

    notion = (await _listing(more_client))["notion"]
    assert notion["status"] == "connected"
    assert notion["account_label"] == "Ada's Workspace"
    assert notion["permissions"] == []
    row = await _stored(more_app, uuid.UUID(owner["id"]), "notion")
    assert row is not None and row.credential_ciphertext is not None
    assert NOTION_ACCESS not in row.credential_ciphertext
    stored = decode_credential(more_app.state.secret_box, row.credential_ciphertext)
    assert stored.access_token == NOTION_ACCESS and stored.refresh_token is None and stored.expires_at is None


async def test_notion_code_the_provider_refuses_stores_nothing(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    response = await _connect_and_callback(more_client, "notion", "wrong-code")
    assert response.headers["location"] == f"{TEST_ORIGIN}/integrations?provider=notion&status=error&reason=exchange_failed"
    assert await _stored(more_app, uuid.UUID(owner["id"]), "notion") is None


# -- read-only tools -------------------------------------------------------------------------


async def test_gmail_tool_requests_metadata_only_and_returns_three_fields(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    await _seed_gmail(more_app, user_id)

    task = await _propose(more_app, user_id, GMAIL_TOOL, {"query": "from:grace", "max_results": 2})
    assert task.status == "completed", task.error
    output = task.result["output"]
    assert output["source"] == "gmail" and output["count"] == 2
    assert set(output["messages"][0]) == {"from", "subject", "date"}
    assert output["messages"][0]["from"] == "Grace <grace@example.com>"
    assert len(output["messages"][0]["subject"]) == 200
    assert output["messages"][1]["subject"] == "Lunch?"
    rendered = json.dumps(task.result)
    assert BODY_TEXT not in rendered and GMAIL_ACCESS not in rendered

    listing = fake.sent_to("gmail/v1/users/me/messages?")
    assert [dict(parse_qs(urlsplit(str(r.url)).query)) for r in listing] == [{"maxResults": ["2"], "q": ["from:grace"]}]
    details = fake.sent_to("gmail/v1/users/me/messages/")
    assert len(details) == 2
    for request in details:
        query = parse_qs(urlsplit(str(request.url)).query)
        assert query["format"] == ["metadata"]
        assert query["metadataHeaders"] == ["From", "Subject", "Date"]
        assert "full" not in str(request.url) and "raw" not in str(request.url)


async def test_drive_tool_escapes_the_name_and_returns_metadata_only(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    await _seed_drive(more_app, user_id)
    name = "O'Neil \\ budget"

    task = await _propose(more_app, user_id, DRIVE_TOOL, {"name_contains": name, "max_results": 5})
    assert task.status == "completed", task.error
    files = task.result["output"]["files"]
    assert [sorted(item) for item in files] == [["mimeType", "modifiedTime", "name", "webViewLink"]] * 2
    assert files[0]["webViewLink"] == "https://docs.google.com/spreadsheets/d/f-1/edit"
    assert files[1]["webViewLink"] is None and len(files[1]["name"]) == 200

    request = fake.sent_to("www.googleapis.com/drive/v3/files")[0]
    query = parse_qs(urlsplit(str(request.url)).query)
    assert query["q"] == [drive_name_query(name)]
    assert query["pageSize"] == ["5"]
    assert query["fields"] == ["files(id,name,mimeType,modifiedTime,webViewLink)"]
    assert "trashed = false" in query["q"][0]


async def test_drive_hostile_name_stays_inside_the_name_literal(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    await _seed_drive(more_app, user_id)

    task = await _propose(more_app, user_id, DRIVE_TOOL, {"name_contains": "x' or trashed = true or name = '"})
    assert task.status == "completed", task.error
    sent = parse_qs(urlsplit(str(fake.sent_to("www.googleapis.com/drive/v3/files")[0].url)).query)["q"][0]
    assert sent.startswith("name contains 'x\\' or trashed = true or name = \\'")
    assert sent.endswith("' and trashed = false")
    assert sent.count(" and trashed = false") == 1
    assert not sent.endswith("trashed = true")


async def test_notion_tool_posts_with_the_version_header_and_returns_titles(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    await _seed_notion(more_app, user_id)

    task = await _propose(more_app, user_id, NOTION_TOOL, {"query": "roadmap", "max_results": 3})
    assert task.status == "completed", task.error
    output = task.result["output"]
    assert output["source"] == "notion" and output["count"] == 2
    assert output["results"] == [
        {"title": "Roadmap 2027", "object": "page", "url": "https://www.notion.so/Roadmap-p1"},
        {"title": "Tasks", "object": "database", "url": "https://www.notion.so/Tasks-d1"},
    ]
    assert NOTION_ACCESS not in json.dumps(task.result)

    request = fake.sent_to("api.notion.com/v1/search")[0]
    assert request.method == "POST"
    assert request.headers["notion-version"] == "2022-06-28"
    assert request.headers["authorization"] == f"Bearer {NOTION_ACCESS}"
    assert json.loads(request.content) == {"query": "roadmap", "page_size": 3}


# -- refusals ----------------------------------------------------------------------------------


async def test_new_tools_refuse_when_not_connected_and_make_no_call(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    for tool, arguments in ((GMAIL_TOOL, {}), (DRIVE_TOOL, {"name_contains": "a"}), (NOTION_TOOL, {"query": "a"})):
        task = await _propose(more_app, user_id, tool, arguments)
        assert task.status == "failed" and task.error["code"] == "not_connected", tool
    assert fake.requests == []


async def test_new_tools_refuse_when_the_provider_is_not_configured(more_app: Any, more_settings: Settings, fake: MoreProviders) -> None:
    unconfigured = more_settings.model_copy(update={"google_oauth_client_id": None, "notion_oauth_client_secret": None})
    specs = {spec.name: spec for spec in integration_tool_specs(unconfigured, fake.client)}
    assert specs[GMAIL_TOOL].status == "requires_configuration"
    assert specs[DRIVE_TOOL].status == "requires_configuration"
    assert specs[NOTION_TOOL].status == "requires_configuration"
    user_id = uuid.uuid4()
    async with more_app.state.session_factory() as db:
        db.add(User(id=user_id, email="gina@example.com", display_name="Gina", password_hash=hash_password("correct-horse-battery"), is_active=True))
        await db.commit()
    gmail = await _propose(more_app, user_id, GMAIL_TOOL, {})
    notion = await _propose(more_app, user_id, NOTION_TOOL, {"query": "a"})
    assert gmail.status == "failed" and gmail.error["code"] == "not_configured"
    assert notion.status == "failed" and notion.error["code"] == "not_configured"
    assert fake.requests == []
    integration_tool_specs(more_settings, fake.client)


async def test_new_tool_arguments_are_refused_before_any_provider_call(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    with pytest.raises(ProposalRefusedError) as gmail_error:
        await _propose(more_app, user_id, GMAIL_TOOL, {"query": "line\nbreak"})
    assert gmail_error.value.code == "invalid_arguments"
    with pytest.raises(ProposalRefusedError):
        await _propose(more_app, user_id, DRIVE_TOOL, {"name_contains": "nul\x00"})
    with pytest.raises(ProposalRefusedError):
        await _propose(more_app, user_id, NOTION_TOOL, {"query": "ok", "max_results": 21})
    assert fake.requests == []


async def test_connections_are_isolated_per_user(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_notion(more_app, uuid.UUID(owner["id"]))
    other = uuid.uuid4()
    async with more_app.state.session_factory() as db:
        db.add(User(id=other, email="second@example.com", display_name="Second", password_hash=hash_password("correct-horse-battery"), is_active=True))
        await db.commit()
    task = await _propose(more_app, other, NOTION_TOOL, {"query": "roadmap"})
    assert task.status == "failed" and task.error["code"] == "not_connected"
    assert fake.requests == []


async def test_revoked_gmail_token_marks_the_connection_for_reconnecting(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    await _seed_gmail(more_app, user_id)
    fake.valid_tokens.discard(GMAIL_ACCESS)
    task = await _propose(more_app, user_id, GMAIL_TOOL, {})
    assert task.status == "failed" and task.error["code"] == "reconnect_required"
    row = await _stored(more_app, user_id, "gmail")
    assert row.status == "expired" and row.last_error_code == "reconnect_required"


# -- connection test and disconnect -------------------------------------------------------------


async def test_gmail_connection_test_reports_the_account(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_gmail(more_app, uuid.UUID(owner["id"]))
    body = (await more_client.post("/api/v1/integrations/gmail/test", headers=csrf_headers(more_client))).json()
    assert body == {"ok": True, "status": "connected", "account_label": "ada@example.com", "message": "The provider accepted the connection."}


async def test_notion_connection_test_probes_users_me_and_keeps_the_workspace_label(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_notion(more_app, uuid.UUID(owner["id"]))
    body = (await more_client.post("/api/v1/integrations/notion/test", headers=csrf_headers(more_client))).json()
    assert body["ok"] is True and body["status"] == "connected"
    assert body["account_label"] == "Ada's Workspace"
    probe = fake.sent_to("api.notion.com/v1/users/me")[0]
    assert probe.method == "GET" and probe.headers["notion-version"] == "2022-06-28"


async def test_notion_connection_test_with_a_revoked_token_reports_reconnect(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_notion(more_app, uuid.UUID(owner["id"]))
    fake.valid_tokens.discard(NOTION_ACCESS)
    body = (await more_client.post("/api/v1/integrations/notion/test", headers=csrf_headers(more_client))).json()
    assert body["ok"] is False and body["status"] == "expired"
    assert (await _listing(more_client))["notion"]["last_error_code"] == "reconnect_required"


async def test_gmail_disconnect_revokes_the_refresh_token(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_gmail(more_app, uuid.UUID(owner["id"]))
    body = (await more_client.delete("/api/v1/integrations/gmail/connection", headers=csrf_headers(more_client))).json()
    assert body["provider_revoked"] is True
    assert [_form(r)["token"] for r in fake.sent_to("oauth2.googleapis.com/revoke")] == [GMAIL_REFRESH]
    row = await _stored(more_app, uuid.UUID(owner["id"]), "gmail")
    assert row.credential_ciphertext is None and row.status == "disconnected"


async def test_notion_disconnect_deletes_locally_and_says_so(more_client: AsyncClient, more_app: Any, fake: MoreProviders) -> None:
    owner = await create_owner(more_client)
    await _seed_notion(more_app, uuid.UUID(owner["id"]))
    body = (await more_client.delete("/api/v1/integrations/notion/connection", headers=csrf_headers(more_client))).json()
    assert body == {"status": "disconnected", "provider_revoked": False, "message": NOTION.no_revoke_message}
    assert all("revoke" not in str(request.url) for request in fake.requests)
    row = await _stored(more_app, uuid.UUID(owner["id"]), "notion")
    assert row.credential_ciphertext is None and row.status == "disconnected"


# -- secrets ------------------------------------------------------------------------------------


async def test_tokens_and_client_secrets_never_reach_responses_or_logs(
    more_client: AsyncClient, more_app: Any, fake: MoreProviders, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner = await create_owner(more_client)
    user_id = uuid.UUID(owner["id"])
    seen: list[str] = []
    seen.append((await _connect_and_callback(more_client, "gmail", "gmail-code")).text)
    seen.append((await _connect_and_callback(more_client, "google_drive", "drive-code")).text)
    seen.append((await _connect_and_callback(more_client, "notion", "notion-code")).text)
    seen.append(json.dumps(await _listing(more_client)))
    for tool, arguments in ((GMAIL_TOOL, {}), (DRIVE_TOOL, {"name_contains": "a"}), (NOTION_TOOL, {"query": "a"})):
        seen.append(json.dumps((await _propose(more_app, user_id, tool, arguments)).result or {}))
    for provider in ("gmail", "google_drive", "notion"):
        seen.append((await more_client.post(f"/api/v1/integrations/{provider}/test", headers=csrf_headers(more_client))).text)
        seen.append((await more_client.delete(f"/api/v1/integrations/{provider}/connection", headers=csrf_headers(more_client))).text)

    # The one-time authorization codes are not checked: the request log records the callback URL, which carries the
    # code (documented in docs/integrations). Tokens and client secrets must never appear anywhere.
    everything = "\n".join(seen) + "\n" + caplog.text
    for secret in (*SECRETS, GOOGLE_CLIENT_SECRET, NOTION_CLIENT_SECRET):
        assert secret not in everything, secret
    assert "Bearer" not in caplog.text and "Basic " not in caplog.text
    assert "oauth connect started provider=notion" in caplog.text
