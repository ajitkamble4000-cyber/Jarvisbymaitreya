"""Persistent scheduler: fires due scheduled jobs as tasks and follows each run to its outcome.

All state lives in PostgreSQL, so a restart resumes from the rows that exist:

* ``ScheduledJob.next_run_at`` is the cursor. It is advanced in the same transaction that claims a slot.
* A ``ScheduleRun`` row, unique on (job, slot, attempt), is the claim. A second attempt to claim the same
  slot hits the constraint and is skipped, so a restart never fires a slot that already has a run.
* A failed attempt with attempts left inserts a ``retry_wait`` run for the same slot and moves the cursor
  to ``now + backoff``. When the cursor comes due, the retry row is claimed and fired.

Propose and settle: the worker proposes a task through the task engine. For device tools, the engine commits
its own transaction, so the claim and the task are committed together at that point. Reconciliation links a
run that lost its ``task_id`` to the task by idempotency key, and times out runs that never finish.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import to_iso, utc_now
from app.database.session import session_scope
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.models.tasks import TERMINAL_TASK_STATUSES, Task
from app.schemas.settings import SettingsDoc
from app.services.events import EventBus
from app.services.notifications import notify
from app.services.schedule_service import (
    RETRY_WAIT_STATUS,
    next_after_now,
    pending_retry,
    retry_delay,
)
from app.services.settings_service import load_settings
from app.services.task_engine import TaskEngine

log = logging.getLogger("jarvis.scheduler")

BATCH_SIZE = 20
# A recurring slot that is older than this when the scheduler reaches it (after downtime, for example) is
# skipped. The cursor moves to the next occurrence instead, so stale device or server actions do not run late.
LATE_SLOT_LIMIT = timedelta(minutes=15)
RECONCILE_BATCH = 200
LIVE_RUN_STATUSES: tuple[str, ...] = ("queued", "running", "awaiting_approval")
TASK_TO_RUN_STATUS: dict[str, str] = {
    "queued": "queued",
    "running": "running",
    "awaiting_approval": "awaiting_approval",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}
_SKIPPED_EMERGENCY = "emergency_stop_active"
_SKIPPED_TOO_LATE = "too_late"


def due_condition(now: datetime) -> Any:
    return and_(
        ScheduledJob.enabled.is_(True),
        ScheduledJob.paused.is_(False),
        ScheduledJob.ended_at.is_(None),
        ScheduledJob.next_run_at.is_not(None),
        ScheduledJob.next_run_at <= now,
    )


def idempotency_key(job_id: uuid.UUID, scheduled_for: datetime, attempt: int) -> str:
    return f"schedule:{job_id}:{to_iso(scheduled_for)}:{attempt}"


def _error_from_task(task: Task, default_code: str) -> dict[str, str]:
    error = task.error if isinstance(task.error, dict) else {}
    return {
        "code": str(error.get("code") or default_code),
        "message": str(error.get("message") or "")[:500],
    }


def mirror_task(run: ScheduleRun, task: Task, now: datetime) -> None:
    """Copy the task's state onto the run. An approval that expired is a failure, so the user is told."""
    status = TASK_TO_RUN_STATUS.get(task.status, "queued")
    error = task.error if isinstance(task.error, dict) else {}
    if task.status == "cancelled" and error.get("code") == "approval_expired":
        status = "failed"
    run.status = status
    if status == "succeeded":
        run.error = None
        run.finished_at = task.finished_at or now
    elif status == "failed":
        run.error = _error_from_task(task, "execution_failed")
        run.finished_at = task.finished_at or now
    elif status == "cancelled":
        run.error = _error_from_task(task, "cancelled")
        run.finished_at = task.finished_at or now


def _end_once(job: ScheduledJob, now: datetime) -> None:
    if job.schedule_kind == "once":
        job.next_run_at = None
        if job.ended_at is None:
            job.ended_at = now


def _advance_cursor(job: ScheduledJob, now: datetime) -> None:
    """Move the cursor past the slot being claimed. A once schedule with one attempt ends here."""
    job.last_run_at = now
    if job.schedule_kind == "once":
        job.next_run_at = None
        if job.retry_max_attempts <= 1 and job.ended_at is None:
            job.ended_at = now
        return
    job.next_run_at = next_after_now(job, now)


def _timed_out(run: ScheduleRun, job: ScheduledJob, now: datetime) -> bool:
    started = run.started_at or run.created_at
    return now >= started + timedelta(seconds=job.max_runtime_seconds)


def _notify_failure(db: AsyncSession, events: EventBus, job: ScheduledJob, run: ScheduleRun, doc: SettingsDoc) -> None:
    code = str((run.error or {}).get("code", "execution_failed"))
    if doc.notifications.include_task_details:
        body = f"'{job.name}' failed after {run.attempt} attempt(s). Reason: {code}."
    else:
        body = f"A scheduled task failed after {run.attempt} attempt(s). Reason: {code}."
    notify(
        db,
        events,
        user_id=job.user_id,
        kind="schedule_failed",
        severity="error",
        title="Scheduled task failed",
        body=body,
        related_type="schedule",
        related_id=str(job.id),
    )


