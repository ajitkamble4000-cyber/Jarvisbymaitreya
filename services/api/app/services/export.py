"""Conversation export as Markdown or JSON.

The export holds the conversation's own text: titles, message content, roles and timestamps. It never
includes provider names, models, tokens or the stored meta of a message. Every query is scoped to the
owner, so another user gets the same NotFoundError as for a missing conversation.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.services.conversation_service import load_conversation

ExportFormat = Literal["markdown", "json"]

FILENAME_MAX_CHARS = 60
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 _-]")
_EXTENSIONS: dict[str, str] = {"markdown": "md", "json": "json"}
_MEDIA_TYPES: dict[str, str] = {"markdown": "text/markdown; charset=utf-8", "json": "application/json; charset=utf-8"}
_LABELS: dict[str, str] = {"user": "User", "assistant": "Assistant", "tool": "Tool", "system": "System"}


@dataclass(frozen=True)
class ConversationExport:
    filename: str
    media_type: str
    body: bytes


def export_filename(title: str, conversation_id: uuid.UUID, fmt: ExportFormat) -> str:
    """The title with every character outside ``[A-Za-z0-9 _-]`` removed, cut to 60 characters.

    Falls back to the conversation id when nothing is left. The result has no path separators or quotes.
    """
    base = _UNSAFE_FILENAME_CHARS.sub("", title)[:FILENAME_MAX_CHARS].strip()
    return f"{base or str(conversation_id)}.{_EXTENSIONS[fmt]}"


async def export_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    fmt: ExportFormat,
) -> ConversationExport:
    conversation = await load_conversation(db, user_id=user_id, conversation_id=conversation_id)
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.user_id == user_id)
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    text = _markdown(conversation, rows) if fmt == "markdown" else _json(conversation, rows)
    return ConversationExport(
        filename=export_filename(conversation.title, conversation.id, fmt),
        media_type=_MEDIA_TYPES[fmt],
        body=text.encode("utf-8"),
    )


def _markdown(conversation: Conversation, rows: list[Message]) -> str:
    parts = [f"# {conversation.title}"]
    for row in rows:
        heading = f"### {_LABELS.get(row.role, row.role.title())}"
        if row.role == "tool":
            parts.append(f"{heading}\n\n_Tool output_\n\n{row.content}")
        else:
            parts.append(f"{heading}\n\n{row.content}")
    return "\n\n".join(parts) + "\n"


def _json(conversation: Conversation, rows: list[Message]) -> str:
    payload = {
        "id": str(conversation.id),
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "messages": [
            {
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat(),
                "status": row.status,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
