"""Conversation and message persistence helpers. Every query is scoped to ``user_id``.

Functions add to or delete from the caller's session; the caller commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.time import utc_now
from app.models.conversation import Conversation, Message
from app.models.tasks import Task

DEFAULT_TITLE = "New conversation"
# Automatic titles come from the first message and are capped at 60 characters.
TITLE_MAX_CHARS = 60
# Column limit for conversations.title.
TITLE_DB_MAX_CHARS = 200
HISTORY_ROLES = ("user", "assistant", "tool")
HISTORY_STATUSES = ("complete", "cancelled")
PENDING_TASK_STATUSES = ("awaiting_approval", "queued")

_LIKE_ESCAPE = "\\"

T = TypeVar("T")


# -- text rules ----------------------------------------------------------------


def normalise_title(value: str | None) -> str:
    """Single-line title. A blank value falls back to the default title."""
    if value is None:
        return DEFAULT_TITLE
    collapsed = " ".join(value.split())[:TITLE_DB_MAX_CHARS]
    return collapsed or DEFAULT_TITLE


def title_from_message(content: str) -> str:
    """First 60 characters of the first message, on one line."""
    return " ".join(content.split())[:TITLE_MAX_CHARS]


def validate_message_content(content: str, *, max_chars: int) -> str:
    text = content.strip()
    if not text:
        raise ValidationAppError(
            "Enter a message before sending.",
            details={"fields": [{"field": "content", "message": "Enter a message."}]},
        )
    if len(text) > max_chars:
        raise ValidationAppError(
            f"Messages can be at most {max_chars} characters.",
            details={"fields": [{"field": "content", "message": f"Use at most {max_chars} characters."}]},
        )
    return text


def escape_like(value: str) -> str:
    return value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2).replace("%", f"{_LIKE_ESCAPE}%").replace("_", f"{_LIKE_ESCAPE}_")


# -- row factories -------------------------------------------------------------


def new_message(
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    status: str = "complete",
    provider_id: str | None = None,
    model: str | None = None,
    task_id: uuid.UUID | None = None,
    meta: dict | None = None,
    created_at: datetime | None = None,
) -> Message:
    """Builds a message with its id and timestamp set here, so ids are known before the row is flushed."""
    return Message(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
        status=status,
        provider_id=provider_id,
        model=model[:200] if model else None,
        task_id=task_id,
        meta=dict(meta or {}),
        created_at=created_at or utc_now(),
    )


def touch_conversation(
    conversation: Conversation,
    *,
    when: datetime,
    provider_id: str | None = None,
    model: str | None = None,
) -> None:
    conversation.updated_at = when
    conversation.last_message_at = when
    if provider_id is not None:
        conversation.provider_id = provider_id
    if model is not None:
        conversation.model = model[:200]


# -- conversations -------------------------------------------------------------


async def list_conversations(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str | None,
    archived: bool,
    limit: int,
    offset: int,
) -> tuple[list[Conversation], int]:
    """Matches the title or any message body (case-insensitive). Pinned first, then newest first."""
    statement = select(Conversation).where(Conversation.user_id == user_id, Conversation.archived == archived)
    if query and query.strip():
        pattern = f"%{escape_like(query.strip())}%"
        message_match = (
            select(Message.id)
            .where(
                Message.conversation_id == Conversation.id,
                Message.user_id == user_id,
                Message.content.ilike(pattern, escape=_LIKE_ESCAPE),
            )
            .exists()
        )
        statement = statement.where(
            or_(Conversation.title.ilike(pattern, escape=_LIKE_ESCAPE), message_match)
        )
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await db.scalars(
        statement.order_by(Conversation.pinned.desc(), Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows.all()), int(total or 0)


async def create_conversation(db: AsyncSession, *, user_id: uuid.UUID, title: str | None) -> Conversation:
    now = utc_now()
    conversation = Conversation(
        id=uuid.uuid4(),
        user_id=user_id,
        title=normalise_title(title),
        archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    return conversation


async def load_conversation(db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    row = await db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if row is None:
        raise NotFoundError("Conversation not found.")
    return row


async def update_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    title: str | None,
    archived: bool | None,
    pinned: bool | None = None,
) -> Conversation:
    conversation = await load_conversation(db, user_id=user_id, conversation_id=conversation_id)
    if title is not None:
        conversation.title = normalise_title(title)
    if archived is not None:
        conversation.archived = archived
    if pinned is not None:
        conversation.pinned = pinned
    conversation.updated_at = utc_now()
    return conversation


async def clear_old_conversations(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    older_than: datetime,
    dry_run: bool,
) -> int:
    """Deletes the owner's conversations last updated before ``older_than``, except pinned ones. Returns the count.

    ``dry_run`` counts without deleting. Archived conversations are included, because they are old too. Messages go
    with their conversation by the database cascade, and tasks keep their history with conversation_id cleared.
    """
    where = (
        Conversation.user_id == user_id,
        Conversation.updated_at < older_than,
        Conversation.pinned.is_(False),
    )
    matched = int(await db.scalar(select(func.count(Conversation.id)).where(*where)) or 0)
    if dry_run or matched == 0:
        return matched
    result = await db.execute(delete(Conversation).where(*where))
    return int(result.rowcount or 0)


async def delete_conversation(db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    """Messages are removed by the database cascade. Tasks keep their history with conversation_id cleared."""
    conversation = await load_conversation(db, user_id=user_id, conversation_id=conversation_id)
    await db.delete(conversation)


# -- messages ------------------------------------------------------------------


async def list_messages(
    db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
) -> list[Message]:
    """The most recent ``limit`` messages, returned oldest first."""
    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.user_id == user_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    ordered = list(rows.all())
    ordered.reverse()
    return ordered


async def load_message(
    db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> Message:
    row = await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.user_id == user_id,
        )
    )
    if row is None:
        raise NotFoundError("Message not found.")
    return row


async def set_message_feedback(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    rating: str | None,
) -> Message:
    """Stores a thumbs up or down on a finished assistant reply, or clears it with ``None``.

    The rating lives in the message's meta under ``feedback``. Only the owner can reach the row, and only a
    complete assistant reply can be rated. The caller commits.
    """
    row = await load_message(db, user_id=user_id, conversation_id=conversation_id, message_id=message_id)
    if row.role != "assistant" or row.status != "complete":
        raise ConflictError("Feedback can be given on a finished assistant reply only.", code="feedback_not_allowed")
    meta = dict(row.meta or {})
    if rating is None:
        meta.pop("feedback", None)
    else:
        meta["feedback"] = {"rating": rating, "given_at": utc_now().isoformat()}
    row.meta = meta
    return row


async def has_user_message_before(
    db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, anchor: Message
) -> bool:
    found = await db.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.user_id == user_id,
            Message.role == "user",
            Message.created_at < anchor.created_at,
        )
        .limit(1)
    )
    return found is not None


async def delete_from(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    anchor: Message,
    include_anchor: bool = True,
) -> list[Message]:
    """Deletes every message after ``anchor``; with ``include_anchor`` the anchor goes too. Returns the removed rows."""
    later = Message.created_at >= anchor.created_at if include_anchor else Message.created_at > anchor.created_at
    removed = list(
        (
            await db.scalars(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.user_id == user_id,
                    later,
                )
            )
        ).all()
    )
    if removed:
        await db.execute(delete(Message).where(Message.id.in_([row.id for row in removed])))
    return removed


async def pending_task_ids(db: AsyncSession, *, user_id: uuid.UUID, messages: Sequence[Message]) -> list[uuid.UUID]:
    """Tasks linked to removed messages that have not started. Those are withdrawn; running ones are not."""
    task_ids = {row.task_id for row in messages if row.task_id is not None}
    if not task_ids:
        return []
    rows = await db.scalars(
        select(Task.id).where(
            Task.id.in_(task_ids),
            Task.user_id == user_id,
            Task.status.in_(PENDING_TASK_STATUSES),
        )
    )
    return list(rows.all())


async def history_rows(
    db: AsyncSession, *, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
) -> list[Message]:
    """The most recent ``limit`` finished messages, oldest first. Errored and in-flight replies are left out."""
    rows = await db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.user_id == user_id,
            Message.role.in_(HISTORY_ROLES),
            Message.status.in_(HISTORY_STATUSES),
            Message.content != "",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    ordered = list(rows.all())
    ordered.reverse()
    return ordered


def select_history(
    items: Sequence[T],
    *,
    text: Callable[[T], str],
    max_messages: int,
    char_budget: int,
) -> list[T]:
    """Keeps the newest items that fit both limits. The newest item is always kept, even if it alone is over budget.

    Once an older item does not fit, nothing older is added, so the window stays contiguous.
    """
    if max_messages <= 0 or not items:
        return []
    window = list(items)[-max_messages:]
    chosen: list[T] = []
    used = 0
    for item in reversed(window):
        size = len(text(item))
        if chosen and used + size > char_budget:
            break
        chosen.append(item)
        used += size
    chosen.reverse()
    return chosen
