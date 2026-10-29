"""The persisted settings document (docs/api.md "Settings").

Every section and key below appears in docs/api.md with the same default. Validation is strict:
unknown keys are rejected, numbers must lie in the documented ranges, enumerations are closed and
booleans must be real booleans. ``automation.emergency_stopped`` is part of the document so that
reads return it, but the settings PATCH route refuses to change it (see settings_service).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

ACCENT_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
NAVIGATION_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,79}$"
LANGUAGE_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$"
CLOCK_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"

ProviderId = Literal["gemini", "huggingface", "openrouter"]


def _reject_bool(value: Any) -> Any:
    """Booleans are valid JSON numbers in Python; a setting that expects a number must not accept them."""
    if isinstance(value, bool):
        raise ValueError("must be a number")
    return value


Unit = Annotated[float, Field(ge=0, le=2), BeforeValidator(_reject_bool)]
Rate = Annotated[float, Field(ge=0.5, le=2.0), BeforeValidator(_reject_bool)]
OutputTokens = Annotated[int, Field(ge=64, le=8192), BeforeValidator(_reject_bool)]
ConcurrentTasks = Annotated[int, Field(ge=1, le=10), BeforeValidator(_reject_bool)]
TaskTimeout = Annotated[int, Field(ge=10, le=1800), BeforeValidator(_reject_bool)]
CallLimit = Annotated[StrictInt | None, Field(ge=1, le=10000)]
RetentionDays = Annotated[int | None, Field(ge=1, le=3650), BeforeValidator(_reject_bool)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiSettings(_Section):
    active_provider: ProviderId = "gemini"
    fallback_provider: ProviderId | None = None
    fallback_enabled: StrictBool = False
    # Most provider calls allowed per UTC day (chat and streamed replies). Null means no limit.
    daily_call_limit: CallLimit = None
    temperature: Unit = 0.7
    max_output_tokens: OutputTokens = 2048
    system_instructions: str = Field(default="", max_length=4000)
    stream_responses: StrictBool = True
    include_memory: StrictBool = True

    @model_validator(mode="after")
    def _fallback_differs(self) -> AiSettings:
        if self.fallback_provider is not None and self.fallback_provider == self.active_provider:
            raise ValueError("fallback_provider must differ from active_provider")
        return self


class VoiceSettings(_Section):
    language: str = Field(default="en-US", min_length=2, max_length=35, pattern=LANGUAGE_PATTERN)
    voice_name: str | None = Field(default=None, max_length=100)
    speaking_rate: Rate = 1.0
    pitch: Rate = 1.0
    captions_enabled: StrictBool = True
    auto_speak: StrictBool = True
    interruptions_enabled: StrictBool = True
    push_to_talk: StrictBool = True
    continuous_listening: StrictBool = False
    wake_word_enabled: StrictBool = False
    microphone_device_id: str | None = Field(default=None, max_length=200)
    audio_output_device_id: str | None = Field(default=None, max_length=200)


class DashboardWidgets(_Section):
    status: StrictBool = True
    devices: StrictBool = True
    tasks: StrictBool = True
    activity: StrictBool = True
    schedule: StrictBool = True
    quick_actions: StrictBool = True


class AppearanceSettings(_Section):
    theme: Literal["dark", "light", "system", "high-contrast"] = "dark"
    accent_color: str = Field(default="#22d3ee", pattern=ACCENT_COLOR_PATTERN)
    orb_style: Literal["orb", "ring", "minimal"] = "orb"
    animation_intensity: Literal["off", "low", "normal", "high"] = "normal"
    reduced_motion: Literal["system", "on", "off"] = "system"
    font_size: Literal["small", "medium", "large"] = "medium"
    sidebar_hidden: list[Annotated[str, Field(pattern=NAVIGATION_ID_PATTERN)]] = Field(
        default_factory=list, max_length=50
    )
    dashboard_widgets: DashboardWidgets = Field(default_factory=DashboardWidgets)


class AutomationSettings(_Section):
    max_concurrent_tasks: ConcurrentTasks = 3
    default_task_timeout_seconds: TaskTimeout = 120
    disabled_tools: list[Annotated[str, Field(pattern=TOOL_NAME_PATTERN)]] = Field(
        default_factory=list, max_length=100
    )
    # Read-only through PATCH /settings. Changed only by the emergency stop and resume endpoints.
    emergency_stopped: StrictBool = False


class NotificationSettings(_Section):
    desktop_notifications: StrictBool = False
    on_task_completed: StrictBool = True
    on_task_failed: StrictBool = True
    on_approval_required: StrictBool = True
    on_device_disconnected: StrictBool = True
    on_integration_error: StrictBool = True
    on_schedule_event: StrictBool = True
    on_security_event: StrictBool = True
    include_task_details: StrictBool = False
    # Quiet hours stop browser pop-ups only. The inbox still lists every notification.
    # "HH:MM" in the reminder time zone. A window that starts later than it ends runs overnight.
    quiet_hours_enabled: StrictBool = False
    quiet_hours_start: str | None = Field(default=None, pattern=CLOCK_PATTERN)
    quiet_hours_end: str | None = Field(default=None, pattern=CLOCK_PATTERN)

    @model_validator(mode="after")
    def _quiet_hours_complete(self) -> NotificationSettings:
        if self.quiet_hours_enabled:
            if self.quiet_hours_start is None or self.quiet_hours_end is None:
                raise ValueError("quiet_hours_start and quiet_hours_end are required when quiet hours are on")
            if self.quiet_hours_start == self.quiet_hours_end:
                raise ValueError("quiet hours must start and end at different times")
        return self


class MemorySettings(_Section):
    enabled: StrictBool = True
    retention_days: RetentionDays = None
    auto_save_summaries: StrictBool = False


class PrivacySettings(_Section):
    store_conversations: StrictBool = True


class SettingsDoc(_Section):
    """The complete settings document. Reads always return every section and key."""

    ai: AiSettings = Field(default_factory=AiSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    # IANA time zone used for "HH:MM" reminders. Null means UTC. Set with PATCH /settings.
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("must be an IANA time zone name such as Europe/London") from None
        return value


SECTION_NAMES: tuple[str, ...] = tuple(SettingsDoc.model_fields)

__all__ = [
    "SECTION_NAMES",
    "AiSettings",
    "AppearanceSettings",
    "AutomationSettings",
    "DashboardWidgets",
    "MemorySettings",
    "NotificationSettings",
    "PrivacySettings",
    "ProviderId",
    "SettingsDoc",
    "VoiceSettings",
]
