"""Audit log filters and isolation, and analytics computed from measured rows, against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from app.core.time import utc_now
from app.models.ai import UsageRecord
from app.models.devices import Device
from app.models.identity import User
from app.models.tasks import Task
from app.services.audit import record_audit
from tests.conftest import create_owner, csrf_headers


async def _other_user(app: Any, email: str = "neighbour@example.com") -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name="Neighbour", password_hash="!", role="owner", is_active=True)
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
    return user_id


# -- audit ---------------------------------------------------------------------


async def test_audit_returns_only_the_users_events_with_total(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)  # writes auth.setup for the owner
    owner_id = uuid.UUID(owner["id"])
    neighbour = await _other_user(app)
    async with app.state.session_factory() as db:
        record_audit(db, action="device.paired", user_id=owner_id, target_type="device", target_id="d-1")
        record_audit(db, action="device.paired", user_id=neighbour, target_type="device", target_id="d-other")
        await db.commit()

    body = (await client.get("/api/v1/audit")).json()
    assert body["total"] == 2
    assert {item["action"] for item in body["items"]} == {"auth.setup", "device.paired"}
    assert all(item["target_id"] != "d-other" for item in body["items"])


async def test_audit_prefix_filter_and_literal_wildcards(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        record_audit(db, action="device.paired", user_id=owner_id)
        record_audit(db, action="device.revoked", user_id=owner_id)
        record_audit(db, action="devices_archive", user_id=owner_id)
        await db.commit()

    devices = (await client.get("/api/v1/audit?action=device.")).json()
    assert sorted(item["action"] for item in devices["items"]) == ["device.paired", "device.revoked"]
    assert devices["total"] == 2

    # "_" and "%" are literal characters in the prefix, not SQL wildcards.
    underscore = (await client.get("/api/v1/audit?action=devices_")).json()
    assert [item["action"] for item in underscore["items"]] == ["devices_archive"]
    percent = (await client.get("/api/v1/audit?action=%25")).json()
    assert percent == {"items": [], "total": 0}


async def test_audit_outcome_filter_and_validation(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        record_audit(db, action="device.revoked", outcome="denied", user_id=owner_id)
        await db.commit()

    denied = (await client.get("/api/v1/audit?outcome=denied")).json()
    assert [item["action"] for item in denied["items"]] == ["device.revoked"]
    assert denied["items"][0]["outcome"] == "denied"

    invalid = await client.get("/api/v1/audit?outcome=maybe")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


async def test_audit_pagination_is_newest_first(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        for index in range(5):
            record_audit(db, action=f"test.event.{index}", user_id=owner_id)
        await db.commit()

    page = (await client.get("/api/v1/audit?limit=2&offset=1")).json()
    assert page["total"] == 6
    assert len(page["items"]) == 2
    assert page["items"][0]["action"] == "test.event.3"


async def test_audit_hides_anonymous_rows_and_sanitises_data(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    # A failed sign-in has no user. It must not appear in any user's audit log.
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "not-the-right-password"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 401

    owner_rows = (await client.get("/api/v1/audit?action=auth.login")).json()
    assert owner_rows["total"] == 0

    async with app.state.session_factory() as db:
        record_audit(
            db,
            action="device.paired",
            user_id=uuid.UUID(owner["id"]),
            data={"device_secret": "s3cret-value", "note": "kept"},
        )
        await db.commit()
    item = (await client.get("/api/v1/audit?action=device.paired")).json()["items"][0]
    assert item["data"] == {"note": "kept"}
    assert "s3cret-value" not in str(item)


# -- analytics -----------------------------------------------------------------


def _metric(section: dict[str, Any], name: str) -> dict[str, Any]:
    metric = section[name]
    assert set(metric) >= {"value", "unit", "source", "note"}, metric
    return metric


async def _seed_device(app: Any, user_id: uuid.UUID, name: str, *, revoked: bool = False) -> uuid.UUID:
    now = utc_now()
    async with app.state.session_factory() as db:
        device = Device(
            user_id=user_id,
            name=name,
            device_type="laptop",
            os="windows",
            pairing_status="revoked" if revoked else "paired",
            is_default=False,
            capabilities=["launch_app"],
            registered_at=now,
            paired_at=now,
            revoked_at=now if revoked else None,
        )
        db.add(device)
        await db.flush()
        device_id = device.id
        await db.commit()
    return device_id


async def _seed_tasks(app: Any, user_id: uuid.UUID, device_id: uuid.UUID) -> None:
    now = utc_now()
    recent = now - timedelta(hours=1)
    rows = [
        # Two completed device tasks: 10 s and 30 s, so the average is 20 s.
        Task(user_id=user_id, title="a", tool_name="launch_app", arguments={}, status="completed",
             device_id=device_id, created_at=recent, started_at=recent, finished_at=recent + timedelta(seconds=10)),
        Task(user_id=user_id, title="b", tool_name="launch_app", arguments={}, status="completed",
             device_id=device_id, created_at=recent, started_at=recent, finished_at=recent + timedelta(seconds=30)),
        Task(user_id=user_id, title="c", tool_name="launch_app", arguments={}, status="failed",
             device_id=device_id, created_at=recent, error={"code": "device_offline", "message": "offline"}),
        Task(user_id=user_id, title="d", tool_name="launch_app", arguments={}, status="failed",
             device_id=device_id, created_at=recent, error={"code": "device_offline", "message": "offline"}),
        Task(user_id=user_id, title="e", tool_name="list_devices", arguments={}, status="cancelled", created_at=recent),
        Task(user_id=user_id, title="f", tool_name="launch_app", arguments={}, status="awaiting_approval",
             device_id=device_id, created_at=recent),
        # Outside a 30-day window.
        Task(user_id=user_id, title="old", tool_name="launch_app", arguments={}, status="completed",
             device_id=device_id, created_at=now - timedelta(days=40), started_at=now - timedelta(days=40),
             finished_at=now - timedelta(days=40) + timedelta(seconds=999)),
    ]
    async with app.state.session_factory() as db:
        db.add_all(rows)
        await db.commit()


async def _seed_usage(app: Any, user_id: uuid.UUID) -> None:
    now = utc_now()
    recent = now - timedelta(hours=1)

    def row(**fields: Any) -> UsageRecord:
        base: dict[str, Any] = {
            "user_id": user_id,
            "created_at": recent,
            "model": "model-x",
            "operation": "chat",
            "success": True,
            "latency_ms": 0,
            "tokens_estimated": False,
        }
        base.update(fields)
        return UsageRecord(**base)

    rows = [
        row(provider_id="gemini", latency_ms=100, input_tokens=10, output_tokens=5, total_tokens=15),
        row(provider_id="gemini", latency_ms=300, input_tokens=20, output_tokens=10, total_tokens=30),
        row(provider_id="gemini", success=False, latency_ms=0, error_code="timeout", operation="stream"),
        row(provider_id="huggingface", latency_ms=200, input_tokens=7, output_tokens=3, total_tokens=10,
            tokens_estimated=True),
        # Outside the window: must not be counted.
        row(provider_id="gemini", latency_ms=9999, input_tokens=500, output_tokens=499, total_tokens=999,
            created_at=now - timedelta(days=40)),
    ]
    async with app.state.session_factory() as db:
        db.add_all(rows)
        await db.commit()


async def test_summary_is_computed_from_measured_rows(client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    online = await _seed_device(app, owner_id, "Laptop")
    await _seed_device(app, owner_id, "Desk PC")
    await _seed_device(app, owner_id, "Old PC", revoked=True)
    monkeypatch.setattr(app.state.device_hub, "is_online", lambda device_id: device_id == online)
    await _seed_tasks(app, owner_id, online)
    await _seed_usage(app, owner_id)

    response = await client.get("/api/v1/analytics/summary?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == 30
    sections = body["sections"]

    tasks = sections["tasks"]
    assert _metric(tasks, "total")["value"] == 6
    assert _metric(tasks, "completed")["value"] == 2
    assert _metric(tasks, "failed")["value"] == 2
    assert _metric(tasks, "cancelled")["value"] == 1
    assert _metric(tasks, "awaiting_approval")["value"] == 1
    assert _metric(tasks, "success_rate")["value"] == 0.5
    assert _metric(tasks, "success_rate")["source"] == "measured"

    latency = _metric(sections["latency"], "avg_device_task_seconds")
    assert latency["value"] == 20.0
    assert latency["source"] == "measured"

    ai = sections["ai"]
    assert _metric(ai, "requests")["value"] == 4
    assert _metric(ai, "failures")["value"] == 1
    assert _metric(ai, "avg_latency_ms")["value"] == 200.0  # (100 + 300 + 200) / 3, the failure has no sample
    assert _metric(ai, "input_tokens")["value"] == 37
    assert _metric(ai, "total_tokens")["value"] == 55
    assert _metric(ai, "total_tokens")["source"] == "estimated"  # one row was estimated locally
    assert _metric(ai, "tokens_estimated_share")["value"] == round(10 / 55, 4)
    providers = {row["provider_id"]: row for row in ai["by_provider"]}
    assert _metric(providers["gemini"], "requests")["value"] == 3
    assert _metric(providers["gemini"], "total_tokens")["value"] == 45
    assert _metric(providers["gemini"], "total_tokens")["source"] == "measured"
    assert _metric(providers["huggingface"], "total_tokens")["source"] == "estimated"

    cost = _metric(sections["cost"], "estimated_usd")
    assert cost == {
        "value": None,
        "unit": "usd",
        "source": "unavailable",
        "note": "No pricing table is configured",
    }

    devices = sections["devices"]
    assert _metric(devices, "total")["value"] == 3
    assert _metric(devices, "online")["value"] == 1
    assert _metric(devices, "offline")["value"] == 1
    assert _metric(devices, "revoked")["value"] == 1

    integrations = sections["integrations"]
    assert _metric(integrations, "connected")["value"] == 0
    # google_calendar, gmail, google_drive, github and notion are implemented (OAuth); discord and slack are planned.
    assert _metric(integrations, "implemented")["value"] == 5

    errors = sections["errors"]
    assert _metric(errors, "task_error_codes")["value"] == [{"code": "device_offline", "count": 2}]
    assert _metric(errors, "ai_error_codes")["value"] == [{"code": "timeout", "count": 1}]


async def test_empty_account_reports_null_not_zero(client: AsyncClient) -> None:
    await create_owner(client)
    sections = (await client.get("/api/v1/analytics/summary")).json()["sections"]

    success = _metric(sections["tasks"], "success_rate")
    assert success["value"] is None and success["source"] == "unavailable"
    latency = _metric(sections["latency"], "avg_device_task_seconds")
    assert latency["value"] is None and latency["source"] == "unavailable"
    ai = sections["ai"]
    assert _metric(ai, "avg_latency_ms")["value"] is None
    assert _metric(ai, "total_tokens")["value"] is None
    assert _metric(ai, "total_tokens")["source"] == "unavailable"
    assert _metric(ai, "tokens_estimated_share")["value"] is None
    assert ai["by_provider"] == []
    assert _metric(sections["cost"], "estimated_usd")["source"] == "unavailable"
    # Counts that really are zero are measured zeros.
    assert _metric(sections["tasks"], "total") == {
        "value": 0,
        "unit": "tasks",
        "source": "measured",
        "note": "Tasks created in this period",
    }


async def test_summary_ignores_other_users_data(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    neighbour = await _other_user(app)
    device_id = await _seed_device(app, neighbour, "Neighbour laptop")
    await _seed_tasks(app, neighbour, device_id)
    await _seed_usage(app, neighbour)

    sections = (await client.get("/api/v1/analytics/summary")).json()["sections"]
    assert _metric(sections["tasks"], "total")["value"] == 0
    assert _metric(sections["tasks"], "success_rate")["value"] is None
    assert _metric(sections["ai"], "requests")["value"] == 0
    assert _metric(sections["devices"], "total")["value"] == 0


@pytest.mark.parametrize("days", [0, 366])
async def test_summary_rejects_days_outside_the_range(client: AsyncClient, days: int) -> None:
    await create_owner(client)
    response = await client.get(f"/api/v1/analytics/summary?days={days}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_summary_accepts_the_full_year(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.get("/api/v1/analytics/summary?days=365")
    assert response.status_code == 200
    assert response.json()["period_days"] == 365
