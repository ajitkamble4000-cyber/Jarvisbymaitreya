"""The developer log and configuration checks are owner-only. The log buffer is process-wide, so a member must not read it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
MEMBER_EMAIL = "member@example.com"
MEMBER_PASSWORD = "member-password-one"


@asynccontextmanager
async def _signed_in_member(app: Any) -> AsyncIterator[AsyncClient]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    try:
        response = await client.post(f"{API}/auth/login", json={"email": MEMBER_EMAIL, "password": MEMBER_PASSWORD})
        assert response.status_code == 200, response.text
        yield client
    finally:
        await client.aclose()


async def test_member_cannot_read_logs_or_config_check(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    created = await client.post(
        f"{API}/users",
        json={"email": MEMBER_EMAIL, "display_name": "Sam", "password": MEMBER_PASSWORD, "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text

    async with _signed_in_member(app) as member:
        for path in ("/developer/logs", "/developer/config-check"):
            denied = await member.get(f"{API}{path}")
            assert denied.status_code == 403, path
            assert denied.json()["error"]["code"] == "forbidden"


async def test_owner_still_reads_logs_and_config_check(client: AsyncClient) -> None:
    await create_owner(client)
    assert (await client.get(f"{API}/developer/logs")).status_code == 200
    assert (await client.get(f"{API}/developer/config-check")).status_code == 200
