"""Pydantic argument models for every tool.

Rules shared by all models:
* ``extra="forbid"``: unknown keys are rejected.
* Resource arguments are labels or ids, never paths: slashes, backslashes and control characters are refused.
* URLs must be http or https, carry no credentials and stay within 2048 characters.
* Error reports carry field names only. Values are never echoed back to the model or the client.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.memory_service import SensitiveContentError, find_sensitive_reason

_CONTROL_OR_PATH = re.compile(r"[\x00-\x1f\x7f/\\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HH_MM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

TaskStatusText = Literal["queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]
MemoryToolCategory = Literal["fact", "preference", "project", "task_context"]


def _clean_label(value: str) -> str:
    """Approved labels and ids: no blanks, no control characters, no path separators."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    if _CONTROL_OR_PATH.search(cleaned):
        raise ValueError("must be an approved label or id, not a path")
    return cleaned


def _clean_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    if _CONTROL.search(cleaned):
        raise ValueError("must not contain control characters")
    return cleaned


def _http_url(value: str) -> str:
    if len(value) > 2048 or any(ch.isspace() for ch in value):
        raise ValueError("must be an http or https address without spaces")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http or https address")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not contain credentials")
    return value


def _no_secret(value: str) -> str:
    if find_sensitive_reason(value) is not None:
        raise SensitiveContentError("content looks like a secret")
    return value


def _when(value: str) -> str:
    """Accept ``HH:MM`` or an ISO 8601 datetime that carries a UTC offset."""
    if _HH_MM.match(value):
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("must be HH:MM or an ISO 8601 datetime with an offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("must include a UTC offset")
    return value


Label = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_clean_label)]
DeviceText = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_clean_text)]
UrlText = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(_http_url)]
SiteText = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_clean_label)]
Message = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_clean_text)]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceToolArgs(ToolArgs):
    """Device tools accept an optional device name. The engine resolves it to a device."""

    device: DeviceText | None = None


class OpenUrlArgs(DeviceToolArgs):
    url: UrlText | None = None
    site: SiteText | None = None

    @model_validator(mode="after")
    def _need_url_or_site(self) -> OpenUrlArgs:
        if self.url is None and self.site is None:
            raise ValueError("either url or site is required")
        return self


class LaunchAppArgs(DeviceToolArgs):
    app: Label


class OpenFolderArgs(DeviceToolArgs):
    folder: Label


class OpenProjectArgs(DeviceToolArgs):
    project: Label
    app: Label = "vscode"


class SystemInfoArgs(DeviceToolArgs):
    pass


class ReadFileArgs(DeviceToolArgs):
    file: Label


class AppAvailabilityArgs(DeviceToolArgs):
    app: Label


class RunCheckArgs(DeviceToolArgs):
    """Name of one approved project check. The command itself lives only in the desktop agent's config."""

    check: Label


class ListDevicesArgs(ToolArgs):
    pass


class ListTasksArgs(ToolArgs):
    status: TaskStatusText | None = None
    limit: int = Field(default=10, ge=1, le=50)


class GetTaskArgs(ToolArgs):
    task_id: uuid.UUID


class CancelTaskArgs(ToolArgs):
    task_id: uuid.UUID


class SaveMemoryArgs(ToolArgs):
    category: MemoryToolCategory
    content: Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_no_secret)]


class CreateReminderArgs(ToolArgs):
    message: Message
    when: Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_when)]


class SendReminderArgs(ToolArgs):
    message: Message


class ToolArgumentError(Exception):
    """Arguments failed validation. ``code`` is ``invalid_arguments`` or ``sensitive_content``."""

    def __init__(self, code: str, fields: list[str]) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields


def validate_tool_arguments(model: type[BaseModel], raw: Any) -> BaseModel:
    """Validate model-supplied arguments. Raises ``ToolArgumentError`` without echoing any value."""
    if not isinstance(raw, Mapping):
        raise ToolArgumentError("invalid_arguments", [])
    try:
        return model.model_validate(dict(raw))
    except ValidationError as exc:
        fields: list[str] = []
        for error in exc.errors(include_url=False, include_input=False):
            context = error.get("ctx") or {}
            if isinstance(context.get("error"), SensitiveContentError):
                raise ToolArgumentError("sensitive_content", []) from None
            loc = error.get("loc") or ()
            if loc:
                fields.append(str(loc[0]))
        raise ToolArgumentError("invalid_arguments", sorted(set(fields))) from None
