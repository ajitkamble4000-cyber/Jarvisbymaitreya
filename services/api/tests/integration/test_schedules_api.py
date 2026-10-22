"""Schedules API against PostgreSQL: creation rules, ordering, derived state, pause and resume, runs and isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.core.time import utc_now
from app.models.devices import Device
from app.models.identity import User
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.schemas.schedules import ScheduleCreate
from app.security.passwords import hash_password
from app.services.schedule_service import create_schedule
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

BASE = "/api/v1/schedules"


def _daily(name: str = "Morning brief", time_of_day: str = "09:00", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "tool_name": "list_devices",
        "arguments": {},
        "schedule": {"kind": "daily", "time_of_day": time_of_day, "timezone": "UTC"},
    }
    body.update(extra)
    return body


def _weekly(name: str = "Standup", weekdays: list[int] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "tool_name": "list_devices",
        "arguments": {},
        "schedule": {"kind": "weekly", "time_of_day": "08:30", "weekdays": weekdays or [0, 4], "timezone": "UTC"},
    }


def _once(name: str = "Call the bank", days: int = 2) -> dict[str, Any]:
    run_at = (utc_now() + timedelta(days=days)).replace(microsecond=0)
    return {
        "name": name,
        "tool_name": "list_devices",
        "arguments": {},
        "schedule": {"kind": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
    }


async def _create(client: AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(BASE, json=body, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def _owner_id(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["id"])


async def _set_job(app: Any, job_id: str, **values: Any) -> None:
    async with app.state.session_factory() as db:
        await db.execute(update(ScheduledJob).where(ScheduledJob.id == uuid.UUID(job_id)).values(**values))
        await db.commit()


async def _second_client(app: Any, email: str, password: str = "another-passphrase-123") -> AsyncClient:
    async with app.state.session_factory() as db:
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


async def test_create_daily_returns_201_with_the_next_local_occurrence(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _daily())
    assert job["state"] == "scheduled"
    assert job["schedule"]["kind"] == "daily"
    assert job["schedule"]["time_of_day"] == "09:00"
    assert job["schedule"]["weekdays"] is None
    assert job["retry"] == {"max_attempts": 1, "backoff_seconds": 300}
    assert job["max_runtime_seconds"] == 300
    next_run = datetime.fromisoformat(job["next_run_at"].replace("Z", "+00:00"))
    assert next_run > utc_now()
    assert next_run.strftime("%H:%M") == "09:00"


async def test_create_weekly_keeps_its_weekdays(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _weekly(weekdays=[4, 0, 4]))
    assert job["schedule"]["kind"] == "weekly"
    assert job["schedule"]["weekdays"] == [0, 4]
    next_run = datetime.fromisoformat(job["next_run_at"].replace("Z", "+00:00"))
    assert next_run.weekday() in (0, 4)


async def test_create_once_uses_the_run_time_as_next_run(client: AsyncClient) -> None:
    await create_owner(client)
    body = _once()
    job = await _create(client, body)
    assert job["schedule"]["kind"] == "once"
    assert job["schedule"]["time_of_day"] is None
    assert datetime.fromisoformat(job["next_run_at"].replace("Z", "+00:00")) == datetime.fromisoformat(
        body["schedule"]["run_at"]
    )


async def test_unknown_tool_is_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.post(BASE, json=_daily(tool_name="format_disk"), headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_arguments"


async def test_arguments_are_validated_against_the_tool(client: AsyncClient) -> None:
    await create_owner(client)
    bad = _daily(tool_name="launch_app", arguments={"app": "chrome", "pid": 4})
    response = await client.post(BASE, json=bad, headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_arguments"
    good = _daily(tool_name="launch_app", arguments={"app": "chrome"})
    assert (await client.post(BASE, json=good, headers=csrf_headers(client))).status_code == 201


async def test_once_in_the_past_is_rejected_with_schedule_in_past(client: AsyncClient) -> None:
    await create_owner(client)
    body = _once()
    body["schedule"]["run_at"] = (utc_now() - timedelta(hours=1)).isoformat()
    response = await client.post(BASE, json=body, headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schedule_in_past"


async def test_invalid_time_zone_and_unknown_fields_are_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    zone = _daily()
    zone["schedule"]["timezone"] = "Mars/Olympus"
    extra = _daily(surprise=True)
    for body in (zone, extra):
        response = await client.post(BASE, json=body, headers=csrf_headers(client))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_retry_attempts_above_five_are_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    body = _daily(retry={"max_attempts": 6, "backoff_seconds": 60})
    response = await client.post(BASE, json=body, headers=csrf_headers(client))
    assert response.status_code == 422


async def test_device_must_belong_to_the_user(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _second_client(app, "devices@example.com")
    async with app.state.session_factory() as db:
        other_user = (await db.scalars(select(User).where(User.email == "devices@example.com"))).one()
        device = Device(user_id=other_user.id, name="Their laptop", registered_at=utc_now(), pairing_status="paired")
        db.add(device)
        await db.commit()
        device_id = str(device.id)
    response = await client.post(BASE, json=_daily(device_id=device_id), headers=csrf_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "device_not_found"


async def test_revoked_device_is_rejected(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    owner_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        device = Device(
            user_id=owner_id,
            name="Old laptop",
            registered_at=utc_now(),
            pairing_status="revoked",
            revoked_at=utc_now(),
        )
        db.add(device)
        await db.commit()
        device_id = str(device.id)
    response = await client.post(BASE, json=_daily(device_id=device_id), headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_revoked"


async def test_list_is_ordered_by_next_run_with_nulls_last(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    late = await _create(client, _daily("late", "23:59"))
    early = await _create(client, _daily("early", "00:01"))
    undated = await _create(client, _daily("undated", "12:00"))
    await _set_job(app, undated["id"], next_run_at=None)
    items = (await client.get(BASE)).json()["items"]
    assert [item["id"] for item in items][-1] == undated["id"]
    dated = [item for item in items if item["next_run_at"] is not None]
    assert [item["next_run_at"] for item in dated] == sorted(item["next_run_at"] for item in dated)
    assert {item["id"] for item in items} == {late["id"], early["id"], undated["id"]}


async def test_derived_states_follow_the_documented_order(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    scheduled = await _create(client, _daily("scheduled"))
    paused = await _create(client, _daily("paused"))
    disabled = await _create(client, _daily("disabled"))
    failing = await _create(client, _daily("failing"))
    completed = await _create(client, _once("completed"))
    await client.patch(f"{BASE}/{paused['id']}", json={"paused": True}, headers=csrf_headers(client))
    await client.patch(f"{BASE}/{disabled['id']}", json={"enabled": False}, headers=csrf_headers(client))
    await _set_job(app, failing["id"], consecutive_failures=2)
    await _set_job(app, completed["id"], ended_at=utc_now(), next_run_at=None)
    states = {item["name"]: item["state"] for item in (await client.get(BASE)).json()["items"]}
    assert states == {
        "scheduled": "scheduled",
        "paused": "paused",
        "disabled": "disabled",
        "failing": "failing",
        "completed": "completed",
    }
    await _set_job(app, completed["id"], enabled=False)
    await _set_job(app, paused["id"], enabled=False, paused=True)
    states = {item["name"]: item["state"] for item in (await client.get(BASE)).json()["items"]}
    assert states["completed"] == "completed"
    assert states["paused"] == "paused"


async def test_pause_and_resume_recomputes_the_next_run_without_catching_up(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _daily("resume me", "09:00"))
    paused = await client.patch(f"{BASE}/{job['id']}", json={"paused": True}, headers=csrf_headers(client))
    assert paused.json()["state"] == "paused"
    # Simulate a slot that came due while the job was paused.
    stale = utc_now() - timedelta(days=3)
    await _set_job(app, job["id"], next_run_at=stale)
    resumed = await client.patch(f"{BASE}/{job['id']}", json={"paused": False}, headers=csrf_headers(client))
    body = resumed.json()
    assert body["state"] == "scheduled"
    assert datetime.fromisoformat(body["next_run_at"].replace("Z", "+00:00")) > utc_now()


async def test_resuming_a_one_time_schedule_whose_run_time_passed_is_refused(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _once("late"))
    await client.patch(f"{BASE}/{job['id']}", json={"enabled": False}, headers=csrf_headers(client))
    await _set_job(app, job["id"], run_at=utc_now() - timedelta(hours=1), next_run_at=utc_now() - timedelta(hours=1))
    response = await client.patch(f"{BASE}/{job['id']}", json={"enabled": True}, headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schedule_in_past"


async def test_disable_keeps_history_and_the_runs_endpoint_still_answers(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _daily("with history"))
    owner_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        db.add(
            ScheduleRun(
                job_id=uuid.UUID(job["id"]),
                user_id=owner_id,
                scheduled_for=datetime(2026, 10, 18, 9, 0, tzinfo=UTC),
                attempt=1,
                status="succeeded",
                created_at=utc_now(),
                finished_at=utc_now(),
            )
        )
        await db.commit()
    disabled = await client.patch(f"{BASE}/{job['id']}", json={"enabled": False}, headers=csrf_headers(client))
    assert disabled.json()["state"] == "disabled"
    runs = await client.get(f"{BASE}/{job['id']}/runs")
    assert runs.status_code == 200
    assert [run["status"] for run in runs.json()["items"]] == ["succeeded"]


async def test_runs_endpoint_respects_the_limit(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _daily("many runs"))
    owner_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        for day in range(1, 6):
            db.add(
                ScheduleRun(
                    job_id=uuid.UUID(job["id"]),
                    user_id=owner_id,
                    scheduled_for=datetime(2026, 10, day, 9, 0, tzinfo=UTC),
                    attempt=1,
                    status="failed",
                    created_at=utc_now(),
                    error={"code": "timeout", "message": "Timed out."},
                )
            )
        await db.commit()
    limited = await client.get(f"{BASE}/{job['id']}/runs", params={"limit": 2})
    assert len(limited.json()["items"]) == 2
    assert limited.json()["items"][0]["scheduled_for"] > limited.json()["items"][1]["scheduled_for"]


async def test_another_users_schedule_is_not_found(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _daily("private"))
    other = await _second_client(app, "stranger@example.com")
    assert (await other.get(f"{BASE}/{job['id']}/runs")).status_code == 404
    patch = await other.patch(f"{BASE}/{job['id']}", json={"paused": True}, headers=csrf_headers(other))
    assert patch.status_code == 404
    assert patch.json()["error"]["code"] == "schedule_not_found"
    assert (await other.delete(f"{BASE}/{job['id']}", headers=csrf_headers(other))).status_code == 404
    assert (await other.get(BASE)).json()["items"] == []
    assert (await client.get(BASE)).json()["items"][0]["state"] == "scheduled"


async def test_malformed_schedule_id_is_not_found(client: AsyncClient) -> None:
    await create_owner(client)
    assert (await client.patch(f"{BASE}/not-a-uuid", json={"paused": True}, headers=csrf_headers(client))).status_code == 404


async def test_patch_schedule_recomputes_the_run_and_cannot_change_the_tool(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _daily("reschedule", "09:00"))
    response = await client.patch(
        f"{BASE}/{job['id']}",
        json={"schedule": {"kind": "weekly", "time_of_day": "17:45", "weekdays": [6], "timezone": "UTC"}},
        headers=csrf_headers(client),
    )
    body = response.json()
    assert body["schedule"]["kind"] == "weekly"
    assert body["schedule"]["weekdays"] == [6]
    assert datetime.fromisoformat(body["next_run_at"].replace("Z", "+00:00")).weekday() == 6
    assert body["tool_name"] == "list_devices"
    rejected = await client.patch(f"{BASE}/{job['id']}", json={"tool_name": "launch_app"}, headers=csrf_headers(client))
    assert rejected.status_code == 422


async def test_patch_arguments_are_revalidated(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _daily(tool_name="launch_app", arguments={"app": "chrome"}))
    bad = await client.patch(f"{BASE}/{job['id']}", json={"arguments": {}}, headers=csrf_headers(client))
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_arguments"
    good = await client.patch(f"{BASE}/{job['id']}", json={"arguments": {"app": "vscode"}}, headers=csrf_headers(client))
    assert good.json()["arguments"] == {"app": "vscode"}


async def test_patch_rejects_explicit_nulls_for_required_fields(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _daily())
    response = await client.patch(f"{BASE}/{job['id']}", json={"name": None}, headers=csrf_headers(client))
    assert response.status_code == 422


async def test_a_completed_schedule_cannot_be_rescheduled(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    job = await _create(client, _once("done"))
    await _set_job(app, job["id"], ended_at=utc_now(), next_run_at=None)
    response = await client.patch(
        f"{BASE}/{job['id']}", json={"schedule": _once()["schedule"]}, headers=csrf_headers(client)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "schedule_completed"


async def test_delete_removes_the_schedule_and_its_history(client: AsyncClient) -> None:
    await create_owner(client)
    job = await _create(client, _daily("bin me"))
    deleted = await client.delete(f"{BASE}/{job['id']}", headers=csrf_headers(client))
    assert deleted.status_code == 204
    assert (await client.get(BASE)).json()["items"] == []
    assert (await client.get(f"{BASE}/{job['id']}/runs")).status_code == 404


async def test_the_active_schedule_limit_is_enforced(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    owner_id = await _owner_id(client)
    async with app.state.session_factory() as db:
        for index in range(100):
            await create_schedule(db, user_id=owner_id, data=ScheduleCreate.model_validate(_daily(f"bulk {index}")), tools=app.state.tools)
        await db.commit()
    response = await client.post(BASE, json=_daily("one too many"), headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "schedule_limit"


async def test_unauthenticated_requests_are_refused(app: Any) -> None:
    anonymous = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN})
    assert (await anonymous.get(BASE)).status_code == 401
