"""Semantic memory search, write-time embeddings and reindex against real PostgreSQL with pgvector.

Embeddings come from a deterministic fake provider (an AIProvider subclass injected through
``app.state.providers``) or, for the wire format, from the real Gemini adapter behind an httpx.MockTransport.
Nothing leaves the process.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.memory.embeddings import VECTOR_DIMENSIONS
from app.models.identity import User
from app.models.memory import Memory
from app.providers.base import (
    AIProvider,
    GenerationRequest,
    GenerationResult,
    HealthCheckResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
)
from app.providers.gemini import EMBED_BATCH_LIMIT, GeminiProvider
from app.providers.registry import ProviderRegistry
from app.services import memory_service
from tests.conftest import create_owner, csrf_headers

pytestmark = pytest.mark.integration

BASE = "/api/v1/memories"
MODEL = "gemini-embedding-001"
GEMINI_KEY = "AIzaSyTEST-semantic-key-0001"

# Three toy "topics" on the first three axes. Everything else is zero except a small constant on axis 9,
# so no vector is all zeros (cosine distance is undefined for a zero vector).
_AXIS_OF = {
    "drink": 0, "coffee": 0, "tea": 0, "espresso": 0,
    "pet": 1, "dog": 1, "cat": 1,
    "chess": 2, "game": 2,
}


def fake_vector(text: str) -> list[float]:
    values = [0.0] * VECTOR_DIMENSIONS
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in _AXIS_OF:
            values[_AXIS_OF[word]] += 1.0
    values[9] = 0.01
    return values


class FakeEmbeddingProvider(AIProvider):
    provider_id = "gemini"
    display_name = "Fake Gemini"

    def __init__(self) -> None:
        super().__init__(api_key="", base_url="http://fake.invalid", http=None)  # type: ignore[arg-type]
        self.mode = "ok"  # "ok" or "fail"
        self.calls: list[list[str]] = []

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(embeddings=True)

    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderError(ProviderErrorCode.UNSUPPORTED, "Not used by these tests.")

    async def health_check(self) -> HealthCheckResult:
        return HealthCheckResult(ok=True, latency_ms=0, message="fake")

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
    ) -> list[list[float]]:
        assert model == MODEL and dimensions == VECTOR_DIMENSIONS
        self.calls.append(list(texts))
        if self.mode == "fail":
            raise ProviderError(ProviderErrorCode.UNAVAILABLE, "Fake embedding service is down.", retryable=True)
        return [fake_vector(text) for text in texts]


class StubRegistry:
    """Same shape as ProviderRegistry.build: returns the adapter and a config with a timeout."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def build(self, db: Any, user_id: uuid.UUID, provider_id: str) -> tuple[AIProvider, Any]:
        return self._provider, SimpleNamespace(timeout_seconds=5.0)


def _enable_semantic(app: Any, provider: AIProvider, *, model: str | None = MODEL) -> None:
    app.state.providers = StubRegistry(provider)
    app.state.settings = app.state.settings.model_copy(update={"gemini_embedding_model": model})


