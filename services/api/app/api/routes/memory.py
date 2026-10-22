"""User-controlled long-term memory: list and search, create, edit, delete, bulk clear, export, history and reindex."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError, NotFoundError
from app.schemas.memory import (
    MemoryCategoryLiteral,
    MemoryClearOut,
    MemoryClearRequest,
    MemoryCreate,
    MemoryHistoryOut,
    MemoryListOut,
    MemoryOut,
    MemoryReindexOut,
    MemoryUpdate,
    memory_history_item,
    memory_out,
)
from app.services import memory_service as memories

router = APIRouter(prefix="/memories", tags=["memory"])


def _parse_memory_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Memory not found.", code="memory_not_found") from exc


def _embedding_source(request: Request) -> memories.EmbeddingSource:
    state = request.app.state
    return memories.build_embedding_source(state.settings, state.providers)


@router.get("", response_model=MemoryListOut)
async def list_memories_endpoint(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=200),
    category: MemoryCategoryLiteral | None = None,
    semantic: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MemoryListOut:
    result = await memories.search_memories(
        db,
        user_id=user.id,
        query=query,
        category=category,
        limit=limit,
        offset=offset,
        semantic=semantic,
        embedding_source=_embedding_source(request),
    )
    enabled = await memories.memory_is_enabled(db, user.id)
    return MemoryListOut(
        items=[memory_out(item) for item in result.items],
        total=result.total,
        enabled=enabled,
        search_mode=result.search_mode,
        fallback_reason=result.fallback_reason,
    )


@router.post("/reindex", response_model=MemoryReindexOut)
async def reindex_memories_endpoint(request: Request, db: DbSession, user: CurrentUser) -> MemoryReindexOut:
    """Embeds up to 200 of the caller's memories that have no vector. A failure is returned as an error, not hidden."""
    result = await memories.reindex_memories(db, user_id=user.id, embedding_source=_embedding_source(request))
    await db.commit()
    if result.reason is not None:
        status_code = 409 if result.reason in memories.CONFIGURATION_REASONS else 503
        raise AppError(
            "Embeddings could not be created. Some memories may still be unembedded.",
            code="embedding_unavailable",
            status_code=status_code,
            details={"reason": result.reason, "updated": result.updated, "remaining": result.remaining},
        )
    return MemoryReindexOut(updated=result.updated, remaining=result.remaining)


@router.get("/export")
async def export_memories(db: DbSession, user: CurrentUser) -> JSONResponse:
    payload = await memories.export_payload(db, user_id=user.id)
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": 'attachment; filename="jarvis-memories.json"', "Cache-Control": "no-store"},
    )


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory_endpoint(body: MemoryCreate, request: Request, db: DbSession, user: CurrentUser) -> MemoryOut:
    memory = await memories.create_memory(
        db,
        user_id=user.id,
        category=body.category,
        content=body.content,
        source="manual",
        embedding_source=_embedding_source(request),
    )
    await db.commit()
    return memory_out(memory)


@router.delete("", response_model=MemoryClearOut)
async def clear_memories_endpoint(body: MemoryClearRequest, db: DbSession, user: CurrentUser) -> MemoryClearOut:
    deleted = await memories.clear_memories(db, user_id=user.id)
    await db.commit()
    return MemoryClearOut(deleted=deleted)


@router.get("/{memory_id}/history", response_model=MemoryHistoryOut)
async def memory_history_endpoint(memory_id: str, db: DbSession, user: CurrentUser) -> MemoryHistoryOut:
    rows = await memories.memory_history(db, user_id=user.id, memory_id=_parse_memory_id(memory_id))
    return MemoryHistoryOut(items=[memory_history_item(row) for row in rows])


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory_endpoint(
    memory_id: str,
    body: MemoryUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> MemoryOut:
    memory = await memories.update_memory(
        db,
        user_id=user.id,
        memory_id=_parse_memory_id(memory_id),
        content=body.content,
        category=body.category,
        embedding_source=_embedding_source(request),
    )
    await db.commit()
    return memory_out(memory)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory_endpoint(memory_id: str, db: DbSession, user: CurrentUser) -> Response:
    await memories.delete_memory(db, user_id=user.id, memory_id=_parse_memory_id(memory_id))
    await db.commit()
    return Response(status_code=204)

