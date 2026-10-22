"""Scheduler worker against PostgreSQL with a fake task engine: claims, duplicates, retries, notifications,
emergency stop, restart safety, outcome following and timeouts. One test uses the real engine for a server tool."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.errors import AppError
from app.core.time import utc_now
from app.models.identity import User
from app.models.platform import Notification
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.models.tasks import Task
from app.schemas.schedules import ScheduleCreate
from app.scheduler.worker import Scheduler, idempotency_key
from app.security.passwords import hash_password
from app.services.schedule_service import create_schedule
from app.services.settings_service import set_emergency_stopped
from app.services.task_states import ProposalRefusedError

SLOT = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)  # the next daily 09:00 UTC slot after NOW
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
DUE = SLOT + timedelta(seconds=1)


class FakeEngine:
    """Test double for TaskEngine.propose and cancel. It writes real Task rows so run foreign keys hold."""

    def __init__(self) -> None:
        self.status = "completed"
        self.error: dict[str, Any] | None = None
        self.proposals: list[dict[str, Any]] = []
        self.cancelled: list[uuid.UUID] = []
        self.raise_for: set[str] = set()
        self.refuse_with: str | None = None

    async def propose(
        self,
        db: Any,
        *,
        user_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        source: str,
        device_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        title: str | None = None,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> Task:
        self.proposals.append({"tool_name": tool_name, "source": source, "idempotency_key": idempotency_key})
        if tool_name in self.raise_for:
            raise RuntimeError("unexpected failure")
        if self.refuse_with is not None:
            raise ProposalRefusedError(self.refuse_with, "Refused by policy.")
        now = utc_now()
        terminal = self.status in ("completed", "failed", "cancelled")
        task = Task(
            user_id=user_id,
            source=source,
            title=title or tool_name,
            description=description,
            tool_name=tool_name,
            arguments=arguments,
            risk_level="low",
            status=self.status,
            required_permissions=[],
            attempt=0,
            max_attempts=1,
            result={"summary": "done", "output": {}} if self.status == "completed" else None,
            error=self.error if self.status == "failed" else None,
            created_at=now,
            updated_at=now,
            started_at=now if terminal else None,
            finished_at=now if terminal else None,
            idempotency_key=idempotency_key,
        )
        db.add(task)
        await db.flush()
        return task

    async def cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        self.cancelled.append(task_id)
        raise AppError("This task has already finished.", code="task_state_conflict", status_code=409)


async def _user(app: Any, email: str = "worker@example.com") -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name="Worker", password_hash=hash_password("unused-passphrase-1"), role="owner", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def _job(app: Any, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    body: dict[str, Any] = {
        "name": "Morning brief",
        "tool_name": "list_devices",
        "arguments": {},
        "schedule": {"kind": "daily", "time_of_day": "09:00", "timezone": "UTC"},
    }
    retry = overrides.pop("retry", None)
    max_runtime = overrides.pop("max_runtime_seconds", None)
    body.update(overrides)
    if retry is not None:
        body["retry"] = retry
    if max_runtime is not None:
        body["max_runtime_seconds"] = max_runtime
    async with app.state.session_factory() as db:
        job = await create_schedule(db, user_id=user_id, data=ScheduleCreate.model_validate(body), tools=app.state.tools, now=NOW)
        await db.commit()
        return job.id


async def _once(app: Any, user_id: uuid.UUID, run_at: datetime, **overrides: Any) -> uuid.UUID:
    return await _job(
        app,
        user_id,
        schedule={"kind": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
        **overrides,
    )


def _scheduler(app: Any, engine: Any) -> Scheduler:
    return Scheduler(settings=app.state.settings, session_factory=app.state.session_factory, tasks=engine, events=app.state.events)


async def _job_row(app: Any, job_id: uuid.UUID) -> ScheduledJob:
    async with app.state.session_factory() as db:
        job = await db.get(ScheduledJob, job_id)
        assert job is not None
        return job


async def _runs(app: Any, job_id: uuid.UUID) -> list[ScheduleRun]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(select(ScheduleRun).where(ScheduleRun.job_id == job_id).order_by(ScheduleRun.attempt))
        return list(rows.all())


async def _notifications(app: Any, user_id: uuid.UUID) -> list[Notification]:
    async with app.state.session_factory() as db:
        rows = await db.scalars(select(Notification).where(Notification.user_id == user_id, Notification.kind == "schedule_failed"))
        return list(rows.all())


async def _set(app: Any, job_id: uuid.UUID, **values: Any) -> None:
    async with app.state.session_factory() as db:
        await db.execute(update(ScheduledJob).where(ScheduledJob.id == job_id).values(**values))
        await db.commit()


async def test_a_due_job_creates_exactly_one_run_and_advances_the_cursor(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    engine = FakeEngine()
    fired = await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    assert fired == 1
    runs = await _runs(app, job_id)
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].attempt == 1
    assert runs[0].scheduled_for == SLOT
    assert runs[0].task_id is not None
    job = await _job_row(app, job_id)
    assert job.next_run_at == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    assert job.consecutive_failures == 0
    assert job.last_status == "succeeded"
    assert engine.proposals == [
        {"tool_name": "list_devices", "source": "schedule", "idempotency_key": idempotency_key(job_id, SLOT, 1)}
    ]


async def test_running_the_same_slot_twice_creates_no_duplicate(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    engine = FakeEngine()
    scheduler = _scheduler(app, engine)
    assert await scheduler.run_due_jobs_once(now=DUE) == 1
    assert await scheduler.run_due_jobs_once(now=DUE) == 0
    # Simulate a crash that lost the cursor update: the slot is due again but its run row exists.
    await _set(app, job_id, next_run_at=SLOT)
    assert await scheduler.run_due_jobs_once(now=DUE) == 0
    assert len(await _runs(app, job_id)) == 1
    assert len(engine.proposals) == 1
    job = await _job_row(app, job_id)
    assert job.next_run_at == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


async def test_a_failing_task_schedules_a_retry_for_the_same_slot(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, retry={"max_attempts": 2, "backoff_seconds": 60})
    engine = FakeEngine()
    engine.status = "failed"
    engine.error = {"code": "execution_failed", "message": "The device refused."}
    await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    runs = await _runs(app, job_id)
    assert [(run.attempt, run.status) for run in runs] == [(1, "failed"), (2, "retry_wait")]
    assert runs[1].scheduled_for == SLOT
    assert runs[0].error == {"code": "execution_failed", "message": "The device refused."}
    job = await _job_row(app, job_id)
    assert job.next_run_at == DUE + timedelta(seconds=60)
    assert job.consecutive_failures == 0
    assert job.last_status == "failed"


async def test_the_final_failure_notifies_once_without_arguments_and_counts_failures(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(
        app,
        user_id,
        tool_name="launch_app",
        arguments={"app": "ZZ-unique-app-label"},
        retry={"max_attempts": 2, "backoff_seconds": 60},
    )
    engine = FakeEngine()
    engine.status = "failed"
    engine.error = {"code": "execution_failed", "message": "Launch failed."}
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=60))
    runs = await _runs(app, job_id)
    assert [run.status for run in runs] == ["failed", "failed"]
    job = await _job_row(app, job_id)
    assert job.consecutive_failures == 1
    notices = await _notifications(app, user_id)
    assert len(notices) == 1
    assert notices[0].severity == "error"
    assert notices[0].related_id == str(job_id)
    assert "ZZ-unique-app-label" not in notices[0].body
    assert "Launch failed." not in notices[0].body


async def test_consecutive_failures_reach_failing_and_a_success_resets_them(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    engine = FakeEngine()
    engine.status = "failed"
    engine.error = {"code": "execution_failed", "message": "Nope."}
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    await scheduler.run_due_jobs_once(now=DUE + timedelta(days=1))
    job = await _job_row(app, job_id)
    assert job.consecutive_failures == 2
    engine.status = "completed"
    await scheduler.run_due_jobs_once(now=DUE + timedelta(days=2))
    job = await _job_row(app, job_id)
    assert job.consecutive_failures == 0


async def test_a_retry_fires_after_its_backoff_and_can_succeed(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, retry={"max_attempts": 3, "backoff_seconds": 120})
    engine = FakeEngine()
    engine.status = "failed"
    engine.error = {"code": "execution_failed", "message": "Busy."}
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    assert await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=60)) == 0  # backoff not over
    engine.status = "completed"
    assert await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=121)) == 1
    runs = await _runs(app, job_id)
    assert [(run.attempt, run.status) for run in runs] == [(1, "failed"), (2, "succeeded")]
    assert engine.proposals[-1]["idempotency_key"] == idempotency_key(job_id, SLOT, 2)
    job = await _job_row(app, job_id)
    assert job.consecutive_failures == 0
    assert job.next_run_at == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


async def test_emergency_stop_skips_the_run_and_creates_no_task(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    async with app.state.session_factory() as db:
        await set_emergency_stopped(db, user_id, True)
        await db.commit()
    engine = FakeEngine()
    await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    runs = await _runs(app, job_id)
    assert [(run.status, (run.error or {}).get("code")) for run in runs] == [("skipped", "emergency_stop_active")]
    assert runs[0].task_id is None
    assert engine.proposals == []
    async with app.state.session_factory() as db:
        assert (await db.scalars(select(Task).where(Task.user_id == user_id))).all() == []
    job = await _job_row(app, job_id)
    assert job.next_run_at == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    assert job.consecutive_failures == 0


async def test_a_new_scheduler_instance_does_not_refire_a_claimed_slot(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    first = FakeEngine()
    assert await _scheduler(app, first).run_due_jobs_once(now=DUE) == 1
    # Restart: a fresh worker with a fresh engine, and the cursor reset as if the commit had been lost.
    restarted = FakeEngine()
    await _set(app, job_id, next_run_at=SLOT)
    assert await _scheduler(app, restarted).run_due_jobs_once(now=DUE) == 0
    assert restarted.proposals == []
    assert len(await _runs(app, job_id)) == 1


async def test_a_queued_task_is_followed_to_completion(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, tool_name="launch_app", arguments={"app": "chrome"})
    engine = FakeEngine()
    engine.status = "queued"
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    run = (await _runs(app, job_id))[0]
    assert run.status == "queued"
    assert run.task_id is not None
    async with app.state.session_factory() as db:
        task = await db.get(Task, run.task_id)
        assert task is not None
        task.status = "completed"
        task.finished_at = utc_now()
        task.result = {"summary": "done", "output": {}}
        await db.commit()
    await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=30))
    run = (await _runs(app, job_id))[0]
    assert run.status == "succeeded"
    assert run.finished_at is not None


async def test_an_approval_wait_is_reported_as_awaiting_approval(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, tool_name="launch_app", arguments={"app": "chrome"})
    engine = FakeEngine()
    engine.status = "awaiting_approval"
    await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    assert (await _runs(app, job_id))[0].status == "awaiting_approval"


async def test_an_expired_approval_is_a_failure_so_the_user_is_told(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, tool_name="launch_app", arguments={"app": "chrome"})
    engine = FakeEngine()
    engine.status = "awaiting_approval"
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    run = (await _runs(app, job_id))[0]
    async with app.state.session_factory() as db:
        task = await db.get(Task, run.task_id)
        assert task is not None
        task.status = "cancelled"
        task.error = {"code": "approval_expired", "message": "Nobody approved it.", "details": {}}
        task.finished_at = utc_now()
        await db.commit()
    await scheduler.run_due_jobs_once(now=DUE + timedelta(minutes=11))
    run = (await _runs(app, job_id))[0]
    assert run.status == "failed"
    assert run.error == {"code": "approval_expired", "message": "Nobody approved it."}


async def test_a_run_that_never_finishes_times_out_and_its_task_is_cancelled(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, max_runtime_seconds=30)
    engine = FakeEngine()
    engine.status = "queued"
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    run = (await _runs(app, job_id))[0]
    await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=31))
    run = (await _runs(app, job_id))[0]
    assert run.status == "failed"
    assert (run.error or {}).get("code") == "timeout"
    assert engine.cancelled == [run.task_id]
    assert len(await _notifications(app, user_id)) == 1


async def test_a_refused_proposal_is_a_failed_run_with_the_refusal_code(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id, tool_name="launch_app", arguments={"app": "chrome"})
    engine = FakeEngine()
    engine.refuse_with = "tool_disabled"
    await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    run = (await _runs(app, job_id))[0]
    assert run.status == "failed"
    assert run.error == {"code": "tool_disabled", "message": "Refused by policy."}
    assert run.task_id is None


async def test_a_one_time_schedule_completes_when_it_fires(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _once(app, user_id, SLOT)
    engine = FakeEngine()
    scheduler = _scheduler(app, engine)
    assert await scheduler.run_due_jobs_once(now=DUE) == 1
    job = await _job_row(app, job_id)
    assert job.ended_at is not None
    assert job.next_run_at is None
    assert await scheduler.run_due_jobs_once(now=DUE + timedelta(days=1)) == 0
    assert len(await _runs(app, job_id)) == 1


async def test_a_one_time_schedule_with_retries_stays_open_until_the_final_attempt(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _once(app, user_id, SLOT, retry={"max_attempts": 2, "backoff_seconds": 60})
    engine = FakeEngine()
    engine.status = "failed"
    engine.error = {"code": "execution_failed", "message": "Still failing."}
    scheduler = _scheduler(app, engine)
    await scheduler.run_due_jobs_once(now=DUE)
    job = await _job_row(app, job_id)
    assert job.ended_at is None
    assert job.next_run_at == DUE + timedelta(seconds=60)
    await scheduler.run_due_jobs_once(now=DUE + timedelta(seconds=60))
    job = await _job_row(app, job_id)
    assert job.ended_at is not None
    assert job.next_run_at is None
    assert len(await _notifications(app, user_id)) == 1


async def test_an_unexpected_error_in_one_job_does_not_stop_the_others(app: Any) -> None:
    user_id = await _user(app)
    broken = await _job(app, user_id, name="Broken", tool_name="launch_app", arguments={"app": "chrome"})
    healthy = await _job(app, user_id, name="Healthy")
    engine = FakeEngine()
    engine.raise_for = {"launch_app"}
    fired = await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    assert fired == 1
    assert len(await _runs(app, healthy)) == 1
    assert await _runs(app, broken) == []
    assert (await _job_row(app, broken)).next_run_at == SLOT  # still due for the next pass


async def test_paused_and_disabled_jobs_are_not_fired(app: Any) -> None:
    user_id = await _user(app)
    paused = await _job(app, user_id, name="Paused")
    disabled = await _job(app, user_id, name="Disabled")
    await _set(app, paused, paused=True)
    await _set(app, disabled, enabled=False)
    engine = FakeEngine()
    assert await _scheduler(app, engine).run_due_jobs_once(now=DUE) == 0
    assert engine.proposals == []


async def test_a_run_that_lost_its_task_link_is_linked_by_its_idempotency_key(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    engine = FakeEngine()
    await _scheduler(app, engine).run_due_jobs_once(now=DUE)
    run = (await _runs(app, job_id))[0]
    async with app.state.session_factory() as db:
        stored = await db.get(ScheduleRun, run.id)
        assert stored is not None
        stored.task_id = None
        stored.status = "queued"
        stored.finished_at = None
        await db.commit()
    await _scheduler(app, engine).run_due_jobs_once(now=DUE + timedelta(seconds=5))
    run = (await _runs(app, job_id))[0]
    assert run.task_id is not None
    assert run.status == "succeeded"


async def test_a_server_tool_runs_through_the_real_task_engine(app: Any) -> None:
    user_id = await _user(app)
    job_id = await _job(app, user_id)
    await _scheduler(app, app.state.task_engine).run_due_jobs_once(now=DUE)
    runs = await _runs(app, job_id)
    assert [run.status for run in runs] == ["succeeded"]
    async with app.state.session_factory() as db:
        task = await db.get(Task, runs[0].task_id)
        assert task is not None
        assert task.source == "schedule"
        assert task.user_id == user_id


async def test_start_and_stop_manage_the_background_loop(app: Any) -> None:
    scheduler = _scheduler(app, FakeEngine())
    await scheduler.start()
    await scheduler.start()
    await scheduler.stop()
    await scheduler.stop()


async def test_a_reminder_is_scheduled_by_the_engine_and_delivered_by_the_worker(app: Any) -> None:
    user_id = await _user(app)
    run_at = utc_now() + timedelta(hours=2)
    async with app.state.session_factory() as db:
        task = await app.state.task_engine.propose(
            db,
            user_id=user_id,
            tool_name="create_reminder",
            arguments={"message": "call the bank", "when": run_at.isoformat()},
            source="chat",
        )
        await db.commit()
    assert task.status == "completed"
    schedule_id = uuid.UUID(task.result["output"]["schedule_id"])
    job = await _job_row(app, schedule_id)
    assert job.tool_name == "send_reminder"
    assert job.schedule_kind == "once"
    await _scheduler(app, app.state.task_engine).run_due_jobs_once(now=run_at + timedelta(seconds=1))
    runs = await _runs(app, schedule_id)
    assert [run.status for run in runs] == ["succeeded"]
    async with app.state.session_factory() as db:
        reminders = (
            await db.scalars(select(Notification).where(Notification.user_id == user_id, Notification.kind == "reminder"))
        ).all()
    assert [row.body for row in reminders] == ["call the bank"]
