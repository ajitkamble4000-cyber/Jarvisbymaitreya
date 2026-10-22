"""Tool catalogue, explore statuses, integrations catalogue, developer console and task traces, against PostgreSQL."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.routes.developer import settings_checks
from app.core.config import Settings
from app.core.time import utc_now
from app.models.ai import ProviderConfig
from app.models.devices import Device
from app.models.identity import User, UserPreference, UserSession
from app.models.tasks import Task, TaskEvent
from app.security.tokens import new_token, sha256_hex
from tests.conftest import TEST_ORIGIN, create_owner

REPO_ROOT = Path(__file__).resolve().parents[4]
INTEGRATION_IDS = {"gmail", "google_calendar", "google_drive", "github", "discord", "slack", "notion"}


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


async def _seed_device(app: Any, user_id: uuid.UUID, name: str, capabilities: list[str]) -> uuid.UUID:
    now = utc_now()
    async with app.state.session_factory() as db:
        device = Device(
            user_id=user_id,
            name=name,
            device_type="laptop",
            os="windows",
            pairing_status="paired",
            is_default=False,
            capabilities=capabilities,
            registered_at=now,
            paired_at=now,
        )
        db.add(device)
        await db.flush()
        device_id = device.id
        await db.commit()
    return device_id


async def _seed_provider(app: Any, user_id: uuid.UUID, provider_id: str, *, enabled: bool, stored: bool) -> None:
    async with app.state.session_factory() as db:
        db.add(
            ProviderConfig(
                user_id=user_id,
                provider_id=provider_id,
                enabled=enabled,
                # Explore only checks presence. The value is never decrypted here.
                credential_ciphertext="stored-ciphertext-placeholder" if stored else None,
            )
        )
        await db.commit()


async def _client_for_user(app: Any, user_id: uuid.UUID) -> AsyncClient:
    """A second browser session for ``user_id``, created directly so the owner stays signed in elsewhere."""
    token = new_token(32)
    now = utc_now()
    async with app.state.session_factory() as db:
        db.add(
            UserSession(
                user_id=user_id,
                token_hash=sha256_hex(token),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await db.commit()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    client.cookies.set("jarvis_session", token)
    return client


# -- tools and integrations ----------------------------------------------------


async def test_every_protected_read_requires_login(client: AsyncClient) -> None:
    paths = [
        "/api/v1/tools",
        "/api/v1/integrations",
        "/api/v1/explore",
        "/api/v1/audit",
        "/api/v1/analytics/summary",
        "/api/v1/developer/version",
        "/api/v1/developer/logs",
        "/api/v1/developer/config-check",
        "/api/v1/developer/traces/00000000-0000-0000-0000-000000000000",
    ]
    for path in paths:
        response = await client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "not_authenticated", path


async def test_tools_lists_every_registry_entry_with_its_shape(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    items = (await client.get("/api/v1/tools")).json()["items"]
    registry_names = {spec.name for spec in app.state.tools.all()}
    assert {item["name"] for item in items} == registry_names
    assert len(items) == len(registry_names)

    by_name = {item["name"]: item for item in items}
    launch = by_name["launch_app"]
    assert launch["target"] == "device"
    assert launch["requires_device"] is True
    assert launch["approval_required"] is True
    assert launch["risk_level"] == "medium"
    assert "properties" in launch["parameters"]

    listing = by_name["list_devices"]
    assert listing["target"] == "server"
    assert listing["requires_device"] is False
    assert listing["status"] == "working"
    for item in items:
        assert set(item) >= {
            "name", "title", "description", "category", "risk_level", "target", "requires_device",
            "approval_required", "status", "example_commands", "notes", "parameters",
        }


async def test_integrations_status_is_honest_and_documented(client: AsyncClient) -> None:
    await create_owner(client)
    items = (await client.get("/api/v1/integrations")).json()["items"]
    assert {item["id"] for item in items} == INTEGRATION_IDS
    implemented = {item["id"] for item in items if item["implemented"]}
    assert implemented == {"google_calendar", "github", "gmail", "google_drive", "notion"}
    for item in items:
        if item["id"] in implemented:
            # No OAuth client ids are configured in the test environment, so the connection is not offered.
            assert item["status"] == "requires_configuration", item["id"]
        else:
            assert item["status"] == "planned"
            assert item["implemented"] is False
        assert item["docs"] == f"docs/integrations/{item['id']}.md"
        assert (REPO_ROOT / item["docs"]).is_file()
        assert item["scopes_requested"], item["id"]
        assert item["requires_configuration"], item["id"]
        assert all(name.isupper() for name in item["requires_configuration"])

    gmail = _by_id(items)["gmail"]
    assert "GOOGLE_OAUTH_CLIENT_ID" in gmail["requires_configuration"]


# -- explore -------------------------------------------------------------------


async def test_explore_reports_honest_statuses_for_a_fresh_account(client: AsyncClient) -> None:
    await create_owner(client)
    body = (await client.get("/api/v1/explore")).json()
    assert body["categories"] == [
        "Productivity", "Communication", "Development", "Files", "Devices", "Scheduling", "Research", "Media", "Automation",
    ]
    items = _by_id(body["items"])

    assert items["launch_app"]["status"] == "requires_configuration"
    assert "Pair a desktop agent" in items["launch_app"]["requirements"][0]
    assert items["list_devices"]["status"] == "working"
    # The test settings run without background workers, so reminders cannot fire.
    assert items["create_reminder"]["status"] == "requires_configuration"
    assert items["schedules"]["status"] == "requires_configuration"
    assert items["chat"]["status"] == "requires_configuration"
    assert items["voice"]["status"] == "working"
    assert "Browser-dependent" in items["voice"]["requirements"][0]
    for integration_id in INTEGRATION_IDS:
        assert items[integration_id]["status"] == "unsupported", integration_id
        assert items[integration_id]["enabled_for_user"] is False, integration_id
        assert items[integration_id]["category"] in body["categories"]
    assert all(item["category"] in body["categories"] for item in body["items"])


async def test_explore_becomes_ready_once_a_device_and_provider_exist(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    await _seed_device(app, user_id, "Laptop", ["launch_app", "open_url"])
    await _seed_provider(app, user_id, "gemini", enabled=True, stored=True)

    items = _by_id((await client.get("/api/v1/explore")).json()["items"])
    assert items["launch_app"]["status"] == "working"
    assert items["launch_app"]["requirements"] == [
        "Needs a paired device that reports the 'launch_app' capability.",
    ]
    assert items["chat"]["status"] == "working"
    assert items["chat"]["requirements"] == []


async def test_a_disabled_provider_does_not_make_chat_ready(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    await _seed_provider(app, uuid.UUID(owner["id"]), "gemini", enabled=False, stored=True)
    items = _by_id((await client.get("/api/v1/explore")).json()["items"])
    assert items["chat"]["status"] == "requires_configuration"


async def test_explore_filters_by_category_and_text(client: AsyncClient) -> None:
    await create_owner(client)
    files = (await client.get("/api/v1/explore?category=files")).json()["items"]
    assert files and all(item["category"] == "Files" for item in files)
    assert "read_file" in {item["id"] for item in files}

    chrome = (await client.get("/api/v1/explore?q=CHROME")).json()["items"]
    assert "launch_app" in {item["id"] for item in chrome}

    unknown = (await client.get("/api/v1/explore?category=Nonexistent")).json()
    assert unknown["items"] == []
    assert len(unknown["categories"]) == 9


async def test_discovery_does_not_authorise_anything(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        db.add(UserPreference(user_id=user_id, settings={"automation": {"disabled_tools": ["launch_app"]}}))
        await db.commit()

    for _ in range(3):
        items = _by_id((await client.get("/api/v1/explore")).json()["items"])
        assert items["launch_app"]["enabled_for_user"] is False
        assert items["list_devices"]["enabled_for_user"] is True
        await client.get("/api/v1/tools")

    async with app.state.session_factory() as db:
        stored = await db.scalar(select(UserPreference.settings).where(UserPreference.user_id == user_id))
        tasks = await db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user_id))
    assert stored == {"automation": {"disabled_tools": ["launch_app"]}}
    assert tasks == 0


# -- developer console ---------------------------------------------------------


async def test_version_reports_facts_without_secrets(client: AsyncClient, settings: Settings) -> None:
    await create_owner(client)
    response = await client.get("/api/v1/developer/version")
    body = response.json()
    assert body["protocol_version"] == 1
    assert body["environment"] == "test"
    assert body["background_workers"] is False
    assert body["python_version"].startswith("3.")
    assert body["app_version"]
    assert settings.encryption_key is not None
    assert settings.encryption_key.get_secret_value() not in response.text


async def test_logs_are_redacted_and_filtered_by_level(client: AsyncClient) -> None:
    await create_owner(client)
    logging.getLogger("jarvis.test").warning("sign-in attempt with token=supersecretvalue123 for the owner")

    response = await client.get("/api/v1/developer/logs?level=WARNING&limit=200")
    assert response.status_code == 200
    messages = [item["message"] for item in response.json()["items"]]
    matching = [message for message in messages if "sign-in attempt" in message]
    assert matching, messages
    assert all("supersecretvalue123" not in message for message in messages)
    assert any("token=[REDACTED]" in message for message in matching)
    assert all(item["level"] in {"WARNING", "ERROR", "CRITICAL"} for item in response.json()["items"])


async def test_logs_reject_an_unknown_level(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.get("/api/v1/developer/logs?level=VERBOSE")
    assert response.status_code == 422


async def test_config_check_reports_presence_and_never_the_key(client: AsyncClient, settings: Settings) -> None:
    await create_owner(client)
    response = await client.get("/api/v1/developer/config-check")
    assert response.status_code == 200
    assert settings.encryption_key is not None
    assert settings.encryption_key.get_secret_value() not in response.text

    checks = {item["key"]: item for item in response.json()["checks"]}
    assert checks["database"]["status"] == "ok"
    assert checks["pgvector"]["status"] in {"ok", "warning"}
    assert checks["encryption_key"]["status"] == "ok"
    assert checks["allowed_origins"]["status"] == "ok"
    assert checks["provider.gemini.credential"]["status"] == "warning"
    assert checks["provider.huggingface.credential"]["status"] == "warning"
    assert checks["paired_device"]["status"] == "warning"
    for item in response.json()["checks"]:
        assert set(item) == {"key", "status", "message"}
        assert item["status"] in {"ok", "warning", "error"}


async def test_config_check_passes_once_a_device_and_credential_exist(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    await _seed_device(app, user_id, "Laptop", ["system_info"])
    await _seed_provider(app, user_id, "gemini", enabled=True, stored=True)
    checks = {item["key"]: item for item in (await client.get("/api/v1/developer/config-check")).json()["checks"]}
    assert checks["paired_device"]["status"] == "ok"
    assert checks["provider.gemini.credential"]["status"] == "ok"
    assert "stored-ciphertext-placeholder" not in str(checks)


def test_production_settings_problems_are_errors_and_carry_no_values() -> None:
    # model_construct skips validation so the failure branches can be seen. Production never starts like this.
    unsafe = Settings.model_construct(
        jarvis_env="production",
        encryption_key=None,
        cookie_secure=False,
        public_base_url="http://jarvis.example",
        allowed_origins=["*"],
        run_background_workers=False,
    )
    checks = {item.key: item for item in settings_checks(unsafe)}
    assert checks["encryption_key"].status == "error"
    assert checks["cookie_secure"].status == "error"
    assert checks["public_base_url"].status == "error"
    assert checks["allowed_origins"].status == "error"
    assert checks["background_workers"].status == "warning"
    assert "jarvis.example" not in " ".join(item.message for item in checks.values())


def test_production_settings_pass_when_configured_safely() -> None:
    safe = Settings.model_construct(
        jarvis_env="production",
        encryption_key="a-key-value-that-must-not-be-printed",
        cookie_secure=True,
        public_base_url="https://jarvis.example",
        allowed_origins=["https://jarvis.example"],
        run_background_workers=True,
    )
    checks = settings_checks(safe)
    assert {item.status for item in checks} == {"ok"}
    assert all("a-key-value-that-must-not-be-printed" not in item.message for item in checks)


# -- task traces ---------------------------------------------------------------


async def _seed_task_with_timeline(app: Any, user_id: uuid.UUID, device_id: uuid.UUID | None) -> uuid.UUID:
    now = utc_now()
    async with app.state.session_factory() as db:
        task = Task(
            user_id=user_id,
            title="Launch Chrome on Laptop",
            tool_name="launch_app",
            arguments={"app_id": "chrome"},
            status="completed",
            device_id=device_id,
            created_at=now,
            started_at=now,
            finished_at=now + timedelta(seconds=4),
            result={"summary": "Launched Google Chrome."},
        )
        db.add(task)
        await db.flush()
        task_id = task.id
        for offset, (kind, message) in enumerate(
            [("created", "Task created"), ("queued", "Queued for the device"), ("completed", "Completed")]
        ):
            db.add(
                TaskEvent(
                    task_id=task_id,
                    user_id=user_id,
                    type=kind,
                    message=message,
                    data={},
                    created_at=now + timedelta(seconds=offset),
                )
            )
        await db.commit()
    return task_id


async def test_trace_returns_the_task_and_its_timeline_in_order(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    user_id = uuid.UUID(owner["id"])
    device_id = await _seed_device(app, user_id, "Laptop", ["launch_app"])
    task_id = await _seed_task_with_timeline(app, user_id, device_id)

    response = await client.get(f"/api/v1/developer/traces/{task_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["task"]["id"] == str(task_id)
    assert body["task"]["status"] == "completed"
    assert body["task"]["device"]["name"] == "Laptop"
    assert body["task"]["device"]["connection_status"] == "offline"
    assert [event["type"] for event in body["events"]] == ["created", "queued", "completed"]


async def test_trace_is_hidden_from_other_users(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    task_id = await _seed_task_with_timeline(app, uuid.UUID(owner["id"]), None)

    neighbour = await _other_user(app)
    other_client = await _client_for_user(app, neighbour)
    try:
        response = await other_client.get(f"/api/v1/developer/traces/{task_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "task_not_found"
    finally:
        await other_client.aclose()


async def test_trace_rejects_unknown_and_malformed_ids(client: AsyncClient) -> None:
    await create_owner(client)
    unknown = await client.get(f"/api/v1/developer/traces/{uuid.uuid4()}")
    malformed = await client.get("/api/v1/developer/traces/not-a-uuid")
    for response in (unknown, malformed):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "task_not_found"


async def _other_user(app: Any) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email="neighbour@example.com", display_name="Neighbour", password_hash="!", role="owner", is_active=True)
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
    return user_id

