"""GET /api/v1/account/export: the holder's data as JSON. No secret may appear, and no other user's data may."""

from __future__ import annotations

import json
import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient

from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
PLANTED_KEY = "AIzaPLANTED-provider-key-0123456789"
FORBIDDEN_KEYS = {
    "password",
    "password_hash",
    "token",
    "token_hash",
    "device_secret",
    "device_secret_hash",
    "credential_ciphertext",
    "credential_hint",
    "ciphertext",
    "api_key",
    "secret",
    "ip_address",
}


def _keys(value: Any) -> set[str]:
    """Every object key anywhere in the document."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


async def test_export_holds_the_account_and_no_secret(client: AsyncClient) -> None:
    owner = await create_owner(client)
    created = await client.post(f"{API}/conversations", json={"title": "Holiday plans"}, headers=csrf_headers(client))
    assert created.status_code == 201
    stored = await client.put(f"{API}/providers/gemini/credential", json={"api_key": PLANTED_KEY}, headers=csrf_headers(client))
    assert stored.status_code == 200, stored.text

    response = await client.get(f"{API}/account/export")

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="jarvis-account-export.json"'
    assert response.headers["cache-control"] == "no-store"
    assert PLANTED_KEY not in response.text
    document = json.loads(response.text)
    assert document["format_version"] == 1
    assert document["sections"] == sorted(document["sections"])
    assert set(document["sections"]) == {
        "profile", "settings", "conversations", "memories", "tasks", "devices", "integrations", "providers", "schedules", "notifications", "audit",
    }
    assert document["profile"]["email"] == "owner@example.com"
    assert document["profile"]["id"] == owner["id"]
    assert [item["title"] for item in document["conversations"]] == ["Holiday plans"]
    assert _keys(document) & FORBIDDEN_KEYS == set()


async def test_provider_entries_say_whether_a_key_exists_without_showing_it(client: AsyncClient) -> None:
    await create_owner(client)
    await client.put(f"{API}/providers/gemini/credential", json={"api_key": PLANTED_KEY}, headers=csrf_headers(client))
    document = (await client.get(f"{API}/account/export")).json()
    assert {"provider_id", "enabled", "selected_model"} == set(document["providers"][0])


async def test_export_is_written_to_the_audit_trail_without_the_ip(client: AsyncClient) -> None:
    await create_owner(client)
    await client.get(f"{API}/account/export")
    trail = (await client.get(f"{API}/audit", params={"action": "account.exported"})).json()
    assert trail["total"] >= 1
    assert all("ip_address" not in item for item in trail["items"])


async def test_a_member_receives_only_their_own_data(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await client.post(f"{API}/conversations", json={"title": "Owner secret plan"}, headers=csrf_headers(client))
    created = await client.post(
        f"{API}/users",
        json={"email": "holder@example.com", "display_name": "Holder", "password": "holder-password-one", "role": "member"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as member:
        await member.get(f"{API}/auth/status")
        login = await member.post(
            f"{API}/auth/login", json={"email": "holder@example.com", "password": "holder-password-one"}, headers=csrf_headers(member)
        )
        assert login.status_code == 200, login.text
        document = (await member.get(f"{API}/account/export")).json()

    assert document["profile"]["email"] == "holder@example.com"
    assert document["conversations"] == []
    assert "Owner secret plan" not in json.dumps(document)
    assert uuid.UUID(document["profile"]["id"])
