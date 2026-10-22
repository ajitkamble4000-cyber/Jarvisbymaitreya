"""Capability discovery: what JARVIS can do for this user, and why an item is or is not ready.

Discovery is read-only. Listing an item never enables a tool, changes a setting or creates a task.
Statuses are derived from the state the server can see right now:

* device tools need a paired device, otherwise they are ``requires_configuration``;
* chat needs an enabled AI provider with a stored credential, otherwise ``requires_configuration``;
* reminders and schedules need the background workers, otherwise ``requires_configuration``;
* integrations are ``unsupported`` because OAuth is planned, not implemented.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.catalog import integration_catalogue
from app.integrations.oauth.providers import GITHUB, GMAIL, GOOGLE_CALENDAR, GOOGLE_DRIVE, NOTION, PROVIDERS
from app.integrations.tools import CALENDAR_TOOL, DRIVE_TOOL, GITHUB_TOOL, GMAIL_TOOL, NOTION_TOOL
from app.models.ai import ProviderConfig
from app.models.platform import Integration
from app.schemas.platform import ExploreItemOut, ExploreOut, ExploreStatus, ToolOut
from app.schemas.devices import filter_capabilities
from app.services.device_resolver import paired_devices, permission_mode
from app.tools.base import ExecutionTarget, ToolRegistry, ToolSpec

EXPLORE_CATEGORIES: tuple[str, ...] = (
    "Productivity",
    "Communication",
    "Development",
    "Files",
    "Devices",
    "Scheduling",
    "Research",
    "Media",
    "Automation",
)

# Tool categories mapped onto the explore categories: the built-in catalog categories from app/tools/catalog.py,
# and every category a plugin may declare (PLUGIN_CATEGORIES in app/plugins/loader.py).
_TOOL_CATEGORY_MAP: dict[str, str] = {
    "apps": "Productivity",
    "memory": "Productivity",
    "web": "Research",
    "files": "Files",
    "projects": "Development",
    "system": "Devices",
    "devices": "Devices",
    "tasks": "Automation",
    "reminders": "Scheduling",
    "productivity": "Productivity",
    "communication": "Communication",
    "development": "Development",
    "scheduling": "Scheduling",
    "research": "Research",
    "media": "Media",
    "automation": "Automation",
}

_REMINDER_CATEGORY = "reminders"
_BACKGROUND_REQUIREMENT = "Background workers must be running (RUN_BACKGROUND_WORKERS=true)."

# Integration tool name -> provider whose connection the tool needs.
_INTEGRATION_TOOL_PROVIDERS: dict[str, str] = {
    CALENDAR_TOOL: GOOGLE_CALENDAR.key,
    GITHUB_TOOL: GITHUB.key,
    GMAIL_TOOL: GMAIL.key,
    DRIVE_TOOL: GOOGLE_DRIVE.key,
    NOTION_TOOL: NOTION.key,
}
# Connection statuses for which the tool refuses to run (see app/integrations/oauth/tokens.fresh_access_token).
_NOT_USABLE_INTEGRATION_STATUSES = frozenset({"not_connected", "disconnected", "expired"})


@dataclass(frozen=True)
class ExploreContext:
    """Server state that decides whether an item is ready for this user."""

    has_paired_device: bool
    provider_configured: bool
    background_workers: bool
    disabled_tools: frozenset[str]
    # Capabilities that some paired device reports and has not switched to "deny". None means unknown,
    # which leaves the capability check to has_paired_device alone.
    usable_capabilities: frozenset[str] | None = None
    # Providers this user has a usable connection for. None means unknown, which leaves the check out.
    connected_integrations: frozenset[str] | None = None


async def load_context(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    background_workers: bool,
    disabled_tools: Iterable[str],
) -> ExploreContext:
    devices = await paired_devices(db, user_id)
    usable: set[str] = set()
    for device in devices:
        for capability in filter_capabilities(device.capabilities or []):
            if permission_mode(device, capability) != "deny":
                usable.add(capability)
    provider = await db.scalar(
        select(ProviderConfig.id).where(
            ProviderConfig.user_id == user_id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.credential_ciphertext.is_not(None),
        )
    )
    integration_rows = await db.execute(
        select(Integration.provider, Integration.status, Integration.credential_ciphertext).where(
            Integration.user_id == user_id
        )
    )
    connected = {
        provider_key
        for provider_key, status, credential in integration_rows.all()
        if credential is not None and status not in _NOT_USABLE_INTEGRATION_STATUSES
    }
    return ExploreContext(
        has_paired_device=bool(devices),
        provider_configured=provider is not None,
        background_workers=background_workers,
        disabled_tools=frozenset(disabled_tools),
        usable_capabilities=frozenset(usable),
        connected_integrations=frozenset(connected),
    )


def tool_out(spec: ToolSpec) -> ToolOut:
    """Public shape of one registry entry. ``status`` is the spec's own status, unchanged."""
    return ToolOut(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        category=spec.category,
        risk_level=str(spec.risk.value),
        target=str(spec.target.value),
        requires_device=spec.target is ExecutionTarget.DEVICE,
        approval_required=spec.approval_required,
        status=spec.status,
        example_commands=list(spec.example_commands),
        notes=spec.notes,
        parameters=spec.parameters_schema(),
    )


