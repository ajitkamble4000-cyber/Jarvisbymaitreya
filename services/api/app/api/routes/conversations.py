"""Conversations, messages and streaming chat. Contract: docs/api.md, "Conversations and chat"."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, parse_uuid
from app.core.time import utc_now
from app.services.audit import record_audit
from app.services.chat import ChatService, GenerationGuard
from app.schemas.conversations import (
    ChatMessageIn,
    ConversationCreate,
    ConversationDetailOut,
    ConversationListOut,
    ConversationOut,
    ConversationPatch,
    ClearConversationsIn,
    ClearConversationsOut,
    MessageEdit,
    MessageFeedback,
    MessageOut,
)
from app.services import conversation_service as conversations
from app.services.export import export_conversation
from app.services.sse import SSE_HEADERS, ServerSentEvent, encode_stream, with_keepalive

router = APIRouter(prefix="", tags=["conversations"])

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _chat_service(request: Request) -> ChatService:
    """Built per request from ``app.state``, so the providers and task engine are read at call time."""
    state = request.app.state
    guard: GenerationGuard | None = getattr(state, "chat_generations", None)
    if guard is None:
        guard = GenerationGuard()
        state.chat_generations = guard
    hub = getattr(state, "device_hub", None)
    return ChatService(
        settings=state.settings,
        session_factory=state.session_factory,
        providers=state.providers,
        tasks=state.task_engine,
        events=state.events,
        tools=state.tools,
        guard=guard,
        device_online=getattr(hub, "is_online", None),
    )


def _sse_response(stream: AsyncIterator[ServerSentEvent]) -> StreamingResponse:
    return StreamingResponse(
        encode_stream(with_keepalive(stream)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# -- conversations -------------------------------------------------------------


@router.get("/conversations", response_model=ConversationListOut)
async def list_conversations_endpoint(
    user: CurrentUser,
    db: DbSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ConversationListOut:
    rows, total = await conversations.list_conversations(
        db,
        user_id=user.id,
        query=query,
        archived=archived,
        limit=limit,
        offset=offset,
    )
    return ConversationListOut(items=[ConversationOut.from_row(row) for row in rows], total=total)


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation_endpoint(
    user: CurrentUser,
    db: DbSession,
    body: ConversationCreate | None = None,
) -> ConversationOut:
    conversation = await conversations.create_conversation(
        db,
        user_id=user.id,
        title=body.title if body is not None else None,
    )
    await db.commit()
    return ConversationOut.from_row(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation_endpoint(
    conversation_id: str,
    user: CurrentUser,
    db: DbSession,
    limit: Limit = 200,
) -> ConversationDetailOut:
    """Returns the most recent ``limit`` messages, oldest first."""
    cid = parse_uuid(conversation_id)
    conversation = await conversations.load_conversation(db, user_id=user.id, conversation_id=cid)
    rows = await conversations.list_messages(db, user_id=user.id, conversation_id=cid, limit=limit)
    return ConversationDetailOut(
        conversation=ConversationOut.from_row(conversation),
        messages=[MessageOut.from_row(row) for row in rows],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def patch_conversation_endpoint(
    conversation_id: str,
    body: ConversationPatch,
    user: CurrentUser,
    db: DbSession,
) -> ConversationOut:
    conversation = await conversations.update_conversation(
        db,
        user_id=user.id,
        conversation_id=parse_uuid(conversation_id),
        title=body.title,
        archived=body.archived,
        pinned=body.pinned,
    )
    await db.commit()
    return ConversationOut.from_row(conversation)


@router.post("/conversations/clear", response_model=ClearConversationsOut)
async def clear_conversations_endpoint(body: ClearConversationsIn, user: CurrentUser, db: DbSession) -> ClearConversationsOut:
    """Counts (``dry_run`` true, the default) or deletes the owner's old conversations. Pinned ones are kept."""
    older_than = utc_now() - timedelta(days=body.days)
    count = await conversations.clear_old_conversations(db, user_id=user.id, older_than=older_than, dry_run=body.dry_run)
    if not body.dry_run:
        record_audit(db, action="conversations.cleared", user_id=user.id, target_type="user", target_id=str(user.id), data={"days": body.days, "deleted": count})
    await db.commit()
    return ClearConversationsOut(matched=count, deleted=0 if body.dry_run else count, dry_run=body.dry_run)


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response)
async def delete_conversation_endpoint(conversation_id: str, user: CurrentUser, db: DbSession) -> Response:
    await conversations.delete_conversation(db, user_id=user.id, conversation_id=parse_uuid(conversation_id))
    await db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/export")
async def export_conversation_endpoint(
    conversation_id: str,
    user: CurrentUser,
    db: DbSession,
    export_format: Annotated[Literal["markdown", "json"], Query(alias="format")] = "markdown",
) -> Response:
    """The whole conversation as an attachment. Owner only: another user's conversation is a 404."""
    export = await export_conversation(
        db,
        user_id=user.id,
        conversation_id=parse_uuid(conversation_id),
        fmt=export_format,
    )
    return Response(
        content=export.body,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "Cache-Control": "no-store",
        },
    )


# -- messages and streaming ------------------------------------------------------


@router.post("/conversations/{conversation_id}/messages")
async def send_message_endpoint(
    conversation_id: str,
    body: ChatMessageIn,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> StreamingResponse:
    """Server-sent events. Validation and the one-reply-at-a-time check happen before the stream starts."""
    chat = _chat_service(request)
    stream = await chat.start_send(db, user_id=user.id, conversation_id=parse_uuid(conversation_id), body=body)
    await db.rollback()  # no transaction stays open while the reply streams
    return _sse_response(stream)


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate")
async def regenerate_endpoint(
    conversation_id: str,
    message_id: str,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> StreamingResponse:
    chat = _chat_service(request)
    stream = await chat.start_regenerate(
        db,
        user_id=user.id,
        conversation_id=parse_uuid(conversation_id),
        message_id=parse_uuid(message_id),
    )
    await db.rollback()
    return _sse_response(stream)


@router.put("/conversations/{conversation_id}/messages/{message_id}/feedback", response_model=MessageOut)
async def message_feedback_endpoint(
    conversation_id: str,
    message_id: str,
    body: MessageFeedback,
    user: CurrentUser,
    db: DbSession,
) -> MessageOut:
    row = await conversations.set_message_feedback(
        db,
        user_id=user.id,
        conversation_id=parse_uuid(conversation_id),
        message_id=parse_uuid(message_id),
        rating=body.rating,
    )
    await db.commit()
    return MessageOut.from_row(row)


@router.patch("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message_endpoint(
    conversation_id: str,
    message_id: str,
    body: MessageEdit,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> MessageOut:
    chat = _chat_service(request)
    return await chat.edit_message(
        db,
        user_id=user.id,
        conversation_id=parse_uuid(conversation_id),
        message_id=parse_uuid(message_id),
        content=body.content,
    )
