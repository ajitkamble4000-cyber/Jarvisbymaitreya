"""User-controlled long-term memory and its change history."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin
from app.memory.embeddings import Vector


class Memory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_user_category", "user_id", "category"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # fact | preference | project | task_context | conversation_summary
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # manual | conversation | system
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pgvector embedding of ``content`` (768 values). NULL until embedded. Deferred: only search loads it.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True, deferred=True)


class MemoryHistory(Base):
    """Append-only record of memory changes. Survives deletion of the memory itself."""

    __tablename__ = "memory_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # created | edited | deleted | cleared
    previous_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
