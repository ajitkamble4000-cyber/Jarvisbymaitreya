"""The voice-automation tools from the Tasura brief: compose_email, web_search, launch_installed_app, create_files.

Pure rules only (no database): argument validation, catalogue policy and resolution onto the device's resources.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.devices.permissions import LOW_RISK_CAPABILITIES
from app.devices.protocol import DEVICE_ACTIONS
from app.services.device_resolver import resolve_target
from app.services.task_states import ProposalRefusedError
from app.tools.args import (
    ComposeEmailArgs,
    CreateFilesArgs,
    LaunchInstalledAppArgs,
    ToolArgumentError,
    WebSearchArgs,
    validate_tool_arguments,
)
from app.tools.base import ExecutionTarget, RiskLevel
from app.tools.registry import build_tool_registry

RESOURCES = {
    "apps": [{"id": "vscode", "label": "Visual Studio Code"}],
    "workspaces": [{"id": "projects", "label": "Jarvis projects"}],
}
NEW_TOOLS = ("compose_email", "web_search", "launch_installed_app", "create_files")


def _device(**overrides: object) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "name": "Laptop",
        "pairing_status": "paired",
        "revoked_at": None,
        "capabilities": list(NEW_TOOLS),
        "permissions": {},
        "resources": RESOURCES,
        "is_default": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _stored(model: type, raw: dict) -> dict:
    return validate_tool_arguments(model, raw).model_dump(mode="json", exclude_none=True)


# ---- catalogue policy -----------------------------------------------------------------------

def test_new_tools_are_device_tools_the_backend_can_dispatch() -> None:
    registry = build_tool_registry()
    for name in NEW_TOOLS:
        spec = registry.require(name)
        assert spec.target is ExecutionTarget.DEVICE
        assert spec.device_action == name
        assert name in DEVICE_ACTIONS


def test_risk_and_approval_match_what_each_tool_can_do() -> None:
    registry = build_tool_registry()
    assert registry.require("web_search").risk is RiskLevel.LOW
    assert "web_search" in LOW_RISK_CAPABILITIES
    assert registry.require("compose_email").approval_required is True
    assert registry.require("launch_installed_app").approval_required is True
    create = registry.require("create_files")
    assert create.risk is RiskLevel.HIGH and create.approval_required is True


# ---- argument validation --------------------------------------------------------------------

def test_compose_email_accepts_a_polished_email_with_line_breaks() -> None:
    args = _stored(
        ComposeEmailArgs,
        {"to": ["rahul.sharma@gmail.com"], "subject": "Project meeting moved to 4 PM", "body": "Dear Rahul,\n\nThe meeting is at 4 PM.\n"},
    )
    assert args["to"] == ["rahul.sharma@gmail.com"]
    assert args["cc"] == []


@pytest.mark.parametrize(
    "raw",
    [
        {"to": [], "subject": "x", "body": ""},
        {"to": ["not-an-email"], "subject": "x", "body": ""},
        {"to": ["a@b.co"], "subject": "two\nlines", "body": ""},
        {"to": ["a@b.co"], "subject": "x", "body": "bell\x07"},
        {"to": ["a@b.co"], "subject": "x", "body": "", "bcc": ["c@d.co"]},
        {"to": ["a@b.co"] * 11, "subject": "x", "body": ""},
    ],
)
def test_compose_email_refuses_bad_input(raw: dict) -> None:
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(ComposeEmailArgs, raw)


def test_web_search_allows_only_known_sites() -> None:
    assert _stored(WebSearchArgs, {"site": "youtube", "query": "SVIS Annual Day"})["site"] == "youtube"
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(WebSearchArgs, {"site": "example.com", "query": "x"})


def test_launch_installed_app_takes_a_name_never_a_path() -> None:
    assert _stored(LaunchInstalledAppArgs, {"name": "Whisper Flow"})["name"] == "Whisper Flow"
    for bad in ("C:\\Windows\\System32\\cmd.exe", "../../evil", ""):
        with pytest.raises(ToolArgumentError):
            validate_tool_arguments(LaunchInstalledAppArgs, {"name": bad})


def test_create_files_accepts_a_small_project() -> None:
    args = _stored(
        CreateFilesArgs,
        {
            "folder": "portfolio",
            "files": [{"path": "index.html", "content": "<h1>Hi</h1>\n"}, {"path": "css/style.css", "content": "h1{}"}],
            "open_in": "vscode",
        },
    )
    assert args["workspace"] == "projects"
    assert [item["path"] for item in args["files"]] == ["index.html", "css/style.css"]


@pytest.mark.parametrize(
    "path",
    ["../escape.txt", "a/../b.txt", "/etc/passwd", "C:/Windows/x", "a\\b.txt", ".git/hooks/pre-commit", "con.txt", "a/", "x."],
)
def test_create_files_refuses_paths_that_could_escape(path: str) -> None:
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(CreateFilesArgs, {"folder": "p", "files": [{"path": path, "content": "x"}]})


def test_create_files_refuses_duplicates_and_oversized_projects() -> None:
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(
            CreateFilesArgs, {"folder": "p", "files": [{"path": "A.txt", "content": ""}, {"path": "a.txt", "content": ""}]}
        )
    big = "x" * 200_000
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(CreateFilesArgs, {"folder": "p", "files": [{"path": f"f{i}.txt", "content": big} for i in range(3)]})


def test_file_contents_may_contain_code_that_mentions_passwords() -> None:
    code = 'password = input("Password: ")\nheaders = {"Authorization": "Bearer " + token}\n'
    assert _stored(CreateFilesArgs, {"folder": "login", "files": [{"path": "app.py", "content": code}]})


# ---- resolution onto the device -------------------------------------------------------------

def test_compose_email_sends_the_draft_fields_to_the_device() -> None:
    spec = build_tool_registry().require("compose_email")
    args = _stored(ComposeEmailArgs, {"to": ["a@b.co"], "cc": ["c@d.co"], "subject": "Hi", "body": "Hello"})
    target = resolve_target(_device(), spec, args)
    assert target.wire_args == {"to": ["a@b.co"], "cc": ["c@d.co"], "subject": "Hi", "body": "Hello"}
    assert target.labels == {"to": "a@b.co", "subject": "Hi"}


def test_create_files_resolves_the_workspace_and_editor_by_label() -> None:
    spec = build_tool_registry().require("create_files")
    args = _stored(
        CreateFilesArgs,
        {"workspace": "jarvis projects", "folder": "site", "files": [{"path": "index.html", "content": "x"}], "open_in": "Visual Studio Code"},
    )
    target = resolve_target(_device(), spec, args)
    assert target.wire_args == {
        "workspace_id": "projects",
        "folder": "site",
        "files": [{"path": "index.html", "content": "x"}],
        "open_in_app_id": "vscode",
    }
    assert target.labels["files"] == "1"


def test_create_files_refuses_a_workspace_the_device_did_not_approve() -> None:
    spec = build_tool_registry().require("create_files")
    args = _stored(CreateFilesArgs, {"workspace": "Desktop", "folder": "x", "files": [{"path": "a.txt", "content": ""}]})
    with pytest.raises(ProposalRefusedError) as raised:
        resolve_target(_device(), spec, args)
    assert raised.value.code == "resource_not_approved"


def test_search_and_launch_pass_the_words_through() -> None:
    registry = build_tool_registry()
    search = resolve_target(_device(), registry.require("web_search"), {"site": "spotify", "query": "lo-fi"})
    assert search.wire_args == {"site": "spotify", "query": "lo-fi"}
    assert search.labels["site"] == "Spotify"
    launch = resolve_target(_device(), registry.require("launch_installed_app"), {"name": "Wispr Flow"})
    assert launch.wire_args == {"name": "Wispr Flow"}
