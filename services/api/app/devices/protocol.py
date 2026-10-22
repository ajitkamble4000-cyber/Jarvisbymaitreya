"""Wire protocol between the backend and paired desktop agents (WebSocket, JSON text frames).

The authoritative description is docs/desktop-protocol.md. This module defines the
message models and the command signing helpers used by the backend. The desktop agent
implements the mirror image in TypeScript and must stay in sync.

Security properties:
* The agent authenticates with the device secret as a bearer token during the WebSocket
  handshake. Only the SHA-256 of the secret is stored server-side for lookup.
* Every server->agent command is HMAC-SHA256 signed with the device secret over the exact
  wire strings. The agent rejects bad signatures, expired commands and repeated command ids.
* Commands carry ``issued_at``/``expires_at`` timestamps and a random nonce.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.time import to_iso
from app.security.signing import serialize_args, sign_fields
from app.security.tokens import new_token

PROTOCOL_VERSION = 1

# Actions the backend may send to an agent. The agent must also refuse anything it does not know.
DEVICE_ACTIONS: tuple[str, ...] = (
    "open_url",
    "launch_app",
    "open_folder",
    "open_project",
    "system_info",
    "read_file",
    "app_availability",
    "run_check",
)
CANCEL_ACTION = "__cancel__"


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# ---- server -> agent ----------------------------------------------------------------

class HelloAck(_WireModel):
    type: Literal["hello_ack"] = "hello_ack"
    device_id: str
    server_time: str
    heartbeat_interval_seconds: int
    protocol_version: int = PROTOCOL_VERSION


class CommandMessage(_WireModel):
    type: Literal["command"] = "command"
    id: str
    action: str
    args_json: str
    issued_at: str
    expires_at: str
    nonce: str
    signature: str
    protocol_version: int = PROTOCOL_VERSION


class CancelMessage(_WireModel):
    type: Literal["cancel"] = "cancel"
    command_id: str
    issued_at: str
    expires_at: str
    nonce: str
    signature: str


class PingMessage(_WireModel):
    type: Literal["ping"] = "ping"
    nonce: str
    sent_at: str


class ServerMessageError(_WireModel):
    type: Literal["error"] = "error"
    code: str
    message: str


ServerMessage = Annotated[
    HelloAck | CommandMessage | CancelMessage | PingMessage | ServerMessageError,
    Field(discriminator="type"),
]


# ---- agent -> server ----------------------------------------------------------------

class AgentHello(_WireModel):
    type: Literal["hello"]
    agent_version: str = Field(max_length=40)
    os: str = Field(max_length=30)
    os_version: str | None = Field(default=None, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    # {"apps": [{"id","label"}], "urls": [{"id","label","url"}], "folders": [{"id","label"}], "projects": [{"id","label"}]}
    resources: dict[str, Any] = Field(default_factory=dict)


class AgentHeartbeat(_WireModel):
    type: Literal["heartbeat"]
    running_command_ids: list[str] = Field(default_factory=list, max_length=32)
    sent_at: str


class AgentProgress(_WireModel):
    type: Literal["progress"]
    command_id: str
    step: str = Field(max_length=200)
    message: str = Field(default="", max_length=500)
    # Only sent when the agent can measure progress; never estimated.
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    sent_at: str


class AgentResult(_WireModel):
    type: Literal["result"]
    command_id: str
    status: Literal["succeeded", "failed", "cancelled"]
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str


class AgentPong(_WireModel):
    type: Literal["pong"]
    nonce: str


AgentMessage = Annotated[
    AgentHello | AgentHeartbeat | AgentProgress | AgentResult | AgentPong,
    Field(discriminator="type"),
]

agent_message_adapter: TypeAdapter[AgentMessage] = TypeAdapter(AgentMessage)
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


# ---- builders -----------------------------------------------------------------------

def _window(ttl_seconds: int, now: datetime | None = None) -> tuple[str, str]:
    issued = now or datetime.now(UTC)
    return to_iso(issued) or "", to_iso(issued + timedelta(seconds=ttl_seconds)) or ""


def build_command(
    device_secret: str,
    *,
    action: str,
    args: dict[str, Any],
    ttl_seconds: int,
    command_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> CommandMessage:
    if action not in DEVICE_ACTIONS:
        raise ValueError(f"Unknown device action: {action}")
    cid = str(command_id or uuid.uuid4())
    issued_at, expires_at = _window(ttl_seconds, now)
    nonce = new_token(16)
    args_json = serialize_args(args)
    signature = sign_fields(
        device_secret,
        {
            "id": cid,
            "action": action,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": nonce,
            "args_json": args_json,
        },
    )
    return CommandMessage(
        id=cid,
        action=action,
        args_json=args_json,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        signature=signature,
    )


def build_cancel(device_secret: str, *, command_id: str, ttl_seconds: int = 60) -> CancelMessage:
    issued_at, expires_at = _window(ttl_seconds)
    nonce = new_token(16)
    signature = sign_fields(
        device_secret,
        {
            "id": command_id,
            "action": CANCEL_ACTION,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": nonce,
            "args_json": "{}",
        },
    )
    return CancelMessage(
        command_id=command_id,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        signature=signature,
    )


def utc_iso() -> str:
    return to_iso(datetime.now(UTC)) or ""
