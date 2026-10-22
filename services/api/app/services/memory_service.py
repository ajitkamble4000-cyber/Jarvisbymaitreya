"""Long-term memory: the writer (sensitive-content checks, history, retention, embeddings), search and the prompt reader.

Every change writes a ``MemoryHistory`` row. History survives deletion of the memory itself.
Every query is scoped to the owning user.

Semantic search and the write-time embedding use the user's Gemini provider and the configured
``gemini_embedding_model``. Embedding never blocks a memory write: a failure stores the memory without
a vector and logs a warning. Search never falls back silently: the response names the reason.

This module also hosts ``load_settings_document``, the single reader of the per-user settings
document used by the task engine and the memory API. It prefers ``settings_service.load_settings``
and falls back to the stored ``UserPreference`` document with the same defaults.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.time import utc_now
from app.memory.embeddings import VECTOR_DIMENSIONS, check_embedding_dimensions, cosine_distance
from app.models.identity import UserPreference
from app.models.memory import Memory, MemoryHistory
from app.providers.base import AIProvider, ProviderError, ProviderErrorCode
from app.providers.gemini import EMBED_BATCH_LIMIT

log = logging.getLogger("jarvis.memory")

EMBEDDING_PROVIDER_ID = "gemini"
REINDEX_LIMIT = 200

# Fallback and failure reasons. These strings are part of the API contract (docs/phase2/memory.md).
REASON_NO_MODEL = "no_embedding_model"
REASON_NOT_CONFIGURED = "provider_not_configured"
REASON_PROVIDER = "provider_unavailable"
REASON_NO_EMBEDDINGS = "no_stored_embeddings"
REASON_NO_QUERY = "no_query"
REASON_DIMENSIONS = "invalid_embedding_dimensions"
# Reasons that need a configuration change rather than a retry. The reindex endpoint maps them to 409.
CONFIGURATION_REASONS: frozenset[str] = frozenset({REASON_NO_MODEL, REASON_NOT_CONFIGURED, REASON_DIMENSIONS})

MEMORY_CATEGORIES: tuple[str, ...] = ("fact", "preference", "project", "task_context", "conversation_summary")
MEMORY_CONTENT_MAX = 2000
CLEAR_CONFIRMATION = "DELETE ALL MEMORIES"

# Patterns that look like credentials. A memory must never hold a secret, so these are refused.
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_\-]+"),  # Google API keys
    re.compile(r"\bhf_[A-Za-z0-9]{6,}"),  # Hugging Face tokens
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),  # "sk-" style API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens (classic and OAuth)
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained personal access tokens
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}"),  # Stripe secret and restricted keys
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWTs (header starts "eyJ")
    re.compile(r"(?i)\bbearer\s+\S+"),  # Authorization header values
    re.compile(r"-----BEGIN [A-Z0-9 ]+-----"),  # PEM blocks
    re.compile(r"(?i)\b(?:password|passwd|secret)\b\s*[:=]\s*\S+"),  # password: x / secret=x
    re.compile(r"(?i)\b(?:password|passwd)\b\s+is\s+\S+"),  # "my password is x"
    re.compile(r"(?i)\b(?:password|passwd)\s+(?=\S*\d)\S{4,}"),  # "password hunter2"
)

_SETTINGS_DEFAULTS: dict[str, Any] = {
    "automation": {"max_concurrent_tasks": 3, "disabled_tools": [], "emergency_stopped": False},
    "notifications": {
        "include_task_details": False,
        "on_task_completed": True,
        "on_task_failed": True,
        "on_approval_required": True,
    },
    "memory": {"enabled": True, "retention_days": None},
}


class SensitiveContentError(ValueError):
    """Raised by validators when content looks like a secret. Carries no value."""


def find_sensitive_reason(text: str) -> str | None:
    """Return a short label for the first credential-like pattern found, or None."""
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            return "credential-like text"
    return None


def assert_not_sensitive(text: str) -> None:
    if find_sensitive_reason(text) is not None:
        raise ValidationAppError(
            "This looks like a password or key, so it was not saved.",
            code="sensitive_content",
        )


def _merge_defaults(doc: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for section, defaults in _SETTINGS_DEFAULTS.items():
        stored = doc.get(section) if isinstance(doc.get(section), Mapping) else {}
        merged[section] = {**defaults, **dict(stored)}
    for key, value in doc.items():
        merged.setdefault(key, value)
    return merged


async def load_settings_document(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Return the user's settings as a plain dict with defaults filled for the sections the engine reads.

    Database errors propagate. Nothing here turns a failed read into a permissive default.
    """
    try:
        module = importlib.import_module("app.services.settings_service")
    except ImportError:
        module = None
    loader = getattr(module, "load_settings", None) if module is not None else None
    if loader is not None:
        document: Any = loader(db, user_id)
        if inspect.isawaitable(document):
            document = await document
        raw = document.model_dump() if hasattr(document, "model_dump") else dict(document)
        return _merge_defaults(raw)
    row = await db.get(UserPreference, user_id)
    return _merge_defaults(dict(row.settings or {}) if row is not None else {})


