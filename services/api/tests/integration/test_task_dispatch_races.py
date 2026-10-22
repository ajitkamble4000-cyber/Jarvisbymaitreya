"""Regression tests for dispatch, approval and result races, and for the policy re-checks at dispatch.

Each race is forced in-process so the ordering is deterministic: the engine's own steps are wrapped so
that a competing call runs at the point where the race would happen. Helpers come from the task policy
test module so the fake hub and the device fixtures stay identical.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.core.errors import ConflictError
from app.core.time import utc_now
from app.models.devices import Device
from app.models.identity import UserPreference
from app.models.tasks import Task, TaskEvent
from app.services.task_engine import TaskEngine
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


@pytest_asyncio.fixture(loop_scope="session")
async def h(app: Any, settings: Any) -> Harness:
    return await build_harness(app, settings, email="races@example.com")


async def _set_automation(h: Harness, automation: dict[str, Any]) -> None:
    async with h.factory() as db:
        row = await db.get(UserPreference, h.user_id)
        if row is None:
            db.add(UserPreference(user_id=h.user_id, settings={"automation": automation}))
        else:
            row.settings = {**(row.settings or {}), "automation": automation}
        await db.commit()


async def test_emergency_stop_that_lands_during_a_dispatch_stops_the_command_that_was_sent(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    original = h.engine._count_running
    stops: list[asyncio.Task] = []

    async def racing_count(db: Any, user_id: uuid.UUID) -> int:
        # Runs inside the dispatch, after the policy read and before the running row is committed.
        if not stops:
            stops.append(asyncio.create_task(h.engine.emergency_stop(user_id)))
        return await original(db, user_id)

    h.engine._count_running = racing_count  # type: ignore[method-assign]
    try:
        task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    finally:
        h.engine._count_running = original  # type: ignore[method-assign]
    outcome = await asyncio.wait_for(stops[0], 10)

    # The command was already committed as running, so the stop asks the device to cancel it instead of
    # reporting it as cancelled while the device runs it.
    assert outcome == {"stopped": True, "cancelled": 0, "cancel_requested": 1}
    assert len(h.hub.commands) == 1
    assert len(h.hub.cancels) == 1
    assert h.hub.cancels[0][1].command_id == str(h.hub.commands[0][1].id)
    final = await fetch(h, task.id)
    assert final.status == "running" and final.cancel_requested is True


async def test_a_user_cancel_that_lands_during_a_dispatch_is_not_lost(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert task.status == "running"
    # A second device is offline, so its task waits in the queue. It dispatches when the device connects.
    queued_device, _ = await add_device(h, name="Tablet", permissions=allow("open_url"))
    queued = await propose(h, "open_url", {"site": "YouTube"}, device_id=queued_device)
    assert queued.status == "queued"
    original = h.engine._count_running
    cancels: list[asyncio.Task] = []

    async def racing_count(db: Any, user_id: uuid.UUID) -> int:
        # Runs inside the dispatch of the queued task, before its running row is committed.
        if not cancels:
            cancels.append(asyncio.create_task(h.engine.cancel(h.user_id, queued.id)))
        return await original(db, user_id)

    h.hub.online.add(queued_device)
    h.engine._count_running = racing_count  # type: ignore[method-assign]
    try:
        await h.engine.on_device_connected(queued_device)
        await asyncio.wait_for(cancels[0], 10)
    finally:
        h.engine._count_running = original  # type: ignore[method-assign]

    # The cancel waited for the dispatch. It then found a running command and asked the device to stop it.
    final = await fetch(h, queued.id)
    assert final.status == "running" and final.cancel_requested is True
    sent_to_queued = [message for device, message in h.hub.commands if device == queued_device]
    assert len(sent_to_queued) == 1
    assert [command.command_id for _, command in h.hub.cancels] == [str(sent_to_queued[0].id)]


async def test_a_second_dispatch_that_read_the_task_early_does_not_send_a_second_command(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert task.status == "queued"
    h.hub.online.add(device_id)
    original = h.engine._count_running
    extra: list[asyncio.Task] = []
    read_done = asyncio.Event()

    async def late_duplicate() -> Any:
        # A second drain that loaded the task before the first one committed "running".
        async with h.factory() as db:
            stale = await db.get(Task, task.id)
            device = await db.get(Device, device_id)
            read_done.set()
            return await h.engine._dispatch(db, stale, device)

    async def racing_count(db: Any, user_id: uuid.UUID) -> int:
        # Runs inside the first dispatch, under the dispatch lock, before its running row is committed.
        if not extra:
            extra.append(asyncio.create_task(late_duplicate()))
            await read_done.wait()
        return await original(db, user_id)

    h.engine._count_running = racing_count  # type: ignore[method-assign]
    try:
        await h.engine.on_device_connected(device_id)
        await asyncio.wait_for(extra[0], 10)
    finally:
        h.engine._count_running = original  # type: ignore[method-assign]

    assert len(h.hub.commands) == 1
    assert (await event_types(h, task.id)).count("dispatched") == 1
    assert (await fetch(h, task.id)).status == "running"


async def test_an_approval_that_read_the_task_early_is_a_conflict_and_sends_nothing_extra(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow())
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    assert task.status == "awaiting_approval"
    original = h.engine._owned
    second: list[asyncio.Task] = []

    async def owned_with_competitor(db: Any, user_id: uuid.UUID, task_id: uuid.UUID, **kwargs: Any) -> Any:
        found = await original(db, user_id, task_id, **kwargs)
        if not second:
            # A second approve reads the task while this one still holds it, then waits for it to finish.
            second.append(asyncio.create_task(h.engine.approve(h.user_id, task_id)))
            await asyncio.sleep(0.3)
        return found

    h.engine._owned = owned_with_competitor  # type: ignore[method-assign]
    try:
        first = await h.engine.approve(h.user_id, task.id)
        with pytest.raises(ConflictError):
            await second[0]
    finally:
        h.engine._owned = original  # type: ignore[method-assign]

    assert first.status in ("queued", "running", "completed")
    assert len(h.hub.commands) == 1


async def test_a_stale_queued_task_is_failed_on_reconnect_instead_of_running_late(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, created_at=utc_now() - timedelta(days=3))
    h.hub.online.add(device_id)

    await h.engine.on_device_connected(device_id)

    final = await fetch(h, task.id)
    assert final.status == "failed"
    assert final.error["code"] == "timeout"
    assert h.hub.commands == []


async def test_permission_changed_to_ask_after_queueing_returns_the_task_for_approval(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert task.status == "queued"
    async with h.factory() as db:
        await db.execute(
            update(Device).where(Device.id == device_id).values(permissions=allow())
        )
        await db.commit()
    h.hub.online.add(device_id)

    await h.engine.on_device_connected(device_id)

    final = await fetch(h, task.id)
    assert final.status == "awaiting_approval"
    assert h.hub.commands == []
    assert "awaiting_approval" in await event_types(h, task.id)


async def test_tool_disabled_after_queueing_is_not_dispatched(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await _set_automation(h, {"disabled_tools": ["open_url"]})
    h.hub.online.add(device_id)

    await h.engine.on_device_connected(device_id)

    final = await fetch(h, task.id)
    assert final.status == "failed"
    assert final.error["code"] == "tool_disabled"
    assert h.hub.commands == []
    await _set_automation(h, {})


async def test_result_that_arrives_after_the_timeout_completes_the_task_and_is_kept_in_the_timeline(h: Harness) -> None:
    # Updated for the late-result decision: a success after a timeout completes the task (see test_late_results.py).
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    await h.engine.sweep_once()
    assert (await fetch(h, task.id)).status == "failed"

    await h.engine.on_agent_result(
        device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Opened YouTube."})
    )

    final = await fetch(h, task.id)
    assert final.status == "completed" and final.error is None
    assert final.result is not None and final.result["summary"] == "Opened YouTube."
    assert "late_result" in await event_types(h, task.id)


async def test_dispatch_and_results_are_written_to_the_audit_log(h: Harness) -> None:
    from sqlalchemy import select

    from app.models.platform import AuditEvent

    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded"))

    async with h.factory() as db:
        actions = set((await db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == str(task.id)))).all())
    assert {"tasks.dispatch", "tasks.result"} <= actions


async def test_expired_approval_is_reported_as_a_conflict_and_posted_once(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow())
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    await backdate(h, task.id, approval_expires_at=utc_now() - timedelta(seconds=1))

    with pytest.raises(ConflictError) as info:
        await h.engine.approve(h.user_id, task.id)

    assert info.value.code == "approval_expired"
    assert (await fetch(h, task.id)).status == "cancelled"
    assert h.hub.commands == []
