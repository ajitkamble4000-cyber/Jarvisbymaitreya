"""AI provider configuration (credentials are stored encrypted) and usage records."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin


class ProviderConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("user_id", "provider_id", name="uq_provider_configs_user_provider"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selected_model: Mapped[str | None] = mapped_column(String(200))
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # Fernet ciphertext of the API key / access token. Never returned by the API.
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    credential_hint: Mapped[str | None] = mapped_column(String(16))
    # not_configured | untested | connected | error
    status: Mapped[str] = mapped_column(String(20), default="not_configured", nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class UsageRecord(Base, UUIDMixin):
    """One row per provider call. Analytics are computed from these measured values."""

    __tablename__ = "usage_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)  # chat | stream | test | list_models
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(60))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Only set when a pricing table is configured; otherwise cost is reported as unavailable.
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
