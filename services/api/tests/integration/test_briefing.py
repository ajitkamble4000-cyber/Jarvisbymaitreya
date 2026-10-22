"""The briefing plugin against PostgreSQL: counts, isolation, calendar states, the summary cap and the schedule API.

Google Calendar is replaced by an httpx.MockTransport. No request leaves the process. The plugin reads the server
settings through ``get_settings`` and opens its HTTP client through ``_new_http_client``, so each calendar test
swaps those two seams and nothing else.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.time import utc_now
from app.integrations.oauth.providers import GOOGLE_CALENDAR
from app.integrations.oauth.tokens import Credential, load_integration, save_connection
from app.models.identity import User, UserPreference
from app.models.platform import Notification
from app.models.scheduling import ScheduledJob
from app.models.tasks import Task
from app.plugins import briefing
from tests.conftest import create_owner, csrf_headers

TOOL = "daily_briefing"
CALENDAR_PATH = "/calendar/v3/calendars/primary/events"
ACCESS_TOKEN = "ya29.access-token-test"


# -- helpers ------------------------------------------------------------------------------------


async def _add_user(app: Any, email: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name=email.split("@")[0], password_hash="!unused", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def _set_timezone(app: Any, user_id: uuid.UUID, zone: str) -> None:
    now = utc_now()
    async with app.state.session_factory() as db:
        db.add(UserPreference(user_id=user_id, settings={"timezone": zone}, created_at=now, updated_at=now))
        await db.commit()


async def _task(
    app: Any,
    user_id: uuid.UUID,
    title: str,
    status: str,
    *,
    finished_at: datetime | None = None,
    created_at: datetime | None = None,
) -> None:
    now = utc_now()
    async with app.state.session_factory() as db:
        db.add(
            Task(
                user_id=user_id,
                source="chat",
                title=title,
                description="",
                tool_name="list_devices",
                arguments={},
                risk_level="low",
                status=status,
                required_permissions=[],
                attempt=0,
                max_attempts=1,
                cancel_requested=False,
                finished_at=finished_at,
                created_at=created_at or now,
                updated_at=now,
            )
        )
        await db.commit()


async def _notification(
    app: Any, user_id: uuid.UUID, *, read_at: datetime | None = None, dismissed_at: datetime | None = None
) -> None:
    async with app.state.session_factory() as db:
        db.add(
            Notification(
                user_id=user_id,
                kind="task_completed",
                severity="info",
                title="Task completed",
                body="",
                read_at=read_at,
                dismissed_at=dismissed_at,
                created_at=utc_now(),
            )
        )
        await db.commit()


async def _schedule(
    app: Any,
    user_id: uuid.UUID,
    name: str,
    next_run_at: datetime | None,
    *,
    enabled: bool = True,
    paused: bool = False,
    ended_at: datetime | None = None,
) -> None:
    now = utc_now()
    async with app.state.session_factory() as db:
        db.add(
            ScheduledJob(
                user_id=user_id,
                name=name,
                description="",
                tool_name=TOOL,
                arguments={},
                schedule_kind="daily",
                time_of_day="07:00",
                weekdays=[],
                timezone="UTC",
                enabled=enabled,
                paused=paused,
                next_run_at=next_run_at,
                ended_at=ended_at,
                consecutive_failures=0,
                max_runtime_seconds=300,
                retry_max_attempts=1,
                retry_backoff_seconds=300,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()


async def _connect_google(app: Any, user_id: uuid.UUID) -> None:
    now = utc_now()
    async with app.state.session_factory() as db:
        await save_connection(
            db,
            box=app.state.secret_box,
            user_id=user_id,
            provider=GOOGLE_CALENDAR,
            credential=Credential(
                ACCESS_TOKEN,
                "refresh-token-test",
                now + timedelta(hours=1),
                "https://www.googleapis.com/auth/calendar.readonly",
            ),
            label="ada@example.com",
            now=now,
        )
        await db.commit()


async def _briefing(app: Any, user_id: uuid.UUID, arguments: dict[str, Any] | None = None) -> Any:
    async with app.state.session_factory() as db:
        task = await app.state.task_engine.propose(
            db, user_id=user_id, tool_name=TOOL, arguments=arguments or {}, source="chat"
        )
        await db.commit()
        return task


def _lines(task: Any) -> list[str]:
    return task.result["output"]["summary"].splitlines()


class FakeGoogle:
    """Answers the Calendar events endpoint. Any other host is an error, so a stray request fails loudly."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = body if body is not None else {"kind": "calendar#events", "items": []}
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "www.googleapis.com" or request.url.path != CALENDAR_PATH:
            return httpx.Response(500, json={"error": "unexpected request"})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "secret-provider-text"})
        return httpx.Response(200, json=self.body)


