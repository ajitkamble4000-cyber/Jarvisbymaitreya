"""Background sweeps and the result-versus-timeout race, against real PostgreSQL.

* The lifespan starts the task sweeps when RUN_BACKGROUND_WORKERS is true, and an expired queued task is failed
  by the running sweep. Poll intervals are shortened only in this test, and the app must shut down cleanly.
* A device result and the timeout sweep racing on one running task end in exactly one final state, and the
  timeline agrees with that state. Both orders are exercised.
* A sweep that runs after a late success leaves the completed state alone.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.services.task_engine as task_engine_module
from app.core.time import utc_now
from app.main import create_app
from app.models.tasks import TaskEvent
from tests.integration.test_tasks_policy import (
    Harness,
    add_device,
    allow,
    backdate,
    build_harness,
    event_types,
    fetch,
    propose,
    result_for,
)

RACE_ROUNDS = 8


@pytest_asyncio.fixture(loop_scope="session")
async def h(app: Any, settings: Any) -> Harness:
    return await build_harness(app, settings, email="sweeps@example.com")


async def _poll_until(predicate: Any, *, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_lifespan_with_background_workers_runs_the_sweep_and_fails_an_expired_queued_task(
    app: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Short poll intervals, only for this test. The production values stay as they are.
    monkeypatch.setattr(task_engine_module, "SWEEP_INTERVAL_SECONDS", 0.02)
    short = settings.model_copy(
        update={
            "run_background_workers": True,
            "task_queue_ttl_seconds": 1,
            "scheduler_poll_seconds": 0.02,
        }
    )
    background = create_app(short)
    harness = await build_harness(background, short, email="lifespan@example.com")
    device_id, _ = await add_device(harness, permissions=allow("open_url"))
    # The device is not connected to this app, so the queued task waits for it.
    queued = await propose(harness, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert queued.status == "queued"

    async with background.router.lifespan_context(background):
        engine = background.state.task_engine
        assert engine._sweep_task is not None and not engine._sweep_task.done()
        assert background.state.scheduler._loop_task is not None
        # The queue time limit has passed. Only the running sweep can fail the task now.
        await backdate(harness, queued.id, created_at=utc_now() - timedelta(minutes=5))

        async def failed() -> bool:
            return (await fetch(harness, queued.id)).status == "failed"

        await _poll_until(failed)

    final = await fetch(harness, queued.id)
    assert final.status == "failed"
    assert final.error is not None and final.error["code"] == "device_offline"
    assert "expired" in await event_types(harness, queued.id)
    assert harness.hub.commands == []

    # Clean shutdown: both loops are stopped and nothing named for them is still running.
    assert background.state.task_engine._sweep_task is None
    assert background.state.scheduler._loop_task is None
    leftover = [t for t in asyncio.all_tasks() if t.get_name() in {"jarvis-task-sweeps", "jarvis-scheduler"} and not t.done()]
    assert leftover == []


async def _timeout_events(h: Harness, task_id: Any) -> list[TaskEvent]:
    async with h.factory() as db:
        rows = await db.scalars(
            select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.type == "failed").order_by(TaskEvent.id)
        )
        return [row for row in rows.all() if (row.data or {}).get("code") == "timeout"]


@pytest.mark.parametrize("agent_status", ["succeeded", "failed"])
async def test_a_result_and_a_timeout_racing_on_one_running_task_end_in_one_final_state(h: Harness, agent_status: str) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    expected = "completed" if agent_status == "succeeded" else "failed"
    orders_seen: set[str] = set()

    for round_number in range(RACE_ROUNDS):
        task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
        assert task.status == "running"
        await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
        command_id = h.hub.commands[-1][1].id
        result = result_for(command_id, agent_status, output={"summary": "Done."}, error={"code": "execution_failed", "message": "Failed."})
        result_call = h.engine.on_agent_result(device_id, result)
        sweep_call = h.engine.sweep_once()
        # Alternate which call is started first, so both orders of the row lock are exercised.
        if round_number % 2 == 0:
            await asyncio.gather(result_call, sweep_call)
        else:
            await asyncio.gather(sweep_call, result_call)

        orders_seen.add(await _check_one_final_state(h, task.id, agent_status, expected, label=f"round {round_number}"))

    assert orders_seen, "no round ran"


async def _check_one_final_state(h: Harness, task_id: Any, agent_status: str, expected: str, *, label: str) -> str:
    """Asserts the one final state and that the timeline agrees with it. Returns which side took the lock first."""
    final = await fetch(h, task_id)
    assert final.status == expected, f"{label}: {final.status}"
    types = await event_types(h, task_id)
    timeouts = await _timeout_events(h, task_id)
    if timeouts:
        # The sweep took the lock first. The result then arrived after the timeout, and a success completes it.
        assert len(timeouts) == 1
        assert types.count("late_result") == 1
        assert types.index("failed") < types.index("late_result")
        if agent_status == "succeeded":
            assert final.error is None
            assert types[-2:] == ["late_result", "completed"]
        else:
            assert final.error is not None and final.error["code"] == "timeout"
            assert types[-1] == "late_result"
        order = "timeout_first"
    else:
        # The result took the lock first. It is the only terminal event, and the sweep changed nothing.
        assert "late_result" not in types
        assert types.count("failed") + types.count("completed") == 1
        order = "result_first"
    assert types.count("completed") == (1 if agent_status == "succeeded" else 0)
    assert types.count("cancelled") == 0
    return order


async def test_timeout_that_holds_the_row_lock_first_then_a_late_success_completes_the_task(h: Harness) -> None:
    """Forced order: the sweep locks the running task, and the result waits for the lock behind it."""
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    command_id = h.hub.commands[0][1].id
    result = result_for(command_id, "succeeded", output={"summary": "Done."})
    original = h.engine._cached_policy
    racing: list[asyncio.Task] = []

    async def policy_while_result_waits(db: Any, user_id: Any, cache: Any) -> Any:
        # Runs inside the sweep, after it locked the running task and before it writes the timeout.
        if not racing:
            racing.append(asyncio.create_task(h.engine.on_agent_result(device_id, result)))
            await asyncio.sleep(0.3)
        return await original(db, user_id, cache)

    h.engine._cached_policy = policy_while_result_waits  # type: ignore[method-assign]
    try:
        await h.engine.sweep_once()
    finally:
        h.engine._cached_policy = original  # type: ignore[method-assign]
    await asyncio.wait_for(racing[0], 10)

    assert await _check_one_final_state(h, task.id, "succeeded", "completed", label="forced timeout-first") == "timeout_first"


async def test_result_that_holds_the_row_lock_first_then_the_sweep_changes_nothing(h: Harness) -> None:
    """Forced order: the result locks the running task, and the sweep waits behind it and then skips the row."""
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    command_id = h.hub.commands[0][1].id
    result = result_for(command_id, "failed", error={"code": "execution_failed", "message": "Failed."})
    original = h.engine._record_running_result
    racing: list[asyncio.Task] = []

    async def result_while_sweep_waits(db: Any, running: Any, device: Any, message: Any) -> None:
        # Runs inside the result, after it locked the running task. The sweep now queues behind that lock.
        if not racing:
            racing.append(asyncio.create_task(h.engine.sweep_once()))
            await asyncio.sleep(0.3)
        await original(db, running, device, message)

    h.engine._record_running_result = result_while_sweep_waits  # type: ignore[method-assign]
    try:
        await h.engine.on_agent_result(device_id, result)
    finally:
        h.engine._record_running_result = original  # type: ignore[method-assign]
    await asyncio.wait_for(racing[0], 10)

    assert await _check_one_final_state(h, task.id, "failed", "failed", label="forced result-first") == "result_first"
    final = await fetch(h, task.id)
    assert final.error is not None and final.error["code"] == "execution_failed"


async def test_a_sweep_after_a_late_success_does_not_overwrite_the_completed_state(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    await h.engine.sweep_once()
    await h.engine.on_agent_result(
        device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Opened YouTube."})
    )
    before_types = await event_types(h, task.id)

    await h.engine.sweep_once()
    await h.engine.sweep_once()

    final = await fetch(h, task.id)
    assert final.status == "completed"
    assert final.result is not None and final.result["summary"] == "Opened YouTube."
    assert final.error is None
    assert await event_types(h, task.id) == before_types
