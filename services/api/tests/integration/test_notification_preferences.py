"""Notification preferences filter the inbox: each setting hides exactly its kinds, hiding is not deleting."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models.identity import User
from app.models.platform import Notification
from app.security.passwords import hash_password
from app.services.notifications import notify
from app.services.settings_service import save_settings_patch
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
BASE = f"{API}/notifications"
SETTINGS = f"{API}/settings"

# Kind -> the preference that hides it. Kinds with no preference are always shown.
KIND_SETTINGS: dict[str, str] = {
    "task_completed": "on_task_completed",
    "task_failed": "on_task_failed",
    "approval_required": "on_approval_required",
    "device_disconnected": "on_device_disconnected",
    "integration_error": "on_integration_error",
    "schedule_failed": "on_schedule_event",
    "schedule_fired": "on_schedule_event",
    "security_login_failed": "on_security_event",
    "auth_token_expired": "on_security_event",
}
ALWAYS_SHOWN: list[str] = ["reminder", "late_result", "something_else"]
ALL_SETTINGS: list[str] = sorted(set(KIND_SETTINGS.values()))


async def _seed_every_kind(app: Any, user_id: uuid.UUID) -> None:
    async with app.state.session_factory() as db:
        for kind in [*KIND_SETTINGS, *ALWAYS_SHOWN]:
            notify(db, app.state.events, user_id=user_id, kind=kind, severity="info", title=kind, body="")
        await db.commit()


async def _patch_notifications(client: AsyncClient, patch: dict[str, bool]) -> None:
    response = await client.patch(SETTINGS, json={"notifications": patch}, headers=csrf_headers(client))
    assert response.status_code == 200, response.text


async def _listing(client: AsyncClient, query: str = "") -> dict[str, Any]:
    response = await client.get(f"{BASE}{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _titles(body: dict[str, Any]) -> list[str]:
    return sorted(item["title"] for item in body["items"])


def _visible_when_off(off: set[str]) -> list[str]:
    hidden = {kind for kind, setting in KIND_SETTINGS.items() if setting in off}
    return sorted([*(kind for kind in KIND_SETTINGS if kind not in hidden), *ALWAYS_SHOWN])


@pytest.mark.parametrize("setting", ALL_SETTINGS)
async def test_each_setting_hides_exactly_its_kinds(client: AsyncClient, app: Any, setting: str) -> None:
    owner = await create_owner(client)
    await _seed_every_kind(app, uuid.UUID(owner["id"]))
    await _patch_notifications(client, {setting: False})

    expected = _visible_when_off({setting})
    hidden_count = sum(1 for name in KIND_SETTINGS.values() if name == setting)

    body = await _listing(client)
    assert _titles(body) == expected
    assert body["hidden_by_preferences"] == hidden_count
    assert body["unread_count"] == len(expected)

    unread_only = await _listing(client, "?unread=true")
    assert _titles(unread_only) == expected
    assert unread_only["hidden_by_preferences"] == hidden_count


async def test_all_preferences_off_leaves_only_always_shown_kinds(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _seed_every_kind(app, uuid.UUID(owner["id"]))
    await _patch_notifications(client, {name: False for name in ALL_SETTINGS})

    body = await _listing(client)
    assert _titles(body) == sorted(ALWAYS_SHOWN)
    assert body["hidden_by_preferences"] == len(KIND_SETTINGS)
    assert body["unread_count"] == len(ALWAYS_SHOWN)


async def test_hiding_keeps_the_rows_and_turning_the_setting_back_on_shows_them(
    client: AsyncClient, app: Any
) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    await _seed_every_kind(app, user_id)
    total_rows = len(KIND_SETTINGS) + len(ALWAYS_SHOWN)

    await _patch_notifications(client, {"on_task_failed": False})
    assert (await _listing(client))["hidden_by_preferences"] == 1
    async with app.state.session_factory() as db:
        stored = await db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user_id))
    assert stored == total_rows

    await _patch_notifications(client, {"on_task_failed": True})
    body = await _listing(client)
    assert "task_failed" in _titles(body)
    assert body["hidden_by_preferences"] == 0
    assert len(body["items"]) == total_rows


async def test_another_users_preferences_do_not_apply(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    await _seed_every_kind(app, owner_id)

    neighbour_password = "neighbour-password-123"
    async with app.state.session_factory() as db:
        neighbour = User(
            email="neighbour@example.com",
            display_name="Neighbour",
            password_hash=hash_password(neighbour_password),
            role="member",
            is_active=True,
        )
        db.add(neighbour)
        await db.flush()
        neighbour_id = neighbour.id
        await save_settings_patch(db, neighbour_id, {"notifications": {name: False for name in ALL_SETTINGS}})
        for kind in [*KIND_SETTINGS, *ALWAYS_SHOWN]:
            notify(db, app.state.events, user_id=neighbour_id, kind=kind, severity="info", title=kind, body="")
        await db.commit()

    own = await _listing(client)
    assert _titles(own) == sorted([*KIND_SETTINGS, *ALWAYS_SHOWN])
    assert own["hidden_by_preferences"] == 0

    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    try:
        login = await other.post(f"{API}/auth/login", json={"email": "neighbour@example.com", "password": neighbour_password})
        assert login.status_code == 200, login.text
        neighbour_view = await _listing(other)
        assert _titles(neighbour_view) == sorted(ALWAYS_SHOWN)
        assert neighbour_view["hidden_by_preferences"] == len(KIND_SETTINGS)
    finally:
        await other.aclose()