def _tool_item(spec: ToolSpec, ctx: ExploreContext) -> ExploreItemOut:
    status: ExploreStatus = spec.status
    requirements: list[str] = []
    provider_key = _INTEGRATION_TOOL_PROVIDERS.get(spec.name)
    if (
        provider_key is not None
        and ctx.connected_integrations is not None
        and provider_key not in ctx.connected_integrations
    ):
        # The tool is refused until this user connects the provider, so it is not reported as working.
        requirements.append(f"Connect {PROVIDERS[provider_key].label} on the Integrations page first.")
        if status == "working":
            status = "requires_configuration"
    if spec.target is ExecutionTarget.DEVICE:
        if not ctx.has_paired_device:
            requirements.append("Pair a desktop agent on the Devices page first.")
            if status == "working":
                status = "requires_configuration"
        elif (
            ctx.usable_capabilities is not None
            and spec.required_capability is not None
            and spec.required_capability not in ctx.usable_capabilities
        ):
            # Dispatch refuses this tool (capability missing or permission deny), so do not report it as working.
            requirements.append("Turn this capability on for a paired device in Devices settings.")
            if status == "working":
                status = "requires_configuration"
        requirements.append(f"Needs a paired device that reports the '{spec.required_capability}' capability.")
    if spec.category == _REMINDER_CATEGORY:
        requirements.append(_BACKGROUND_REQUIREMENT)
        if status == "working" and not ctx.background_workers:
            status = "requires_configuration"
    return ExploreItemOut(
        id=spec.name,
        title=spec.title,
        category=_TOOL_CATEGORY_MAP.get(spec.category, "Automation"),
        status=status,
        description=spec.description,
        example_commands=list(spec.example_commands),
        requirements=requirements,
        enabled_for_user=spec.name not in ctx.disabled_tools,
    )


def _other_items(ctx: ExploreContext) -> list[ExploreItemOut]:
    chat_ready = ctx.provider_configured
    schedules_ready = ctx.background_workers
    items: list[ExploreItemOut] = [
        ExploreItemOut(
            id="chat",
            title="Chat with JARVIS",
            category="Productivity",
            status="working" if chat_ready else "requires_configuration",
            description=(
                "Ask questions and get answers from your AI provider. "
                "Actions are only proposed, and they wait for your approval when required."
            ),
            example_commands=["What is on my plate today?"],
            requirements=[] if chat_ready else ["Add an AI provider key and enable it on the AI Models page."],
            enabled_for_user=True,
        ),
        ExploreItemOut(
            id="voice",
            title="Voice",
            category="Media",
            status="working",
            description="Speak to JARVIS and hear replies in the web app. This depends on the browser.",
            example_commands=[],
            requirements=["Browser-dependent: the browser must provide speech recognition and speech synthesis."],
            enabled_for_user=True,
        ),
        ExploreItemOut(
            id="schedules",
            title="Scheduled tasks",
            category="Scheduling",
            status="working" if schedules_ready else "requires_configuration",
            description=(
                "Run an approved tool once, daily or weekly. Device tools still ask for approval "
                "unless the device permission is allow and the tool is low risk."
            ),
            example_commands=["Check my laptop every weekday at 08:00."],
            requirements=[] if schedules_ready else [_BACKGROUND_REQUIREMENT],
            enabled_for_user=True,
        ),
        ExploreItemOut(
            id="emergency_stop",
            title="Emergency stop",
            category="Automation",
            status="working",
            description="Cancel queued, waiting and running automation, and block new automation until you resume it.",
            example_commands=[],
            requirements=[],
            enabled_for_user=True,
        ),
    ]
    for spec in integration_catalogue():
        items.append(
            ExploreItemOut(
                id=spec.id,
                title=spec.name,
                category=spec.category,
                status="unsupported",
                description="Planned integration. OAuth is not implemented, so it cannot be connected.",
                example_commands=[],
                requirements=[f"Planned setup: {', '.join(spec.requires_configuration)}."],
                enabled_for_user=False,
            )
        )
    return items


def build_explore(registry: ToolRegistry, ctx: ExploreContext) -> list[ExploreItemOut]:
    tool_items = [_tool_item(spec, ctx) for spec in registry.all()]
    return tool_items + _other_items(ctx)


def explore_response(
    items: list[ExploreItemOut],
    *,
    category: str | None,
    query: str | None,
) -> ExploreOut:
    """Filter by category (case-insensitive exact match) and free text, then order by category and title."""
    wanted_category = category.strip().lower() if category else None
    needle = query.strip().lower() if query else None
    selected: list[ExploreItemOut] = []
    for item in items:
        if wanted_category is not None and item.category.lower() != wanted_category:
            continue
        if needle is not None and not _matches(item, needle):
            continue
        selected.append(item)
    order = {name: index for index, name in enumerate(EXPLORE_CATEGORIES)}
    selected.sort(key=lambda item: (order.get(item.category, len(order)), item.title.lower()))
    return ExploreOut(categories=list(EXPLORE_CATEGORIES), items=selected)


def _matches(item: ExploreItemOut, needle: str) -> bool:
    haystack = [item.id, item.title, item.description, *item.example_commands]
    return any(needle in text.lower() for text in haystack)
