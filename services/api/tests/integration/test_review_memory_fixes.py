"""Regression test for the memory review finding: a whitespace-only semantic query is an invalid query, so the response
names ``no_query`` and does not reach the embedder. Runs against real PostgreSQL through the HTTP client."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import create_owner


async def test_a_whitespace_only_semantic_query_reports_no_query(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.get("/api/v1/memories", params={"query": "   ", "semantic": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["search_mode"] == "keyword"
    assert body["fallback_reason"] == "no_query"
