"""Regression tests for first-run setup and API response headers."""

from __future__ import annotations

import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.identity import User
from tests.conftest import csrf_headers


async def test_concurrent_setup_requests_create_exactly_one_owner(client: AsyncClient, app: object) -> None:
    await client.get("/api/v1/auth/status")
    headers = csrf_headers(client) or {}
    bodies = [
        {"email": f"owner{index}@example.com", "password": "correct-horse-battery", "display_name": "Owner"}
        for index in range(2)
    ]
    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/setup", json=body, headers=headers) for body in bodies)
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        count = await db.scalar(select(func.count(User.id)))
    assert count == 1


async def test_api_responses_are_not_stored_and_carry_a_content_security_policy(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/status")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