def _use_google(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeGoogle, *, configured: bool = True
) -> None:
    update: dict[str, Any] = {}
    if configured:
        update = {
            "google_oauth_client_id": "client-id-test",
            "google_oauth_client_secret": SecretStr("client-secret-test"),
        }
    plugin_settings = settings.model_copy(update=update)
    monkeypatch.setattr(briefing, "get_settings", lambda: plugin_settings)
    monkeypatch.setattr(
        briefing, "_new_http_client", lambda _settings: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )


def _event(title: str, start: dict[str, str], status: str = "confirmed") -> dict[str, Any]:
    return {"id": title, "status": status, "summary": title, "start": start, "end": start}


# -- counts and sections ------------------------------------------------------------------------


async def test_counts_sections_and_schedules_for_one_user(app: Any) -> None:
    now = utc_now()
    user_id = await _add_user(app, "ada@example.com")
    await _set_timezone(app, user_id, "Asia/Tokyo")

    await _task(app, user_id, "Queued one", "queued")
    await _task(app, user_id, "Queued two", "queued")
    await _task(app, user_id, "Running", "running")
    for index, title in enumerate(("Approve alpha", "Approve beta", "Approve gamma")):
        await _task(app, user_id, title, "awaiting_approval", created_at=now - timedelta(minutes=10 - index))
    await _task(app, user_id, "Done recently", "completed", finished_at=now - timedelta(hours=1))
    await _task(app, user_id, "Done recently 2", "completed", finished_at=now - timedelta(hours=2))
    await _task(app, user_id, "Done long ago", "completed", finished_at=now - timedelta(hours=30))
    await _task(app, user_id, "Failed recently", "failed", finished_at=now - timedelta(hours=3))
    await _task(app, user_id, "Failed long ago", "failed", finished_at=now - timedelta(hours=25))
    await _task(app, user_id, "Cancelled", "cancelled")

    await _notification(app, user_id)
    await _notification(app, user_id)
    await _notification(app, user_id, read_at=now)
    await _notification(app, user_id, dismissed_at=now)

    soon = now + timedelta(hours=1)
    await _schedule(app, user_id, "Morning news", soon)
    await _schedule(app, user_id, "Weekly report", now + timedelta(hours=2))
    await _schedule(app, user_id, "Paused job", now + timedelta(minutes=30), paused=True)
    await _schedule(app, user_id, "Disabled job", now + timedelta(minutes=40), enabled=False)
    await _schedule(app, user_id, "Ended job", now + timedelta(minutes=50), ended_at=now)
    await _schedule(app, user_id, "Later", now + timedelta(days=5))

    task = await _briefing(app, user_id, {"include_calendar": False})
    assert task.status == "completed", task.error
    output = task.result["output"]
    tasks = output["sections"]["tasks"]

    assert tasks["counts"] == {
        "queued": 2,
        "running": 1,
        "awaiting_approval": 3,
        "completed": 3,
        "failed": 2,
        "cancelled": 1,
    }
    assert tasks["awaiting_approval"] == ["Approve alpha", "Approve beta", "Approve gamma"]
    assert tasks["last_24_hours"] == {"completed": 2, "failed": 1}
    assert output["sections"]["notifications"] == {"unread": 2}

    names = [item["name"] for item in output["sections"]["schedules"]["next"]]
    assert names == ["Morning news", "Weekly report", "Later"]
    first = output["sections"]["schedules"]["next"][0]
    expected_local = soon.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%a %Y-%m-%d %H:%M")
    assert first["next_run_local"] == expected_local
    assert output["sections"]["schedules"]["timezone"] == "Asia/Tokyo"

    lines = _lines(task)
    assert lines[0].endswith("Times are in Asia/Tokyo.")
    assert "Tasks: queued 2, running 1, awaiting approval 3, completed 3, failed 2, cancelled 1." in lines
    assert "Awaiting approval:" in lines and "- Approve gamma" in lines
    assert "Last 24 hours: 2 completed, 1 failed." in lines
    assert "Notifications: 2 unread." in lines
    assert "Next schedules:" in lines
    assert "Calendar: not requested." in lines


async def test_empty_user_gets_zero_counts_and_plain_lines(app: Any) -> None:
    user_id = await _add_user(app, "empty@example.com")
    task = await _briefing(app, user_id, {"include_calendar": False})
    assert task.status == "completed", task.error
    lines = _lines(task)
    assert "Tasks: queued 0, running 0, awaiting approval 0, completed 0, failed 0, cancelled 0." in lines
    assert "Last 24 hours: 0 completed, 0 failed." in lines
    assert "Notifications: 0 unread." in lines
    assert "Next schedules: none enabled." in lines
    assert task.result["output"]["sections"]["schedules"]["timezone"] == "UTC"


