"""Approved project checks: catalogue entry, capability and permission rules, resource sanitising, argument mapping.

No database. The end-to-end policy (approval, dispatch, sweep) is in tests/integration/test_project_checks.py.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ValidationAppError
from app.devices.permissions import default_permissions, validate_permissions
from app.devices.protocol import DEVICE_ACTIONS, build_command
from app.schemas.devices import MAX_RESOURCE_ENTRIES, sanitise_resources
from app.services.device_resolver import dispatch_refusal, ensure_capability, resolve_target
from app.services.task_engine import AGENT_ERROR_CODES, AutomationPolicy, _agent_error
from app.services.task_states import ProposalRefusedError
from app.tools.args import RunCheckArgs, ToolArgumentError, validate_tool_arguments
from app.tools.base import ExecutionTarget, RiskLevel
from app.tools.registry import build_tool_registry

CHECKS = [
    {"id": "tests", "label": "JARVIS unit tests"},
    {"id": "lint", "label": "Lint"},
]


def _device(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Laptop",
        "pairing_status": "paired",
        "revoked_at": None,
        "capabilities": ["run_check", "system_info"],
        "permissions": {"run_check": {"mode": "ask"}},
        "resources": {"checks": CHECKS},
        "is_default": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_project_check_is_a_high_risk_device_tool_that_always_needs_approval() -> None:
    spec = build_tool_registry().require("run_project_check")
    assert spec.target is ExecutionTarget.DEVICE
    assert spec.device_action == "run_check"
    assert spec.required_capability == "run_check"
    assert spec.risk is RiskLevel.HIGH
    assert spec.approval_required is True
    assert spec.category == "development"
    assert spec.status == "working"
    assert "run_check" in DEVICE_ACTIONS


def test_run_project_check_schema_requires_a_bounded_check_and_nothing_else() -> None:
    schema = build_tool_registry().require("run_project_check").parameters_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"device", "check"}
    assert schema["required"] == ["check"]
    assert schema["properties"]["check"]["maxLength"] == 100
    assert schema["properties"]["check"]["minLength"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"check": ""},
        {"check": "x" * 101},
        {"check": "../etc"},
        {"check": "C:\\tools\\run"},
        {"check": "tests", "command": "rm -rf /"},
        {"check": "tests", "argv": ["cmd", "/c", "x"]},
        {},
    ],
)
def test_run_check_args_refuse_paths_commands_and_extra_keys(arguments: dict[str, Any]) -> None:
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(RunCheckArgs, arguments)


def test_run_check_args_accept_a_label_and_an_optional_device() -> None:
    model = validate_tool_arguments(RunCheckArgs, {"device": "Laptop", "check": "JARVIS unit tests"})
    assert model.check == "JARVIS unit tests"
    assert model.device == "Laptop"


def test_run_check_is_a_default_ask_capability_and_allow_is_refused() -> None:
    assert default_permissions(["run_check"]) == {"run_check": {"mode": "ask"}}
    assert validate_permissions({"run_check": {"mode": "ask"}}) == {"run_check": {"mode": "ask"}}
    assert validate_permissions({"run_check": {"mode": "deny"}}) == {"run_check": {"mode": "deny"}}
    with pytest.raises(ValidationAppError) as raised:
        validate_permissions({"run_check": {"mode": "allow"}})
    assert raised.value.code == "permission_not_allowed"


def test_run_check_missing_from_device_is_capability_missing() -> None:
    spec = build_tool_registry().require("run_project_check")
    with pytest.raises(ProposalRefusedError) as raised:
        ensure_capability(_device(capabilities=["system_info"]), spec)
    assert raised.value.code == "capability_missing"
    refusal = dispatch_refusal(_device(capabilities=["system_info"]), spec)
    assert refusal is not None and refusal[0] == "capability_missing"


def test_check_resources_keep_only_id_and_label() -> None:
    raw = {
        "checks": [
            {"id": "tests", "label": "Unit tests", "argv": ["npm", "test"], "cwd": "C:\\repo", "command": "npm"},
            {"id": "lint", "label": "Lint", "path": "/usr/bin/eslint"},
            {"id": "Bad Id", "label": "Rejected"},
            {"id": "long", "label": "x" * 121},
            {"id": "blank", "label": "   "},
        ]
    }
    out = sanitise_resources(raw)
    assert out["checks"] == [{"id": "tests", "label": "Unit tests"}, {"id": "lint", "label": "Lint"}]
    rendered = repr(out)
    assert "argv" not in rendered and "npm" not in rendered and "C:" not in rendered and "/usr" not in rendered


def test_check_resources_are_capped_at_fifty_unique_entries() -> None:
    checks = [{"id": f"check-{index}", "label": f"Check {index}"} for index in range(MAX_RESOURCE_ENTRIES + 5)]
    checks.append({"id": "check-1", "label": "Duplicate"})
    out = sanitise_resources({"checks": checks})
    assert len(out["checks"]) == MAX_RESOURCE_ENTRIES
    assert len({entry["id"] for entry in out["checks"]}) == MAX_RESOURCE_ENTRIES


def test_resolver_sends_only_the_check_id_matched_by_id_or_label() -> None:
    spec = build_tool_registry().require("run_project_check")
    device = _device()
    by_id = resolve_target(device, spec, {"check": "TESTS"})
    assert by_id.wire_args == {"check_id": "tests"}
    assert by_id.labels == {"check": "JARVIS unit tests"}
    by_label = resolve_target(device, spec, {"check": "jarvis unit tests"})
    assert by_label.wire_args == {"check_id": "tests"}


def test_unknown_check_is_resource_not_approved_and_lists_approved_labels() -> None:
    spec = build_tool_registry().require("run_project_check")
    with pytest.raises(ProposalRefusedError) as raised:
        resolve_target(_device(), spec, {"check": "rm -rf /"})
    assert raised.value.code == "resource_not_approved"
    assert raised.value.details == {"approved": ["JARVIS unit tests", "Lint"]}


def test_a_device_with_no_checks_refuses_every_check() -> None:
    spec = build_tool_registry().require("run_project_check")
    with pytest.raises(ProposalRefusedError) as raised:
        resolve_target(_device(resources={}), spec, {"check": "tests"})
    assert raised.value.code == "resource_not_approved"


def test_wire_command_for_a_check_carries_only_check_id() -> None:
    spec = build_tool_registry().require("run_project_check")
    target = resolve_target(_device(), spec, {"check": "tests"})
    command = build_command("s" * 40, action=spec.device_action, args=target.wire_args, ttl_seconds=60)
    assert command.action == "run_check"
    assert json.loads(command.args_json) == {"check_id": "tests"}


def test_automation_timeout_is_read_only_when_valid() -> None:
    def policy(value: object) -> AutomationPolicy:
        automation: dict[str, Any] = {} if value is None else {"default_task_timeout_seconds": value}
        return AutomationPolicy.from_document({"automation": automation})

    assert policy(30).default_task_timeout_seconds == 30
    assert policy(10).default_task_timeout_seconds == 10
    assert policy(1800).default_task_timeout_seconds == 1800
    assert policy(None).default_task_timeout_seconds is None
    for bad in (9, 1801, True, "120", 12.5):
        assert policy(bad).default_task_timeout_seconds is None, bad


def test_agent_error_codes_include_check_failed_and_unknown_codes_fall_back() -> None:
    assert "check_failed" in AGENT_ERROR_CODES
    assert _agent_error({"code": "check_failed", "message": "Exit 1."}) == ("check_failed", "Exit 1.")
    assert _agent_error({"code": "rm_rf", "message": "x"})[0] == "execution_failed"
