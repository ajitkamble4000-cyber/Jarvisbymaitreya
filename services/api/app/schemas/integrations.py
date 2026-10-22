"""Response models for the integration catalogue and the OAuth connection routes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

IntegrationStatus = Literal[
    "planned",
    "requires_configuration",
    "not_connected",
    "connected",
    "expired",
    "error",
    "disconnected",
]


class IntegrationOut(BaseModel):
    id: str
    name: str
    category: str
    status: IntegrationStatus
    implemented: bool
    requires_configuration: list[str]
    scopes_requested: list[str]
    # Scopes the provider granted, from storage. Empty unless a connection exists.
    permissions: list[str]
    docs: str
    account_label: str | None = None
    last_checked_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


class IntegrationListOut(BaseModel):
    items: list[IntegrationOut]


class ConnectOut(BaseModel):
    authorization_url: str


class ConnectionTestOut(BaseModel):
    ok: bool
    status: IntegrationStatus
    account_label: str | None
    message: str


class DisconnectOut(BaseModel):
    status: Literal["disconnected"]
    provider_revoked: bool
    message: str


__all__ = [
    "ConnectOut",
    "ConnectionTestOut",
    "DisconnectOut",
    "IntegrationListOut",
    "IntegrationOut",
    "IntegrationStatus",
]
