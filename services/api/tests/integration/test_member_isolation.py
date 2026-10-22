"""Member isolation: a member sees none of the owner's devices, tasks, conversations, memories or audit events.

Two members are created through the owner's /users API, so the tests also cover one member against another.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
PASSWORD = "member-password-one"
AGENT_CAPABILITIES = ["open_url", "launch_app", "system_info"]
AGENT_RESOURCES: dict[str, Any] = {"apps": [], "urls": [], "folders": [], "projects": [], "files": []}


def _new_client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})


@asynccontextmanager
async def signed_in(app: Any, email: str) -> AsyncIterator[AsyncClient]:
    client = _new_client(app)
    try:
        response = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client
    finally:
        await client.aclose()


async def _create_member(owner: AsyncClient, email: str) -> None:
    response = await owner.post(
        f"{API}/users",
        json={"email": email, "display_name": email.split("@")[0], "password": PASSWORD, "role": "member"},
        headers=csrf_headers(owner),
    )
    assert response.status_code == 201, response.text


async def _seed_owner_data(owner: AsyncClient, app: Any) -> dict[str, str]:
    """Creates one device, task, conversation and memory for the owner. Returns their ids."""
    code = (await owner.post(f"{API}/devices/pairing-codes", headers=csrf_headers(owner))).json()["code"]
    async with _new_client(app) as agent:
        paired = await agent.post(
            f"{API}/agent/pair",
            json={
                "code": code,
                "device_name": "Owner Laptop",
                "device_type": "laptop",
                "os": "windows",
                "os_version": "10.0.22631",
                "agent_version": "1.0.0",
                "capabilities": AGENT_CAPABILITIES,
                "resources": AGENT_RESOURCES,
            },
        )
    assert paired.status_code == 200, paired.text

    task = await owner.post(f"{API}/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(owner))
    assert task.status_code == 201, task.text
    conversation = await owner.post(f"{API}/conversations", json={"title": "Owner plans"}, headers=csrf_headers(owner))
    assert conversation.status_code == 201, conversation.text
    memory = await owner.post(
        f"{API}/memories", json={"category": "fact", "content": "Owner secret fact."}, headers=csrf_headers(owner)
    )
    assert memory.status_code == 201, memory.text

    devices = (await owner.get(f"{API}/devices")).json()["items"]
    assert len(devices) == 1
    return {
        "device": devices[0]["id"],
        "task": task.json()["id"],
        "conversation": conversation.json()["id"],
        "memory": memory.json()["id"],
    }


def _assert_no_leak(body: str, ids: list[str]) -> None:
    leaked = [value for value in ids if value in body]
    assert leaked == [], f"owner identifiers visible to a member: {leaked}"


async def test_member_sees_none_of_the_owners_devices_tasks_conversations_or_memories(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    ids = await _seed_owner_data(client, app)
    owner_ids = [owner["id"], *ids.values()]
    await _create_member(client, "member@example.com")

    async with signed_in(app, "member@example.com") as member:
        listings = {
            "devices": await member.get(f"{API}/devices"),
            "tasks": await member.get(f"{API}/tasks"),
            "conversations": await member.get(f"{API}/conversations"),
            "memories": await member.get(f"{API}/memories"),
            "audit": await member.get(f"{API}/audit"),
        }
        for name, response in listings.items():
            assert response.status_code == 200, (name, response.text)
            _assert_no_leak(response.text, owner_ids)
        assert listings["devices"].json() == {"items": []}
        assert listings["tasks"].json()["total"] == 0
        assert listings["conversations"].json() == {"items": [], "total": 0}
        assert listings["memories"].json()["total"] == 0

        headers = csrf_headers(member)
        # Reads of the owner's rows by id look exactly like rows that do not exist.
        assert (await member.get(f"{API}/tasks/{ids['task']}")).status_code == 404
        assert (await member.post(f"{API}/tasks/{ids['task']}/cancel", headers=headers)).status_code == 404
        assert (await member.get(f"{API}/conversations/{ids['conversation']}")).status_code == 404
        assert (
            await member.patch(f"{API}/conversations/{ids['conversation']}", json={"title": "stolen"}, headers=headers)
        ).status_code == 404
        assert (await member.delete(f"{API}/conversations/{ids['conversation']}", headers=headers)).status_code == 404
        assert (await member.get(f"{API}/memories/{ids['memory']}/history")).status_code == 404
        assert (
            await member.patch(f"{API}/memories/{ids['memory']}", json={"content": "stolen"}, headers=headers)
        ).status_code == 404
        assert (await member.delete(f"{API}/memories/{ids['memory']}", headers=headers)).status_code == 404
        assert (await member.post(f"{API}/devices/{ids['device']}/ping", headers=headers)).status_code == 404
        assert (await member.post(f"{API}/devices/{ids['device']}/revoke", headers=headers)).status_code == 404
        assert (await member.delete(f"{API}/devices/{ids['device']}", headers=headers)).status_code == 404

    # The owner's data is untouched by everything the member tried.
    owner_devices = (await client.get(f"{API}/devices")).json()["items"]
    assert [device["id"] for device in owner_devices] == [ids["device"]] and owner_devices[0]["revoked_at"] is None
    assert (await client.get(f"{API}/tasks/{ids['task']}")).status_code == 200
    assert (await client.get(f"{API}/conversations/{ids['conversation']}")).json()["conversation"]["title"] == "Owner plans"
    memories = (await client.get(f"{API}/memories")).json()
    assert [item["content"] for item in memories["items"]] == ["Owner secret fact."]


async def test_member_audit_trail_excludes_the_owners_events(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _create_member(client, "member@example.com")
    async with signed_in(app, "member@example.com") as member:
        actions = [item["action"] for item in (await member.get(f"{API}/audit")).json()["items"]]
    assert "auth.login" in actions
    assert "auth.setup" not in actions and "users.created" not in actions


async def test_members_cannot_see_or_change_each_other(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _create_member(client, "alice@example.com")
    await _create_member(client, "bob@example.com")

    async with signed_in(app, "alice@example.com") as alice, signed_in(app, "bob@example.com") as bob:
        alice_conversation = await alice.post(f"{API}/conversations", json={"title": "Alice plans"}, headers=csrf_headers(alice))
        alice_memory = await alice.post(
            f"{API}/memories", json={"category": "fact", "content": "Alice likes tea."}, headers=csrf_headers(alice)
        )
        alice_task = await alice.post(f"{API}/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(alice))
        assert alice_conversation.status_code == alice_memory.status_code == alice_task.status_code == 201
        alice_ids = [alice_conversation.json()["id"], alice_memory.json()["id"], alice_task.json()["id"]]

        # Alice sees her own rows.
        assert (await alice.get(f"{API}/conversations")).json()["total"] == 1
        assert (await alice.get(f"{API}/memories")).json()["total"] == 1
        assert (await alice.get(f"{API}/tasks")).json()["total"] == 1

        # Bob sees none of them and cannot reach them by id.
        headers = csrf_headers(bob)
        assert (await bob.get(f"{API}/conversations")).json() == {"items": [], "total": 0}
        assert (await bob.get(f"{API}/memories")).json()["total"] == 0
        assert (await bob.get(f"{API}/tasks")).json()["total"] == 0
        _assert_no_leak(
            (await bob.get(f"{API}/conversations")).text + (await bob.get(f"{API}/memories")).text,
            alice_ids,
        )
        assert (await bob.get(f"{API}/conversations/{alice_ids[0]}")).status_code == 404
        assert (await bob.get(f"{API}/memories/{alice_ids[1]}/history")).status_code == 404
        assert (await bob.get(f"{API}/tasks/{alice_ids[2]}")).status_code == 404
        assert (await bob.delete(f"{API}/memories/{alice_ids[1]}", headers=headers)).status_code == 404
        assert (await bob.patch(f"{API}/conversations/{alice_ids[0]}", json={"title": "x"}, headers=headers)).status_code == 404

    # Both members are invisible to the owner's per-user views as well: the owner sees only the owner's rows.
    assert (await client.get(f"{API}/conversations")).json()["total"] == 0
    assert (await client.get(f"{API}/memories")).json()["total"] == 0
    assert (await client.get(f"{API}/tasks")).json()["total"] == 0