async def test_only_the_first_ten_awaiting_titles_are_listed(app: Any) -> None:
    user_id = await _add_user(app, "many@example.com")
    now = utc_now()
    for index in range(12):
        await _task(
            app, user_id, f"Item {index:02d}", "awaiting_approval", created_at=now - timedelta(minutes=12 - index)
        )
    task = await _briefing(app, user_id, {"include_calendar": False})
    assert task.status == "completed", task.error
    lines = _lines(task)
    assert len(task.result["output"]["sections"]["tasks"]["awaiting_approval"]) == 10
    assert "- Item 00" in lines and "- Item 09" in lines and "- Item 10" not in lines
    assert "- and 2 more" in lines


# -- isolation ----------------------------------------------------------------------------------


async def test_another_users_rows_and_connection_never_appear(app: Any) -> None:
    ada = await _add_user(app, "ada@example.com")
    bob = await _add_user(app, "bob@example.com")
    now = utc_now()
    await _task(app, ada, "Ada own task", "awaiting_approval")
    await _task(app, bob, "Bob secret task", "awaiting_approval")
    await _task(app, bob, "Bob finished", "completed", finished_at=now - timedelta(hours=1))
    await _notification(app, bob)
    await _notification(app, bob)
    await _schedule(app, bob, "Bob schedule", now + timedelta(hours=1))
    await _connect_google(app, bob)

    task = await _briefing(app, ada, {"include_calendar": False})
    assert task.status == "completed", task.error
    output = task.result["output"]
    assert output["sections"]["tasks"]["counts"]["awaiting_approval"] == 1
    assert output["sections"]["tasks"]["counts"]["completed"] == 0
    assert output["sections"]["notifications"] == {"unread": 0}
    assert output["sections"]["schedules"]["next"] == []
    assert "Bob" not in output["summary"]
    assert "Bob" not in json.dumps(task.result)


# -- calendar -----------------------------------------------------------------------------------


