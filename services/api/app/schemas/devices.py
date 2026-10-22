"""Request and response models for devices, pairing and the agent handshake (docs/api.md "Devices").

Also holds the sanitisers for agent-reported capabilities and resources. Agent input is untrusted:
unknown capabilities are dropped, and resources keep only ids, labels and (for URLs) an http(s) address.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.devices.protocol import DEVICE_ACTIONS
from app.models.devices import Device

RESOURCE_KINDS: tuple[str, ...] = ("apps", "urls", "folders", "projects", "files", "checks")
MAX_RESOURCE_ENTRIES = 50
MAX_RESOURCE_LABEL = 120
MAX_RESOURCE_URL = 2048
_RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DEVICE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")
WEBSOCKET_PATH = "/api/v1/agent/ws"
PROTOCOL_VERSION = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- sanitisers ---------------------------------------------------------------------

def filter_capabilities(values: Iterable[str]) -> list[str]:
    """Keep only capability names the backend knows how to dispatch, preserving order and dropping duplicates."""
    seen: list[str] = []
    for value in values:
        if isinstance(value, str) and value in DEVICE_ACTIONS and value not in seen:
            seen.append(value)
    return seen


def _is_http_url(value: str) -> bool:
    if len(value) > MAX_RESOURCE_URL:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _clean_entry(kind: str, entry: Any) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    entry_id = entry.get("id")
    label = entry.get("label")
    if not isinstance(entry_id, str) or not _RESOURCE_ID_RE.match(entry_id):
        return None
    if not isinstance(label, str):
        return None
    label = label.strip()
    if not label or len(label) > MAX_RESOURCE_LABEL or _CONTROL_CHARS.search(label):
        return None
    cleaned: dict[str, str] = {"id": entry_id, "label": label}
    if kind == "urls":
        url = entry.get("url")
        if not isinstance(url, str) or not _is_http_url(url):
            return None
        cleaned["url"] = url
    return cleaned


def sanitise_resources(raw: Any) -> dict[str, list[dict[str, str]]]:
    """Return exactly the six resource kinds, each a list of valid entries (at most 50, unique ids).

    Unknown keys, malformed entries, bad ids, labels over 120 characters and non-http(s) URLs are dropped.
    Local paths and command lines never survive this step: only ``id``, ``label`` and ``url`` (URLs only) are
    copied, so a ``checks`` entry keeps just ``id`` and ``label``.
    """
    result: dict[str, list[dict[str, str]]] = {kind: [] for kind in RESOURCE_KINDS}
    if not isinstance(raw, dict):
        return result
    for kind in RESOURCE_KINDS:
        values = raw.get(kind)
        if not isinstance(values, list):
            continue
        seen_ids: set[str] = set()
        for entry in values:
            cleaned = _clean_entry(kind, entry)
            if cleaned is None or cleaned["id"] in seen_ids:
                continue
            seen_ids.add(cleaned["id"])
            result[kind].append(cleaned)
            if len(result[kind]) >= MAX_RESOURCE_ENTRIES:
                break
    return result


def _clean_name(value: str, *, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} must not be empty.")
    if len(cleaned) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    if _CONTROL_CHARS.search(cleaned):
        raise ValueError(f"{field} must not contain control characters.")
    return cleaned


# ---- pairing ------------------------------------------------------------------------

class PairingCodeOut(BaseModel):
    code: str
    expires_at: datetime


class PairRequest(BaseModel):
    """Body of ``POST /agent/pair``. Agent-facing, so unknown fields are ignored rather than rejected."""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=32)
    device_name: str = Field(min_length=1, max_length=120)
    device_type: str = Field(default="laptop", max_length=30)
    os: str = Field(default="windows", max_length=30)
    os_version: str | None = Field(default=None, max_length=120)
    agent_version: str | None = Field(default=None, max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    resources: dict[str, Any] = Field(default_factory=dict)

    @field_validator("device_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _clean_name(value, field="device_name", max_length=120)

    @field_validator("device_type")
    @classmethod
    def _check_device_type(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _DEVICE_TYPE_RE.match(cleaned):
            raise ValueError("device_type must be a short lower-case identifier.")
        return cleaned


class PairResponse(BaseModel):
    device_id: uuid.UUID
    device_secret: str
    websocket_path: str = WEBSOCKET_PATH
    heartbeat_interval_seconds: int
    protocol_version: int = PROTOCOL_VERSION


# ---- device management --------------------------------------------------------------

class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    device_type: str
    os: str
    os_version: str | None
    agent_version: str | None
    pairing_status: str
    connection_status: str
    is_default: bool
    capabilities: list[str]
    permissions: dict[str, dict[str, str]]
    resources: dict[str, list[dict[str, str]]]
    registered_at: datetime
    paired_at: datetime | None
    last_seen_at: datetime | None
    last_connected_at: datetime | None
    revoked_at: datetime | None


class DeviceListOut(BaseModel):
    items: list[DeviceOut]


class DeviceUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    device_type: str | None = Field(default=None, max_length=30)
    is_default: bool | None = None
    permissions: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value, field="name", max_length=120)

    @field_validator("device_type")
    @classmethod
    def _check_device_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not _DEVICE_TYPE_RE.match(cleaned):
            raise ValueError("device_type must be a short lower-case identifier.")
        return cleaned


class RevokeRequest(_Strict):
    reason: str | None = Field(default=None, max_length=200)


class PingOut(BaseModel):
    online: bool
    latency_ms: int | None
    message: str


def device_out(device: Device, connection_status: str) -> DeviceOut:
    """Map a Device row to the public shape. Credentials (hash, ciphertext) are never copied."""
    return DeviceOut(
        id=device.id,
        name=device.name,
        device_type=device.device_type,
        os=device.os,
        os_version=device.os_version,
        agent_version=device.agent_version,
        pairing_status=device.pairing_status,
        connection_status=connection_status,
        is_default=device.is_default,
        capabilities=filter_capabilities(device.capabilities or []),
        permissions=dict(device.permissions or {}),
        resources=sanitise_resources(device.resources),
        registered_at=device.registered_at,
        paired_at=device.paired_at,
        last_seen_at=device.last_seen_at,
        last_connected_at=device.last_connected_at,
        revoked_at=device.revoked_at,
    )