def memory_enabled_from(doc: Mapping[str, Any]) -> bool:
    memory = doc.get("memory")
    return bool(memory.get("enabled", True)) if isinstance(memory, Mapping) else True


async def memory_is_enabled(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return memory_enabled_from(await load_settings_document(db, user_id))


def _retention_expiry(doc: Mapping[str, Any]) -> Any:
    memory = doc.get("memory")
    days = memory.get("retention_days") if isinstance(memory, Mapping) else None
    if isinstance(days, int) and days > 0:
        return utc_now() + timedelta(days=days)
    return None


def _normalise_content(content: str) -> str:
    text = content.strip()
    if not text:
        raise ValidationAppError("Memory content must not be empty.", code="validation_error")
    if len(text) > MEMORY_CONTENT_MAX:
        raise ValidationAppError("Memory content is too long.", code="validation_error")
    return text


# -- embeddings ---------------------------------------------------------------


class ProviderLookup(Protocol):
    """The part of ``ProviderRegistry`` that embeddings need. Tests inject a stub with the same shape."""

    async def build(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> tuple[AIProvider, Any]: ...


@dataclass(frozen=True)
class EmbeddingSource:
    """Where embeddings come from: the user's Gemini provider, the embedding model and the vector size."""

    providers: ProviderLookup
    model: str | None
    dimensions: int


def build_embedding_source(settings: Settings, providers: ProviderLookup) -> EmbeddingSource:
    return EmbeddingSource(
        providers=providers,
        model=settings.gemini_embedding_model,
        dimensions=settings.embedding_dimensions,
    )


class EmbeddingUnavailable(Exception):
    """Embeddings cannot be produced. ``reason`` is a stable code; ``code`` is the provider code for logs only."""

    def __init__(self, reason: str, *, code: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


async def _embed_texts(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: EmbeddingSource | None,
    texts: list[str],
) -> list[list[float]]:
    """One vector per text, in order. Raises ``EmbeddingUnavailable`` with a reason instead of any provider detail."""
    if source is None or not source.model:
        raise EmbeddingUnavailable(REASON_NO_MODEL)
    try:
        check_embedding_dimensions(source.dimensions)
    except ValueError:
        raise EmbeddingUnavailable(REASON_DIMENSIONS) from None
    try:
        provider, config = await source.providers.build(db, user_id, EMBEDDING_PROVIDER_ID)
    except ProviderError as exc:
        if exc.code is ProviderErrorCode.NOT_CONFIGURED:
            raise EmbeddingUnavailable(REASON_NOT_CONFIGURED) from None
        raise EmbeddingUnavailable(REASON_PROVIDER, code=exc.code.value) from None
    try:
        vectors = await provider.embed(
            texts,
            model=source.model,
            dimensions=source.dimensions,
            timeout_seconds=float(config.timeout_seconds),
        )
    except ProviderError as exc:
        raise EmbeddingUnavailable(REASON_PROVIDER, code=exc.code.value) from None
    if len(vectors) != len(texts) or any(len(vector) != VECTOR_DIMENSIONS for vector in vectors):
        raise EmbeddingUnavailable(REASON_PROVIDER, code="bad_embedding_shape")
    # A NaN or infinite value cannot be stored in the vector column. Treat it as unavailable, not as a database error.
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise EmbeddingUnavailable(REASON_PROVIDER, code="bad_embedding_values")
    return vectors


async def _embedding_for_write(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: EmbeddingSource | None,
    text: str,
) -> list[float] | None:
    """Embeds one memory text for storage. Never raises: a failure is logged and the memory is saved without a vector."""
    if source is None:
        return None
    try:
        (vector,) = await _embed_texts(db, user_id, source, [text])
        return vector
    except EmbeddingUnavailable as exc:
        log.warning("Memory saved without an embedding.", extra={"reason": exc.reason, "code": exc.code})
    except Exception as exc:  # the save must not fail because of embeddings
        log.warning("Memory saved without an embedding.", extra={"reason": REASON_PROVIDER, "error_type": type(exc).__name__})
    return None


async def create_memory(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    category: str,
    content: str,
    source: str = "manual",
    embedding_source: EmbeddingSource | None = None,
) -> Memory:
    """Create a memory. Raises 409 ``memory_disabled`` when memory is off and 422 ``sensitive_content`` for secrets.

    With ``embedding_source`` the text is embedded first. An embedding failure never fails the save.
    """
    doc = await load_settings_document(db, user_id)
    if not memory_enabled_from(doc):
        raise ConflictError("Memory is turned off in settings.", code="memory_disabled")
    if category not in MEMORY_CATEGORIES:
        raise ValidationAppError("Unknown memory category.", code="validation_error")
    text = _normalise_content(content)
    assert_not_sensitive(text)
    vector = await _embedding_for_write(db, user_id, embedding_source, text)
    now = utc_now()
    memory = Memory(
        user_id=user_id,
        category=category,
        content=text,
        source=source,
        expires_at=_retention_expiry(doc),
        embedding=vector,
        created_at=now,
        updated_at=now,
    )
    db.add(memory)
    await db.flush()
    db.add(
        MemoryHistory(
            memory_id=memory.id,
            user_id=user_id,
            action="created",
            previous_content=None,
            new_content=text,
            created_at=now,
        )
    )
    return memory


async def _owned_memory(db: AsyncSession, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory:
    memory = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    if memory is None:
        raise NotFoundError("Memory not found.", code="memory_not_found")
    return memory


async def update_memory(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
    content: str | None = None,
    category: str | None = None,
    embedding_source: EmbeddingSource | None = None,
) -> Memory:
    memory = await _owned_memory(db, user_id, memory_id)
    now = utc_now()
    if category is not None:
        if category not in MEMORY_CATEGORIES:
            raise ValidationAppError("Unknown memory category.", code="validation_error")
        memory.category = category
    if content is not None:
        text = _normalise_content(content)
        assert_not_sensitive(text)
        if text != memory.content:
            db.add(
                MemoryHistory(
                    memory_id=memory.id,
                    user_id=user_id,
                    action="edited",
                    previous_content=memory.content,
                    new_content=text,
                    created_at=now,
                )
            )
            memory.content = text
            # The old vector describes the old text. It is replaced, or cleared when re-embedding fails.
            memory.embedding = await _embedding_for_write(db, user_id, embedding_source, text)
    memory.updated_at = now
    return memory


async def delete_memory(db: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID) -> None:
    memory = await _owned_memory(db, user_id, memory_id)
    db.add(
        MemoryHistory(
            memory_id=memory.id,
            user_id=user_id,
            action="deleted",
            previous_content=memory.content,
            new_content=None,
            created_at=utc_now(),
        )
    )
    await db.delete(memory)


async def clear_memories(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Delete every memory of the user, recording one ``cleared`` history row per memory."""
    rows = (await db.scalars(select(Memory).where(Memory.user_id == user_id))).all()
    now = utc_now()
    for memory in rows:
        db.add(
            MemoryHistory(
                memory_id=memory.id,
                user_id=user_id,
                action="cleared",
                previous_content=memory.content,
                new_content=None,
                created_at=now,
            )
        )
        await db.delete(memory)
    return len(rows)


def _live_filter(user_id: uuid.UUID) -> Any:
    now = utc_now()
    return (Memory.user_id == user_id) & ((Memory.expires_at.is_(None)) | (Memory.expires_at > now))


def _keyword_pattern(query: str) -> str:
    """Bound ILIKE pattern for the user's text. Backslash, ``%`` and ``_`` are escaped; use with a backslash escape."""
    escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


async def list_memories(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Memory], int]:
    conditions = [_live_filter(user_id)]
    if category is not None:
        conditions.append(Memory.category == category)
    if query:
        conditions.append(Memory.content.ilike(_keyword_pattern(query), escape="\\"))
    where = conditions[0]
    for extra in conditions[1:]:
        where = where & extra
    total = await db.scalar(select(func.count(Memory.id)).where(where)) or 0
    items = (
        await db.scalars(
            select(Memory).where(where).order_by(Memory.created_at.desc(), Memory.id).limit(limit).offset(offset)
        )
    ).all()
    return list(items), int(total)


# -- search -------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    items: list[Memory]
    total: int
    search_mode: Literal["semantic", "keyword"]
    # Set only when a semantic request was answered by keyword search. Never silent.
    fallback_reason: str | None


async def _semantic_precheck(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: EmbeddingSource | None,
) -> str | None:
    """Returns the reason semantic search cannot run, or None. Checks configuration before any provider call."""
    if source is None or not source.model:
        return REASON_NO_MODEL
    try:
        check_embedding_dimensions(source.dimensions)
    except ValueError:
        return REASON_DIMENSIONS
    has_vector = await db.scalar(
        select(Memory.id).where(_live_filter(user_id), Memory.embedding.is_not(None)).limit(1)
    )
    return None if has_vector is not None else REASON_NO_EMBEDDINGS


async def search_memories(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str | None,
    category: str | None,
    limit: int,
    offset: int,
    semantic: bool,
    embedding_source: EmbeddingSource | None = None,
) -> SearchResult:
    """Keyword search by default. With ``semantic`` the query is embedded. Memories with a vector are ranked by
    cosine distance (closest first). Memories without a vector are included only when they match by keyword, and
    are listed before the ranked ones. Any reason for falling back to keyword search is returned."""
    if not semantic:
        items, total = await list_memories(
            db, user_id=user_id, query=query, category=category, limit=limit, offset=offset
        )
        return SearchResult(items, total, "keyword", None)
    if not query or not query.strip():
        items, total = await list_memories(
            db, user_id=user_id, query=None, category=category, limit=limit, offset=offset
        )
        return SearchResult(items, total, "keyword", REASON_NO_QUERY)

    reason = await _semantic_precheck(db, user_id, embedding_source)
    vector: list[float] | None = None
    if reason is None:
        try:
            (vector,) = await _embed_texts(db, user_id, embedding_source, [query])
        except EmbeddingUnavailable as exc:
            reason = exc.reason
        except Exception as exc:  # any provider or transport failure is reported as unavailable
            log.warning("Semantic search fell back to keyword.", extra={"error_type": type(exc).__name__})
            reason = REASON_PROVIDER
    if reason is not None or vector is None:
        items, total = await list_memories(
            db, user_id=user_id, query=query, category=category, limit=limit, offset=offset
        )
        return SearchResult(items, total, "keyword", reason or REASON_PROVIDER)

    conditions = [
        _live_filter(user_id),
        # Memories with a vector are ranked. Memories without one only match by keyword.
        or_(
            Memory.embedding.is_not(None),
            Memory.content.ilike(_keyword_pattern(query), escape="\\"),
        ),
    ]
    if category is not None:
        conditions.append(Memory.category == category)
    where = and_(*conditions)
    total = await db.scalar(select(func.count(Memory.id)).where(where)) or 0
    # Order: keyword matches that have no vector first (newest first, an exact hit is never buried), then the
    # embedded memories by cosine distance, closest first.
    rows = await db.scalars(
        select(Memory)
        .where(where)
        .order_by(
            Memory.embedding.is_not(None),
            cosine_distance(Memory.embedding, vector),
            Memory.created_at.desc(),
            Memory.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return SearchResult(list(rows.all()), int(total), "semantic", None)


@dataclass(frozen=True)
class ReindexResult:
    updated: int
    remaining: int
    # None when every selected memory was embedded. Otherwise the reason the run stopped early.
    reason: str | None


async def reindex_memories(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    embedding_source: EmbeddingSource | None,
    limit: int = REINDEX_LIMIT,
) -> ReindexResult:
    """Embeds up to ``limit`` of the user's live memories that have no vector. Only this user's rows are touched."""
    rows = (
        await db.scalars(
            select(Memory)
            .where(_live_filter(user_id), Memory.embedding.is_(None))
            .order_by(Memory.created_at, Memory.id)
            .limit(limit)
        )
    ).all()
    updated = 0
    reason: str | None = None
    for start in range(0, len(rows), EMBED_BATCH_LIMIT):
        chunk = list(rows[start : start + EMBED_BATCH_LIMIT])
        try:
            vectors = await _embed_texts(db, user_id, embedding_source, [memory.content for memory in chunk])
        except EmbeddingUnavailable as exc:
            reason = exc.reason
            break
        except Exception as exc:
            log.warning("Memory reindex stopped.", extra={"error_type": type(exc).__name__})
            reason = REASON_PROVIDER
            break
        for memory, vector in zip(chunk, vectors, strict=True):
            memory.embedding = vector
            updated += 1
    await db.flush()
    remaining = await db.scalar(
        select(func.count(Memory.id)).where(_live_filter(user_id), Memory.embedding.is_(None))
    )
    return ReindexResult(updated=updated, remaining=int(remaining or 0), reason=reason)


async def prompt_memories(db: AsyncSession, user_id: uuid.UUID, limit: int = 20) -> list[Memory]:
    """Newest live memories for the chat prompt. Empty when memory is disabled. Never another user's."""
    if not await memory_is_enabled(db, user_id):
        return []
    rows = await db.scalars(
        select(Memory).where(_live_filter(user_id)).order_by(Memory.created_at.desc(), Memory.id).limit(limit)
    )
    return list(rows.all())


async def export_payload(db: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    rows = (await db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at))).all()
    now = utc_now()
    return {
        "exported_at": now.isoformat().replace("+00:00", "Z"),
        "memories": [
            {
                "id": str(memory.id),
                "category": memory.category,
                "content": memory.content,
                "source": memory.source,
                "created_at": memory.created_at.isoformat().replace("+00:00", "Z"),
                "updated_at": memory.updated_at.isoformat().replace("+00:00", "Z"),
                "expires_at": memory.expires_at.isoformat().replace("+00:00", "Z") if memory.expires_at else None,
            }
            for memory in rows
        ],
    }


async def memory_history(db: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID) -> list[MemoryHistory]:
    """History for one memory. Deleted memories keep their history; other users' ids are not found."""
    owned = await db.scalar(select(Memory.id).where(Memory.id == memory_id, Memory.user_id == user_id))
    rows = (
        await db.scalars(
            select(MemoryHistory)
            .where(MemoryHistory.memory_id == memory_id, MemoryHistory.user_id == user_id)
            .order_by(MemoryHistory.id)
        )
    ).all()
    if owned is None and not rows:
        raise NotFoundError("Memory not found.", code="memory_not_found")
    return list(rows)


async def save_from_tool(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    category: str,
    content: str,
    embedding_source: EmbeddingSource | None = None,
) -> Memory:
    """Entry point used by the ``save_memory`` server tool. Records the source as a conversation.

    Without ``embedding_source`` the memory is saved unembedded. ``POST /memories/reindex`` embeds it later.
    """
    memory = await create_memory(
        db,
        user_id=user_id,
        category=category,
        content=content,
        source="conversation",
        embedding_source=embedding_source,
    )
    log.info("Memory saved.", extra={"category": category})
    return memory


__all__ = [
    "CLEAR_CONFIRMATION",
    "CONFIGURATION_REASONS",
    "MEMORY_CATEGORIES",
    "REASON_DIMENSIONS",
    "REASON_NOT_CONFIGURED",
    "REASON_NO_EMBEDDINGS",
    "REASON_NO_MODEL",
    "REASON_NO_QUERY",
    "REASON_PROVIDER",
    "EmbeddingSource",
    "EmbeddingUnavailable",
    "ReindexResult",
    "SearchResult",
    "SensitiveContentError",
    "assert_not_sensitive",
    "build_embedding_source",
    "clear_memories",
    "create_memory",
    "delete_memory",
    "export_payload",
    "find_sensitive_reason",
    "list_memories",
    "load_settings_document",
    "memory_enabled_from",
    "memory_history",
    "memory_is_enabled",
    "prompt_memories",
    "reindex_memories",
    "save_from_tool",
    "search_memories",
    "update_memory",
]