async def test_calendar_not_connected_line_is_exact_and_needs_no_server_config(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_read_settings() -> Settings:
        raise AssertionError("a user without a connection must not reach the provider path")

    monkeypatch.setattr(briefing, "get_settings", must_not_read_settings)
    user_id = await _add_user(app, "plain@example.com")
    task = await _briefing(app, user_id)
    assert task.status == "completed", task.error
    assert "Calendar: not connected." in _lines(task)
    assert task.result["output"]["sections"]["calendar"] == {"status": "not_connected"}


async def test_calendar_lists_events_in_the_users_zone_and_skips_cancelled(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _add_user(app, "cal@example.com")
    await _set_timezone(app, user_id, "Asia/Tokyo")
    await _connect_google(app, user_id)
    items = [
        _event("Cancelled sync", {"dateTime": "2026-10-23T08:00:00+00:00"}, status="cancelled"),
        _event("Design review", {"dateTime": "2026-10-23T08:00:00+00:00"}),
        _event("Holiday", {"date": "2026-10-22"}),
        _event("x" * 150, {"dateTime": "2026-10-23T09:00:00+00:00"}),
    ]
    items += [_event(f"Meeting {index}", {"dateTime": f"2026-10-23T1{index % 10}:00:00+00:00"}) for index in range(8)]
    fake = FakeGoogle(body={"kind": "calendar#events", "items": items})
    _use_google(monkeypatch, app.state.settings, fake)

    task = await _briefing(app, user_id)
    assert task.status == "completed", task.error
    lines = _lines(task)
    calendar = task.result["output"]["sections"]["calendar"]
    assert calendar["status"] == "ok"
    assert calendar["count"] == 10
    assert "Calendar: 10 event(s) in the next 24 hours (first 10 shown)." in lines
    assert "- 2026-10-23 17:00: Design review" in lines
    assert "- 2026-10-22 (all day): Holiday" in lines
    assert "Cancelled sync" not in json.dumps(task.result)
    long_line = next(line for line in lines if "x" * 20 in line)
    assert long_line.endswith("x" * 77 + "...")
    assert "x" * 78 not in long_line

    sent = fake.requests[0]
    assert sent.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert sent.url.params["maxResults"] == "10"
    assert ACCESS_TOKEN not in json.dumps(task.result), "a token must never reach the result"


async def test_calendar_with_no_events_says_so(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _add_user(app, "quiet@example.com")
    await _connect_google(app, user_id)
    _use_google(monkeypatch, app.state.settings, FakeGoogle())
    task = await _briefing(app, user_id)
    assert task.status == "completed", task.error
    assert "Calendar: no events in the next 24 hours." in _lines(task)


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (500, None, "provider_unavailable"),
        (401, None, "reconnect_required"),
        (200, {"kind": "calendar#events"}, "provider_error"),
        (200, None, "not_configured"),
    ],
)
async def test_calendar_failure_is_reported_as_unavailable_not_as_empty(
    app: Any, monkeypatch: pytest.MonkeyPatch, status: int, body: Any, code: str
) -> None:
    user_id = await _add_user(app, f"fail-{code}@example.com")
    await _connect_google(app, user_id)
    fake = FakeGoogle(status=status, body=body)
    _use_google(monkeypatch, app.state.settings, fake, configured=code != "not_configured")

    task = await _briefing(app, user_id)
    assert task.status == "completed", task.error
    lines = _lines(task)
    assert f"Calendar: unavailable ({code})." in lines
    assert not any("no events" in line for line in lines)
    assert task.result["output"]["sections"]["calendar"] == {"status": "unavailable", "code": code}
    assert "secret-provider-text" not in json.dumps(task.result)


async def test_calendar_is_skipped_when_not_requested(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _add_user(app, "skip@example.com")
    await _connect_google(app, user_id)
    fake = FakeGoogle()
    _use_google(monkeypatch, app.state.settings, fake)
    task = await _briefing(app, user_id, {"include_calendar": False})
    assert task.status == "completed", task.error
    assert "Calendar: not requested." in _lines(task)
    assert fake.requests == [], "no calendar request may be sent when the caller did not ask for one"


async def test_the_provider_refusal_marks_the_connection_for_reconnect(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _add_user(app, "revoked@example.com")
    await _connect_google(app, user_id)
    _use_google(monkeypatch, app.state.settings, FakeGoogle(status=401))
    task = await _briefing(app, user_id)
    assert task.status == "completed", task.error
    async with app.state.session_factory() as db:
        row = await load_integration(db, user_id, GOOGLE_CALENDAR.key)
    assert row is not None and row.status == "expired"


# -- the summary cap ----------------------------------------------------------------------------


async def test_summary_is_capped_at_2000_characters_with_long_titles(app: Any) -> None:
    user_id = await _add_user(app, "long@example.com")
    now = utc_now()
    for index in range(10):
        await _task(
            app,
            user_id,
            f"{index:02d}-" + "A" * 190,
            "awaiting_approval",
            created_at=now - timedelta(minutes=10 - index),
        )
    for index in range(5):
        await _schedule(app, user_id, f"{index}-" + "S" * 195, now + timedelta(hours=index + 1))

    task = await _briefing(app, user_id, {"include_calendar": False})
    assert task.status == "completed", task.error
    summary = task.result["output"]["summary"]
    assert len(summary) == 2000
    assert summary.endswith("...")
    assert len(task.result["output"]["sections"]["tasks"]["awaiting_approval"]) == 10


# -- registry, /tools and the schedule API ------------------------------------------------------


async def test_the_tool_is_described_as_low_risk_read_only_and_working(client: Any) -> None:
    await create_owner(client)
    items = {item["name"]: item for item in (await client.get("/api/v1/tools")).json()["items"]}
    item = items[TOOL]
    assert item["risk_level"] == "low"
    assert item["approval_required"] is False
    assert item["target"] == "server"
    assert item["category"] == "productivity"
    assert item["status"] == "working"


async def test_the_schedule_api_accepts_daily_briefing(client: Any, app: Any) -> None:
    await create_owner(client)
    response = await client.post(
        "/api/v1/schedules",
        json={
            "name": "Morning briefing",
            "tool_name": TOOL,
            "arguments": {"include_calendar": False},
            "schedule": {"kind": "daily", "time_of_day": "07:00", "timezone": "UTC"},
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["tool_name"] == TOOL

    async with app.state.session_factory() as db:
        job = await db.scalar(select(ScheduledJob).where(ScheduledJob.tool_name == TOOL))
        assert job is not None
        assert job.arguments == {"include_calendar": False}
        assert job.next_run_at is not None
        count = await db.scalar(select(func.count()).select_from(ScheduledJob).where(ScheduledJob.tool_name == TOOL))
        assert count == 1


async def test_the_schedule_api_refuses_bad_briefing_arguments(client: Any) -> None:
    await create_owner(client)
    for arguments in ({"include_calendar": "sometimes"}, {"days": 3}):
        response = await client.post(
            "/api/v1/schedules",
            json={
                "name": "Bad briefing",
                "tool_name": TOOL,
                "arguments": arguments,
                "schedule": {"kind": "daily", "time_of_day": "07:00", "timezone": "UTC"},
            },
            headers=csrf_headers(client),
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_arguments"
