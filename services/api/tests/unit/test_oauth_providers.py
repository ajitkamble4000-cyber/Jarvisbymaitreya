"""Gmail, Google Drive and Notion: provider definitions, Drive query escaping, metadata-only Gmail, Notion Basic auth.

No database and no network. Provider calls go to an httpx.MockTransport that records every request.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.catalog import INTEGRATIONS
from app.integrations.oauth.flow import (
    basic_auth_value,
    build_authorization_url,
    exchange_code,
    new_code_verifier,
)
from app.integrations.oauth.providers import (
    DRIVE_METADATA_READONLY_SCOPE,
    GITHUB,
    GMAIL,
    GMAIL_READONLY_SCOPE,
    GOOGLE_DRIVE,
    NOTION,
    NOTION_VERSION,
    PROVIDERS,
    credentials,
    get_provider,
    is_configured,
)
from app.integrations.tools import (
    DriveSearchFilesArgs,
    GmailRecentMessagesArgs,
    NotionSearchArgs,
    drive_name_query,
    gmail_message_url,
    summarize_gmail_message,
)
from app.tools.args import ToolArgumentError, validate_tool_arguments

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
PUBLIC_BASE = "https://jarvis.example.com"
NOTION_SECRET = "notion-client-secret-value"
NOTION_CODE = "notion-auth-code-001"
NOTION_TOKEN = "ntn_notion-access-token-001"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "jarvis_env": "test",
        "encryption_key": Fernet.generate_key().decode("ascii"),
        "public_base_url": PUBLIC_BASE,
        "google_oauth_client_id": "google-client-id.apps.example",
        "google_oauth_client_secret": "google-client-secret-value",
        "notion_oauth_client_id": "notion-client-id",
        "notion_oauth_client_secret": NOTION_SECRET,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _literal_and_rest(query: str) -> tuple[str, str]:
    """Split a Drive query after ``name contains '`` into the quoted literal (escapes kept) and the rest.

    Escape pairs are consumed two characters at a time, so the literal ends only at an unescaped quote.
    """
    prefix = "name contains '"
    assert query.startswith(prefix)
    index = len(prefix)
    literal: list[str] = []
    while True:
        char = query[index]
        if char == "\\":
            literal.append(query[index : index + 2])
            index += 2
            continue
        if char == "'":
            return "".join(literal), query[index + 1 :]
        literal.append(char)
        index += 1


# -- provider definitions ---------------------------------------------------------------------


def test_registry_lists_the_five_implemented_providers_and_nothing_else() -> None:
    assert set(PROVIDERS) == {"google_calendar", "gmail", "google_drive", "github", "notion"}
    assert get_provider("gmail") is GMAIL
    assert get_provider("google_drive") is GOOGLE_DRIVE
    assert get_provider("notion") is NOTION
    assert get_provider("discord") is None and get_provider("slack") is None


def test_scope_lists_are_the_minimal_read_only_sets() -> None:
    assert GMAIL.scopes == ("openid", "email", "https://www.googleapis.com/auth/gmail.readonly")
    assert GOOGLE_DRIVE.scopes == ("openid", "email", "https://www.googleapis.com/auth/drive.metadata.readonly")
    assert GMAIL.required_scopes == (GMAIL_READONLY_SCOPE,)
    assert GOOGLE_DRIVE.required_scopes == (DRIVE_METADATA_READONLY_SCOPE,)
    # Notion sends no scope parameter and its token response has no scope list.
    assert NOTION.scopes == () and NOTION.required_scopes == ()
    write_words = ("modify", "send", "compose", "write", "insert", "full", "delete")
    for provider in PROVIDERS.values():
        for scope in provider.scopes:
            assert not any(word in scope for word in write_words), (provider.key, scope)


def test_google_providers_share_the_calendar_client_settings_and_endpoints() -> None:
    for provider in (GMAIL, GOOGLE_DRIVE):
        assert provider.client_id_setting == "google_oauth_client_id"
        assert provider.client_secret_setting == "google_oauth_client_secret"
        assert provider.token_url == "https://oauth2.googleapis.com/token"
        assert provider.revoke_url == "https://oauth2.googleapis.com/revoke"
        assert provider.account_field == "email"
        assert provider.token_request == "form" and provider.account_from_token is False


def test_notion_uses_its_own_settings_json_basic_and_workspace_label() -> None:
    assert NOTION.client_id_setting == "notion_oauth_client_id"
    assert NOTION.client_secret_setting == "notion_oauth_client_secret"
    assert NOTION.token_request == "json_basic"
    assert NOTION.account_from_token is True and NOTION.account_field == "workspace_name"
    assert NOTION.revoke_url is None
    assert ("Notion-Version", NOTION_VERSION) in NOTION.api_headers
    assert dict(NOTION.authorize_params) == {"owner": "user"}


def test_each_provider_is_configured_only_from_its_own_client_settings() -> None:
    settings = _settings()
    assert is_configured(settings, GMAIL) and is_configured(settings, GOOGLE_DRIVE)
    assert is_configured(settings, NOTION)
    assert not is_configured(_settings(notion_oauth_client_secret=None), NOTION)
    assert not is_configured(_settings(google_oauth_client_id=None), GMAIL)
    assert credentials(settings, GOOGLE_DRIVE) == ("google-client-id.apps.example", "google-client-secret-value")


def test_catalogue_requirements_name_the_environment_variables_that_are_read() -> None:
    catalogue = {spec.id: spec for spec in INTEGRATIONS}
    google = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET")
    assert catalogue["gmail"].requires_configuration == google
    assert catalogue["google_drive"].requires_configuration == google
    assert catalogue["notion"].requires_configuration == ("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET")
    assert catalogue["gmail"].implemented and catalogue["google_drive"].implemented and catalogue["notion"].implemented
    assert not catalogue["discord"].implemented and not catalogue["slack"].implemented
    assert catalogue["gmail"].scopes_requested == GMAIL.scopes
    assert catalogue["google_drive"].scopes_requested == GOOGLE_DRIVE.scopes
    assert GITHUB.no_revoke_message and "GitHub Settings > Applications" in GITHUB.no_revoke_message


# -- authorization URLs -----------------------------------------------------------------------


def test_notion_authorization_url_sends_owner_code_and_pkce_but_no_scope() -> None:
    url = build_authorization_url(_settings(), NOTION, state="state-token", challenge="challenge-value")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://api.notion.com/v1/oauth/authorize"
    assert query["owner"] == ["user"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge"] == ["challenge-value"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{PUBLIC_BASE}/api/v1/integrations/notion/callback"]
    assert "scope" not in query and "access_type" not in query


@pytest.mark.parametrize(
    ("provider", "scope"),
    [
        (GMAIL, "openid email https://www.googleapis.com/auth/gmail.readonly"),
        (GOOGLE_DRIVE, "openid email https://www.googleapis.com/auth/drive.metadata.readonly"),
    ],
)
def test_google_read_only_authorization_urls_ask_for_the_exact_scopes(provider: Any, scope: str) -> None:
    url = build_authorization_url(_settings(), provider, state="s", challenge="c")
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == [scope]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["redirect_uri"] == [f"{PUBLIC_BASE}/api/v1/integrations/{provider.key}/callback"]


# -- Drive query escaping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("budget", "name contains 'budget' and trashed = false"),
        ("O'Neil", "name contains 'O\\'Neil' and trashed = false"),
        ("c:\\temp", "name contains 'c:\\\\temp' and trashed = false"),
        ("a\\'b", "name contains 'a\\\\\\'b' and trashed = false"),
    ],
)
def test_drive_query_escapes_backslashes_and_single_quotes(name: str, expected: str) -> None:
    assert drive_name_query(name) == expected


def test_hostile_drive_name_cannot_change_the_structure_of_the_query() -> None:
    hostile = "x' or name != 'y"
    query = drive_name_query(hostile)
    literal, rest = _literal_and_rest(query)
    # The whole hostile text is one literal; the only clause after it is the fixed trashed filter.
    assert literal == "x\\' or name != \\'y"
    assert rest == " and trashed = false"
    assert query.count(" and ") == 1 and " or " not in rest


def test_backslash_before_a_quote_does_not_close_the_literal_early() -> None:
    hostile = "a\\' or trashed = true or name = '"
    literal, rest = _literal_and_rest(drive_name_query(hostile))
    assert rest == " and trashed = false"
    assert literal == "a\\\\\\' or trashed = true or name = \\'"


@pytest.mark.parametrize("bad", ["line\nbreak", "tab\there", "nul\x00byte", "del\x7fchar", "   ", "x" * 101])
def test_drive_name_with_control_characters_blank_or_too_long_is_refused(bad: str) -> None:
    with pytest.raises(ToolArgumentError) as exc:
        validate_tool_arguments(DriveSearchFilesArgs, {"name_contains": bad})
    assert exc.value.code == "invalid_arguments"
    assert exc.value.fields == ["name_contains"]


def test_drive_arguments_accept_the_limits_and_default_the_page_size() -> None:
    args = validate_tool_arguments(DriveSearchFilesArgs, {"name_contains": "x" * 100})
    assert isinstance(args, DriveSearchFilesArgs) and args.max_results == 10
    for value in (0, 21):
        with pytest.raises(ToolArgumentError):
            validate_tool_arguments(DriveSearchFilesArgs, {"name_contains": "a", "max_results": value})


# -- Gmail: metadata only ---------------------------------------------------------------------


def test_gmail_defaults_and_limits() -> None:
    defaults = validate_tool_arguments(GmailRecentMessagesArgs, {})
    assert isinstance(defaults, GmailRecentMessagesArgs)
    assert defaults.query == "is:unread" and defaults.max_results == 5
    for raw in ({"query": "a\nb"}, {"query": "x" * 101}, {"max_results": 11}, {"max_results": 0}, {"query": " "}):
        with pytest.raises(ToolArgumentError):
            validate_tool_arguments(GmailRecentMessagesArgs, raw)


def test_gmail_message_request_asks_for_metadata_headers_only() -> None:
    url = gmail_message_url("18c0a1b2d3e4f5a6")
    query = parse_qs(urlsplit(url).query)
    assert query == {
        "format": ["metadata"],
        "metadataHeaders": ["From", "Subject", "Date"],
    }
    assert urlsplit(url).path == "/gmail/v1/users/me/messages/18c0a1b2d3e4f5a6"


@pytest.mark.parametrize("bad_id", ["abc/../../x", "a?format=full", "id with space", "", "x" * 65])
def test_gmail_message_id_that_is_not_a_plain_id_is_refused(bad_id: str) -> None:
    with pytest.raises(AppError) as exc:
        gmail_message_url(bad_id)
    assert exc.value.code == "provider_error"


def test_gmail_summary_keeps_three_headers_and_never_reads_body_or_snippet() -> None:
    body: dict[str, Any] = {
        "id": "m1",
        "snippet": "SECRET-SNIPPET-TEXT",
        "payload": {
            "body": {"data": "U0VDUkVULUJPRFk="},
            "headers": [
                {"name": "FROM", "value": "Ada <ada@example.com>"},
                {"name": "Subject", "value": "S" * 250},
                {"name": "Date", "value": "Wed, 21 Oct 2026 09:00:00 +0000"},
                {"name": "To", "value": "someone@example.com"},
            ],
        },
    }
    summary = summarize_gmail_message(body)
    assert set(summary) == {"from", "subject", "date"}
    assert summary["from"] == "Ada <ada@example.com>"
    assert len(summary["subject"] or "") == 200
    assert summary["date"] == "Wed, 21 Oct 2026 09:00:00 +0000"
    rendered = json.dumps(summary)
    assert "SECRET-SNIPPET-TEXT" not in rendered and "U0VDUkVULUJPRFk" not in rendered
    assert "someone@example.com" not in rendered


def test_gmail_summary_truncates_from_and_defaults_missing_subject() -> None:
    body = {"payload": {"headers": [{"name": "From", "value": "f" * 300}]}}
    summary = summarize_gmail_message(body)
    assert len(summary["from"] or "") == 120
    assert summary["subject"] == "(no subject)"
    assert summary["date"] is None


# -- Notion ------------------------------------------------------------------------------------


def test_notion_basic_auth_header_is_built_from_the_client_id_and_secret() -> None:
    assert basic_auth_value("client", "secret") == "Basic Y2xpZW50OnNlY3JldA=="
    expected = base64.b64encode(f"notion-client-id:{NOTION_SECRET}".encode()).decode("ascii")
    assert basic_auth_value("notion-client-id", NOTION_SECRET) == f"Basic {expected}"


def test_notion_search_arguments_are_bounded() -> None:
    args = validate_tool_arguments(NotionSearchArgs, {"query": "roadmap"})
    assert isinstance(args, NotionSearchArgs) and args.max_results == 10
    for raw in ({"query": ""}, {"query": "x" * 101}, {"query": "a\x00b"}, {"query": "ok", "max_results": 21}):
        with pytest.raises(ToolArgumentError):
            validate_tool_arguments(NotionSearchArgs, raw)


def _notion_transport(
    seen: list[httpx.Request], *, token_status: int = 200, token_body: dict[str, Any] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if token_body is not None:
            return httpx.Response(token_status, json=token_body)
        return httpx.Response(
            200,
            json={
                "access_token": NOTION_TOKEN,
                "token_type": "bearer",
                "bot_id": "bot-1",
                "workspace_name": "Ada's Workspace",
            },
        )

    return httpx.MockTransport(handle)


async def test_notion_token_request_is_json_with_basic_auth_and_no_secret_in_the_body() -> None:
    seen: list[httpx.Request] = []
    verifier = new_code_verifier()
    async with httpx.AsyncClient(transport=_notion_transport(seen)) as http:
        credential, body = await exchange_code(
            http, _settings(), NOTION, code=NOTION_CODE, verifier=verifier, now=NOW
        )
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.notion.com/v1/oauth/token"
    assert request.headers["authorization"] == basic_auth_value("notion-client-id", NOTION_SECRET)
    assert request.headers["content-type"].startswith("application/json")
    sent = json.loads(request.content)
    assert sent == {
        "grant_type": "authorization_code",
        "code": NOTION_CODE,
        "redirect_uri": f"{PUBLIC_BASE}/api/v1/integrations/notion/callback",
        "code_verifier": verifier,
    }
    assert NOTION_SECRET not in request.content.decode("utf-8")
    assert credential.access_token == NOTION_TOKEN
    assert credential.refresh_token is None and credential.expires_at is None
    assert body["workspace_name"] == "Ada's Workspace"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (400, {"object": "error", "status": 400, "code": "invalid_grant", "message": "expired"}, "exchange_failed"),
        (400, {"object": "error", "status": 400, "code": "unauthorized", "message": "no"}, "exchange_failed"),
        (503, {"object": "error", "message": "down"}, "provider_unavailable"),
        (200, {"workspace_name": "No token here"}, "exchange_failed"),
    ],
)
async def test_notion_token_failures_map_to_stable_codes_without_provider_text(
    status: int, body: dict[str, Any], code: str
) -> None:
    async with httpx.AsyncClient(transport=_notion_transport([], token_status=status, token_body=body)) as http:
        with pytest.raises(AppError) as exc:
            await exchange_code(http, _settings(), NOTION, code=NOTION_CODE, verifier=new_code_verifier(), now=NOW)
    assert exc.value.code == code
    assert "expired" not in exc.value.message and NOTION_SECRET not in exc.value.message
    assert NOTION_CODE not in exc.value.message


async def test_notion_token_response_that_is_not_json_is_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(AppError) as exc:
            await exchange_code(http, _settings(), NOTION, code=NOTION_CODE, verifier=new_code_verifier(), now=NOW)
    assert exc.value.code == "exchange_failed"


def test_notion_disconnect_message_says_nothing_is_revoked_remotely() -> None:
    assert NOTION.no_revoke_message is not None
    assert "does not revoke Notion tokens" in NOTION.no_revoke_message
    assert "GitHub" not in NOTION.no_revoke_message
