"""OAuth providers JARVIS can connect to.

Endpoints, scopes and parameter names are constants in this module. Nothing a user sends can change
them. A provider is offered only when both of its client settings are set (see ``credentials``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from app.core.config import Settings

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GITHUB_REPOSITORIES_URL = "https://api.github.com/user/repos"
NOTION_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_SEARCH_URL = "https://api.notion.com/v1/search"
NOTION_USERS_ME_URL = "https://api.notion.com/v1/users/me"
NOTION_VERSION = "2022-06-28"

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_METADATA_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"

MAX_SCOPES = 50
MAX_SCOPE_CHARS = 200
MAX_ACCOUNT_LABEL_CHARS = 200

_SCOPE_SEPARATORS = re.compile(r"[\s,]+")


@dataclass(frozen=True)
class OAuthProvider:
    """Fixed description of one provider. The key doubles as the integration id used in routes and the database."""

    key: str
    label: str
    authorize_url: str
    token_url: str
    account_url: str
    account_field: str
    scopes: tuple[str, ...]
    client_id_setting: str
    client_secret_setting: str
    revoke_url: str | None = None
    authorize_params: tuple[tuple[str, str], ...] = ()
    # Granted scopes that must be present before a connection is stored. Google issues a token even when the user
    # unticks a permission, so the callback checks these.
    required_scopes: tuple[str, ...] = ()
    # "form": form fields with the client credentials in the body (Google, GitHub).
    # "json_basic": a JSON body with the client credentials as HTTP Basic auth (Notion).
    token_request: Literal["form", "json_basic"] = "form"
    # True when the account label is the name in the token response (Notion's workspace), not an account call.
    account_from_token: bool = False
    # Headers that every API call to the provider needs.
    api_headers: tuple[tuple[str, str], ...] = ()
    # Disconnect message for a provider without a revoke endpoint. The stored token is deleted locally only.
    no_revoke_message: str | None = None


GOOGLE_CALENDAR = OAuthProvider(
    key="google_calendar",
    label="Google Calendar",
    authorize_url=GOOGLE_AUTHORIZE_URL,
    token_url=GOOGLE_TOKEN_URL,
    revoke_url=GOOGLE_REVOKE_URL,
    account_url=GOOGLE_USERINFO_URL,
    account_field="email",
    scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.readonly",
    ),
    client_id_setting="google_oauth_client_id",
    client_secret_setting="google_oauth_client_secret",
    # offline + consent make Google issue a refresh token on every connect.
    authorize_params=(("access_type", "offline"), ("prompt", "consent")),
    required_scopes=("https://www.googleapis.com/auth/calendar.readonly",),
)

# Gmail and Drive use the Google client that Calendar uses. Each needs its own redirect URI registered at Google.
GMAIL = OAuthProvider(
    key="gmail",
    label="Gmail",
    authorize_url=GOOGLE_AUTHORIZE_URL,
    token_url=GOOGLE_TOKEN_URL,
    revoke_url=GOOGLE_REVOKE_URL,
    account_url=GOOGLE_USERINFO_URL,
    account_field="email",
    scopes=("openid", "email", GMAIL_READONLY_SCOPE),
    client_id_setting="google_oauth_client_id",
    client_secret_setting="google_oauth_client_secret",
    authorize_params=(("access_type", "offline"), ("prompt", "consent")),
    required_scopes=(GMAIL_READONLY_SCOPE,),
)

GOOGLE_DRIVE = OAuthProvider(
    key="google_drive",
    label="Google Drive",
    authorize_url=GOOGLE_AUTHORIZE_URL,
    token_url=GOOGLE_TOKEN_URL,
    revoke_url=GOOGLE_REVOKE_URL,
    account_url=GOOGLE_USERINFO_URL,
    account_field="email",
    scopes=("openid", "email", DRIVE_METADATA_READONLY_SCOPE),
    client_id_setting="google_oauth_client_id",
    client_secret_setting="google_oauth_client_secret",
    authorize_params=(("access_type", "offline"), ("prompt", "consent")),
    required_scopes=(DRIVE_METADATA_READONLY_SCOPE,),
)

GITHUB = OAuthProvider(
    key="github",
    label="GitHub",
    authorize_url="https://github.com/login/oauth/authorize",
    token_url="https://github.com/login/oauth/access_token",
    # GitHub does not expose token revocation to OAuth apps without app credentials, so there is no revoke_url.
    revoke_url=None,
    account_url="https://api.github.com/user",
    account_field="login",
    scopes=("read:user",),
    client_id_setting="github_oauth_client_id",
    client_secret_setting="github_oauth_client_secret",
    required_scopes=("read:user",),
    no_revoke_message=(
        "The stored token was deleted. JARVIS cannot revoke a GitHub token without app credentials, "
        "so the grant stays active at GitHub until you remove JARVIS under GitHub Settings > Applications."
    ),
)

# Notion grants access per page or database chosen on its consent screen and sends no scope list. PKCE is sent,
# but Notion does not document it, so whether Notion checks it is unverified.
NOTION = OAuthProvider(
    key="notion",
    label="Notion",
    authorize_url=NOTION_AUTHORIZE_URL,
    token_url=NOTION_TOKEN_URL,
    revoke_url=None,
    account_url=NOTION_USERS_ME_URL,
    account_field="workspace_name",
    scopes=(),
    client_id_setting="notion_oauth_client_id",
    client_secret_setting="notion_oauth_client_secret",
    authorize_params=(("owner", "user"),),
    token_request="json_basic",
    account_from_token=True,
    api_headers=(("Notion-Version", NOTION_VERSION),),
    no_revoke_message=(
        "The stored token was deleted. JARVIS does not revoke Notion tokens, so the connection stays active at "
        "Notion until you remove JARVIS from your Notion workspace connections."
    ),
)

PROVIDERS: dict[str, OAuthProvider] = {
    provider.key: provider for provider in (GOOGLE_CALENDAR, GMAIL, GOOGLE_DRIVE, GITHUB, NOTION)
}


def get_provider(key: str) -> OAuthProvider | None:
    return PROVIDERS.get(key)


def _secret_text(value: SecretStr | str | None) -> str:
    # str() on a SecretStr returns the mask, so the value is unwrapped explicitly.
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


def credentials(settings: Settings, provider: OAuthProvider) -> tuple[str, str] | None:
    """Client id and secret for the provider, or None when either one is missing."""
    client_id = str(getattr(settings, provider.client_id_setting) or "").strip()
    client_secret = _secret_text(getattr(settings, provider.client_secret_setting)).strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def is_configured(settings: Settings, provider: OAuthProvider) -> bool:
    return credentials(settings, provider) is not None


def redirect_uri(settings: Settings, provider: OAuthProvider) -> str:
    """The exact callback URL. It is built only from PUBLIC_BASE_URL and must match the provider's app settings."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/integrations/{provider.key}/callback"


def parse_scopes(raw: str | None) -> list[str]:
    """Granted scopes from a token response. Google separates them with spaces, GitHub with commas."""
    if not raw:
        return []
    scopes: list[str] = []
    for item in _SCOPE_SEPARATORS.split(raw.strip()):
        if not item or len(item) > MAX_SCOPE_CHARS or item in scopes:
            continue
        scopes.append(item)
        if len(scopes) == MAX_SCOPES:
            break
    return scopes
