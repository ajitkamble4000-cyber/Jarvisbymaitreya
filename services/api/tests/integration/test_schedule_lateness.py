"""Late schedule slots against real PostgreSQL.

A recurring (daily or weekly) slot more than 15 minutes late is recorded as a ``skipped`` run with error code
``too_late``. Nothing is proposed, and the job moves on to its next occurrence. A once schedule still fires late by
design. The boundary is exact: 15 minutes late still fires, 16 minutes late does not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.identity import User
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.scheduler.worker import Scheduler
from app.schemas.schedules import ScheduleCreate
from app.security.passwords import hash_password
from app.services.schedule_service import create_schedule
from tests.integration.test_scheduler_worker import FakeEngine

SLOT = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)  # a Monday, so weekly schedules on weekday 0 fire on this slot
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
NEXT_DAILY = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
NEXT_WEEKLY = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


async def _user(app: Any) -> Any:
    async with app.state.session_factory() as db:
        user = User(email="late-slot@example.com", display_name="Late", password_hash=hash_password("unused-passphrase-1"), role="owner", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def _job(app: Any, user_id: Any, schedule: dict[str, Any]) -> Any:
    body = {"name": "Morning brief", "tool_name": "list_devices", "arguments": {}, "schedule": schedule}
    async with app.state.session_factory() as db:
        job = await create_schedule(db, user_id=user_id, data=ScheduleCreate.model_validate(body), tools=app.state.tools, now=NOW)
        await db.commit()
        return job.id


def _scheduler(app: Any, engine: FakeEngine) -> Scheduler:
    return Scheduler(settings=app.state.settings, session_factory=app.state.session_factory, tasks=engine, events=app.state.events)


async def _row(app: Any, job_id: Any) -> ScheduledJob:
    async with app.state.session_factory() as db:
        job = await db.get(ScheduledJob, job_id)
        assert job is not None
        return job


async def _runs(app: Any, job_id: Any) -> list[ScheduleRun]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(select(ScheduleRun).where(ScheduleRun.job_id == job_id).order_by(ScheduleRun.created_at, ScheduleRun.attempt))
        return list(rows.all())


async def test_a_daily_slot_more_than_15_minutes_late_is_skipped_and_the_job_moves_on(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, {"kind": "daily", "time_of_day": "09:00", "timezone": "UTC"})
    engine = FakeEngine()
    scheduler = _scheduler(app, engine)

    fired = await scheduler.run_due_jobs_once(now=SLOT + timedelta(minutes=20))

    assert fired == 0
    assert engine.proposals == []
    [run] = await _runs(app, job_id)
    assert run.status == "skipped"
    assert (run.error or {}).get("code") == "too_late"
    assert run.scheduled_for == SLOT
    assert run.attempt == 1
    assert run.task_id is None
    assert run.finished_at is not None
    job = await _row(app, job_id)
    assert job.next_run_at == NEXT_DAILY
    assert job.last_status == "skipped"
    assert job.last_run_at is None  # nothing ran, so the last run time does not move
    assert job.consecutive_failures == 0
    assert job.ended_at is None


async def test_the_skipped_slot_is_recorded_once_and_the_next_occurrence_fires_normally(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, {"kind": "daily", "time_of_day": "09:00", "timezone": "UTC"})
    engine = FakeEngine()
    scheduler = _scheduler(app, engine)
    late = SLOT + timedelta(minutes=20)

    await scheduler.run_due_jobs_once(now=late)
    assert await scheduler.run_due_jobs_once(now=late) == 0
    # Simulate a crash that lost the cursor update: the same slot is due again.
    async with app.state.session_factory() as db:
        job = await db.get(ScheduledJob, job_id)
        assert job is not None
        job.next_run_at = SLOT
        await db.commit()
    assert await scheduler.run_due_jobs_once(now=late) == 0
    assert [r.status for r in await _runs(app, job_id)] == ["skipped"]

    on_time = NEXT_DAILY + timedelta(seconds=30)
    assert await scheduler.run_due_jobs_once(now=on_time) == 1
    runs = await _runs(app, job_id)
    assert [(r.status, r.scheduled_for) for r in runs] == [("skipped", SLOT), ("succeeded", NEXT_DAILY)]
    assert len(engine.proposals) == 1


async def test_a_weekly_slot_more_than_15_minutes_late_is_skipped_and_advances_a_week(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, {"kind": "weekly", "time_of_day": "09:00", "weekdays": [0], "timezone": "UTC"})
    engine = FakeEngine()

    fired = await _scheduler(app, engine).run_due_jobs_once(now=SLOT + timedelta(hours=1))

    assert fired == 0 and engine.proposals == []
    [run] = await _runs(app, job_id)
    assert (run.status, (run.error or {}).get("code")) == ("skipped", "too_late")
    job = await _row(app, job_id)
    assert job.next_run_at == NEXT_WEEKLY


@pytest.mark.parametrize(
    ("minutes_late", "expected_status"),
    [(14, "succeeded"), (15, "succeeded"), (16, "skipped")],
)
async def test_the_15_minute_boundary_is_exact(app: Any, minutes_late: int, expected_status: str) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, {"kind": "daily", "time_of_day": "09:00", "timezone": "UTC"})
    engine = FakeEngine()

    await _scheduler(app, engine).run_due_jobs_once(now=SLOT + timedelta(minutes=minutes_late))

    [run] = await _runs(app, job_id)
    assert run.status == expected_status
    if expected_status == "skipped":
        assert (run.error or {}).get("code") == "too_late"
        assert engine.proposals == []
    else:
        assert len(engine.proposals) == 1


async def test_a_once_schedule_still_fires_when_it_is_late(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, {"kind": "once", "run_at": SLOT.isoformat(), "timezone": "UTC"})
    engine = FakeEngine()

    fired = await _scheduler(app, engine).run_due_jobs_once(now=SLOT + timedelta(hours=2))

    assert fired == 1
    [run] = await _runs(app, job_id)
    assert run.status == "succeeded"
    assert run.error is None
    assert len(engine.proposals) == 1
    job = await _row(app, job_id)
    assert job.ended_at is not None and job.next_run_at is None
