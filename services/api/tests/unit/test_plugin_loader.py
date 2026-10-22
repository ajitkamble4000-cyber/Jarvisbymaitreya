"""Plugin loader: the static allowlist, every validation rule, and the clock arguments.

Fake plugin modules are injected through ``sys.modules`` and the handler registry is replaced for each test, so
nothing here leaks into other tests. No database is needed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import ConfigurationError
from app.plugins import clock, loader
from app.plugins.clock import CurrentTimeArgs
from app.tools import server_handlers
from app.tools.args import ToolArgs
from app.tools.base import ExecutionTarget, RiskLevel, ToolSpec
from app.tools.server_handlers import get_server_handler


class _NoArgs(ToolArgs):
    pass


async def _noop(_db: Any, _user_id: Any, _args: Any) -> dict:
    return {"summary": "ok"}


async def _other(_db: Any, _user_id: Any, _args: Any) -> dict:
    return {"summary": "other"}


def _spec(name: str, *, target: ExecutionTarget = ExecutionTarget.SERVER, category: str = "productivity") -> ToolSpec:
    return ToolSpec(
        name=name,
        title=name,
        description=f"Test tool {name}.",
        args_model=_NoArgs,
        risk=RiskLevel.LOW,
        target=target,
        category=category,
        approval_required=False,
    )


def _fake_module(
    *,
    plugin_name: str,
    specs: list[ToolSpec] | None = None,
    handlers: dict[str, Callable[..., Any]] | None = None,
    version: str = "0.1.0",
) -> ModuleType:
    module = ModuleType(f"fake_plugin_{plugin_name}")
    module.PLUGIN_NAME = plugin_name  # type: ignore[attr-defined]
    module.PLUGIN_VERSION = version  # type: ignore[attr-defined]
    module.tool_specs = lambda: list(specs or [])  # type: ignore[attr-defined]
    module.server_handlers = lambda: dict(handlers or {})  # type: ignore[attr-defined]
    return module


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    registry: dict = {}
    monkeypatch.setattr(server_handlers, "_HANDLERS", registry)
    yield registry


def _install(monkeypatch: pytest.MonkeyPatch, module: ModuleType, allow_name: str) -> None:
    """Make ``allow_name`` point at ``module`` in the allowlist, the way the real entries do."""
    module_path = f"fake_plugin_module_{allow_name}"
    monkeypatch.setitem(sys.modules, module_path, module)
    monkeypatch.setitem(loader.PLUGIN_MODULES, allow_name, module_path)


# -- allowlist ---------------------------------------------------------------------------------


def test_allowlist_contains_the_shipped_plugins() -> None:
    assert loader.PLUGIN_MODULES == {
        "clock": "app.plugins.clock",
        "calculator": "app.plugins.calculator",
        "briefing": "app.plugins.briefing",
    }


def test_no_plugins_enabled_loads_nothing() -> None:
    assert loader.load_plugin_tools([]) == []
    assert get_server_handler("current_time") is None
    assert get_server_handler("calculate") is None


def test_unknown_name_fails_with_the_valid_names(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []
    real_import = loader.importlib.import_module

    def spy(name: str, *args: Any, **kwargs: Any) -> ModuleType:
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(loader.importlib, "import_module", spy)
    with pytest.raises(ConfigurationError) as raised:
        loader.load_plugin_tools(["clock", "os"])
    message = str(raised.value)
    assert "os" in message
    assert "calculator" in message and "clock" in message
    assert imported == [], "nothing may be imported when a name is unknown"


@pytest.mark.parametrize("name", ["", "../clock", "app.plugins.clock", "Clock", "clock "])
def test_names_outside_the_allowlist_are_refused(name: str) -> None:
    with pytest.raises(ConfigurationError):
        loader.load_plugin_tools([name])


def test_a_name_listed_twice_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="more than once"):
        loader.load_plugin_tools(["clock", "clock"])


# -- shipped plugins ---------------------------------------------------------------------------


def test_shipped_plugins_register_their_tools_and_handlers() -> None:
    specs = loader.load_plugin_tools(["clock", "calculator"])
    assert [spec.name for spec in specs] == ["current_time", "calculate"]
    assert get_server_handler("current_time") is not None
    assert get_server_handler("calculate") is not None


def test_loading_the_same_plugin_twice_is_harmless() -> None:
    first = loader.load_plugin_tools(["clock"])
    second = loader.load_plugin_tools(["clock"])
    assert [spec.name for spec in first] == [spec.name for spec in second] == ["current_time"]


def test_shipped_specs_follow_the_plugin_contract() -> None:
    for spec in loader.load_plugin_tools(["clock", "calculator"]):
        assert spec.target is ExecutionTarget.SERVER
        assert spec.risk is RiskLevel.LOW
        assert spec.approval_required is False
        assert spec.category in loader.PLUGIN_CATEGORIES


# -- contract checks on fake plugins -----------------------------------------------------------


def test_duplicate_tool_name_across_plugins_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_module(plugin_name="dup_a", specs=[_spec("shared")], handlers={"shared": _noop}), "dup_a")
    _install(monkeypatch, _fake_module(plugin_name="dup_b", specs=[_spec("shared")], handlers={"shared": _other}), "dup_b")
    with pytest.raises(ConfigurationError, match="both 'dup_a' and 'dup_b'"):
        loader.load_plugin_tools(["dup_a", "dup_b"])


def test_tool_name_that_clashes_with_the_catalogue_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _fake_module(plugin_name="clash", specs=[_spec("list_tasks")], handlers={"list_tasks": _noop})
    _install(monkeypatch, module, "clash")
    with pytest.raises(ConfigurationError, match="clashes with a built-in tool"):
        loader.load_plugin_tools(["clash"])


def test_spec_without_a_handler_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_module(plugin_name="nohandler", specs=[_spec("lonely")], handlers={}), "nohandler")
    with pytest.raises(ConfigurationError, match="no server handler for: lonely"):
        loader.load_plugin_tools(["nohandler"])


def test_handler_without_a_spec_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_module(plugin_name="orphan", specs=[], handlers={"ghost": _noop}), "orphan")
    with pytest.raises(ConfigurationError, match="without a tool spec: ghost"):
        loader.load_plugin_tools(["orphan"])


def test_plugin_name_must_match_its_allowlist_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_module(plugin_name="someone_else"), "mismatch")
    with pytest.raises(ConfigurationError, match="PLUGIN_NAME"):
        loader.load_plugin_tools(["mismatch"])


def test_plugin_must_declare_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_module(plugin_name="noversion", version="  "), "noversion")
    with pytest.raises(ConfigurationError, match="PLUGIN_VERSION"):
        loader.load_plugin_tools(["noversion"])


def test_device_target_is_refused_for_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec("device_like", target=ExecutionTarget.DEVICE)
    _install(monkeypatch, _fake_module(plugin_name="devicey", specs=[spec], handlers={"device_like": _noop}), "devicey")
    with pytest.raises(ConfigurationError, match="must target the server"):
        loader.load_plugin_tools(["devicey"])


def test_category_must_be_an_explore_category(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec("odd", category="Productivity")  # the explore label is title case; tool specs use lower case
    _install(monkeypatch, _fake_module(plugin_name="oddcat", specs=[spec], handlers={"odd": _noop}), "oddcat")
    with pytest.raises(ConfigurationError, match="has category 'Productivity'"):
        loader.load_plugin_tools(["oddcat"])


def test_a_failing_plugin_stops_startup_and_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _fake_module(plugin_name="good", specs=[_spec("good_tool")], handlers={"good_tool": _noop})
    _install(monkeypatch, good, "good")
    bad = _fake_module(plugin_name="bad", specs=[_spec("bad_tool")], handlers={})
    _install(monkeypatch, bad, "bad")
    with pytest.raises(ConfigurationError):
        loader.load_plugin_tools(["good", "bad"])
    assert get_server_handler("good_tool") is None, "validation must finish before any handler is registered"


def test_a_plugin_that_raises_while_describing_itself_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _fake_module(plugin_name="raises")

    def explode() -> list[ToolSpec]:
        raise RuntimeError("internal detail that must not be echoed")

    module.tool_specs = explode  # type: ignore[attr-defined]
    _install(monkeypatch, module, "raises")
    with pytest.raises(ConfigurationError) as raised:
        loader.load_plugin_tools(["raises"])
    assert "internal detail" not in str(raised.value)


def test_handler_name_taken_by_another_function_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    server_handlers.register_server_handler("taken_tool", _other)
    _install(monkeypatch, _fake_module(plugin_name="taken", specs=[_spec("taken_tool")], handlers={"taken_tool": _noop}), "taken")
    with pytest.raises(ConfigurationError, match="already taken"):
        loader.load_plugin_tools(["taken"])


def test_missing_module_is_reported_as_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(loader.PLUGIN_MODULES, "absent", "app.plugins.does_not_exist")
    with pytest.raises(ConfigurationError, match="could not be imported"):
        loader.load_plugin_tools(["absent"])


# -- clock arguments ---------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["Europe/London", "UTC", "Asia/Tokyo", "America/New_York"])
def test_clock_accepts_iana_zones(zone: str) -> None:
    assert CurrentTimeArgs(timezone=zone).timezone == zone


def test_clock_timezone_defaults_to_none() -> None:
    assert CurrentTimeArgs().timezone is None


@pytest.mark.parametrize(
    "zone",
    ["Mars/Olympus", "Europe", "../etc/passwd", "/etc/localtime", "", "   ", "Europe/London\x00", "x" * 65],
)
def test_clock_rejects_unknown_or_unsafe_zones(zone: str) -> None:
    with pytest.raises(ValidationError):
        CurrentTimeArgs(timezone=zone)


def test_clock_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        CurrentTimeArgs.model_validate({"timezone": "UTC", "format": "x"})


def test_clock_module_exposes_the_contract_names() -> None:
    assert clock.PLUGIN_NAME == "clock"
    assert clock.PLUGIN_VERSION
    assert set(clock.server_handlers()) == {"current_time"}
    assert [spec.name for spec in clock.tool_specs()] == ["current_time"]
