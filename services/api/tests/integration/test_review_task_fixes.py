"""Regression tests for the task review findings, against real PostgreSQL and the late-results harness.

* A cancel that the device never confirms ends ``cancelled`` (docs/phase2/late-results.md), not ``failed``/timeout.
* A late success that the desktop could not verify is worded as not confirmed, like an on-time result.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest_asyncio

from app.core.time import utc_now
from tests.integration.test_late_results import _conversation, _messages, _timed_out
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
    return await build_harness(app, settings, email="review-tasks@example.com")


async def _running_task(h: Harness, *, conversation_id: uuid.UUID | None = None) -> tuple[uuid.UUID, Any]:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id, conversation_id=conversation_id)
    assert task.status == "running"
    return device_id, task


async def test_a_cancel_the_device_never_confirms_ends_cancelled_not_timed_out(h: Harness) -> None:
    device_id, task = await _running_task(h)
    requested = await h.engine.cancel(h.user_id, task.id)
    assert requested.status == "running" and requested.cancel_requested is True

    # The device never answers the cancel, so the time limit ends the task.
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    await h.engine.sweep_once()
    swept = await fetch(h, task.id)
    assert swept.status == "cancelled"
    assert swept.error is not None and swept.error["code"] == "cancelled_by_user"
    assert "did not confirm the stop" in swept.error["message"]

    # A success reported later is recorded as an action that finished after the cancel. The task stays cancelled.
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(
        device_id, result_for(command_id, "succeeded", output={"summary": "Opened YouTube.", "verified": True})
    )
    after = await fetch(h, task.id)
    assert after.status == "cancelled"
    assert "late_result" in await event_types(h, task.id)


async def test_a_late_success_the_desktop_could_not_verify_is_not_called_finished(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, timed_out = await _timed_out(h, conversation_id=conversation_id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(
        device_id,
        result_for(command_id, "succeeded", output={"summary": "Started Chrome (pid 4313).", "verified": False}),
    )

    assert (await fetch(h, timed_out.id)).status == "completed"
    messages = await _messages(h, conversation_id)
    assert messages[-1].content == (
        "The earlier request finished after it timed out, but the result is not confirmed: Started Chrome (pid 4313)."
    )
