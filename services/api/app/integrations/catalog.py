"""Catalogue of external integrations JARVIS offers.

Five entries are implemented with OAuth 2.0 and PKCE (Google Calendar, Gmail, Google Drive, GitHub and Notion, see
``oauth``). The other entries are planned: no code connects to them. An implemented entry's status is computed from the server
configuration and the user's stored connection (see ``integration_status``). The environment variable names
in ``requires_configuration`` are the ones read by ``app.core.config``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.integrations.oauth.providers import GMAIL, GOOGLE_CALENDAR, GOOGLE_DRIVE

PLANNED_STATUS = "planned"
REQUIRES_CONFIGURATION_STATUS = "requires_configuration"
NOT_CONNECTED_STATUS = "not_connected"

_GOOGLE_OAUTH = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET")


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    name: str
    category: str
    requires_configuration: tuple[str, ...]
    scopes_requested: tuple[str, ...]
    implemented: bool = False
    # Repository path of the setup doc. Defaults to docs/integrations/<id>.md.
    docs_path: str | None = None

    @property
    def docs(self) -> str:
        return self.docs_path or f"docs/integrations/{self.id}.md"


INTEGRATIONS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        id="gmail",
        name="Gmail",
        category="Communication",
        requires_configuration=_GOOGLE_OAUTH,
        scopes_requested=GMAIL.scopes,
        implemented=True,
    ),
    IntegrationSpec(
        id="google_calendar",
        name="Google Calendar",
        category="Productivity",
        requires_configuration=_GOOGLE_OAUTH,
        scopes_requested=GOOGLE_CALENDAR.scopes,
        implemented=True,
    ),
    IntegrationSpec(
        id="google_drive",
        name="Google Drive",
        category="Files",
        requires_configuration=_GOOGLE_OAUTH,
        scopes_requested=GOOGLE_DRIVE.scopes,
        implemented=True,
    ),
    IntegrationSpec(
        id="github",
        name="GitHub",
        category="Development",
        requires_configuration=("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"),
        scopes_requested=("read:user",),
        implemented=True,
    ),
    IntegrationSpec(
        id="discord",
        name="Discord",
        category="Communication",
        requires_configuration=("DISCORD_OAUTH_CLIENT_ID", "DISCORD_OAUTH_CLIENT_SECRET"),
        scopes_requested=("identify",),
    ),
    IntegrationSpec(
        id="slack",
        name="Slack",
        category="Communication",
        requires_configuration=("SLACK_OAUTH_CLIENT_ID", "SLACK_OAUTH_CLIENT_SECRET"),
        scopes_requested=("channels:read",),
    ),
    IntegrationSpec(
        id="notion",
        name="Notion",
        category="Productivity",
        requires_configuration=("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET"),
        scopes_requested=("read_content",),
        implemented=True,
    ),
)


def integration_catalogue() -> tuple[IntegrationSpec, ...]:
    return INTEGRATIONS


def implemented_count() -> int:
    return sum(1 for spec in INTEGRATIONS if spec.implemented)


def integration_status(spec: IntegrationSpec, *, configured: bool, stored_status: str | None) -> str:
    """Planned entries stay planned. Implemented ones need configuration first, then show the stored state."""
    if not spec.implemented:
        return PLANNED_STATUS
    if not configured:
        return REQUIRES_CONFIGURATION_STATUS
    return stored_status or NOT_CONNECTED_STATUS
