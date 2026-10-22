"""Builds the server-side tool catalogue: built-in tools, enabled plugins and integration tools."""

from __future__ import annotations

import httpx

from app.core.config import Settings, get_settings
from app.integrations.tools import integration_tool_specs
from app.plugins.loader import load_plugin_tools
from app.tools.base import ToolRegistry
from app.tools.catalog import TOOL_SPECS


def build_tool_registry(settings: Settings | None = None, http: httpx.AsyncClient | None = None) -> ToolRegistry:
    settings = settings or get_settings()
    specs = [
        *TOOL_SPECS,
        *load_plugin_tools(settings.enabled_plugins),
        *integration_tool_specs(settings, http),
    ]
    return ToolRegistry(specs)
