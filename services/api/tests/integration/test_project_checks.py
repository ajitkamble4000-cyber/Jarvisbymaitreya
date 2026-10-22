"""Approved project checks through the task engine: approval, refusals, wire args, results and the timeout sweep.

Runs against PostgreSQL with the fake device hub from test_tasks_policy.py. The desktop agent is simulated by
the hub double, which records the signed command the engine sends.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.time import utc_now
from app.models.identity import UserPreference
from app.models.tasks import Task
from app.security.signing import verify_fields
from app.services.task_states import ProposalRefusedError
from tests.integration.test_tasks_policy import (
    Harness,
    add_device,
    backdate,
    build_harness,
    fetch,
    propose,
    result_for,
)

pytestmark = pytest.mark.integration

CHECK_CAPABILITIES = ["run_check", "system_info", "open_url"]
CHECK_RESOURCES = {
    "checks": [
        {"id": "tests", "label": "JARVIS unit tests"},
        {"id": "lint", "label": "Lint"},
    ],
}


@pytest_asyncio.fixture(loop_scope="session")
async def h(app: Any, settings: Any) -> Harness:
    return await build_harness(app, settings, email=f"checks-{uuid.uuid4().hex[:8]}@example.com")


async def _check_device(h: Harness, **overrides: Any) -> tuple[uuid.UUID, str]:
    options: dict[str, Any] = {
        "capabilities": CHECK_CAPABILITIES,
        "permissions": {"run_check": {"mode": "ask"}, "system_info": {"mode": "ask"}, "open_url": {"mode": "ask"}},
        "resources": CHECK_RESOURCES,
    }
    options.update(overrides)
    device_id, secret = await add_device(h, **options)
    h.hub.online.add(device_id)
    return device_id, secret


async def _set_timeout(h: Harness, seconds: int) -> None:
    async with h.factory() as db:
        db.add(UserPreference(user_id=h.user_id, settings={"automation": {"default_task_timeout_seconds": seconds}}))
        await db.commit()


async def _tasks_of(h: Harness) -> list[Task]:
    async with h.factory() as db:
        return list((await db.scalars(select(Task).where(Task.user_id == h.user_id))).all())


async def test_a_check_always_waits_for_approval_even_when_the_device_mode_is_allow(h: Harness) -> None:
    # The permission API refuses allow for run_check. This stored value simulates a bypass: the engine still asks.
    device_id, _ = await _check_device(
        h, permissions={"run_check": {"mode": "allow"}, "system_info": {"mode": "ask"}, "open_url": {"mode": "ask"}}
    )
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    assert task.status == "awaiting_approval"
    assert task.risk_level == "high"
    assert h.hub.commands == []


async def test_approved_check_sends_only_the_check_id_in_a_signed_command(h: Harness) -> None:
    device_id, secret = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "JARVIS unit tests"}, device_id=device_id)
    assert task.status == "awaiting_approval"
    assert task.title == "Run check JARVIS unit tests on Laptop"

    approved = await h.engine.approve(h.user_id, task.id)
    assert approved.status == "running"
    assert len(h.hub.commands) == 1
    _, command = h.hub.commands[0]
    assert command.action == "run_check"
    assert json.loads(command.args_json) == {"check_id": "tests"}
    signed = {
        "id": command.id,
        "action": command.action,
        "issued_at": command.issued_at,
        "expires_at": command.expires_at,
        "nonce": command.nonce,
        "args_json": command.args_json,
    }
    assert verify_fields(secret, signed, command.signature)
    stored = await fetch(h, task.id)
    assert stored.arguments == {"check": "JARVIS unit tests"}


async def test_unknown_check_is_refused_and_no_task_is_created(h: Harness) -> None:
    device_id, _ = await _check_device(h)
    before = len(await _tasks_of(h))
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "run_project_check", {"check": "Firefox"}, device_id=device_id)
    assert info.value.code == "resource_not_approved"
    assert info.value.details == {"approved": ["JARVIS unit tests", "Lint"]}
    assert len(await _tasks_of(h)) == before


async def test_a_device_without_the_run_check_capability_is_refused(h: Harness) -> None:
    device_id, _ = await _check_device(h, capabilities=["open_url"], permissions={"open_url": {"mode": "ask"}})
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    assert info.value.code == "capability_missing"
    assert h.hub.commands == []


async def test_a_failed_check_is_stored_with_check_failed_and_its_output(h: Harness) -> None:
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(
        device_id,
        result_for(
            command_id,
            "failed",
            output={"check_id": "tests", "label": "JARVIS unit tests", "exit_code": 1, "output_tail": "2 failed", "truncated": False},
            error={"code": "check_failed", "message": "The check exited with code 1.", "details": {}},
        ),
    )
    failed = await fetch(h, task.id)
    assert failed.status == "failed"
    assert failed.error["code"] == "check_failed"


async def test_a_check_result_that_reports_success_completes_the_task(h: Harness) -> None:
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "lint"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(
        device_id,
        result_for(
            command_id,
            "succeeded",
            output={"check_id": "lint", "label": "Lint", "exit_code": 0, "output_tail": "ok", "duration_ms": 1200},
        ),
    )
    done = await fetch(h, task.id)
    assert done.status == "completed"
    assert done.result is not None and done.result["output"]["exit_code"] == 0


async def test_the_dispatch_stores_a_deadline_from_the_users_timeout_setting(h: Harness) -> None:
    await _set_timeout(h, 10)
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    running = await fetch(h, task.id)
    assert running.status == "running"
    assert running.run_deadline_at is not None and running.started_at is not None
    assert running.run_deadline_at - running.started_at == timedelta(seconds=10)


async def test_the_sweep_times_out_a_check_at_the_stored_deadline(h: Harness) -> None:
    await _set_timeout(h, 10)
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    # The deadline has passed (the device never answered) while the start time is recent: the stored deadline decides.
    await backdate(h, task.id, run_deadline_at=utc_now() - timedelta(seconds=1))
    await h.engine.sweep_once()
    timed = await fetch(h, task.id)
    assert timed.status == "failed"
    assert timed.error["code"] == "timeout"


async def test_the_sweep_keeps_a_check_running_until_its_own_deadline(h: Harness) -> None:
    # No setting stored: the default deadline is the configured device timeout (120 s). Started 30 s ago, it is still running.
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    await backdate(h, task.id, started_at=utc_now() - timedelta(seconds=30))
    await h.engine.sweep_once()
    assert (await fetch(h, task.id)).status == "running"


async def test_a_running_task_without_a_stored_deadline_uses_the_configured_timeout(h: Harness) -> None:
    # Rows dispatched before run_deadline_at existed: started_at plus the configured device timeout still applies.
    device_id, _ = await _check_device(h)
    task = await propose(h, "run_project_check", {"check": "tests"}, device_id=device_id)
    await h.engine.approve(h.user_id, task.id)
    old_started = utc_now() - timedelta(seconds=h.app.state.settings.device_command_timeout_seconds + 5)
    await backdate(h, task.id, started_at=old_started, run_deadline_at=None)
    await h.engine.sweep_once()
    assert (await fetch(h, task.id)).status == "failed"
