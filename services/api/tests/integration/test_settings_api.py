"""Settings API against PostgreSQL: defaults, deep merge, read-only emergency stop, persistence, audit and isolation."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.identity import User, UserPreference
from app.models.platform import AuditEvent
from app.schemas.settings import SECTION_NAMES
from app.security.passwords import hash_password
from app.services.settings_service import load_settings, save_settings_patch, set_emergency_stopped
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

BASE = "/api/v1/settings"


async def _owner_id(client: AsyncClient) -> uuid.UUID:
    me = await client.get("/api/v1/auth/me")
    return uuid.UUID(me.json()["id"])


async def _second_client(app: Any, email: str, password: str = "another-passphrase-123") -> AsyncClient:
    factory = app.state.session_factory
    async with factory() as db:
        db.add(
            User(
                email=email,
                display_name=email.split("@")[0],
                password_hash=hash_password(password),
                role="owner",
                is_active=True,
            )
        )
        await db.commit()
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    login = await other.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return other


async def _audit_rows(app: Any, user_id: uuid.UUID) -> list[AuditEvent]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.user_id == user_id, AuditEvent.action == "settings.updated")
            .order_by(AuditEvent.id)
        )
        return list(rows.all())


async def test_get_returns_the_complete_document_with_defaults(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(SECTION_NAMES)
    assert body["ai"]["active_provider"] == "gemini"
    assert body["ai"]["max_output_tokens"] == 2048
    assert body["automation"]["emergency_stopped"] is False
    assert body["appearance"]["dashboard_widgets"]["quick_actions"] is True


async def test_patch_merges_one_key_and_keeps_the_others(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(BASE, json={"ai": {"temperature": 1.5}}, headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ai"]["temperature"] == 1.5
    assert body["ai"]["max_output_tokens"] == 2048
    assert body["ai"]["stream_responses"] is True
    assert body["voice"]["language"] == "en-US"


async def test_patch_merges_nested_widgets_per_key(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(
        BASE,
        json={"appearance": {"dashboard_widgets": {"tasks": False}}},
        headers=csrf_headers(client),
    )
    widgets = response.json()["appearance"]["dashboard_widgets"]
    assert widgets["tasks"] is False
    assert widgets["status"] is True


async def test_emergency_stop_cannot_be_changed_through_patch(client: AsyncClient) -> None:
    await create_owner(client)
    for value in (True, False):
        refused = await client.patch(
            BASE, json={"automation": {"emergency_stopped": value}}, headers=csrf_headers(client)
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "read_only_setting"
    current = await client.get(BASE)
    assert current.json()["automation"]["emergency_stopped"] is False


async def test_emergency_stop_flag_set_by_the_service_is_visible_and_still_read_only(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        await set_emergency_stopped(db, user_id, True)
        await db.commit()
    assert (await client.get(BASE)).json()["automation"]["emergency_stopped"] is True
    refused = await client.patch(BASE, json={"automation": {"max_concurrent_tasks": 2}}, headers=csrf_headers(client))
    assert refused.status_code == 200
    assert refused.json()["automation"]["emergency_stopped"] is True
    async with app.state.session_factory() as db:
        await set_emergency_stopped(db, user_id, False)
        await db.commit()
    assert (await client.get(BASE)).json()["automation"]["emergency_stopped"] is False


async def test_values_persist_across_requests(client: AsyncClient) -> None:
    await create_owner(client)
    await client.patch(
        BASE,
        json={"appearance": {"accent_color": "#FF8800"}, "memory": {"retention_days": 30}},
        headers=csrf_headers(client),
    )
    again = (await client.get(BASE)).json()
    assert again["appearance"]["accent_color"] == "#FF8800"
    assert again["memory"]["retention_days"] == 30


async def test_invalid_patch_is_refused_with_field_names_and_changes_nothing(client: AsyncClient) -> None:
    await create_owner(client)
    refused = await client.patch(BASE, json={"ai": {"temperature": 3}}, headers=csrf_headers(client))
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "validation_error"
    assert "ai.temperature" in [field["field"] for field in error["details"]["fields"]]
    assert (await client.get(BASE)).json()["ai"]["temperature"] == 0.7


async def test_unknown_keys_are_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    top = await client.patch(BASE, json={"surprise": {"on": True}}, headers=csrf_headers(client))
    nested = await client.patch(BASE, json={"voice": {"volume": 9}}, headers=csrf_headers(client))
    assert top.status_code == nested.status_code == 422
    assert top.json()["error"]["code"] == nested.json()["error"]["code"] == "validation_error"


async def test_fallback_equal_to_active_is_refused(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(
        BASE,
        json={"ai": {"active_provider": "gemini", "fallback_provider": "gemini"}},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_audit_records_key_names_and_never_the_instruction_text(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    secret_wording = "Always answer in the voice of a pirate captain"
    await client.patch(
        BASE,
        json={"ai": {"system_instructions": secret_wording, "temperature": 1}},
        headers=csrf_headers(client),
    )
    rows = await _audit_rows(app, user_id)
    assert rows, "every accepted PATCH is audited"
    changed = rows[-1].data.get("changed", [])
    assert "ai.system_instructions" in changed
    assert "ai.temperature" in changed
    assert secret_wording not in str(rows[-1].data)


async def test_refused_emergency_patch_is_audited_as_denied(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    await client.patch(BASE, json={"automation": {"emergency_stopped": True}}, headers=csrf_headers(client))
    rows = await _audit_rows(app, user_id)
    assert rows[-1].outcome == "denied"
    assert rows[-1].data == {"fields": ["automation.emergency_stopped"]}


async def test_each_user_sees_only_their_own_settings(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await client.patch(BASE, json={"ai": {"temperature": 1.9}}, headers=csrf_headers(client))
    other = await _second_client(app, "second@example.com")
    seen = (await other.get(BASE)).json()
    assert seen["ai"]["temperature"] == 0.7
    await other.patch(BASE, json={"ai": {"temperature": 0.2}}, headers=csrf_headers(other))
    assert (await client.get(BASE)).json()["ai"]["temperature"] == 1.9
    assert (await other.get(BASE)).json()["ai"]["temperature"] == 0.2


async def test_first_read_creates_the_row_with_defaults(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    await client.get(BASE)
    async with app.state.session_factory() as db:
        row = await db.get(UserPreference, user_id)
        assert row is not None


async def test_a_corrupt_stored_document_fails_closed_instead_of_reverting_to_defaults(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        await set_emergency_stopped(db, user_id, True)
        row = await db.get(UserPreference, user_id)
        assert row is not None
        row.settings = {"ai": {"temperature": 99}, "automation": {"emergency_stopped": True}}
        await db.commit()
    response = await client.get(BASE)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "settings_unavailable"


async def test_service_patch_and_load_agree_with_the_api(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    user_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        saved = await save_settings_patch(db, user_id, {"privacy": {"store_conversations": False}})
        await db.commit()
    assert saved.privacy.store_conversations is False
    async with app.state.session_factory() as db:
        loaded = await load_settings(db, user_id)
    assert loaded.privacy.store_conversations is False
    assert (await client.get(BASE)).json()["privacy"]["store_conversations"] is False
