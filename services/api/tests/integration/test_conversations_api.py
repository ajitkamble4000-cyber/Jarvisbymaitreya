"""Conversation CRUD, search, edit, regenerate and cross-user isolation against real PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models.conversation import Message
from app.models.identity import User
from app.security.passwords import hash_password
from app.services.conversation_service import new_message
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers
from tests.integration.test_chat_stream import (  # chat_env is imported as a fixture; the parameter names below use it
    chat_env,  # noqa: F401
    event_names,
    new_conversation,
    parse_frames,
    send,
    stored_messages,
)

API = "/api/v1"
SECOND_EMAIL = "second@example.com"
SECOND_PASSWORD = "second-user-password"


async def add_message(app: Any, *, user_id: uuid.UUID, conversation_id: str, role: str, content: str) -> uuid.UUID:
    """Inserts a finished message directly, so ordering is exact and no provider is involved."""
    async with app.state.session_factory() as db:
        row = new_message(
            user_id=user_id,
            conversation_id=uuid.UUID(conversation_id),
            role=role,
            content=content,
        )
        db.add(row)
        await db.commit()
        return row.id


async def add_user(app: Any, email: str, password: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(
            email=email,
            display_name="Second user",
            password_hash=hash_password(password),
            role="member",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
    return user_id


async def login_client(app: Any, email: str, password: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


async def owner_user_id(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await create_owner(client))["id"])


async def count_rows(app: Any, conversation_id: str) -> int:
    async with app.state.session_factory() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(Message).where(Message.conversation_id == uuid.UUID(conversation_id))
            )
            or 0
        )


# -- create, read, update, delete -------------------------------------------------------


async def test_create_uses_the_default_title_and_normalises_a_given_one(client: AsyncClient) -> None:
    await create_owner(client)

    default = await client.post(f"{API}/conversations", json={}, headers=csrf_headers(client))
    renamed = await client.post(f"{API}/conversations", json={"title": "  Plan\nmy   week  "}, headers=csrf_headers(client))
    blank = await client.post(f"{API}/conversations", json={"title": "   "}, headers=csrf_headers(client))
    no_body = await client.post(f"{API}/conversations", headers=csrf_headers(client))

    assert default.status_code == 201
    assert default.json()["title"] == "New conversation"
    assert default.json()["archived"] is False
    assert default.json()["provider_id"] is None and default.json()["last_message_at"] is None
    assert renamed.json()["title"] == "Plan my week"
    assert blank.json()["title"] == "New conversation"
    assert no_body.status_code == 201


async def test_create_rejects_unknown_fields(client: AsyncClient) -> None:
    await create_owner(client)

    response = await client.post(f"{API}/conversations", json={"title": "x", "owner_id": "nope"}, headers=csrf_headers(client))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_matches_titles_and_message_text_with_pagination_and_wildcards_escaped(
    client: AsyncClient, app: Any
) -> None:
    user_id = await owner_user_id(client)
    lisbon = await new_conversation(client, "Trip to Lisbon")
    budget = await new_conversation(client, "Budget review")
    garden = await new_conversation(client, "Garden")
    await add_message(app, user_id=user_id, conversation_id=budget, role="user", content="quarterly forecast for the Lisbon office")
    await add_message(app, user_id=user_id, conversation_id=garden, role="user", content="tomatoes and basil")

    by_title_or_body = await client.get(f"{API}/conversations", params={"query": "lisbon"}, headers=csrf_headers(client))
    literal_percent = await client.get(f"{API}/conversations", params={"query": "%"}, headers=csrf_headers(client))
    paged = await client.get(f"{API}/conversations", params={"limit": 1, "offset": 0})

    assert by_title_or_body.status_code == 200
    assert {item["id"] for item in by_title_or_body.json()["items"]} == {lisbon, budget}
    assert by_title_or_body.json()["total"] == 2
    assert literal_percent.json() == {"items": [], "total": 0}
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 1


async def test_archived_filter_separates_active_and_archived_conversations(client: AsyncClient) -> None:
    await create_owner(client)
    active = await new_conversation(client, "Active one")
    archived = await new_conversation(client, "Archived one")

    patched = await client.patch(f"{API}/conversations/{archived}", json={"archived": True}, headers=csrf_headers(client))
    default = await client.get(f"{API}/conversations")
    only_archived = await client.get(f"{API}/conversations", params={"archived": "true"})

    assert patched.json()["archived"] is True
    assert [item["id"] for item in default.json()["items"]] == [active]
    assert [item["id"] for item in only_archived.json()["items"]] == [archived]
    assert only_archived.json()["total"] == 1


async def test_get_returns_the_most_recent_messages_oldest_first_and_honours_limit(client: AsyncClient, app: Any) -> None:
    user_id = await owner_user_id(client)
    cid = await new_conversation(client, "Ordering")
    for index in range(5):
        await add_message(app, user_id=user_id, conversation_id=cid, role="user", content=f"m{index}")

    full = await client.get(f"{API}/conversations/{cid}", headers=csrf_headers(client))
    limited = await client.get(f"{API}/conversations/{cid}", params={"limit": 3})
    invalid = await client.get(f"{API}/conversations/{cid}", params={"limit": 0})

    assert [m["content"] for m in full.json()["messages"]] == ["m0", "m1", "m2", "m3", "m4"]
    assert [m["content"] for m in limited.json()["messages"]] == ["m2", "m3", "m4"]
    assert limited.json()["conversation"]["id"] == cid
    assert invalid.status_code == 422


async def test_rename_and_archive_and_unknown_fields(client: AsyncClient) -> None:
    await create_owner(client)
    cid = await new_conversation(client, "Original")

    renamed = await client.patch(f"{API}/conversations/{cid}", json={"title": "Renamed\nlong title"}, headers=csrf_headers(client))
    unchanged = await client.patch(f"{API}/conversations/{cid}", json={}, headers=csrf_headers(client))
    unknown = await client.patch(f"{API}/conversations/{cid}", json={"provider_id": "gemini"}, headers=csrf_headers(client))

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed long title"
    assert unchanged.json()["title"] == "Renamed long title"
    assert unknown.status_code == 422


async def test_delete_removes_the_conversation_and_its_messages(client: AsyncClient, app: Any) -> None:
    user_id = await owner_user_id(client)
    cid = await new_conversation(client, "To delete")
    await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="bye")

    deleted = await client.delete(f"{API}/conversations/{cid}", headers=csrf_headers(client))
    gone = await client.get(f"{API}/conversations/{cid}")

    assert deleted.status_code == 204
    assert gone.status_code == 404
    assert await count_rows(app, cid) == 0


async def test_malformed_and_unknown_ids_are_not_found(client: AsyncClient) -> None:
    await create_owner(client)

    malformed = await client.get(f"{API}/conversations/not-a-uuid")
    unknown = await client.get(f"{API}/conversations/{uuid.uuid4()}")

    assert malformed.status_code == 404 and malformed.json()["error"]["code"] == "not_found"
    assert unknown.status_code == 404


async def test_requests_without_a_session_are_refused(app: Any) -> None:
    anonymous = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    try:
        response = await anonymous.get(f"{API}/conversations")
    finally:
        await anonymous.aclose()

    assert response.status_code == 401


# -- edit and regenerate ------------------------------------------------------------------


async def test_editing_a_user_message_removes_every_later_message(client: AsyncClient, app: Any) -> None:
    user_id = await owner_user_id(client)
    cid = await new_conversation(client, "Editing")
    first_user = await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="original question")
    first_reply = await add_message(app, user_id=user_id, conversation_id=cid, role="assistant", content="first answer")
    await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="follow-up")
    await add_message(app, user_id=user_id, conversation_id=cid, role="assistant", content="follow-up answer")

    edited = await client.patch(
        f"{API}/conversations/{cid}/messages/{first_user}",
        json={"content": "  edited question  "},
        headers=csrf_headers(client),
    )

    assert edited.status_code == 200
    assert edited.json()["content"] == "edited question"
    assert edited.json()["role"] == "user"
    assert edited.json()["edited_at"] is not None
    remaining = await client.get(f"{API}/conversations/{cid}")
    assert [m["id"] for m in remaining.json()["messages"]] == [str(first_user)]
    assert str(first_reply) not in [m["id"] for m in remaining.json()["messages"]]


async def test_only_user_messages_can_be_edited_and_content_is_checked(client: AsyncClient, app: Any) -> None:
    user_id = await owner_user_id(client)
    cid = await new_conversation(client, "Editing rules")
    user_msg = await add_message(app, user_id=user_id, conversation_id=cid, role="user", content="hi")
    reply = await add_message(app, user_id=user_id, conversation_id=cid, role="assistant", content="hello")

    on_reply = await client.patch(f"{API}/conversations/{cid}/messages/{reply}", json={"content": "x"}, headers=csrf_headers(client))
    blank = await client.patch(f"{API}/conversations/{cid}/messages/{user_msg}", json={"content": "  "}, headers=csrf_headers(client))
    too_long = await client.patch(f"{API}/conversations/{cid}/messages/{user_msg}", json={"content": "a" * 9000}, headers=csrf_headers(client))

    assert on_reply.status_code == 409 and on_reply.json()["error"]["code"] == "message_not_editable"
    assert blank.status_code == 422
    assert too_long.status_code == 422
    assert await count_rows(app, cid) == 2


async def test_regenerate_replaces_the_reply_and_removes_later_messages(chat_env: Any, client: AsyncClient) -> None:  # noqa: F811
    await create_owner(client)
    cid = await new_conversation(client)
    await send(client, cid, "first question")
    await send(client, cid, "second question")
    before = await stored_messages(chat_env.app, cid)
    assert [m["role"] for m in before] == ["user", "assistant", "user", "assistant"]
    first_reply = before[1]

    response = await client.post(
        f"{API}/conversations/{cid}/messages/{first_reply['id']}/regenerate",
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    frames = parse_frames(response.text)
    assert event_names(frames) == ["assistant_start", "text_delta", "text_delta", "text_delta", "assistant_done", "done"]
    after = await stored_messages(chat_env.app, cid)
    assert [m["role"] for m in after] == ["user", "assistant"]
    assert after[0]["content"] == "first question"
    assert after[1]["id"] != first_reply["id"]
    assert after[1]["content"] == "Hello, world."
    assert after[1]["status"] == "complete"


async def test_regenerate_is_only_offered_for_assistant_messages(chat_env: Any, client: AsyncClient) -> None:  # noqa: F811
    await create_owner(client)
    cid = await new_conversation(client)
    await send(client, cid, "question")
    user_message = (await stored_messages(chat_env.app, cid))[0]

    response = await client.post(
        f"{API}/conversations/{cid}/messages/{user_message['id']}/regenerate",
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "message_not_regenerable"
    assert len(await stored_messages(chat_env.app, cid)) == 2


async def test_regenerate_an_unknown_message_is_not_found(chat_env: Any, client: AsyncClient) -> None:  # noqa: F811
    await create_owner(client)
    cid = await new_conversation(client)

    response = await client.post(
        f"{API}/conversations/{cid}/messages/{uuid.uuid4()}/regenerate",
        headers=csrf_headers(client),
    )

    assert response.status_code == 404


# -- isolation ----------------------------------------------------------------------------


async def test_another_user_cannot_see_or_change_a_conversation(chat_env: Any, client: AsyncClient, app: Any) -> None:  # noqa: F811
    owner_id = await owner_user_id(client)
    cid = await new_conversation(client, "Private plans")
    user_msg = await add_message(app, user_id=owner_id, conversation_id=cid, role="user", content="secret plan")
    reply = await add_message(app, user_id=owner_id, conversation_id=cid, role="assistant", content="the plan")
    await add_user(app, SECOND_EMAIL, SECOND_PASSWORD)
    other = await login_client(app, SECOND_EMAIL, SECOND_PASSWORD)
    try:
        headers = csrf_headers(other)
        assert (await other.get(f"{API}/conversations/{cid}")).status_code == 404
        assert (await other.patch(f"{API}/conversations/{cid}", json={"title": "stolen"}, headers=headers)).status_code == 404
        assert (await other.delete(f"{API}/conversations/{cid}", headers=headers)).status_code == 404
        assert (await other.patch(f"{API}/conversations/{cid}/messages/{user_msg}", json={"content": "x"}, headers=headers)).status_code == 404
        assert (await other.post(f"{API}/conversations/{cid}/messages/{reply}/regenerate", headers=headers)).status_code == 404
        assert (await other.post(f"{API}/conversations/{cid}/messages", json={"content": "hi"}, headers=headers)).status_code == 404

        listing = await other.get(f"{API}/conversations", params={"query": "plan"})
        assert listing.json() == {"items": [], "total": 0}
    finally:
        await other.aclose()

    assert await count_rows(app, cid) == 2
    owner_view = await client.get(f"{API}/conversations/{cid}")
    assert owner_view.json()["conversation"]["title"] == "Private plans"
    assert [m["content"] for m in owner_view.json()["messages"]] == ["secret plan", "the plan"]


async def test_each_user_sees_only_their_own_conversations(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    mine = await new_conversation(client, "Mine")
    await add_user(app, SECOND_EMAIL, SECOND_PASSWORD)
    other = await login_client(app, SECOND_EMAIL, SECOND_PASSWORD)
    try:
        theirs = (await other.post(f"{API}/conversations", json={"title": "Theirs"}, headers=csrf_headers(other))).json()["id"]
        other_list = await other.get(f"{API}/conversations")
        owner_list = await client.get(f"{API}/conversations")
    finally:
        await other.aclose()

    assert [item["id"] for item in other_list.json()["items"]] == [theirs]
    assert [item["id"] for item in owner_list.json()["items"]] == [mine]
    async with app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 2
