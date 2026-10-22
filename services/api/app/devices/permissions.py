"""Per-capability permission modes for paired devices.

Modes: ``ask`` (approval per run, the default), ``allow`` (no approval, LOW-risk tools only)
and ``deny``. ``LOW_RISK_CAPABILITIES`` mirrors the tools whose ``ToolSpec.risk`` is LOW; if a
LOW-risk tool is added or re-rated, update this set together with the tool catalogue.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.errors import ValidationAppError
from app.devices.protocol import DEVICE_ACTIONS

PERMISSION_MODES: tuple[str, ...] = ("ask", "allow", "deny")
DEFAULT_PERMISSION_MODE = "ask"
LOW_RISK_CAPABILITIES: frozenset[str] = frozenset({"open_url", "open_folder", "system_info", "app_availability"})

PermissionMap = dict[str, dict[str, str]]


def default_permissions(capabilities: Iterable[str]) -> PermissionMap:
    """``{cap: {"mode": "ask"}}`` for every capability the agent reports."""
    return {cap: {"mode": DEFAULT_PERMISSION_MODE} for cap in capabilities if cap in DEVICE_ACTIONS}


def merge_default_permissions(current: Mapping[str, Any], capabilities: Iterable[str]) -> PermissionMap:
    """Keep existing entries and add ``ask`` for any reported capability that has none yet."""
    merged: PermissionMap = {key: dict(value) for key, value in current.items() if isinstance(value, Mapping)}
    for cap in capabilities:
        if cap in DEVICE_ACTIONS and cap not in merged:
            merged[cap] = {"mode": DEFAULT_PERMISSION_MODE}
    return merged


def _field_error(field: str, message: str, code: str | None = None) -> ValidationAppError:
    return ValidationAppError(
        message,
        code=code,
        details={"fields": [{"field": field, "message": message}]},
    )


def validate_permissions(raw: Mapping[str, Any]) -> PermissionMap:
    """Validate a permissions patch and return the cleaned ``{cap: {"mode": mode}}`` mapping.

    Raises ``ValidationAppError`` (422). Unknown capabilities, malformed entries and unknown modes
    use ``validation_error``. ``allow`` on a capability that is not LOW risk uses ``permission_not_allowed``.
    """
    cleaned: PermissionMap = {}
    for cap, policy in raw.items():
        field = f"permissions.{cap}"
        if cap not in DEVICE_ACTIONS:
            raise _field_error(field, "Unknown capability.")
        if not isinstance(policy, Mapping) or set(policy.keys()) != {"mode"}:
            raise _field_error(field, 'Permission must be an object with a single "mode" field.')
        mode = policy["mode"]
        if not isinstance(mode, str) or mode not in PERMISSION_MODES:
            raise _field_error(field, "Mode must be one of ask, allow or deny.")
        if mode == "allow" and cap not in LOW_RISK_CAPABILITIES:
            raise _field_error(
                field,
                "Allow is only available for low-risk capabilities.",
                code="permission_not_allowed",
            )
        cleaned[cap] = {"mode": mode}
    return cleaned
