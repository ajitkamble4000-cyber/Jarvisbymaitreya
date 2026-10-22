"""Plugin ``clock``: one read-only server tool, ``current_time``.

It reports the local date and time in an IANA time zone. Without a ``timezone`` argument it uses the time zone in the
user's settings, and UTC when none is set. The handler reads the settings of the user who asked, never of anyone else.

This is trusted code that runs in-process. See docs/plugins.md.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.core.time import utc_now
from app.services.settings_service import load_settings
from app.tools.args import ToolArgs
from app.tools.base import ExecutionTarget, RiskLevel, ToolSpec
from app.tools.server_handlers import ServerHandler

PLUGIN_NAME: Final = "clock"
PLUGIN_VERSION: Final = "1.0.0"
TOOL_NAME: Final = "current_time"
_ZONE_HELP = "must be an IANA time zone name such as Europe/London"


class CurrentTimeArgs(ToolArgs):
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not name:
            raise ValueError(_ZONE_HELP)
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError(_ZONE_HELP) from None
        return name


def _format_offset(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds()) // 60
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


async def _current_time(db: AsyncSession, user_id: UUID, args: BaseModel) -> dict:
    if not isinstance(args, CurrentTimeArgs):
        raise ValidationAppError("The arguments are not valid.", code="invalid_arguments")
    zone_name = args.timezone
    if zone_name is None:
        document = await load_settings(db, user_id)
        zone_name = document.timezone or "UTC"
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValidationAppError("The time zone is not valid.", code="invalid_timezone") from None

    local_now = utc_now().astimezone(zone)
    offset = _format_offset(local_now.utcoffset() or timedelta(0))
    local_text = local_now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "summary": f"It is {local_text} in {zone_name} (UTC{offset}).",
        "timezone": zone_name,
        "local_time": local_now.isoformat(timespec="seconds"),
        "utc_offset": offset,
    }


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_NAME,
            title="Current time",
            description=(
                "Read the current local date and time in an IANA time zone such as Europe/London. "
                "Without a time zone it uses the zone in the user's settings, or UTC when none is set. "
                "Read-only."
            ),
            args_model=CurrentTimeArgs,
            risk=RiskLevel.LOW,
            target=ExecutionTarget.SERVER,
            category="productivity",
            approval_required=False,
            example_commands=("What time is it?", "What time is it in Tokyo?"),
            notes="Provided by the clock plugin.",
        )
    ]


def server_handlers() -> dict[str, ServerHandler]:
    return {TOOL_NAME: _current_time}
