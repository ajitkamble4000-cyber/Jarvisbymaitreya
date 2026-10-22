"""Tool definitions shared by the AI orchestrator, the task engine and the UI.

A tool is a server-side description of an operation the assistant may propose.
The model never executes anything itself: it proposes a call with JSON arguments,
the server validates them against ``args_model``, applies the policy (device,
permission, approval) and only then dispatches an approved, typed action.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.base import ToolDefinition

ToolStatus = Literal["working", "requires_configuration", "experimental", "unsupported"]


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionTarget(StrEnum):
    SERVER = "server"  # runs inside the backend (for example listing tasks)
    DEVICE = "device"  # dispatched to a paired desktop agent


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    args_model: type[BaseModel]
    risk: RiskLevel
    target: ExecutionTarget
    category: str
    # Action name sent to the desktop agent (device tools only).
    device_action: str | None = None
    # Capability key that the device must report and the user must not have denied.
    required_capability: str | None = None
    # Whether a run needs explicit user approval by default. The device permission mode can
    # tighten this but cannot relax it for medium and higher risk tools.
    approval_required: bool = True
    status: ToolStatus = "working"
    example_commands: tuple[str, ...] = ()
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def parameters_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def declaration(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters_schema(),
        )


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        if spec.target is ExecutionTarget.DEVICE and not spec.device_action:
            raise ValueError(f"Device tool '{spec.name}' must define device_action.")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> ToolSpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(name)
        return spec

    def all(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def declarations(self, names: Iterable[str] | None = None) -> list[ToolDefinition]:
        selected = self.all() if names is None else [self._specs[n] for n in names if n in self._specs]
        return [spec.declaration() for spec in selected]
