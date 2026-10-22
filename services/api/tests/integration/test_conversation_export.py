"""Conversation export: attachment headers, filename sanitising, Markdown and JSON bodies, owner-only access."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.conversation_service import create_conversation, new_message
from app.services.export import export_filename
from tests.conftest import create_owner, csrf_headers
from tests.integration.test_conversations_api import add_message, add_user

API = "/api/v1"


async def _conversation(client: AsyncClient, title: str) -> str:
    response = await client.post(f"{API}/conversations", json={"title": title}, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Trip: plans/2026 ✓ 'quoted'", "Trip plans2026  quoted.md"),
        ("../../etc/passwd", "etcpasswd.md"),
        ('say "hi"\\there', "say hithere.md"),
        ("  padded  ", "padded.md"),
        ("___-A1 ", "___-A1.md"),
        ("a" * 100, "a" * 60 + ".md"),
    ],
)
def test_filename_keeps_only_safe_characters_and_is_cut_to_sixty(title: str, expected: str) -> None:
    assert export_filename(title, uuid.uuid4(), "markdown") == expected


def test_filename_falls_back_to_the_conversation_id_when_nothing_is_left() -> None:
    conversation_id = uuid.uuid4()
    assert export_filename("日本語 🙂", conversation_id, "json") == f"{conversation_id}.json"


@pytest.mark.parametrize(
    ("title", "expected_name"),
    [
        ("Trip: plans/2026", "Trip plans2026.md"),
        ("../../etc/passwd", "etcpasswd.md"),
        ('say "hi"', "say hi.md"),
        ("日本語", "{id}.md"),
    ],
)
async def test_attachment_headers_carry_the_sanitised_filename(
    client: AsyncClient, title: str, expected_name: str
) -> None:
    await create_owner(client)
    cid = await _conversation(client, title)
    response = await client.get(f"{API}/conversations/{cid}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="{expected_name.format(id=cid)}"'
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["cache-control"] == "no-store"


async def test_markdown_body_labels_each_role(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    cid = await _conversation(client, "Trip plans")
    await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="Hello there")
    await add_message(app, user_id=user_id, conversation_id=cid, role="assistant", content="Hi!")
    await add_message(app, user_id=user_id, conversation_id=cid, role="tool", content="launch_app result: ok")

    response = await client.get(f"{API}/conversations/{cid}/export", params={"format": "markdown"})
    assert response.status_code == 200
    assert response.text == (
        "# Trip plans\n\n"
        "### User\n\nHello there\n\n"
        "### Assistant\n\nHi!\n\n"
        "### Tool\n\n_Tool output_\n\nlaunch_app result: ok\n"
    )


async def test_json_body_has_the_documented_shape_and_no_provider_details(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    cid = await _conversation(client, "Notes")
    async with app.state.session_factory() as db:
        db.add(
            new_message(
                user_id=user_id,
                conversation_id=uuid.UUID(cid),
                role="assistant",
                content="Sure thing.",
                provider_id="gemini",
                model="gemini-model-x",
                meta={"latency_ms": 12, "usage": {"input_tokens": 5}},
            )
        )
        await db.commit()
    await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="Remember milk")

    response = await client.get(f"{API}/conversations/{cid}/export", params={"format": "json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == 'attachment; filename="Notes.json"'
    body = response.json()
    assert set(body) == {"id", "title", "created_at", "messages"}
    assert body["id"] == cid and body["title"] == "Notes"
    assert [set(item) for item in body["messages"]] == [{"role", "content", "created_at", "status"}] * 2
    assert [(item["role"], item["content"], item["status"]) for item in body["messages"]] == [
        ("assistant", "Sure thing.", "complete"),
        ("user", "Remember milk", "complete"),
    ]
    raw = response.text
    for leaked in ("gemini", "latency_ms", "input_tokens", "provider", "model"):
        assert leaked not in raw


async def test_export_is_owner_only(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    other_id = await add_user(app, "second@example.com", "second-user-password")
    async with app.state.session_factory() as db:
        conversation = await create_conversation(db, user_id=other_id, title="Private")
        other_cid = str(conversation.id)
        await db.commit()
    await add_message(app, user_id=other_id, conversation_id=other_cid, role="user", content="secret")

    for fmt in ("markdown", "json"):
        response = await client.get(f"{API}/conversations/{other_cid}/export", params={"format": fmt})
        assert response.status_code == 404
        assert "secret" not in response.text
    assert (await client.get(f"{API}/conversations/{uuid.uuid4()}/export")).status_code == 404
    assert (await client.get(f"{API}/conversations/not-a-uuid/export")).status_code == 404


async def test_unknown_format_is_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    cid = await _conversation(client, "Any")
    assert (await client.get(f"{API}/conversations/{cid}/export", params={"format": "pdf"})).status_code == 422


async def test_export_keeps_messages_in_order(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    cid = await _conversation(client, "Order")
    for index in range(5):
        await add_message(app, user_id=user_id, conversation_id=cid, role="user", content=f"line {index}")

    body = (await client.get(f"{API}/conversations/{cid}/export", params={"format": "json"})).json()
    assert [item["content"] for item in body["messages"]] == [f"line {index}" for index in range(5)]
