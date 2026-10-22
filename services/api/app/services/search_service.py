"""One search across the account: conversations (title or message text), memories, and tasks.

Every query is scoped to the owner. Memories past their expiry are not returned. The query is matched literally:
``%`` and ``_`` are escaped, so typing them does not match everything. Each group is capped by ``limit``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.conversation import Conversation, Message
from app.models.memory import Memory
from app.models.tasks import Task
from app.services.conversation_service import escape_like

MIN_QUERY_CHARS = 2
EXCERPT_CHARS = 140
_ESCAPE = "\\"


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= EXCERPT_CHARS else collapsed[: EXCERPT_CHARS - 1] + "…"


async def search_everything(db: AsyncSession, *, user_id: uuid.UUID, query: str, limit: int) -> dict[str, Any]:
    text = query.strip()
    pattern = f"%{escape_like(text)}%"
    now = utc_now()

    # Conversations match on the title, or on any message in them. A title match is reported as such.
    message_match = (
        select(Message.id)
        .where(Message.conversation_id == Conversation.id, Message.user_id == user_id, Message.content.ilike(pattern, escape=_ESCAPE))
        .exists()
    )
    title_match = Conversation.title.ilike(pattern, escape=_ESCAPE)
    conversation_rows = (
        await db.execute(
            select(Conversation.id, Conversation.title, Conversation.updated_at, title_match.label("by_title"))
            .where(Conversation.user_id == user_id, or_(title_match, message_match))
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
        )
    ).all()

    memory_rows = (
        await db.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.content.ilike(pattern, escape=_ESCAPE),
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
    ).all()

    task_rows = (
        await db.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                or_(Task.title.ilike(pattern, escape=_ESCAPE), Task.tool_name.ilike(pattern, escape=_ESCAPE)),
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(limit)
        )
    ).all()

    return {
        "query": text,
        "conversations": [
            {
                "id": str(row.id),
                "title": row.title or "Untitled",
                "match": "title" if row.by_title else "message",
                "updated_at": row.updated_at.isoformat(),
            }
            for row in conversation_rows
        ],
        "memories": [
            {"id": str(row.id), "category": row.category, "excerpt": _excerpt(row.content)} for row in memory_rows
        ],
        "tasks": [
            {"id": str(row.id), "title": row.title, "tool_name": row.tool_name, "status": row.status} for row in task_rows
        ],
    }
