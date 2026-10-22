"""Registered devices, their pairing state and their revocable credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin


class Device(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (
        Index("ix_devices_user_pairing", "user_id", "pairing_status"),
        # At most one default device per user.
        Index(
            "uq_devices_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    device_type: Mapped[str] = mapped_column(String(30), default="laptop", nullable=False)
    os: Mapped[str] = mapped_column(String(30), default="windows", nullable=False)
    os_version: Mapped[str | None] = mapped_column(String(120))
    agent_version: Mapped[str | None] = mapped_column(String(40))
    # paired | revoked
    pairing_status: Mapped[str] = mapped_column(String(20), default="paired", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Capability keys the agent reports (e.g. ["launch_app", "open_url"]).
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # Per-capability user policy: {"launch_app": {"mode": "ask" | "allow" | "deny"}}.
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Approved resources reported by the agent (ids and labels only): apps, urls, folders, projects.
    resources: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # SHA-256 of the device secret, used to authenticate the agent. Unique and indexed.
    credential_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Fernet ciphertext of the device secret, needed to sign commands (HMAC key).
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(200))


class DevicePairingCode(Base, UUIDMixin):
    """Single-use pairing code. Only a keyed hash of the code is stored."""

    __tablename__ = "device_pairing_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="SET NULL"))
    failed_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
