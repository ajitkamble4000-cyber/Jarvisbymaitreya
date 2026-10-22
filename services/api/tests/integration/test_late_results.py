"""Results that arrive after a task already ended: the late-result rules, against real PostgreSQL.

Covered: a success after a device timeout completes the task (with a follow-up chat message); a failure after a
timeout leaves it failed (timeline only); a success after a user cancel leaves it cancelled (timeline and follow-up);
a second result, or a result for an unknown command, changes nothing. The late path runs under the same row lock
as the timeout sweep and the cancel, so the sweep never overwrites the completed state.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import select

from app.core.time import utc_now
from app.models.conversation import Conversation, Message
from app.models.identity import UserPreference
from app.models.platform import AuditEvent
from app.models.tasks import Task, TaskEvent
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
    return await build_harness(app, settings, email="late@example.com")


async def _conversation(h: Harness) -> uuid.UUID:
    async with h.factory() as db:
        row = Conversation(user_id=h.user_id, title="Late results")
        db.add(row)
        await db.commit()
        return row.id


async def _messages(h: Harness, conversation_id: uuid.UUID) -> list[Message]:
    async with h.factory() as db:
        rows = await db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id)
        )
        return list(rows.all())


async def _event_rows(h: Harness, task_id: uuid.UUID, event_type: str) -> list[TaskEvent]:
    async with h.factory() as db:
        rows = await db.scalars(
            select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.type == event_type).order_by(TaskEvent.id)
        )
        return list(rows.all())


async def _dispatched(h: Harness, *, conversation_id: uuid.UUID | None = None) -> tuple[uuid.UUID, Task]:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id, conversation_id=conversation_id)
    assert task.status == "running"
    return device_id, task


async def _timed_out(h: Harness, *, conversation_id: uuid.UUID | None = None) -> tuple[uuid.UUID, Task]:
    device_id, task = await _dispatched(h, conversation_id=conversation_id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(days=1), run_deadline_at=utc_now() - timedelta(days=1))
    await h.engine.sweep_once()
    timed_out = await fetch(h, task.id)
    assert timed_out.status == "failed" and timed_out.error is not None and timed_out.error["code"] == "timeout"
    return device_id, timed_out


async def _cancelled_by_user(h: Harness, *, conversation_id: uuid.UUID | None = None) -> tuple[uuid.UUID, Task]:
    device_id, task = await _dispatched(h, conversation_id=conversation_id)
    # The device is unreachable, so the cancel is recorded now and the command may still finish on the device.
    h.hub.online.discard(device_id)
    cancelled = await h.engine.cancel(h.user_id, task.id)
    assert cancelled.status == "cancelled" and cancelled.error["code"] == "cancelled_by_user"
    return device_id, cancelled


async def test_success_after_a_timeout_completes_the_task_and_tells_the_conversation(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, timed_out = await _timed_out(h, conversation_id=conversation_id)
    before = len(await _messages(h, conversation_id))
    command_id = h.hub.commands[0][1].id

    await h.engine.on_agent_result(
        device_id, result_for(command_id, "succeeded", output={"summary": "Opened YouTube.", "verified": True})
    )

    final = await fetch(h, timed_out.id)
    assert final.status == "completed"
    assert final.error is None
    assert final.result == {"summary": "Opened YouTube.", "output": {"summary": "Opened YouTube.", "verified": True}}
    assert final.finished_at is not None

    # The timeline shows the late result before the completion it caused.
    types = await event_types(h, final.id)
    assert types[-2:] == ["late_result", "completed"]
    [late] = await _event_rows(h, final.id, "late_result")
    assert late.data == {"status": "succeeded", "reported_after": "timeout"}

    # The conversation gets exactly one follow-up, after the timeout outcome, with the real summary only.
    messages = await _messages(h, conversation_id)
    assert len(messages) == before + 1
    follow_up = messages[-1]
    assert follow_up.role == "assistant"
    assert follow_up.content == "The earlier request finished after it timed out: Opened YouTube."
    assert follow_up.task_id == final.id
    assert follow_up.meta["kind"] == "late_result"


async def test_success_after_a_timeout_is_audited(h: Harness) -> None:
    device_id, task = await _timed_out(h)
    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Done."}))

    async with h.factory() as db:
        actions = set((await db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == str(task.id)))).all())
    assert {"tasks.dispatch", "tasks.late_result"} <= actions


async def test_success_without_a_real_summary_posts_the_sentence_without_inventing_one(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, timed_out = await _timed_out(h, conversation_id=conversation_id)

    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"verified": True}))

    messages = await _messages(h, conversation_id)
    assert messages[-1].content == "The earlier request finished after it timed out."
    assert (await fetch(h, timed_out.id)).status == "completed"


async def test_success_without_a_conversation_completes_the_task_and_posts_nothing(h: Harness) -> None:
    device_id, task = await _timed_out(h)

    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Done."}))

    assert (await fetch(h, task.id)).status == "completed"
    assert len(await _event_rows(h, task.id, "late_result")) == 1


async def test_success_is_recorded_but_no_message_is_written_when_conversation_storage_is_off(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, task = await _timed_out(h, conversation_id=conversation_id)
    async with h.factory() as db:
        row = await db.get(UserPreference, h.user_id)
        if row is None:
            db.add(UserPreference(user_id=h.user_id, settings={"privacy": {"store_conversations": False}}))
        else:
            row.settings = {**(row.settings or {}), "privacy": {"store_conversations": False}}
        await db.commit()
    before = len(await _messages(h, conversation_id))

    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Done."}))

    assert (await fetch(h, task.id)).status == "completed"
    assert len(await _messages(h, conversation_id)) == before


async def test_failure_after_a_timeout_stays_failed_and_writes_no_chat_message(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, task = await _timed_out(h, conversation_id=conversation_id)
    before = len(await _messages(h, conversation_id))

    await h.engine.on_agent_result(
        device_id,
        result_for(h.hub.commands[0][1].id, "failed", error={"code": "execution_failed", "message": "The window was closed."}),
    )

    final = await fetch(h, task.id)
    assert final.status == "failed"
    assert final.error is not None and final.error["code"] == "timeout"
    [late] = await _event_rows(h, task.id, "late_result")
    assert late.data == {"status": "failed"}
    assert len(await _messages(h, conversation_id)) == before


async def test_success_after_a_user_cancel_stays_cancelled_and_says_so(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, cancelled = await _cancelled_by_user(h, conversation_id=conversation_id)
    before = len(await _messages(h, conversation_id))

    await h.engine.on_agent_result(
        device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "Opened YouTube."})
    )

    final = await fetch(h, cancelled.id)
    assert final.status == "cancelled"
    assert final.result is None
    [late] = await _event_rows(h, cancelled.id, "late_result")
    assert late.data == {"status": "succeeded", "reported_after": "cancel"}
    assert late.message == "The operation completed even though it was cancelled."

    messages = await _messages(h, conversation_id)
    assert len(messages) == before + 1
    assert messages[-1].content == "The action completed even though you cancelled it: Opened YouTube."
    assert messages[-1].meta["kind"] == "late_result"


async def test_a_second_result_for_a_task_is_ignored(h: Harness) -> None:
    conversation_id = await _conversation(h)
    device_id, task = await _timed_out(h, conversation_id=conversation_id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(device_id, result_for(command_id, "succeeded", output={"summary": "Done."}))
    before_types = await event_types(h, task.id)
    before_messages = len(await _messages(h, conversation_id))

    await h.engine.on_agent_result(device_id, result_for(command_id, "succeeded", output={"summary": "Again."}))
    await h.engine.on_agent_result(device_id, result_for(command_id, "failed", error={"code": "execution_failed"}))

    final = await fetch(h, task.id)
    assert final.status == "completed" and final.result is not None and final.result["summary"] == "Done."
    assert await event_types(h, task.id) == before_types
    assert len(await _messages(h, conversation_id)) == before_messages


async def test_a_second_result_after_a_late_failure_is_ignored(h: Harness) -> None:
    device_id, task = await _timed_out(h)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(device_id, result_for(command_id, "failed", error={"code": "execution_failed"}))

    await h.engine.on_agent_result(device_id, result_for(command_id, "succeeded", output={"summary": "Done."}))

    final = await fetch(h, task.id)
    assert final.status == "failed" and final.error is not None and final.error["code"] == "timeout"
    assert len(await _event_rows(h, task.id, "late_result")) == 1


async def test_a_result_that_is_not_a_success_after_a_user_cancel_is_ignored(h: Harness) -> None:
    device_id, task = await _cancelled_by_user(h)
    before = await event_types(h, task.id)

    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "failed", error={"code": "execution_failed"}))

    assert (await fetch(h, task.id)).status == "cancelled"
    assert await event_types(h, task.id) == before


async def test_an_unknown_command_after_a_timeout_changes_nothing(h: Harness) -> None:
    device_id, task = await _timed_out(h)
    before = await event_types(h, task.id)

    await h.engine.on_agent_result(device_id, result_for(uuid.uuid4(), "succeeded", output={"summary": "Spoof."}))

    final = await fetch(h, task.id)
    assert final.status == "failed" and final.error is not None and final.error["code"] == "timeout"
    assert await event_types(h, task.id) == before

