"""Memory API and writer rules against real PostgreSQL: sensitive content, history, confirmation and isolation."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.models.identity import User, UserPreference
from app.models.memory import MemoryHistory
from app.services import memory_service
from tests.conftest import create_owner, csrf_headers

BASE = "/api/v1/memories"


async def _set_memory_enabled(app: Any, user_id: str, enabled: bool) -> None:
    async with app.state.session_factory() as db:
        row = await db.get(UserPreference, uuid.UUID(user_id))
        document = {"memory": {"enabled": enabled}}
        if row is None:
            db.add(UserPreference(user_id=uuid.UUID(user_id), settings=document))
        else:
            row.settings = document
        await db.commit()


async def _history(app: Any, memory_id: str) -> list[MemoryHistory]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(
            select(MemoryHistory).where(MemoryHistory.memory_id == uuid.UUID(memory_id)).order_by(MemoryHistory.id)
        )
        return list(rows.all())


async def _create(client: AsyncClient, content: str, category: str = "fact") -> dict:
    response = await client.post(BASE, json={"category": category, "content": content}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_list_and_history_row_is_written(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    created = await _create(client, "Prefers dark mode and short answers.", category="preference")
    assert created["category"] == "preference" and created["source"] == "manual"

    listing = (await client.get(BASE)).json()
    assert listing["total"] == 1 and listing["enabled"] is True
    assert listing["items"][0]["content"] == "Prefers dark mode and short answers."

    history = await _history(app, created["id"])
    assert [row.action for row in history] == ["created"]
    assert history[0].new_content == "Prefers dark mode and short answers."


async def test_sensitive_content_is_rejected_and_nothing_is_stored(client: AsyncClient) -> None:
    await create_owner(client)
    samples = [
        "my password is hunter2-long-value",
        "password: correct-horse-9",
        "AIzaSyD-example-key-0123456789abcdef",
        "hf_abcdefghijklmnopqrstuvwxyz",
        "sk-proj-abcdefghijklmnop",
        "Authorization: Bearer abcdef.ghijkl",
        "-----BEGIN RSA PRIVATE KEY-----",
    ]
    for text in samples:
        response = await client.post(BASE, json={"category": "fact", "content": text}, headers=csrf_headers(client))
        assert response.status_code == 422, text
        assert response.json()["error"]["code"] == "sensitive_content"
        assert text not in response.text
    assert (await client.get(BASE)).json()["total"] == 0


async def test_disabled_memory_refuses_create_and_says_so(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _set_memory_enabled(app, owner["id"], False)
    response = await client.post(BASE, json={"category": "fact", "content": "A fact"}, headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "memory_disabled"
    listing = (await client.get(BASE)).json()
    assert listing["enabled"] is False and listing["total"] == 0


async def test_edit_delete_keeps_history_and_bulk_clear_needs_exact_confirmation(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    first = await _create(client, "Office is on floor two.")
    second = await _create(client, "Project codename is Atlas.", category="project")

    edited = await client.patch(
        f"{BASE}/{first['id']}", json={"content": "Office moved to floor three."}, headers=csrf_headers(client)
    )
    assert edited.status_code == 200 and edited.json()["content"] == "Office moved to floor three."
    edits = await _history(app, first["id"])
    assert [row.action for row in edits] == ["created", "edited"]
    assert edits[1].previous_content == "Office is on floor two."
    assert edits[1].new_content == "Office moved to floor three."

    deleted = await client.delete(f"{BASE}/{first['id']}", headers=csrf_headers(client))
    assert deleted.status_code == 204
    history = await client.get(f"{BASE}/{first['id']}/history")
    assert history.status_code == 200
    assert [item["action"] for item in history.json()["items"]] == ["created", "edited", "deleted"]
    assert history.json()["items"][2]["previous_content"] == "Office moved to floor three."

    wrong = await client.request("DELETE", BASE, json={"confirm": "delete all"}, headers=csrf_headers(client))
    assert wrong.status_code == 422
    missing = await client.request("DELETE", BASE, json={}, headers=csrf_headers(client))
    assert missing.status_code == 422
    assert (await client.get(BASE)).json()["total"] == 1

    cleared = await client.request(
        "DELETE", BASE, json={"confirm": "DELETE ALL MEMORIES"}, headers=csrf_headers(client)
    )
    assert cleared.status_code == 200 and cleared.json() == {"deleted": 1}
    assert (await client.get(BASE)).json()["total"] == 0
    assert [row.action for row in await _history(app, second["id"])] == ["created", "cleared"]


async def test_export_is_a_json_attachment(client: AsyncClient) -> None:
    await create_owner(client)
    await _create(client, "Likes tea.", category="preference")
    response = await client.get(f"{BASE}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["exported_at"].endswith("Z")
    assert [item["content"] for item in body["memories"]] == ["Likes tea."]


async def test_memories_are_isolated_between_users(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    async with app.state.session_factory() as db:
        other = User(email="other@example.com", display_name="Other", password_hash="!", role="owner", is_active=True)
        db.add(other)
        await db.flush()
        foreign = await memory_service.create_memory(
            db, user_id=other.id, category="fact", content="Their private note."
        )
        await db.commit()
        foreign_id = str(foreign.id)

    listing = (await client.get(BASE)).json()
    assert listing["total"] == 0
    assert foreign_id not in str(listing)
    assert (await client.get(f"{BASE}/{foreign_id}/history")).status_code == 404
    assert (
        await client.patch(f"{BASE}/{foreign_id}", json={"content": "x"}, headers=csrf_headers(client))
    ).status_code == 404
    assert (await client.delete(f"{BASE}/{foreign_id}", headers=csrf_headers(client))).status_code == 404
    assert (await client.get(f"{BASE}/not-a-uuid/history")).status_code == 404


async def test_prompt_memories_are_empty_when_disabled_and_never_cross_users(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _create(client, "Owner fact one.")
    await _create(client, "Owner fact two.")
    async with app.state.session_factory() as db:
        prompt = await memory_service.prompt_memories(db, uuid.UUID(owner["id"]), limit=20)
        assert {memory.content for memory in prompt} == {"Owner fact one.", "Owner fact two."}
    await _set_memory_enabled(app, owner["id"], False)
    async with app.state.session_factory() as db:
        assert await memory_service.prompt_memories(db, uuid.UUID(owner["id"])) == []
