"""Argument models, catalogue sanity and the sensitive-content rule for tool arguments."""

from __future__ import annotations

import uuid

import pytest

from app.devices.protocol import DEVICE_ACTIONS
from app.tools.args import (
    CancelTaskArgs,
    CreateReminderArgs,
    GetTaskArgs,
    LaunchAppArgs,
    ListTasksArgs,
    OpenUrlArgs,
    SaveMemoryArgs,
    ToolArgumentError,
    validate_tool_arguments,
)
from app.tools.base import ExecutionTarget
from app.tools.catalog import TOOL_SPECS
from app.tools.registry import build_tool_registry

SECRET = "hunter2-very-secret-value"


def _rejects(model, raw) -> ToolArgumentError:
    with pytest.raises(ToolArgumentError) as info:
        validate_tool_arguments(model, raw)
    return info.value


def test_extra_fields_are_rejected_and_only_the_name_is_reported() -> None:
    error = _rejects(LaunchAppArgs, {"app": "chrome", "shell": "rm -rf /"})
    assert error.code == "invalid_arguments"
    assert error.fields == ["shell"]
    assert "rm -rf" not in str(error.fields)


def test_bad_uuid_is_rejected() -> None:
    assert _rejects(GetTaskArgs, {"task_id": "not-a-uuid"}).code == "invalid_arguments"
    assert _rejects(CancelTaskArgs, {"task_id": "12345"}).code == "invalid_arguments"
    valid = uuid.uuid4()
    assert validate_tool_arguments(GetTaskArgs, {"task_id": str(valid)}).task_id == valid


def test_string_length_limits() -> None:
    assert validate_tool_arguments(LaunchAppArgs, {"app": "a" * 100}).app == "a" * 100
    assert _rejects(LaunchAppArgs, {"app": "a" * 101}).fields == ["app"]
    assert _rejects(LaunchAppArgs, {"app": "   "}).fields == ["app"]
    assert _rejects(CreateReminderArgs, {"message": "x" * 501, "when": "18:30"}).fields == ["message"]


def test_list_limit_is_bounded() -> None:
    assert validate_tool_arguments(ListTasksArgs, {}).limit == 10
    assert _rejects(ListTasksArgs, {"limit": 0}).fields == ["limit"]
    assert _rejects(ListTasksArgs, {"limit": 51}).fields == ["limit"]
    assert _rejects(ListTasksArgs, {"status": "done"}).fields == ["status"]


def test_open_url_needs_url_or_site_and_an_http_scheme() -> None:
    assert _rejects(OpenUrlArgs, {}).code == "invalid_arguments"
    assert validate_tool_arguments(OpenUrlArgs, {"site": "YouTube"}).site == "YouTube"
    assert validate_tool_arguments(OpenUrlArgs, {"url": "https://www.youtube.com"}).url == "https://www.youtube.com"
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "https://user:pw@example.com"):
        assert _rejects(OpenUrlArgs, {"url": bad}).code == "invalid_arguments", bad
    assert _rejects(OpenUrlArgs, {"url": "https://example.com/" + "a" * 2100}).fields == ["url"]


def test_resource_labels_are_not_paths() -> None:
    assert _rejects(LaunchAppArgs, {"app": "C:/Program Files/app.exe"}).fields == ["app"]
    assert _rejects(LaunchAppArgs, {"app": "..\\secret"}).fields == ["app"]


def test_reminder_time_formats() -> None:
    assert validate_tool_arguments(CreateReminderArgs, {"message": "call", "when": "18:30"}).when == "18:30"
    assert validate_tool_arguments(
        CreateReminderArgs, {"message": "call", "when": "2030-01-02T09:00:00+01:00"}
    ).when.endswith("+01:00")
    assert _rejects(CreateReminderArgs, {"message": "call", "when": "25:00"}).fields == ["when"]
    assert _rejects(CreateReminderArgs, {"message": "call", "when": "2030-01-02T09:00:00"}).fields == ["when"]


def test_save_memory_refuses_secrets_without_echoing_them() -> None:
    samples = [
        f"my password is {SECRET}",
        f"password: {SECRET}",
        f"api key AIza{SECRET}",
        f"hf_{SECRET}",
        f"sk-{SECRET}",
        f"Authorization: Bearer {SECRET}",
        "-----BEGIN PRIVATE KEY-----\nMIIE...",
        f"secret={SECRET}",
    ]
    for text in samples:
        error = _rejects(SaveMemoryArgs, {"category": "fact", "content": text})
        assert error.code == "sensitive_content", text
        assert SECRET not in str(error.fields)


def test_save_memory_accepts_ordinary_facts() -> None:
    model = validate_tool_arguments(
        SaveMemoryArgs, {"category": "preference", "content": "I prefer dark mode and short replies."}
    )
    assert model.category == "preference"
    assert _rejects(SaveMemoryArgs, {"category": "conversation_summary", "content": "x"}).fields == ["category"]


def test_non_mapping_arguments_are_invalid() -> None:
    assert _rejects(LaunchAppArgs, ["chrome"]).code == "invalid_arguments"
    assert _rejects(LaunchAppArgs, None).code == "invalid_arguments"


def test_catalogue_sanity() -> None:
    registry = build_tool_registry()
    names = [spec.name for spec in registry.all()]
    assert len(names) == len(set(names)), "duplicate tool names"
    builtin = {spec.name for spec in TOOL_SPECS}
    assert builtin <= set(names)
    # Plugin and integration tools are added at startup. Check the ones that must exist, not an exact count,
    # so that adding a tool does not break unrelated tests.
    assert {"current_time", "daily_briefing", "calendar_upcoming_events", "github_list_repositories"} <= set(names)
    for spec in registry.all():
        assert spec.description.strip(), spec.name
        assert spec.title.strip(), spec.name
        assert 1 <= len(spec.example_commands) <= 3, spec.name
        assert all(example.strip() for example in spec.example_commands), spec.name
        if spec.target is ExecutionTarget.DEVICE:
            # The wire action and its capability are the same name, which is not always the tool name
            # (run_project_check sends run_check).
            assert spec.device_action in DEVICE_ACTIONS, spec.name
            assert spec.required_capability == spec.device_action, spec.name
            if spec.name != "run_project_check":
                assert spec.device_action == spec.name, spec.name
        else:
            assert spec.approval_required is False, spec.name
            assert spec.device_action is None, spec.name


def test_registry_rejects_duplicate_and_bare_device_tools() -> None:
    registry = build_tool_registry()
    with pytest.raises(ValueError):
        registry.register(TOOL_SPECS[0])


def test_device_tool_risk_matches_the_brief() -> None:
    registry = build_tool_registry()
    expected = {
        "open_url": ("low", False),
        "launch_app": ("medium", True),
        "open_folder": ("low", False),
        "open_project": ("medium", True),
        "system_info": ("low", False),
        "read_file": ("high", True),
        "app_availability": ("low", False),
        "run_project_check": ("high", True),
    }
    for name, (risk, approval) in expected.items():
        spec = registry.require(name)
        assert spec.risk.value == risk, name
        assert spec.approval_required is approval, name
