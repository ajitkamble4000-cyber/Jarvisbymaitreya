"""Device and resource resolution for device tasks.

Device rules (docs/architecture.md, policy 2 and 3):
* An explicit ``device_id`` must belong to the user. A revoked device is ``device_revoked``.
* A device name is matched case-insensitively: an exact match wins, then a prefix, then a substring.
  One match is used. Several matches are ``needs_device_selection`` with the candidate list.
* Without a name, the default device is used. Without a default, a single paired device is used.
  Otherwise the answer is ``needs_device_selection``, or ``device_not_found`` when nothing is paired.

Resource rules: arguments name an app, folder, project, file, site or check. Each must match an entry the
device reported, by id or label (case-insensitive). A URL must equal an approved URL exactly. A check is sent
as its id only; the command line stays on the device.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.devices.protocol import DEVICE_ACTIONS
from app.models.devices import Device
from app.schemas.devices import filter_capabilities, sanitise_resources
from app.services.task_states import ProposalRefusedError
from app.tools.base import RiskLevel, ToolSpec

PERMISSION_MODES: frozenset[str] = frozenset({"ask", "allow", "deny"})


class OnlineChecker(Protocol):
    def is_online(self, device_id: uuid.UUID) -> bool: ...


@dataclass(frozen=True)
class ResolvedTarget:
    """What the agent receives on the wire (ids only), plus labels for the human title."""

    wire_args: dict[str, str]
    labels: dict[str, str]


def connection_status(device: Device, hub: OnlineChecker) -> str:
    if device.pairing_status == "revoked" or device.revoked_at is not None:
        return "revoked"
    return "online" if hub.is_online(device.id) else "offline"


async def paired_devices(db: AsyncSession, user_id: uuid.UUID) -> list[Device]:
    rows = await db.scalars(
        select(Device)
        .where(Device.user_id == user_id, Device.pairing_status == "paired", Device.revoked_at.is_(None))
        .order_by(Device.name, Device.registered_at, Device.id)
    )
    return list(rows.all())


def match_device_name(devices: Sequence[Device], text: str) -> list[Device]:
    """Exact matches win. Otherwise prefix matches, then substring matches."""
    needle = text.strip().casefold()
    exact = [device for device in devices if device.name.casefold() == needle]
    if exact:
        return exact
    prefix = [device for device in devices if device.name.casefold().startswith(needle)]
    if prefix:
        return prefix
    return [device for device in devices if needle in device.name.casefold()]


def _selection_refusal(candidates: Sequence[Device], hub: OnlineChecker) -> ProposalRefusedError:
    return ProposalRefusedError(
        "needs_device_selection",
        "More than one device matches. Choose the device to use.",
        details={
            "candidates": [
                {"id": str(device.id), "name": device.name, "connection_status": connection_status(device, hub)}
                for device in candidates
            ]
        },
    )


async def resolve_device(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    hub: OnlineChecker,
    device_id: uuid.UUID | None,
    device_text: str | None,
) -> Device:
    if device_id is not None:
        device = await db.scalar(select(Device).where(Device.id == device_id, Device.user_id == user_id))
        if device is None:
            raise ProposalRefusedError("device_not_found", "Device not found.")
        if device.pairing_status != "paired" or device.revoked_at is not None:
            raise ProposalRefusedError("device_revoked", "This device has been revoked.")
        return device

    devices = await paired_devices(db, user_id)
    if device_text:
        matches = match_device_name(devices, device_text)
        if not matches:
            raise ProposalRefusedError("device_not_found", "No paired device matches that name.")
        if len(matches) > 1:
            raise _selection_refusal(matches, hub)
        return matches[0]

    default = next((device for device in devices if device.is_default), None)
    if default is not None:
        return default
    if not devices:
        raise ProposalRefusedError("device_not_found", "No paired device is available.")
    if len(devices) > 1:
        raise _selection_refusal(devices, hub)
    return devices[0]


def ensure_capability(device: Device, spec: ToolSpec) -> None:
    capability = spec.required_capability
    if capability is None:
        return
    if capability not in filter_capabilities(device.capabilities or []):
        raise ProposalRefusedError("capability_missing", "This device does not report that capability.")


def permission_mode(device: Device, capability: str | None) -> str:
    """The stored mode for a capability. Missing or malformed entries default to ``ask``."""
    if capability is None or capability not in DEVICE_ACTIONS:
        return "ask"
    entry = (device.permissions or {}).get(capability)
    mode = entry.get("mode") if isinstance(entry, Mapping) else None
    return mode if isinstance(mode, str) and mode in PERMISSION_MODES else "ask"


def effective_mode(mode: str, risk: RiskLevel) -> str:
    """``allow`` is honoured only for LOW-risk tools. For anything else it behaves like ``ask``."""
    if mode == "allow" and risk is not RiskLevel.LOW:
        return "ask"
    return mode


def _find(entries: Sequence[Mapping[str, str]], value: str, what: str) -> Mapping[str, str]:
    needle = value.strip().casefold()
    for entry in entries:
        if needle in (entry["id"].casefold(), entry["label"].casefold()):
            return entry
    raise ProposalRefusedError(
        "resource_not_approved",
        f"That {what} is not approved on this device.",
        details={"approved": [entry["label"] for entry in entries]},
    )


def resolve_target(device: Device, spec: ToolSpec, arguments: Mapping[str, Any]) -> ResolvedTarget:
    """Map validated arguments onto approved resource ids. Raises ``resource_not_approved`` on any miss."""
    resources = sanitise_resources(device.resources)
    action = spec.device_action

    if action == "launch_app" or action == "app_availability":
        app = _find(resources["apps"], str(arguments["app"]), "app")
        return ResolvedTarget({"app_id": app["id"]}, {"app": app["label"]})

    if action == "open_folder":
        folder = _find(resources["folders"], str(arguments["folder"]), "folder")
        return ResolvedTarget({"folder_id": folder["id"]}, {"folder": folder["label"]})

    if action == "open_project":
        project = _find(resources["projects"], str(arguments["project"]), "project")
        app = _find(resources["apps"], str(arguments.get("app") or "vscode"), "app")
        return ResolvedTarget(
            {"project_id": project["id"], "app_id": app["id"]},
            {"project": project["label"], "app": app["label"]},
        )

    if action == "read_file":
        file_entry = _find(resources["files"], str(arguments["file"]), "file")
        return ResolvedTarget({"file_id": file_entry["id"]}, {"file": file_entry["label"]})

    if action == "open_url":
        urls = resources["urls"]
        site = arguments.get("site")
        url = arguments.get("url")
        if site is not None:
            entry = _find(urls, str(site), "site")
            if url is not None and url != entry.get("url"):
                raise ProposalRefusedError("resource_not_approved", "That URL does not match the approved site.")
            return ResolvedTarget({"url_id": entry["id"]}, {"site": entry["label"]})
        exact = next((entry for entry in urls if entry.get("url") == url), None)
        if exact is None:
            raise ProposalRefusedError(
                "resource_not_approved",
                "That URL is not approved on this device.",
                details={"approved": [entry["label"] for entry in urls]},
            )
        return ResolvedTarget({"url": str(url)}, {"site": exact["label"]})

    if action == "run_check":
        check = _find(resources["checks"], str(arguments["check"]), "check")
        return ResolvedTarget({"check_id": check["id"]}, {"check": check["label"]})

    if action == "system_info":
        return ResolvedTarget({}, {})

    raise ProposalRefusedError("unsupported", "This action is not supported.")


def dispatch_refusal(device: Device, spec: ToolSpec) -> tuple[str, str] | None:
    """Re-check policy at dispatch or approval time. Returns (code, message) when the task must not run."""
    if device.pairing_status != "paired" or device.revoked_at is not None:
        return "device_revoked", "The device was revoked before the task could run."
    capability = spec.required_capability
    if capability is not None and capability not in filter_capabilities(device.capabilities or []):
        return "capability_missing", "The device no longer reports that capability."
    if effective_mode(permission_mode(device, capability), spec.risk) == "deny":
        return "permission_denied", "This capability is turned off for the device."
    return None
