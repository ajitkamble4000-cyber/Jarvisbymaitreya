"""Pure device-matching and resource-resolution rules (no database)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.device_resolver import (
    dispatch_refusal,
    effective_mode,
    match_device_name,
    permission_mode,
    resolve_target,
)
from app.services.task_states import ProposalRefusedError
from app.tools.base import RiskLevel
from app.tools.catalog import TOOL_SPECS
from app.tools.registry import build_tool_registry

RESOURCES = {
    "apps": [{"id": "chrome", "label": "Google Chrome"}, {"id": "vscode", "label": "Visual Studio Code"}],
    "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}],
    "folders": [{"id": "downloads", "label": "Downloads"}],
    "projects": [{"id": "jarvis", "label": "JARVIS project"}],
    "files": [{"id": "notes", "label": "Meeting notes"}],
}


def _device(name: str, **overrides: object) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "name": name,
        "pairing_status": "paired",
        "revoked_at": None,
        "capabilities": ["launch_app", "open_url", "open_folder", "open_project", "system_info", "read_file", "app_availability"],
        "permissions": {},
        "resources": RESOURCES,
        "is_default": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exact_name_wins_over_prefix_and_substring() -> None:
    laptop = _device("Laptop")
    laptop_work = _device("Laptop Work")
    desk = _device("Desk Laptop")
    assert match_device_name([laptop, laptop_work, desk], "laptop") == [laptop]


def test_prefix_wins_over_substring_when_no_exact_match() -> None:
    laptop_work = _device("Laptop Work")
    desk = _device("Desk Laptop")
    assert match_device_name([laptop_work, desk], "lap") == [laptop_work]


def test_several_matches_are_all_returned_for_selection() -> None:
    first = _device("Laptop Work")
    second = _device("Laptop Home")
    assert match_device_name([first, second], "Laptop") == [first, second]
    assert match_device_name([first, second], "Tablet") == []


def test_permission_defaults_to_ask_and_allow_is_limited_to_low_risk() -> None:
    device = _device("Laptop", permissions={"launch_app": {"mode": "allow"}, "open_url": {"mode": "allow"}})
    assert permission_mode(device, "launch_app") == "allow"
    assert effective_mode("allow", RiskLevel.MEDIUM) == "ask"
    assert effective_mode("allow", RiskLevel.LOW) == "allow"
    assert permission_mode(_device("Laptop"), "launch_app") == "ask"
    assert permission_mode(_device("Laptop", permissions={"launch_app": "bad"}), "launch_app") == "ask"


def test_launch_app_resolves_by_id_or_label_case_insensitively() -> None:
    spec = build_tool_registry().require("launch_app")
    device = _device("Laptop")
    assert resolve_target(device, spec, {"app": "CHROME"}).wire_args == {"app_id": "chrome"}
    target = resolve_target(device, spec, {"app": "google chrome"})
    assert target.wire_args == {"app_id": "chrome"}
    assert target.labels == {"app": "Google Chrome"}


def test_unknown_app_is_not_approved_and_lists_approved_labels() -> None:
    spec = build_tool_registry().require("launch_app")
    with pytest.raises(ProposalRefusedError) as info:
        resolve_target(_device("Laptop"), spec, {"app": "Firefox"})
    assert info.value.code == "resource_not_approved"
    assert info.value.details == {"approved": ["Google Chrome", "Visual Studio Code"]}


def test_open_url_by_site_sends_url_id_and_by_exact_url_sends_url() -> None:
    spec = build_tool_registry().require("open_url")
    device = _device("Laptop")
    assert resolve_target(device, spec, {"site": "YouTube"}).wire_args == {"url_id": "youtube"}
    assert resolve_target(device, spec, {"url": "https://www.youtube.com"}).wire_args == {"url": "https://www.youtube.com"}
    with pytest.raises(ProposalRefusedError) as info:
        resolve_target(device, spec, {"url": "https://www.youtube.com/"})
    assert info.value.code == "resource_not_approved"


def test_open_project_defaults_to_vscode_and_checks_both_resources() -> None:
    spec = build_tool_registry().require("open_project")
    device = _device("Laptop")
    target = resolve_target(device, spec, {"project": "jarvis", "app": "vscode"})
    assert target.wire_args == {"project_id": "jarvis", "app_id": "vscode"}
    with pytest.raises(ProposalRefusedError):
        resolve_target(device, spec, {"project": "other", "app": "vscode"})


def test_system_info_needs_no_resources() -> None:
    spec = build_tool_registry().require("system_info")
    assert resolve_target(_device("Laptop"), spec, {}).wire_args == {}


def test_dispatch_refusal_reports_revocation_capability_and_deny() -> None:
    spec = build_tool_registry().require("launch_app")
    assert dispatch_refusal(_device("Laptop"), spec) is None
    assert dispatch_refusal(_device("Laptop", revoked_at="x"), spec)[0] == "device_revoked"
    assert dispatch_refusal(_device("Laptop", capabilities=[]), spec)[0] == "capability_missing"
    assert dispatch_refusal(_device("Laptop", permissions={"launch_app": {"mode": "deny"}}), spec)[0] == "permission_denied"


def test_every_device_tool_resolves_with_a_full_resource_set() -> None:
    registry = build_tool_registry()
    samples = {
        "open_url": {"site": "YouTube"},
        "launch_app": {"app": "chrome"},
        "open_folder": {"folder": "downloads"},
        "open_project": {"project": "jarvis", "app": "vscode"},
        "system_info": {},
        "read_file": {"file": "notes"},
        "app_availability": {"app": "vscode"},
    }
    for spec in TOOL_SPECS:
        if spec.name in samples:
            resolve_target(_device("Laptop"), registry.require(spec.name), samples[spec.name])
