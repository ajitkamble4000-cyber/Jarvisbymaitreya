"""Conversations and messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin


class Conversation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_archived_updated", "user_id", "archived", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), default="New conversation", nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pinned conversations list first in the same archived state (see list_conversations).
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Newest message covered by the latest automatic summary (services/summaries.py). Deliberately not a
    # foreign key: an edit or regenerate that removes that message must not change the conversation row.
    summary_through_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Message(Base, UUIDMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised owner so every query can be scoped to the user without a join.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # complete | streaming | error | cancelled
    status: Mapped[str] = mapped_column(String(20), default="complete", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
