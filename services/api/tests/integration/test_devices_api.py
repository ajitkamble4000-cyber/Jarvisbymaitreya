"""Device REST API and agent pairing against real PostgreSQL: pairing, permissions, ownership, revocation, deletion."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.core.time import utc_now
from app.models.devices import Device, DevicePairingCode
from app.models.identity import User
from app.models.platform import AuditEvent
from app.security.passwords import hash_password
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
AGENT_CAPABILITIES = ["open_url", "launch_app", "system_info"]
AGENT_RESOURCES: dict[str, Any] = {
    "apps": [{"id": "chrome", "label": "Google Chrome", "path": "C:\\chrome.exe"}],
    "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}],
    "folders": [],
    "projects": [],
    "files": [],
}


def _anonymous(app: object) -> AsyncClient:
    """A client with no cookies, as the desktop agent sees the server."""
    return AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    )


async def _issue_code(client: AsyncClient) -> str:
    response = await client.post(f"{API}/devices/pairing-codes", headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()["code"]


def _pair_body(code: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "device_name": "Laptop",
        "device_type": "laptop",
        "os": "windows",
        "os_version": "10.0.22631",
        "agent_version": "1.0.0",
        "capabilities": AGENT_CAPABILITIES,
        "resources": AGENT_RESOURCES,
    }
    body.update(overrides)
    return body


async def _pair(agent: AsyncClient, code: str, **overrides: Any) -> dict[str, Any]:
    response = await agent.post(f"{API}/agent/pair", json=_pair_body(code, **overrides))
    assert response.status_code == 200, response.text
    return response.json()


async def _pair_new_device(client: AsyncClient, app: object, **overrides: Any) -> dict[str, Any]:
    code = await _issue_code(client)
    async with _anonymous(app) as agent:
        return await _pair(agent, code, **overrides)


async def _device_row(app: object, device_id: str) -> Device:
    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        row = await db.get(Device, uuid.UUID(device_id))
    assert row is not None
    return row


async def _login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text


# ---- pairing ----------------------------------------------------------------------------

async def test_pairing_returns_secret_once_and_code_is_single_use(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)

    async with _anonymous(app) as agent:
        paired = await _pair(agent, code)
        assert uuid.UUID(paired["device_id"])
        assert len(paired["device_secret"]) >= 40
        assert paired["websocket_path"] == "/api/v1/agent/ws"
        assert paired["heartbeat_interval_seconds"] == 15
        assert paired["protocol_version"] == 1

        replay = await agent.post(f"{API}/agent/pair", json=_pair_body(code))
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "pairing_code_invalid"

    listing = (await client.get(f"{API}/devices")).json()
    assert len(listing["items"]) == 1
    device = listing["items"][0]
    assert device["pairing_status"] == "paired"
    assert device["connection_status"] == "offline"
    assert device["is_default"] is True
    assert device["permissions"] == {cap: {"mode": "ask"} for cap in AGENT_CAPABILITIES}
    assert "device_secret" not in listing["items"][0]
    assert "credential_hash" not in listing["items"][0]

    row = await _device_row(app, paired["device_id"])
    assert row.credential_hash == hashlib.sha256(paired["device_secret"].encode()).hexdigest()
    assert row.secret_ciphertext is not None
    assert paired["device_secret"] not in row.secret_ciphertext


async def test_pairing_accepts_typed_variants_of_the_code(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)
    typed = code.lower().replace("-", " ")
    async with _anonymous(app) as agent:
        response = await agent.post(f"{API}/agent/pair", json=_pair_body(typed))
    assert response.status_code == 200, response.text


async def test_a_new_code_invalidates_the_earlier_unused_one(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    first = await _issue_code(client)
    second = await _issue_code(client)
    async with _anonymous(app) as agent:
        stale = await agent.post(f"{API}/agent/pair", json=_pair_body(first))
        assert stale.status_code == 401
        fresh = await agent.post(f"{API}/agent/pair", json=_pair_body(second))
        assert fresh.status_code == 200


async def test_expired_code_is_rejected(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)
    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        await db.execute(update(DevicePairingCode).values(expires_at=utc_now() - timedelta(seconds=1)))
        await db.commit()
    async with _anonymous(app) as agent:
        response = await agent.post(f"{API}/agent/pair", json=_pair_body(code))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "pairing_code_invalid"


async def test_wrong_codes_below_the_attempt_limit_do_not_block_the_right_one(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)
    limit = app.state.settings.device_max_pairing_attempts  # type: ignore[attr-defined]
    async with _anonymous(app) as agent:
        for _ in range(limit - 1):
            wrong = await agent.post(f"{API}/agent/pair", json=_pair_body("ZZZZ-ZZZZ"))
            assert wrong.status_code == 401
            assert wrong.json()["error"]["code"] == "pairing_code_invalid"
        await _pair(agent, code)


async def test_wrong_codes_do_not_invalidate_the_owners_active_code(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)
    limit = app.state.settings.device_max_pairing_attempts  # type: ignore[attr-defined]
    async with _anonymous(app) as agent:
        # Guesses from an unauthenticated client must not cancel the owner's live code.
        for _ in range(limit + 2):
            wrong = await agent.post(f"{API}/agent/pair", json=_pair_body("ZZZZ-ZZZZ"))
            assert wrong.status_code == 401
            assert wrong.json()["error"]["code"] == "pairing_code_invalid"
        await _pair(agent, code)

    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        active = await db.scalar(select(DevicePairingCode).order_by(DevicePairingCode.created_at.desc()).limit(1))
    assert active is not None and active.invalidated_at is None and active.used_at is not None


async def test_pairing_rejects_a_bad_payload_and_resources_are_sanitised(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    code = await _issue_code(client)
    async with _anonymous(app) as agent:
        missing_name = await agent.post(f"{API}/agent/pair", json={"code": code})
        assert missing_name.status_code == 422
        assert missing_name.json()["error"]["code"] == "validation_error"

        noisy = await _pair(
            agent,
            code,
            capabilities=["open_url", "format_disk"],
            resources={
                "apps": [{"id": "Bad Id", "label": "x"}, {"id": "vscode", "label": "VS Code"}],
                "urls": [{"id": "file", "label": "F", "url": "file:///C:/secret"}],
                "shell": [{"id": "x", "label": "x"}],
            },
        )

    listing = (await client.get(f"{API}/devices")).json()["items"]
    device = next(item for item in listing if item["id"] == noisy["device_id"])
    assert device["capabilities"] == ["open_url"]
    assert device["resources"]["apps"] == [{"id": "vscode", "label": "VS Code"}]
    assert device["resources"]["urls"] == []
    assert "shell" not in device["resources"]


async def test_pairing_is_rate_limited_per_client(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    app.state.rate_limiter.reset()  # type: ignore[attr-defined]
    limit = app.state.settings.login_rate_limit_per_minute  # type: ignore[attr-defined]
    app.state.settings.login_rate_limit_per_minute = 2  # type: ignore[attr-defined]
    try:
        async with _anonymous(app) as agent:
            statuses = [
                (await agent.post(f"{API}/agent/pair", json=_pair_body("ZZZZ-ZZZZ"))).status_code for _ in range(3)
            ]
    finally:
        app.state.settings.login_rate_limit_per_minute = limit  # type: ignore[attr-defined]
    assert statuses == [401, 401, 429]


async def test_pairing_code_creation_requires_a_signed_in_owner(app: object) -> None:
    async with _anonymous(app) as anonymous:
        response = await anonymous.post(f"{API}/devices/pairing-codes")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


# ---- device management ------------------------------------------------------------------

async def test_default_device_is_unique_per_user(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    first = await _pair_new_device(client, app, device_name="Desk")
    second = await _pair_new_device(client, app, device_name="Laptop")
    assert (await _device_row(app, first["device_id"])).is_default is True
    assert (await _device_row(app, second["device_id"])).is_default is False

    promoted = await client.patch(
        f"{API}/devices/{second['device_id']}",
        json={"is_default": True},
        headers=csrf_headers(client),
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["is_default"] is True

    listing = {item["id"]: item for item in (await client.get(f"{API}/devices")).json()["items"]}
    assert listing[first["device_id"]]["is_default"] is False
    assert listing[second["device_id"]]["is_default"] is True

    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        defaults = await db.scalar(select(func.count()).select_from(Device).where(Device.is_default.is_(True)))
    assert defaults == 1


async def test_permission_patch_rules(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    url = f"{API}/devices/{device['device_id']}"
    headers = csrf_headers(client)

    rejected = await client.patch(url, json={"permissions": {"launch_app": {"mode": "allow"}}}, headers=headers)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "permission_not_allowed"

    unknown = await client.patch(url, json={"permissions": {"format_disk": {"mode": "ask"}}}, headers=headers)
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"

    bad_mode = await client.patch(url, json={"permissions": {"open_url": {"mode": "maybe"}}}, headers=headers)
    assert bad_mode.status_code == 422

    unknown_field = await client.patch(url, json={"resources": {"apps": []}}, headers=headers)
    assert unknown_field.status_code == 422

    allowed = await client.patch(url, json={"permissions": {"open_url": {"mode": "allow"}}}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["permissions"]["open_url"] == {"mode": "allow"}
    assert allowed.json()["permissions"]["launch_app"] == {"mode": "ask"}

    denied = await client.patch(url, json={"permissions": {"open_url": {"mode": "deny"}}}, headers=headers)
    assert denied.json()["permissions"]["open_url"] == {"mode": "deny"}


async def test_another_users_device_is_not_visible_or_changeable(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    victim = await _pair_new_device(client, app)
    device_url = f"{API}/devices/{victim['device_id']}"

    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        db.add(
            User(
                email="second@example.com",
                display_name="Second",
                password_hash=hash_password("another-long-password"),
                role="owner",
                is_active=True,
            )
        )
        await db.commit()

    await client.post(f"{API}/auth/logout", headers=csrf_headers(client))
    await _login(client, "second@example.com", "another-long-password")
    headers = csrf_headers(client)

    assert (await client.get(f"{API}/devices")).json()["items"] == []
    assert (await client.patch(device_url, json={"name": "Hijacked"}, headers=headers)).status_code == 404
    assert (await client.post(f"{device_url}/ping", headers=headers)).status_code == 404
    assert (await client.post(f"{device_url}/revoke", json={}, headers=headers)).status_code == 404
    assert (await client.delete(device_url, headers=headers)).status_code == 404

    row = await _device_row(app, victim["device_id"])
    assert row.name == "Laptop"
    assert row.pairing_status == "paired"


async def test_revoke_clears_the_secret_and_is_idempotent(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    url = f"{API}/devices/{device['device_id']}/revoke"

    revoked = await client.post(url, json={"reason": "lost laptop"}, headers=csrf_headers(client))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["pairing_status"] == "revoked"
    assert revoked.json()["connection_status"] == "revoked"
    assert revoked.json()["revoked_at"] is not None

    again = await client.post(url, headers=csrf_headers(client))  # no body at all is allowed
    assert again.status_code == 200
    assert again.json()["pairing_status"] == "revoked"

    row = await _device_row(app, device["device_id"])
    assert row.credential_hash is None
    assert row.secret_ciphertext is None
    assert row.is_default is False
    assert row.revoked_reason == "lost laptop"

    async with app.state.session_factory() as db:  # type: ignore[attr-defined]
        events = await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "device.revoked")
        )
    assert events == 1

    locked = await client.patch(
        f"{API}/devices/{device['device_id']}",
        json={"name": "Renamed"},
        headers=csrf_headers(client),
    )
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "device_revoked"


async def test_delete_is_allowed_only_for_revoked_devices(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    url = f"{API}/devices/{device['device_id']}"

    refused = await client.delete(url, headers=csrf_headers(client))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "device_not_revoked"

    await client.post(f"{url}/revoke", json={}, headers=csrf_headers(client))
    removed = await client.delete(url, headers=csrf_headers(client))
    assert removed.status_code == 204
    assert (await client.get(f"{API}/devices")).json()["items"] == []


async def test_ping_of_an_offline_device_is_a_normal_answer(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    response = await client.post(f"{API}/devices/{device['device_id']}/ping", headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json() == {"online": False, "latency_ms": None, "message": "Device is offline."}


async def test_ping_of_a_revoked_device_reports_revoked(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    await client.post(f"{API}/devices/{device['device_id']}/revoke", json={}, headers=csrf_headers(client))
    response = await client.post(f"{API}/devices/{device['device_id']}/ping", headers=csrf_headers(client))
    assert response.status_code == 200
    assert response.json() == {"online": False, "latency_ms": None, "message": "Device is revoked."}


async def test_malformed_device_id_is_not_found(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(f"{API}/devices/not-a-uuid", json={"name": "x"}, headers=csrf_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "device_not_found"


async def test_revoke_body_rejects_unknown_fields(app: object, client: AsyncClient) -> None:
    await create_owner(client)
    device = await _pair_new_device(client, app)
    response = await client.post(
        f"{API}/devices/{device['device_id']}/revoke",
        json={"reason": "x", "extra": 1},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422
