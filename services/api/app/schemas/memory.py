"""Request and response models for the memory API (docs/api.md "Memory")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.memory import Memory, MemoryHistory

MemoryCategoryLiteral = Literal["fact", "preference", "project", "task_context", "conversation_summary"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryCreate(_Strict):
    category: MemoryCategoryLiteral
    content: str = Field(min_length=1, max_length=2000)


class MemoryUpdate(_Strict):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: MemoryCategoryLiteral | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> MemoryUpdate:
        if self.content is None and self.category is None:
            raise ValueError("Provide content or category.")
        return self


class MemoryClearRequest(_Strict):
    confirm: Literal["DELETE ALL MEMORIES"]


class MemoryOut(BaseModel):
    id: uuid.UUID
    category: str
    content: str
    source: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


class MemoryListOut(BaseModel):
    items: list[MemoryOut]
    total: int
    enabled: bool
    # "semantic" when ranked by embeddings. "keyword" otherwise, and then fallback_reason says why.
    search_mode: Literal["semantic", "keyword"] = "keyword"
    fallback_reason: str | None = None


class MemoryReindexOut(BaseModel):
    updated: int
    remaining: int


class MemoryClearOut(BaseModel):
    deleted: int


class MemoryHistoryItem(BaseModel):
    action: str
    previous_content: str | None
    new_content: str | None
    created_at: datetime


class MemoryHistoryOut(BaseModel):
    items: list[MemoryHistoryItem]


def memory_out(memory: Memory) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        category=memory.category,
        content=memory.content,
        source=memory.source,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        expires_at=memory.expires_at,
    )


def memory_history_item(row: MemoryHistory) -> MemoryHistoryItem:
    return MemoryHistoryItem(
        action=row.action,
        previous_content=row.previous_content,
        new_content=row.new_content,
        created_at=row.created_at,
    )
