"""Loads the tool specifications of enabled plugins. Unknown or failing plugins are reported, never silently ignored.

Only the modules named in ``PLUGIN_MODULES`` are ever imported. Plugins run in-process with full privileges, so
this static allowlist is the review gate: adding a module is a code change that goes through review. See
docs/plugins.md.

Loading happens in two passes. Every enabled plugin is imported and validated first. Handlers are registered only
after all of them pass, so a bad plugin list never leaves half of its tools registered.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.core.config import ConfigurationError
from app.integrations.tools import INTEGRATION_TOOL_NAMES
from app.tools.base import ExecutionTarget, ToolSpec
from app.tools.catalog import TOOL_SPECS
from app.tools.server_handlers import ServerHandler, get_server_handler, register_server_handler

log = logging.getLogger("jarvis.plugins")

PLUGIN_MODULES: Final[dict[str, str]] = {
    "clock": "app.plugins.clock",
    "calculator": "app.plugins.calculator",
    "briefing": "app.plugins.briefing",
}

# Explore categories, in the lower-case form that tool specs use.
PLUGIN_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "productivity",
        "communication",
        "development",
        "files",
        "devices",
        "scheduling",
        "research",
        "media",
        "automation",
    }
)


@dataclass(frozen=True)
class _Plugin:
    name: str
    version: str
    specs: tuple[ToolSpec, ...]
    handlers: dict[str, ServerHandler]


def load_plugin_tools(names: Iterable[str]) -> list[ToolSpec]:
    """Validate and load the named plugins. Returns their tool specifications and registers their handlers.

    Raises ``ConfigurationError`` for an unknown name, a duplicate name, a module that does not follow the plugin
    contract, a duplicate tool name, a tool name that already exists in the built-in catalogue, or a spec and
    handler set that do not match.
    """
    requested = list(names)
    _check_names(requested)
    plugins = [_load(name) for name in requested]
    specs = _check_tool_names(plugins)
    _check_handler_names(plugins)
    for plugin in plugins:
        for tool_name, handler in plugin.handlers.items():
            _register(plugin.name, tool_name, handler)
        log.info("Loaded plugin %s version %s.", plugin.name, plugin.version)
    return specs


def _valid_names() -> str:
    return ", ".join(sorted(PLUGIN_MODULES))


def _check_names(names: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or name not in PLUGIN_MODULES:
            shown = str(name)[:64]
            raise ConfigurationError(f"Unknown plugin '{shown}'. Valid plugin names are: {_valid_names()}.")
        if name in seen:
            raise ConfigurationError(f"Plugin '{name}' is listed more than once in the enabled plugin list.")
        seen.add(name)


def _load(name: str) -> _Plugin:
    # The name has passed the allowlist check, so the import target is a fixed module path from PLUGIN_MODULES.
    try:
        module = importlib.import_module(PLUGIN_MODULES[name])
    except ImportError as exc:
        raise ConfigurationError(f"Plugin '{name}' could not be imported.") from exc

    declared = getattr(module, "PLUGIN_NAME", None)
    if declared != name:
        raise ConfigurationError(f"Plugin module for '{name}' declares PLUGIN_NAME {declared!r}.")
    version = getattr(module, "PLUGIN_VERSION", None)
    if not isinstance(version, str) or not version.strip():
        raise ConfigurationError(f"Plugin '{name}' must declare a non-empty PLUGIN_VERSION.")
    tool_specs_fn = getattr(module, "tool_specs", None)
    handlers_fn = getattr(module, "server_handlers", None)
    if not callable(tool_specs_fn) or not callable(handlers_fn):
        raise ConfigurationError(f"Plugin '{name}' must define tool_specs() and server_handlers().")

    try:
        specs = list(tool_specs_fn())
        handlers = dict(handlers_fn())
    except Exception as exc:  # a plugin failure stops startup; the cause is not echoed
        raise ConfigurationError(f"Plugin '{name}' failed to describe its tools.") from exc

    for spec in specs:
        if not isinstance(spec, ToolSpec):
            raise ConfigurationError(f"Plugin '{name}' returned an item that is not a ToolSpec.")
        if spec.target is not ExecutionTarget.SERVER:
            raise ConfigurationError(f"Plugin '{name}' tool '{spec.name}' must target the server.")
        if spec.category not in PLUGIN_CATEGORIES:
            raise ConfigurationError(
                f"Plugin '{name}' tool '{spec.name}' has category '{spec.category}'. "
                f"Valid categories are: {', '.join(sorted(PLUGIN_CATEGORIES))}."
            )

    spec_names = [spec.name for spec in specs]
    duplicates = sorted({tool for tool in spec_names if spec_names.count(tool) > 1})
    if duplicates:
        raise ConfigurationError(f"Plugin '{name}' declares tool names more than once: {', '.join(duplicates)}.")
    missing = sorted(set(spec_names) - set(handlers))
    if missing:
        raise ConfigurationError(f"Plugin '{name}' has no server handler for: {', '.join(missing)}.")
    orphans = sorted(set(handlers) - set(spec_names))
    if orphans:
        raise ConfigurationError(f"Plugin '{name}' has a server handler without a tool spec: {', '.join(orphans)}.")
    for tool_name, handler in handlers.items():
        if not callable(handler):
            raise ConfigurationError(f"Plugin '{name}' handler for '{tool_name}' is not callable.")

    return _Plugin(name=name, version=version, specs=tuple(specs), handlers=handlers)


def _check_tool_names(plugins: list[_Plugin]) -> list[ToolSpec]:
    # Integration tools are registered after plugins, so their names are reserved here too.
    reserved = {spec.name for spec in TOOL_SPECS} | INTEGRATION_TOOL_NAMES
    owner: dict[str, str] = {}
    specs: list[ToolSpec] = []
    for plugin in plugins:
        for spec in plugin.specs:
            if spec.name in reserved:
                raise ConfigurationError(f"Plugin '{plugin.name}' tool '{spec.name}' clashes with a built-in tool.")
            if spec.name in owner:
                raise ConfigurationError(
                    f"Tool '{spec.name}' is provided by both '{owner[spec.name]}' and '{plugin.name}'."
                )
            owner[spec.name] = plugin.name
            specs.append(spec)
    return specs


def _check_handler_names(plugins: list[_Plugin]) -> None:
    """Refuse before any handler is registered, so a failed load leaves the process-wide handler map unchanged."""
    for plugin in plugins:
        for tool_name, handler in plugin.handlers.items():
            existing = get_server_handler(tool_name)
            if existing is not None and existing is not handler:
                raise ConfigurationError(
                    f"Plugin '{plugin.name}' cannot register a handler for '{tool_name}': the name is already taken."
                )


def _register(plugin_name: str, tool_name: str, handler: ServerHandler) -> None:
    try:
        register_server_handler(tool_name, handler)
    except ValueError as exc:
        raise ConfigurationError(
            f"Plugin '{plugin_name}' cannot register a handler for '{tool_name}': the name is already taken."
        ) from exc