async def _schedule_retry(db: AsyncSession, job: ScheduledJob, run: ScheduleRun, now: datetime) -> None:
    job.next_run_at = retry_delay(job, now)
    # Flush the job first so a rolled-back savepoint below cannot also revert the cursor.
    await db.flush()
    retry = ScheduleRun(
        job_id=job.id,
        user_id=job.user_id,
        scheduled_for=run.scheduled_for,
        attempt=run.attempt + 1,
        status=RETRY_WAIT_STATUS,
        created_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(retry)
            await db.flush()
    except IntegrityError:
        log.warning("Retry already recorded: job_id=%s", job.id)


class Scheduler:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        tasks: TaskEngine,
        events: EventBus,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._tasks = tasks
        self._events = events
        self._loop_task: asyncio.Task[None] | None = None

    # ---- lifecycle ---------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True from start() until stop(), while the background loop task has not finished."""
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._run_forever(), name="jarvis-scheduler")

    async def stop(self) -> None:
        task, self._loop_task = self._loop_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_due_jobs_once()
            except Exception as exc:  # one failed pass must not stop the loop; the next pass retries
                log.error("Scheduler pass failed: error=%s", type(exc).__name__)
            await asyncio.sleep(self._settings.scheduler_poll_seconds)

    # ---- one pass ----------------------------------------------------------------------

    async def run_due_jobs_once(self, now: datetime | None = None) -> int:
        """One pass: follow in-flight runs, then fire due jobs. Returns the number of slots claimed."""
        moment = now or utc_now()
        await self._reconcile_runs(moment)
        async with self._session_factory() as session:
            job_ids = list(
                (
                    await session.scalars(
                        select(ScheduledJob.id)
                        .where(due_condition(moment))
                        .order_by(ScheduledJob.next_run_at)
                        .limit(BATCH_SIZE)
                    )
                ).all()
            )
        fired = 0
        for job_id in job_ids:
            try:
                if await self._fire_job(job_id, moment):
                    fired += 1
            except Exception as exc:  # one job must not stop the pass; log the id only
                log.error("Scheduled job could not be processed: job_id=%s error=%s", job_id, type(exc).__name__)
        return fired

    def _announce(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        for user_id, job_id in pairs:
            self._events.publish(user_id, "schedule.updated", {"schedule_id": str(job_id)})

    # ---- firing ------------------------------------------------------------------------

    async def _fire_job(self, job_id: uuid.UUID, now: datetime) -> bool:
        announce: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with session_scope(self._session_factory) as db:
            job = await db.scalar(
                select(ScheduledJob)
                .where(ScheduledJob.id == job_id, due_condition(now))
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            claimed = await self._claim(db, job, now)
            announce.append((job.user_id, job.id))
        self._announce(announce)
        return claimed

    async def _claim(self, db: AsyncSession, job: ScheduledJob, now: datetime) -> bool:
        """Claim the due slot or retry. Returns False only for a duplicate slot that was skipped."""
        doc = await load_settings(db, job.user_id)
        retry = await pending_retry(db, job.id)
        if retry is not None:
            retry.status = "queued"
            retry.started_at = now
            _advance_cursor(job, now)
            await self._execute(db, job, retry, doc, now)
            return True
        if job.next_run_at is None:
            return False
        if job.schedule_kind != "once" and now - job.next_run_at > LATE_SLOT_LIMIT:
            await self._skip_late_slot(db, job, now, doc)
            return False
        run = ScheduleRun(
            job_id=job.id,
            user_id=job.user_id,
            scheduled_for=job.next_run_at,
            attempt=1,
            status="queued",
            created_at=now,
            started_at=now,
        )
        _advance_cursor(job, now)
        # Flush the cursor first so a rolled-back savepoint below cannot also revert it.
        await db.flush()
        try:
            async with db.begin_nested():
                db.add(run)
                await db.flush()
        except IntegrityError:
            # The slot was already claimed. The cursor has been advanced, so this is a skip, not a second run.
            log.info("Duplicate slot skipped: job_id=%s", job.id)
            return False
        await self._execute(db, job, run, doc, now)
        return True

    async def _skip_late_slot(self, db: AsyncSession, job: ScheduledJob, now: datetime, doc: SettingsDoc) -> None:
        """A recurring slot that is more than LATE_SLOT_LIMIT late is recorded as a skipped run, not fired.

        The cursor moves to the next occurrence. ``last_run_at`` is left alone because nothing ran.
        """
        slot = job.next_run_at
        if slot is None:
            return
        run = ScheduleRun(
            job_id=job.id,
            user_id=job.user_id,
            scheduled_for=slot,
            attempt=1,
            status="skipped",
            created_at=now,
            finished_at=now,
            error={"code": _SKIPPED_TOO_LATE, "message": "The slot was more than 15 minutes late, so this run was skipped."},
        )
        job.next_run_at = next_after_now(job, now)
        # Flush the cursor first so a rolled-back savepoint below cannot also revert it.
        await db.flush()
        try:
            async with db.begin_nested():
                db.add(run)
                await db.flush()
        except IntegrityError:
            log.info("Late slot already recorded: job_id=%s", job.id)
            return
        log.info("Late recurring slot skipped: job_id=%s", job.id)
        await self._settle(db, job, run, doc, now)

    async def _execute(
        self, db: AsyncSession, job: ScheduledJob, run: ScheduleRun, doc: SettingsDoc, now: datetime
    ) -> None:
        if doc.automation.emergency_stopped:
            run.status = "skipped"
            run.finished_at = now
            run.error = {"code": _SKIPPED_EMERGENCY, "message": "The emergency stop is on, so this run was skipped."}
            await self._settle(db, job, run, doc, now)
            return
        try:
            task = await self._tasks.propose(
                db,
                user_id=job.user_id,
                tool_name=job.tool_name,
                arguments=dict(job.arguments or {}),
                source="schedule",
                device_id=job.device_id,
                title=job.name,
                idempotency_key=idempotency_key(job.id, run.scheduled_for, run.attempt),
            )
        except AppError as exc:
            run.status = "failed"
            run.finished_at = now
            run.error = {"code": exc.code, "message": exc.message[:500]}
            await self._settle(db, job, run, doc, now)
            return
        run.task_id = task.id
        mirror_task(run, task, now)
        await self._settle(db, job, run, doc, now)

    async def _settle(
        self, db: AsyncSession, job: ScheduledJob, run: ScheduleRun, doc: SettingsDoc, now: datetime
    ) -> None:
        """Apply a run's current status to its job: failure counting, retries, completion and notification."""
        job.last_status = run.status
        if run.status in LIVE_RUN_STATUSES:
            return
        if run.status == "succeeded":
            job.consecutive_failures = 0
            _end_once(job, now)
            return
        if run.status == "failed":
            if run.attempt < job.retry_max_attempts:
                await _schedule_retry(db, job, run, now)
                return
            job.consecutive_failures += 1
            _end_once(job, now)
            if doc.notifications.on_schedule_event:
                _notify_failure(db, self._events, job, run, doc)
            return
        # cancelled and skipped are final but are not failures.
        _end_once(job, now)

    # ---- following runs ----------------------------------------------------------------

    async def _reconcile_runs(self, now: datetime) -> None:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ScheduleRun.id, ScheduleRun.job_id)
                    .where(ScheduleRun.status.in_(LIVE_RUN_STATUSES))
                    .order_by(ScheduleRun.created_at)
                    .limit(RECONCILE_BATCH)
                )
            ).all()
        for run_id, job_id in rows:
            try:
                await self._reconcile_run(run_id, now)
            except Exception as exc:
                log.error("Scheduled run could not be reconciled: job_id=%s error=%s", job_id, type(exc).__name__)

    async def _reconcile_run(self, run_id: uuid.UUID, now: datetime) -> None:
        cancel: tuple[uuid.UUID, uuid.UUID] | None = None
        announce: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with session_scope(self._session_factory) as db:
            run = await db.scalar(
                select(ScheduleRun)
                .where(ScheduleRun.id == run_id, ScheduleRun.status.in_(LIVE_RUN_STATUSES))
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return
            job = await db.scalar(select(ScheduledJob).where(ScheduledJob.id == run.job_id).with_for_update())
            if job is None:
                return
            doc = await load_settings(db, job.user_id)
            task = await self._task_for_run(db, run, job)
            if task is not None:
                mirror_task(run, task, now)
            if run.status in ("queued", "running") and _timed_out(run, job, now):
                if task is not None and task.status not in TERMINAL_TASK_STATUSES:
                    cancel = (job.user_id, task.id)
                run.status = "failed"
                run.finished_at = now
                run.error = {"code": "timeout", "message": "The run did not finish within its time limit."}
            if run.status not in LIVE_RUN_STATUSES:
                await self._settle(db, job, run, doc, now)
            announce.append((job.user_id, job.id))
        if cancel is not None:
            try:
                await self._tasks.cancel(cancel[0], cancel[1])
            except AppError as exc:
                log.info("Timed-out task was not cancelled: code=%s", exc.code)
        self._announce(announce)

    async def _task_for_run(self, db: AsyncSession, run: ScheduleRun, job: ScheduledJob) -> Task | None:
        """The run's task. A run that lost its link after a crash is linked by its idempotency key."""
        if run.task_id is not None:
            return await db.scalar(select(Task).where(Task.id == run.task_id, Task.user_id == job.user_id))
        task = await db.scalar(
            select(Task).where(
                Task.user_id == job.user_id,
                Task.idempotency_key == idempotency_key(job.id, run.scheduled_for, run.attempt),
            )
        )
        if task is not None:
            run.task_id = task.id
        return task


__all__ = ["BATCH_SIZE", "Scheduler", "due_condition", "idempotency_key", "mirror_task"]
