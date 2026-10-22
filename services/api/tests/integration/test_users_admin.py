"""Owner-managed member accounts: authorisation on every /users route, creation, deactivation, password reset, audit."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.identity import User
from app.models.platform import AuditEvent
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
USERS = f"{API}/users"
OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct-horse-battery"
MEMBER_EMAIL = "member@example.com"
MEMBER_PASSWORD = "member-password-one"
NEW_PASSWORD = "member-password-two"
GENERIC_LOGIN_FAILURE = "Email or password is incorrect."


def _new_client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})


@asynccontextmanager
async def signed_in(app: Any, email: str, password: str) -> AsyncIterator[AsyncClient]:
    client = _new_client(app)
    try:
        response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        yield client
    finally:
        await client.aclose()


def _member_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": MEMBER_EMAIL,
        "display_name": "Sam Member",
        "password": MEMBER_PASSWORD,
        "role": "member",
    }
    body.update(overrides)
    return body


async def _create_member(client: AsyncClient, **overrides: Any) -> str:
    response = await client.post(USERS, json=_member_body(**overrides), headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _attempt_login(app: Any, email: str, password: str) -> tuple[int, dict[str, Any]]:
    async with _new_client(app) as client:
        response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
        return response.status_code, response.json()


async def _user_row(app: Any, user_id: str) -> User:
    async with app.state.session_factory() as db:
        row = await db.get(User, uuid.UUID(user_id))
    assert row is not None
    return row


async def _users_audit(app: Any) -> list[dict[str, Any]]:
    async with app.state.session_factory() as db:
        rows = (await db.scalars(select(AuditEvent).where(AuditEvent.action.like("users.%")).order_by(AuditEvent.id))).all()
        return [
            {
                "action": row.action,
                "user_id": row.user_id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "outcome": row.outcome,
                "data": dict(row.data),
            }
            for row in rows
        ]


# ---- listing ----------------------------------------------------------------------------


async def test_owner_lists_users_without_password_material(client: AsyncClient) -> None:
    owner = await create_owner(client)
    response = await client.get(USERS)
    assert response.status_code == 200, response.text
    assert list(response.json()) == ["items"]
    item = response.json()["items"][0]
    assert set(item) == {"id", "email", "display_name", "role", "is_active", "created_at", "last_login_at"}
    assert item["id"] == owner["id"]
    assert item["role"] == "owner" and item["is_active"] is True
    assert "password" not in response.text and "$argon2" not in response.text


# ---- creation ---------------------------------------------------------------------------


async def test_owner_creates_a_member_who_can_sign_in_with_member_role(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    response = await client.post(USERS, json=_member_body(email="  Member@Example.COM "), headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["email"] == MEMBER_EMAIL
    assert created["role"] == "member" and created["is_active"] is True and created["last_login_at"] is None
    assert MEMBER_PASSWORD not in response.text and "password" not in created

    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        me = (await member.get(f"{API}/auth/me")).json()
    assert me["id"] == created["id"] and me["role"] == "member"


async def test_duplicate_email_is_refused_case_insensitively(client: AsyncClient) -> None:
    await create_owner(client)
    await _create_member(client)
    duplicate = await client.post(USERS, json=_member_body(email="MEMBER@example.com"), headers=csrf_headers(client))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_taken"
    owner_email = await client.post(USERS, json=_member_body(email=OWNER_EMAIL), headers=csrf_headers(client))
    assert owner_email.status_code == 409
    assert owner_email.json()["error"]["code"] == "email_taken"
    assert len((await client.get(USERS)).json()["items"]) == 2


async def test_create_rejects_bad_input_and_writes_nothing(client: AsyncClient) -> None:
    await create_owner(client)
    without_role = _member_body()
    del without_role["role"]
    bad_bodies: list[dict[str, Any]] = [
        _member_body(role="owner"),  # a second owner can never be created here
        without_role,
        _member_body(password="short-pass"),
        _member_body(email="not-an-email"),
        _member_body(display_name="   "),
        _member_body(display_name="bad\x07name"),
        _member_body(display_name="x" * 121),
        {**_member_body(), "is_admin": True},
    ]
    for body in bad_bodies:
        response = await client.post(USERS, json=body, headers=csrf_headers(client))
        assert response.status_code == 422, (body, response.text)
        assert response.json()["error"]["code"] == "validation_error"
        assert MEMBER_PASSWORD not in response.text and "short-pass" not in response.text
    assert [item["role"] for item in (await client.get(USERS)).json()["items"]] == ["owner"]


async def test_member_creation_is_rate_limited_per_owner(client: AsyncClient, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await create_owner(client)
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 2)
    for index in range(2):
        await _create_member(client, email=f"member{index}@example.com")
    limited = await client.post(USERS, json=_member_body(email="member9@example.com"), headers=csrf_headers(client))
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


# ---- authorisation on every route -------------------------------------------------------


async def test_every_users_route_refuses_members_and_anonymous_clients(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    member_id = await _create_member(client)
    routes: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", USERS, {}),
        ("POST", USERS, _member_body(email="intruder@example.com")),
        ("PATCH", f"{USERS}/{member_id}", {"is_active": False}),
        ("POST", f"{USERS}/{member_id}/password", {"new_password": NEW_PASSWORD}),
    ]
    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        async with _new_client(app) as anonymous:
            for method, path, body in routes:
                kwargs: dict[str, Any] = {"json": body} if body else {}
                denied = await member.request(method, path, headers=csrf_headers(member), **kwargs)
                assert denied.status_code == 403, (method, path, denied.text)
                assert denied.json()["error"]["code"] == "forbidden"
                refused = await anonymous.request(method, path, **kwargs)
                assert refused.status_code == 401, (method, path, refused.text)
                assert refused.json()["error"]["code"] == "not_authenticated"

        # Authorisation runs before validation: a member with a malformed body still gets 403, not 422.
        malformed = await member.patch(f"{USERS}/not-a-uuid", json={"role": "owner"}, headers=csrf_headers(member))
        assert malformed.status_code == 403

    # The refused calls changed nothing: the member is still active and still signs in with the old password.
    status, _ = await _attempt_login(app, MEMBER_EMAIL, MEMBER_PASSWORD)
    assert status == 200
    items = (await client.get(USERS)).json()["items"]
    assert [item["is_active"] for item in items] == [True, True]
    assert len(items) == 2


# ---- update: deactivation, reactivation, rename -----------------------------------------


async def test_deactivating_a_member_revokes_sessions_and_blocks_sign_in(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    member_id = await _create_member(client)
    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        assert (await member.get(f"{API}/auth/me")).status_code == 200

        response = await client.patch(f"{USERS}/{member_id}", json={"is_active": False}, headers=csrf_headers(client))
        assert response.status_code == 200, response.text
        assert response.json()["is_active"] is False

        stale = await member.get(f"{API}/auth/me")
        assert stale.status_code == 401
        assert stale.json()["error"]["code"] == "session_expired"

    status, body = await _attempt_login(app, MEMBER_EMAIL, MEMBER_PASSWORD)
    assert status == 401 and body["error"]["code"] == "invalid_credentials"
    assert body["error"]["message"] == GENERIC_LOGIN_FAILURE

    audit = [row for row in await _users_audit(app) if row["action"] == "users.updated"]
    assert audit[-1]["target_id"] == member_id and audit[-1]["data"]["is_active"] is False
    assert audit[-1]["data"]["sessions_revoked"] >= 1


async def test_inactive_flag_alone_stops_an_existing_cookie(client: AsyncClient, app: Any) -> None:
    """Sessions are not revoked here. The is_active check in get_current_user must still refuse the cookie."""
    await create_owner(client)
    member_id = await _create_member(client)
    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        async with app.state.session_factory() as db:
            row = await db.get(User, uuid.UUID(member_id))
            assert row is not None
            row.is_active = False
            await db.commit()
        refused = await member.get(f"{API}/auth/me")
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "account_unavailable"
        status = (await member.get(f"{API}/auth/status")).json()
        assert status["authenticated"] is False


async def test_reactivated_member_can_sign_in_again_with_a_new_session(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    member_id = await _create_member(client)
    await client.patch(f"{USERS}/{member_id}", json={"is_active": False}, headers=csrf_headers(client))
    assert (await _attempt_login(app, MEMBER_EMAIL, MEMBER_PASSWORD))[0] == 401

    response = await client.patch(f"{USERS}/{member_id}", json={"is_active": True}, headers=csrf_headers(client))
    assert response.status_code == 200 and response.json()["is_active"] is True
    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        assert (await member.get(f"{API}/auth/me")).status_code == 200


async def test_owner_cannot_deactivate_themselves(client: AsyncClient) -> None:
    owner = await create_owner(client)
    response = await client.patch(f"{USERS}/{owner['id']}", json={"is_active": False}, headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "owner_protected"
    assert (await client.get(f"{API}/auth/me")).status_code == 200
    assert (await client.get(USERS)).json()["items"][0]["is_active"] is True


async def test_owner_can_rename_themselves_and_send_an_empty_update(client: AsyncClient) -> None:
    owner = await create_owner(client)
    renamed = await client.patch(f"{USERS}/{owner['id']}", json={"display_name": "  Sam  "}, headers=csrf_headers(client))
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Sam"
    unchanged = await client.patch(f"{USERS}/{owner['id']}", json={}, headers=csrf_headers(client))
    assert unchanged.status_code == 200 and unchanged.json()["display_name"] == "Sam"


async def test_update_rejects_unknown_fields_and_unknown_or_malformed_ids(client: AsyncClient) -> None:
    await create_owner(client)
    member_id = await _create_member(client)
    headers = csrf_headers(client)
    for body in ({"role": "owner"}, {"display_name": "   "}, {"is_active": False, "email": "x@example.com"}):
        response = await client.patch(f"{USERS}/{member_id}", json=body, headers=headers)
        assert response.status_code == 422, (body, response.text)
    missing = await client.patch(f"{USERS}/{uuid.uuid4()}", json={"is_active": False}, headers=headers)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "user_not_found"
    malformed = await client.patch(f"{USERS}/not-a-uuid", json={"is_active": False}, headers=headers)
    assert malformed.status_code == 422


# ---- password reset ---------------------------------------------------------------------


async def test_owner_resets_a_member_password_and_revokes_that_members_sessions(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    member_id = await _create_member(client)
    async with signed_in(app, MEMBER_EMAIL, MEMBER_PASSWORD) as member:
        response = await client.post(
            f"{USERS}/{member_id}/password", json={"new_password": NEW_PASSWORD}, headers=csrf_headers(client)
        )
        assert response.status_code == 204, response.text
        assert response.content == b""
        stale = await member.get(f"{API}/auth/me")
        assert stale.status_code == 401 and stale.json()["error"]["code"] == "session_expired"

    assert (await _attempt_login(app, MEMBER_EMAIL, MEMBER_PASSWORD))[0] == 401
    async with signed_in(app, MEMBER_EMAIL, NEW_PASSWORD) as member:
        assert (await member.get(f"{API}/auth/me")).status_code == 200

    stored = await _user_row(app, member_id)
    assert stored.password_hash.startswith("$argon2") and NEW_PASSWORD not in stored.password_hash
    # The owner's own session is untouched.
    assert (await client.get(f"{API}/auth/me")).json()["id"] == owner["id"]
    reset = [row for row in await _users_audit(app) if row["action"] == "users.password_reset"]
    assert reset and reset[-1]["target_id"] == member_id and reset[-1]["data"]["sessions_revoked"] >= 1


async def test_password_reset_rules(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    member_id = await _create_member(client)
    headers = csrf_headers(client)

    short = await client.post(f"{USERS}/{member_id}/password", json={"new_password": "too-short"}, headers=headers)
    assert short.status_code == 422 and short.json()["error"]["code"] == "validation_error"
    extra = await client.post(
        f"{USERS}/{member_id}/password", json={"new_password": NEW_PASSWORD, "role": "owner"}, headers=headers
    )
    assert extra.status_code == 422

    own = await client.post(f"{USERS}/{owner['id']}/password", json={"new_password": NEW_PASSWORD}, headers=headers)
    assert own.status_code == 422 and own.json()["error"]["code"] == "owner_protected"

    missing = await client.post(f"{USERS}/{uuid.uuid4()}/password", json={"new_password": NEW_PASSWORD}, headers=headers)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "user_not_found"

    # None of the refused resets changed the member's password.
    assert (await _attempt_login(app, MEMBER_EMAIL, MEMBER_PASSWORD))[0] == 200


# ---- audit and secrets ------------------------------------------------------------------


async def test_user_changes_are_audited_against_the_owner_without_secrets(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    member_id = await _create_member(client)
    await client.patch(f"{USERS}/{member_id}", json={"display_name": "Renamed"}, headers=csrf_headers(client))
    await client.post(f"{USERS}/{member_id}/password", json={"new_password": NEW_PASSWORD}, headers=csrf_headers(client))

    rows = await _users_audit(app)
    assert [row["action"] for row in rows] == ["users.created", "users.updated", "users.password_reset"]
    assert all(row["user_id"] == uuid.UUID(owner["id"]) and row["outcome"] == "success" for row in rows)
    assert all(row["target_id"] == member_id and row["target_type"] == "user" for row in rows)
    assert rows[0]["data"] == {"role": "member"}
    serialised = repr(rows)
    assert NEW_PASSWORD not in serialised and MEMBER_PASSWORD not in serialised
    assert MEMBER_EMAIL not in serialised


# ---- login stays generic for members ----------------------------------------------------


async def test_member_login_failures_do_not_reveal_whether_an_email_exists(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _create_member(client)
    wrong_password = await _attempt_login(app, MEMBER_EMAIL, "wrong-password-value")
    unknown_email = await _attempt_login(app, "nobody-here@example.com", "wrong-password-value")
    assert wrong_password[0] == unknown_email[0] == 401
    assert wrong_password[1]["error"]["message"] == unknown_email[1]["error"]["message"] == GENERIC_LOGIN_FAILURE