async def _create(client: AsyncClient, content: str, category: str = "fact") -> dict:
    response = await client.post(BASE, json={"category": category, "content": content}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def _has_vector(app: Any, memory_id: str) -> bool:
    async with app.state.session_factory() as db:
        value = await db.scalar(select(Memory.embedding.is_not(None)).where(Memory.id == uuid.UUID(memory_id)))
        return bool(value)


async def _search(client: AsyncClient, query: str, **params: Any) -> dict:
    response = await client.get(BASE, params={"query": query, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def test_stored_vector_round_trips_through_postgres(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _enable_semantic(app, FakeEmbeddingProvider())
    created = await _create(client, "Prefers strong coffee in the morning.")

    async with app.state.session_factory() as db:
        stored = await db.scalar(select(Memory.embedding).where(Memory.id == uuid.UUID(created["id"])))
    assert isinstance(stored, list) and len(stored) == VECTOR_DIMENSIONS
    # pgvector stores 32-bit floats, so compare with a tolerance.
    expected = fake_vector("Prefers strong coffee in the morning.")
    assert all(abs(a - b) < 1e-6 for a, b in zip(stored, expected, strict=True))


async def test_semantic_search_ranks_the_closest_memory_first(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    coffee = await _create(client, "Prefers strong coffee in the morning.", category="preference")
    dog = await _create(client, "Owns a dog named Rex.")
    assert await _has_vector(app, coffee["id"]) and await _has_vector(app, dog["id"])

    result = await _search(client, "a morning drink", semantic="true")
    assert result["search_mode"] == "semantic"
    assert result["fallback_reason"] is None
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [coffee["id"], dog["id"]]


async def test_keyword_search_is_the_default_and_says_so(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _enable_semantic(app, FakeEmbeddingProvider())
    await _create(client, "Prefers strong coffee in the morning.")
    await _create(client, "Owns a dog named Rex.")

    result = await _search(client, "coffee")
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] is None
    assert [item["content"] for item in result["items"]] == ["Prefers strong coffee in the morning."]

    plain = (await client.get(BASE)).json()
    assert plain["search_mode"] == "keyword" and plain["fallback_reason"] is None and plain["total"] == 2


async def test_semantic_request_without_an_embedding_model_reports_the_reason(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake, model=None)
    await _create(client, "Prefers strong coffee in the morning.")

    result = await _search(client, "coffee", semantic="true")
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] == "no_embedding_model"
    assert [item["content"] for item in result["items"]] == ["Prefers strong coffee in the morning."]
    assert fake.calls == []


async def test_semantic_request_without_a_query_is_reported(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _enable_semantic(app, FakeEmbeddingProvider())
    await _create(client, "Owns a dog named Rex.")
    result = (await client.get(BASE, params={"semantic": "true"})).json()
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] == "no_query"
    assert result["total"] == 1


async def test_provider_error_during_create_still_saves_the_memory(
    client: AsyncClient, app: Any, caplog: pytest.LogCaptureFixture
) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    fake.mode = "fail"
    _enable_semantic(app, fake)
    caplog.set_level(logging.WARNING, logger="jarvis.memory")

    created = await _create(client, "Plays chess on Fridays.")
    assert created["content"] == "Plays chess on Fridays."
    assert await _has_vector(app, created["id"]) is False
    warnings = [record for record in caplog.records if record.name == "jarvis.memory"]
    assert warnings and warnings[0].levelno == logging.WARNING
    assert getattr(warnings[0], "reason", None) == "provider_unavailable"
    # The memory text and the provider message are never logged.
    assert "Plays chess" not in caplog.text
    assert "Fake embedding service is down" not in caplog.text


async def test_memories_without_an_embedding_are_still_found_by_keyword(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    coffee = await _create(client, "Prefers strong coffee in the morning.")
    fake.mode = "fail"
    chess = await _create(client, "Plays chess on Fridays.")
    fake.mode = "ok"

    # The unembedded memory matches by keyword, so it is listed first. The embedded memory follows.
    by_keyword = await _search(client, "chess", semantic="true")
    assert by_keyword["search_mode"] == "semantic"
    assert [item["id"] for item in by_keyword["items"]] == [chess["id"], coffee["id"]]

    # Without a keyword match the unembedded memory is not returned at all.
    ranked = await _search(client, "a morning drink", semantic="true")
    assert [item["id"] for item in ranked["items"]] == [coffee["id"]]


async def test_no_stored_embeddings_is_reported_without_calling_the_provider(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    fake.mode = "fail"
    _enable_semantic(app, fake)
    await _create(client, "Plays chess on Fridays.")
    fake.mode = "ok"
    fake.calls.clear()

    result = await _search(client, "chess", semantic="true")
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] == "no_stored_embeddings"
    assert fake.calls == []


async def test_provider_failure_on_the_query_falls_back_with_a_reason(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    await _create(client, "Prefers strong coffee in the morning.")
    fake.mode = "fail"

    result = await _search(client, "coffee", semantic="true")
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] == "provider_unavailable"
    assert [item["content"] for item in result["items"]] == ["Prefers strong coffee in the morning."]


async def test_unconfigured_gemini_key_reports_provider_not_configured(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    await _create(client, "Prefers strong coffee in the morning.")

    # The real registry has no saved key for this user, so the query cannot be embedded.
    app.state.providers = ProviderRegistry(
        settings=app.state.settings,
        secret_box=app.state.secret_box,
        http=httpx.AsyncClient(),
        session_factory=app.state.session_factory,
        events=app.state.events,
    )
    result = await _search(client, "coffee", semantic="true")
    assert result["search_mode"] == "keyword"
    assert result["fallback_reason"] == "provider_not_configured"


async def test_dimension_mismatch_is_refused_at_runtime(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    app.state.settings = app.state.settings.model_copy(update={"embedding_dimensions": 1536})

    created = await _create(client, "Prefers strong coffee in the morning.")
    assert await _has_vector(app, created["id"]) is False
    result = await _search(client, "coffee", semantic="true")
    assert result["fallback_reason"] == "invalid_embedding_dimensions"
    assert fake.calls == []


async def test_reindex_embeds_only_the_current_users_memories(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    fake.mode = "fail"
    first = await _create(client, "Prefers strong coffee in the morning.")
    second = await _create(client, "Owns a dog named Rex.")

    async with app.state.session_factory() as db:
        stranger = User(email="stranger@example.com", display_name="Stranger", password_hash="!", role="owner", is_active=True)
        db.add(stranger)
        await db.flush()
        foreign = await memory_service.create_memory(db, user_id=stranger.id, category="fact", content="Plays tea games.")
        await db.commit()
        foreign_id = str(foreign.id)
    assert not await _has_vector(app, first["id"]) and not await _has_vector(app, foreign_id)

    fake.mode = "ok"
    fake.calls.clear()
    response = await client.post(f"{BASE}/reindex", headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 2, "remaining": 0}
    assert sorted(text for call in fake.calls for text in call) == sorted(
        ["Prefers strong coffee in the morning.", "Owns a dog named Rex."]
    )
    assert await _has_vector(app, first["id"]) and await _has_vector(app, second["id"])
    assert await _has_vector(app, foreign_id) is False


async def test_reindex_processes_at_most_200_and_in_provider_batches(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    async with app.state.session_factory() as db:
        for index in range(205):
            await memory_service.create_memory(
                db, user_id=uuid.UUID(owner["id"]), category="fact", content=f"Fact number {index} about tea."
            )
        await db.commit()

    first = (await client.post(f"{BASE}/reindex", headers=csrf_headers(client))).json()
    assert first == {"updated": 200, "remaining": 5}
    assert [len(call) for call in fake.calls] == [EMBED_BATCH_LIMIT, EMBED_BATCH_LIMIT]

    second = (await client.post(f"{BASE}/reindex", headers=csrf_headers(client))).json()
    assert second == {"updated": 5, "remaining": 0}


async def test_reindex_reports_provider_failure_and_configuration_problems(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    fake.mode = "fail"
    await _create(client, "Prefers strong coffee in the morning.")

    failed = await client.post(f"{BASE}/reindex", headers=csrf_headers(client))
    assert failed.status_code == 503
    error = failed.json()["error"]
    assert error["code"] == "embedding_unavailable"
    assert error["details"] == {"reason": "provider_unavailable", "updated": 0, "remaining": 1}
    assert "Fake embedding service" not in failed.text

    _enable_semantic(app, fake, model=None)
    unconfigured = await client.post(f"{BASE}/reindex", headers=csrf_headers(client))
    assert unconfigured.status_code == 409
    assert unconfigured.json()["error"]["details"]["reason"] == "no_embedding_model"


async def test_another_users_memories_are_never_ranked(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    mine = await _create(client, "Owns a dog named Rex.")

    async with app.state.session_factory() as db:
        stranger = User(email="other@example.com", display_name="Other", password_hash="!", role="owner", is_active=True)
        db.add(stranger)
        await db.flush()
        foreign = await memory_service.create_memory(
            db,
            user_id=stranger.id,
            category="preference",
            content="Strong coffee every morning.",
            embedding_source=memory_service.build_embedding_source(app.state.settings, app.state.providers),
        )
        await db.commit()
        foreign_id = str(foreign.id)

    result = await _search(client, "a morning drink", semantic="true")
    assert [item["id"] for item in result["items"]] == [mine["id"]]
    assert foreign_id not in json.dumps(result)


async def test_query_text_with_sql_metacharacters_is_bound_safely(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    await _create(client, "Prefers strong coffee in the morning.")
    await _create(client, "Owns a dog named Rex.")
    hostile = "x'); DROP TABLE memories; --%_\\ \" OR 1=1 --"

    semantic = await client.get(BASE, params={"query": hostile, "semantic": "true"})
    keyword = await client.get(BASE, params={"query": hostile})
    assert semantic.status_code == 200 and keyword.status_code == 200
    assert keyword.json()["total"] == 0
    assert semantic.json()["search_mode"] == "semantic"

    async with app.state.session_factory() as db:
        remaining = await db.scalar(select(func.count(Memory.id)))
    assert remaining == 2


async def test_editing_content_replaces_the_vector_and_a_failed_reembed_clears_it(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    fake = FakeEmbeddingProvider()
    _enable_semantic(app, fake)
    created = await _create(client, "Owns a dog named Rex.")
    assert await _has_vector(app, created["id"])

    fake.mode = "fail"
    edited = await client.patch(
        f"{BASE}/{created['id']}", json={"content": "Likes strong coffee."}, headers=csrf_headers(client)
    )
    assert edited.status_code == 200
    assert await _has_vector(app, created["id"]) is False

    category_only = await client.patch(
        f"{BASE}/{created['id']}", json={"category": "preference"}, headers=csrf_headers(client)
    )
    assert category_only.status_code == 200
    assert await _has_vector(app, created["id"]) is False

    fake.mode = "ok"
    fake.calls.clear()
    edited_again = await client.patch(
        f"{BASE}/{created['id']}", json={"content": "Prefers strong coffee."}, headers=csrf_headers(client)
    )
    assert edited_again.status_code == 200
    assert await _has_vector(app, created["id"])
    assert fake.calls == [["Prefers strong coffee."]]


async def test_real_gemini_adapter_sends_the_batch_embedding_request(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "generativelanguage.googleapis.com":
            return httpx.Response(500, json={})
        seen.append(request)
        body = json.loads(request.content)
        embeddings = [{"values": fake_vector(item["content"]["parts"][0]["text"])} for item in body["requests"]]
        return httpx.Response(200, json={"embeddings": embeddings})

    registry = ProviderRegistry(
        settings=app.state.settings,
        secret_box=app.state.secret_box,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        session_factory=app.state.session_factory,
        events=app.state.events,
    )
    async with app.state.session_factory() as db:
        await registry.set_credential(db, uuid.UUID(owner["id"]), "gemini", GEMINI_KEY)
        await db.commit()
    app.state.providers = registry
    app.state.settings = app.state.settings.model_copy(update={"gemini_embedding_model": MODEL})

    created = await _create(client, "Prefers strong coffee in the morning.")
    assert await _has_vector(app, created["id"])
    result = await _search(client, "a morning drink", semantic="true")
    assert result["search_mode"] == "semantic"
    assert result["items"][0]["id"] == created["id"]

    embed_calls = [request for request in seen if request.url.path.endswith(":batchEmbedContents")]
    assert len(embed_calls) == 2
    for request in embed_calls:
        assert request.url.path == f"/v1beta/models/{MODEL}:batchEmbedContents"
        assert request.headers["x-goog-api-key"] == GEMINI_KEY
        assert GEMINI_KEY not in str(request.url)
        body = json.loads(request.content)
        assert set(body) == {"requests"}
        for item in body["requests"]:
            assert item["model"] == f"models/{MODEL}"
            assert item["outputDimensionality"] == VECTOR_DIMENSIONS
            assert set(item["content"]) == {"parts"}


async def test_gemini_adapter_rejects_wrong_sized_vectors_and_too_large_batches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [{"values": [0.1] * 767}]})

    adapter = GeminiProvider(
        api_key=GEMINI_KEY,
        base_url="https://generativelanguage.googleapis.com",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderError) as too_large:
        await adapter.embed(["x"] * (EMBED_BATCH_LIMIT + 1), model=MODEL, dimensions=VECTOR_DIMENSIONS)
    assert too_large.value.code is ProviderErrorCode.INVALID_REQUEST

    with pytest.raises(ProviderError) as wrong_size:
        await adapter.embed(["hello"], model=MODEL, dimensions=VECTOR_DIMENSIONS)
    assert wrong_size.value.code is ProviderErrorCode.UNKNOWN
    assert GEMINI_KEY not in wrong_size.value.message


async def test_base_provider_embed_is_unsupported_for_hugging_face() -> None:
    from app.providers.huggingface import HuggingFaceProvider

    adapter = HuggingFaceProvider(
        api_key="hf_x", base_url="https://router.invalid", http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert adapter.get_capabilities().embeddings is False
    with pytest.raises(ProviderError) as info:
        await adapter.embed(["hello"], model=MODEL, dimensions=VECTOR_DIMENSIONS)
    assert info.value.code is ProviderErrorCode.UNSUPPORTED


