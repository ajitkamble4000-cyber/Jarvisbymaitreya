"""POST /api/v1/schedules/preview: the next runs of a spec, with the scheduler's rules, and nothing saved."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from typing import Any

from httpx import ASGITransport, AsyncClient

from app.schemas.schedules import ScheduleSpec
from app.services.schedule_service import preview_runs
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_daily_runs_are_consecutive_days_at_the_local_time() -> None:
    spec = ScheduleSpec(kind="daily", time_of_day="07:30", timezone="Europe/London")
    runs = preview_runs(spec, NOW)
    assert len(runs) == 5
    assert runs[0] == datetime(2026, 10, 22, 6, 30, tzinfo=UTC)  # BST ends on 25 October, so 07:30 London is 06:30 UTC
    assert all(later > earlier for earlier, later in zip(runs, runs[1:], strict=False))
    assert {run.astimezone(ZoneInfo("Europe/London")).hour for run in runs} == {7}


def test_weekly_runs_fall_only_on_the_chosen_weekdays() -> None:
    # 0 is Monday and 2 is Wednesday.
    spec = ScheduleSpec(kind="weekly", time_of_day="09:00", weekdays=[2, 0], timezone="UTC")
    runs = preview_runs(spec, NOW)
    assert [run.weekday() for run in runs] == [0, 2, 0, 2, 0]
    assert runs[0] == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def test_a_spring_forward_gap_still_gives_a_real_instant() -> None:
    # 29 March 2026 in London goes from 01:00 to 02:00. A 01:30 daily run that day must land on a real instant.
    spec = ScheduleSpec(kind="daily", time_of_day="01:30", timezone="Europe/London")
    runs = preview_runs(spec, datetime(2026, 3, 28, 12, 0, tzinfo=UTC), count=3)
    assert len(runs) == 3
    assert all(run.tzinfo is not None for run in runs)
    assert len({run for run in runs}) == 3


def test_a_one_time_run_in_the_past_has_no_preview() -> None:
    past = ScheduleSpec(kind="once", run_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    future = ScheduleSpec(kind="once", run_at=datetime(2026, 10, 30, 9, 0, tzinfo=UTC))
    assert preview_runs(past, NOW) == []
    assert preview_runs(future, NOW) == [datetime(2026, 10, 30, 9, 0, tzinfo=UTC)]


async def test_preview_returns_five_runs_and_stores_nothing(client: AsyncClient) -> None:
    await create_owner(client)
    before = (await client.get(f"{API}/schedules")).json()
    response = await client.post(
        f"{API}/schedules/preview",
        json={"kind": "daily", "time_of_day": "07:30", "timezone": "UTC"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["runs"]) == 5
    assert (await client.get(f"{API}/schedules")).json() == before


async def test_preview_refuses_a_bad_spec(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    bad_zone = await client.post(
        f"{API}/schedules/preview", json={"kind": "daily", "time_of_day": "07:30", "timezone": "Mars/Olympus"}, headers=csrf_headers(client)
    )
    assert bad_zone.status_code == 422
    missing_time = await client.post(f"{API}/schedules/preview", json={"kind": "daily", "timezone": "UTC"}, headers=csrf_headers(client))
    assert missing_time.status_code == 422
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"Origin": TEST_ORIGIN}) as anonymous:
        refused = await anonymous.post(f"{API}/schedules/preview", json={"kind": "daily", "time_of_day": "07:30", "timezone": "UTC"}, headers=csrf_headers(anonymous))
    assert refused.status_code == 401
