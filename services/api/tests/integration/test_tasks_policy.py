"""Task engine policy and lifecycle against real PostgreSQL, with a fake device hub.

The fake hub records every command and cancel it is asked to send and reports the online set, so each
path (dispatch, approval, offline queueing, results, timeouts, emergency stop) is observable and signed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.errors import ConflictError, NotFoundError
from app.core.time import to_iso, utc_now
from app.devices.protocol import AgentProgress, AgentResult
from app.models.devices import Device
from app.models.identity import User, UserPreference
from app.models.platform import Notification
from app.models.tasks import Task, TaskEvent
from app.security.signing import verify_fields
from app.security.tokens import new_token, sha256_hex
from app.services.task_engine import TaskEngine
from app.services.task_states import ProposalRefusedError
from app.tools.registry import build_tool_registry
from tests.conftest import create_owner, csrf_headers

ALL_CAPABILITIES = ["open_url", "launch_app", "open_folder", "open_project", "system_info", "read_file", "app_availability"]
RESOURCES = {
    "apps": [{"id": "chrome", "label": "Google Chrome"}, {"id": "vscode", "label": "Visual Studio Code"}],
    "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}],
    "folders": [{"id": "downloads", "label": "Downloads"}],
    "projects": [{"id": "jarvis", "label": "JARVIS project"}],
    "files": [{"id": "notes", "label": "Meeting notes"}],
}


class FakeHub:
    """Test double for DeviceHub. ``accept`` controls whether a send is taken by the hub."""

    def __init__(self) -> None:
        self.online: set[uuid.UUID] = set()
        self.commands: list[tuple[uuid.UUID, Any]] = []
        self.cancels: list[tuple[uuid.UUID, Any]] = []
        self.accept = True

    def is_online(self, device_id: uuid.UUID) -> bool:
        return device_id in self.online

    async def send_command(self, device_id: uuid.UUID, message: Any) -> bool:
        if device_id not in self.online or not self.accept:
            return False
        self.commands.append((device_id, message))
        return True

    async def send_cancel(self, device_id: uuid.UUID, message: Any) -> bool:
        if device_id not in self.online:
            return False
        self.cancels.append((device_id, message))
        return True


@dataclass
class Harness:
    app: Any
    engine: TaskEngine
    hub: FakeHub
    factory: Any
    user_id: uuid.UUID


async def _insert_user(factory: Any, email: str) -> uuid.UUID:
    async with factory() as db:
        user = User(email=email, display_name=email.split("@")[0], password_hash="!unused", role="owner", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def build_harness(app: Any, settings: Any, email: str = "harness@example.com") -> Harness:
    hub = FakeHub()
    engine = TaskEngine(
        settings=settings,
        session_factory=app.state.session_factory,
        secret_box=app.state.secret_box,
        events=app.state.events,
        tools=build_tool_registry(),
        devices=hub,
    )
    user_id = await _insert_user(app.state.session_factory, email)
    return Harness(app=app, engine=engine, hub=hub, factory=app.state.session_factory, user_id=user_id)


@pytest_asyncio.fixture(loop_scope="session")
async def h(app: Any, settings: Any) -> Harness:
    return await build_harness(app, settings)


async def add_device(
    h: Harness,
    *,
    name: str = "Laptop",
    capabilities: list[str] | None = None,
    permissions: dict[str, dict[str, str]] | None = None,
    is_default: bool = False,
    revoked: bool = False,
    user_id: uuid.UUID | None = None,
    resources: dict | None = None,
) -> tuple[uuid.UUID, str]:
    caps = list(ALL_CAPABILITIES if capabilities is None else capabilities)
    secret = new_token(32)
    now = utc_now()
    async with h.factory() as db:
        device = Device(
            user_id=user_id or h.user_id,
            name=name,
            device_type="laptop",
            os="windows",
            os_version="10.0.22631",
            agent_version="1.0.0",
            pairing_status="revoked" if revoked else "paired",
            revoked_at=now if revoked else None,
            is_default=is_default,
            capabilities=caps,
            permissions=permissions if permissions is not None else {c: {"mode": "ask"} for c in caps},
            resources=copy.deepcopy(resources if resources is not None else RESOURCES),
            credential_hash=sha256_hex(secret),
            secret_ciphertext=h.app.state.secret_box.encrypt(secret),
            registered_at=now,
            paired_at=now,
        )
        db.add(device)
        await db.commit()
        return device.id, secret


def allow(*names: str) -> dict[str, dict[str, str]]:
    return {c: {"mode": "allow" if c in names else "ask"} for c in ALL_CAPABILITIES}


async def propose(h: Harness, tool: str, arguments: dict, *, user_id: uuid.UUID | None = None, **kwargs: Any) -> Task:
    async with h.factory() as db:
        task = await h.engine.propose(
            db,
            user_id=user_id or h.user_id,
            tool_name=tool,
            arguments=arguments,
            source=kwargs.pop("source", "chat"),
            **kwargs,
        )
        await db.commit()
        return task


async def fetch(h: Harness, task_id: uuid.UUID) -> Task:
    async with h.factory() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        return task


async def event_types(h: Harness, task_id: uuid.UUID) -> list[str]:
    async with h.factory() as db:
        rows = await db.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id))
        return list(rows.all())


async def notifications_of(h: Harness) -> list[Notification]:
    async with h.factory() as db:
        rows = await db.scalars(select(Notification).where(Notification.user_id == h.user_id).order_by(Notification.created_at))
        return list(rows.all())


async def backdate(h: Harness, task_id: uuid.UUID, **values: Any) -> None:
    async with h.factory() as db:
        await db.execute(update(Task).where(Task.id == task_id).values(**values))
        await db.commit()


def result_for(command_id: Any, status: str, *, output: dict | None = None, error: dict | None = None) -> AgentResult:
    return AgentResult(
        type="result",
        command_id=str(command_id),
        status=status,
        output=output or {},
        error=error,
        finished_at=to_iso(utc_now()) or "",
    )


async def test_low_tool_with_allow_dispatches_immediately_with_a_signed_command(h: Harness) -> None:
    device_id, secret = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"url": "https://www.youtube.com"}, device_id=device_id)
    assert task.status == "running"
    assert task.attempt == 1
    sent_to, message = h.hub.commands[0]
    assert sent_to == device_id
    assert message.action == "open_url"
    assert json.loads(message.args_json) == {"url": "https://www.youtube.com"}
    assert task.command_id == uuid.UUID(message.id)
    assert verify_fields(
        secret,
        {
            "id": message.id,
            "action": message.action,
            "issued_at": message.issued_at,
            "expires_at": message.expires_at,
            "nonce": message.nonce,
            "args_json": message.args_json,
        },
        message.signature,
    )


async def test_default_permission_asks_even_for_low_risk_tools(h: Harness) -> None:
    device_id, _ = await add_device(h)
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert task.status == "awaiting_approval"
    assert h.hub.commands == []


async def test_medium_tool_waits_for_approval_then_dispatches_after_approve(h: Harness) -> None:
    device_id, _ = await add_device(h)
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "Chrome"}, device_id=device_id)
    assert task.status == "awaiting_approval"
    assert task.approval_expires_at is not None
    assert h.hub.commands == []
    approvals = [n for n in await notifications_of(h) if n.kind == "approval_required"]
    assert [n.body for n in approvals] == ["Jarvis wants to: Launch Google Chrome on Laptop"]

    approved = await h.engine.approve(h.user_id, task.id)
    assert approved.status == "running"
    _, message = h.hub.commands[0]
    assert message.action == "launch_app"
    assert json.loads(message.args_json) == {"app_id": "chrome"}
    types = await event_types(h, task.id)
    assert types[:4] == ["created", "awaiting_approval", "approved", "queued"]
    assert "dispatched" in types and types[-1] == "running"


async def test_deny_cancels_and_keeps_the_reason_out_of_notifications(h: Harness) -> None:
    device_id, _ = await add_device(h)
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    denied = await h.engine.deny(h.user_id, task.id, "do not open this")
    assert denied.status == "cancelled"
    assert denied.error["code"] == "approval_denied"
    assert denied.error["details"] == {"reason": "do not open this"}
    assert h.hub.commands == []
    for note in await notifications_of(h):
        assert "do not open this" not in note.body and "do not open this" not in note.title


async def test_approval_expiry_cancels_on_sweep(h: Harness) -> None:
    device_id, _ = await add_device(h)
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    await backdate(h, task.id, approval_expires_at=utc_now() - timedelta(seconds=1))
    await h.engine.sweep_once()
    expired = await fetch(h, task.id)
    assert expired.status == "cancelled"
    assert expired.error["code"] == "approval_expired"
    assert "expired" in await event_types(h, task.id)


async def test_late_approval_after_expiry_cancels_instead_of_running(h: Harness) -> None:
    device_id, _ = await add_device(h)
    h.hub.online.add(device_id)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    await backdate(h, task.id, approval_expires_at=utc_now() - timedelta(seconds=1))
    # The late approval is refused with a conflict. The expiry is committed and reported in the chat.
    with pytest.raises(ConflictError) as info:
        await h.engine.approve(h.user_id, task.id)
    assert info.value.code == "approval_expired"
    expired = await fetch(h, task.id)
    assert expired.status == "cancelled"
    assert expired.error["code"] == "approval_expired"
    assert h.hub.commands == []


async def test_offline_device_leaves_task_queued_then_dispatches_on_connect(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    assert task.status == "queued"
    assert task.current_step == "waiting_for_device"
    assert task.command_id is None and task.attempt == 0
    assert h.hub.commands == []

    h.hub.online.add(device_id)
    await h.engine.on_device_connected(device_id)
    running = await fetch(h, task.id)
    assert running.status == "running"
    assert json.loads(h.hub.commands[0][1].args_json) == {"url_id": "youtube"}


async def test_hub_refusing_the_command_reverts_to_queued_without_claiming_execution(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    h.hub.accept = False
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    reverted = await fetch(h, task.id)
    assert reverted.status == "queued"
    assert reverted.command_id is None
    assert reverted.attempt == 0
    assert reverted.current_step == "waiting_for_device"
    types = await event_types(h, task.id)
    assert types[-1] == "queued"
    assert "completed" not in types and "failed" not in types
    async with h.factory() as db:
        last = await db.scalar(select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id.desc()))
    assert last is not None and "did not accept" in last.message


async def test_agent_success_completes_the_task_and_notifies_without_arguments(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(
        device_id, result_for(command_id, "succeeded", output={"summary": "Opened YouTube.", "verified": False})
    )
    done = await fetch(h, task.id)
    assert done.status == "completed"
    assert done.result == {"summary": "Opened YouTube.", "output": {"summary": "Opened YouTube.", "verified": False}}
    assert done.finished_at is not None and done.error is None
    completed = [n for n in await notifications_of(h) if n.kind == "task_completed"]
    assert len(completed) == 1
    assert completed[0].related_id == str(task.id)
    assert "YouTube" not in completed[0].body


async def test_agent_failure_stores_the_error_and_unknown_codes_fall_back(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    first = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_agent_result(
        device_id,
        result_for(h.hub.commands[0][1].id, "failed", error={"code": "not_installed", "message": "Browser not found."}),
    )
    failed = await fetch(h, first.id)
    assert failed.status == "failed"
    assert failed.error["code"] == "not_installed"
    assert failed.error["message"] == "Browser not found."

    second = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_agent_result(
        device_id, result_for(h.hub.commands[1][1].id, "failed", error={"code": "invented_code", "message": "x"})
    )
    assert (await fetch(h, second.id)).error["code"] == "execution_failed"
    assert [n.kind for n in await notifications_of(h)].count("task_failed") == 2


async def test_unknown_or_foreign_command_results_are_ignored(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    other_device, _ = await add_device(h, name="Desk")
    h.hub.online.update({device_id, other_device})
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_result(device_id, result_for(uuid.uuid4(), "succeeded"))
    await h.engine.on_agent_result(other_device, result_for(command_id, "succeeded", output={"summary": "spoof"}))
    assert (await fetch(h, task.id)).status == "running"


async def test_progress_updates_step_and_only_stores_measured_progress(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    command_id = h.hub.commands[0][1].id
    await h.engine.on_agent_progress(
        device_id,
        AgentProgress(type="progress", command_id=command_id, step="Waiting for window", message="", progress=None, sent_at=to_iso(utc_now()) or ""),
    )
    assert (await fetch(h, task.id)).current_step == "Waiting for window"
    assert (await fetch(h, task.id)).progress is None
    await h.engine.on_agent_progress(
        device_id,
        AgentProgress(type="progress", command_id=command_id, step="Loading", message="", progress=0.5, sent_at=to_iso(utc_now()) or ""),
    )
    assert (await fetch(h, task.id)).progress == {"value": 0.5, "step": "Loading"}


async def test_device_permission_deny_blocks_the_proposal(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions={"launch_app": {"mode": "deny"}})
    h.hub.online.add(device_id)
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    assert info.value.code == "permission_denied"
    async with h.factory() as db:
        assert (await db.scalars(select(Task).where(Task.user_id == h.user_id))).all() == []


async def test_emergency_stop_blocks_proposals_until_resumed(h: Harness) -> None:
    device_id, _ = await add_device(h)
    assert await h.engine.emergency_stop(h.user_id) == {"stopped": True, "cancelled": 0, "cancel_requested": 0}
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "system_info", {}, device_id=device_id)
    assert info.value.code == "emergency_stop_active"
    assert await h.engine.emergency_resume(h.user_id) == {"stopped": False}
    task = await propose(h, "system_info", {}, device_id=device_id)
    assert task.status == "awaiting_approval"


async def test_emergency_stop_cancels_queued_awaiting_and_running_work(h: Harness) -> None:
    laptop, _ = await add_device(h, name="Laptop", permissions=allow("open_url"))
    tablet, _ = await add_device(h, name="Tablet", permissions=allow("open_url"))
    h.hub.online.add(laptop)
    running = await propose(h, "open_url", {"site": "YouTube"}, device_id=laptop)
    awaiting = await propose(h, "launch_app", {"app": "chrome"}, device_id=laptop)
    queued = await propose(h, "open_url", {"site": "YouTube"}, device_id=tablet)
    assert (running.status, awaiting.status, queued.status) == ("running", "awaiting_approval", "queued")

    outcome = await h.engine.emergency_stop(h.user_id)
    # The running task is only asked to stop, so it is not counted as cancelled.
    assert outcome == {"stopped": True, "cancelled": 2, "cancel_requested": 1}
    assert len(h.hub.cancels) == 1
    assert h.hub.cancels[0][1].command_id == str(h.hub.commands[0][1].id)
    assert (await fetch(h, awaiting.id)).status == "cancelled"
    assert (await fetch(h, queued.id)).status == "cancelled"
    still_running = await fetch(h, running.id)
    assert still_running.status == "running" and still_running.cancel_requested is True

    await h.engine.on_agent_result(laptop, result_for(h.hub.commands[0][1].id, "cancelled"))
    assert (await fetch(h, running.id)).status == "cancelled"


async def test_cancel_on_an_offline_device_cancels_now_with_an_honest_note(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    h.hub.online.discard(device_id)
    cancelled = await h.engine.cancel(h.user_id, task.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error["code"] == "cancelled_by_user"
    assert "may still have completed" in cancelled.error["details"]["note"]


async def test_server_tool_runs_immediately_and_is_recorded_as_completed(h: Harness) -> None:
    await add_device(h)
    task = await propose(h, "list_devices", {})
    assert task.status == "completed"
    assert task.result["output"]["devices"][0]["name"] == "Laptop"
    assert await event_types(h, task.id) == ["created", "completed"]


async def test_server_tool_failure_is_recorded_honestly(h: Harness) -> None:
    missing = uuid.uuid4()
    task = await propose(h, "get_task", {"task_id": str(missing)})
    assert task.status == "failed"
    assert task.error["code"] == "task_not_found"


async def test_reminder_refuses_honestly_when_scheduling_is_not_available(h: Harness) -> None:
    if importlib.util.find_spec("app.services.schedule_service") is not None:
        pytest.skip("scheduling service is present; its behaviour is covered by the scheduling tests")
    when = (utc_now() + timedelta(hours=2)).isoformat()
    task = await propose(h, "create_reminder", {"message": "call the bank", "when": when})
    assert task.status == "failed"
    assert task.error["code"] == "unavailable"


async def test_idempotency_key_returns_the_same_task(h: Harness) -> None:
    first = await propose(h, "list_devices", {}, idempotency_key="key-1")
    second = await propose(h, "list_devices", {}, idempotency_key="key-1")
    assert first.id == second.id
    async with h.factory() as db:
        count = len((await db.scalars(select(Task).where(Task.user_id == h.user_id))).all())
    assert count == 1


async def test_idempotency_key_is_scoped_to_the_user(h: Harness) -> None:
    other = await _insert_user(h.factory, "second@example.com")
    first = await propose(h, "list_devices", {}, idempotency_key="shared")
    second = await propose(h, "list_devices", {}, user_id=other, idempotency_key="shared")
    assert first.id != second.id and second.user_id == other


async def test_retry_is_only_for_failed_or_cancelled_and_reapplies_policy(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    completed = await propose(h, "list_devices", {})
    with pytest.raises(ConflictError):
        await h.engine.retry(h.user_id, completed.id)

    failed = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "failed", error={"code": "timeout"}))
    retried = await h.engine.retry(h.user_id, failed.id)
    assert retried.id != failed.id
    assert retried.tool_name == "open_url" and retried.arguments == {"site": "YouTube"}
    assert retried.description == f"Retry of task {failed.id}."
    assert retried.status == "running"
    assert (await fetch(h, failed.id)).status == "failed"
    assert len(h.hub.commands) == 2


async def test_retry_refuses_when_the_device_is_now_revoked(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    failed = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "failed", error={"code": "timeout"}))
    async with h.factory() as db:
        await db.execute(update(Device).where(Device.id == device_id).values(pairing_status="revoked", revoked_at=utc_now()))
        await db.commit()
    with pytest.raises(ProposalRefusedError) as info:
        await h.engine.retry(h.user_id, failed.id)
    assert info.value.code == "device_revoked"


async def test_ambiguous_device_name_needs_selection_and_creates_nothing(h: Harness) -> None:
    work, _ = await add_device(h, name="Laptop Work")
    home, _ = await add_device(h, name="Laptop Home")
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome", "device": "Laptop"})
    assert info.value.code == "needs_device_selection"
    names = sorted(candidate["name"] for candidate in info.value.details["candidates"])
    assert names == ["Laptop Home", "Laptop Work"]
    async with h.factory() as db:
        assert (await db.scalars(select(Task).where(Task.user_id == h.user_id))).all() == []
    task = await propose(h, "launch_app", {"app": "chrome", "device": "Laptop Home"})
    assert task.device_id == home
    assert work != home


async def test_default_device_is_used_when_no_name_is_given(h: Harness) -> None:
    default_id, _ = await add_device(h, name="Laptop", is_default=True)
    await add_device(h, name="Desk")
    task = await propose(h, "launch_app", {"app": "chrome"})
    assert task.device_id == default_id


async def test_single_paired_device_is_used_without_a_name(h: Harness) -> None:
    only, _ = await add_device(h, name="Laptop")
    task = await propose(h, "launch_app", {"app": "chrome"})
    assert task.device_id == only


async def test_no_paired_device_is_device_not_found(h: Harness) -> None:
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome"})
    assert info.value.code == "device_not_found"


async def test_another_users_device_and_revoked_device_are_refused(h: Harness) -> None:
    other = await _insert_user(h.factory, "neighbour@example.com")
    foreign, _ = await add_device(h, name="Their Laptop", user_id=other)
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome"}, device_id=foreign)
    assert info.value.code == "device_not_found"

    revoked, _ = await add_device(h, name="Old", revoked=True)
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome"}, device_id=revoked)
    assert info.value.code == "device_revoked"


async def test_missing_capability_is_refused(h: Harness) -> None:
    device_id, _ = await add_device(h, capabilities=["open_url"])
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    assert info.value.code == "capability_missing"


async def test_unapproved_resource_is_refused_with_the_approved_labels(h: Harness) -> None:
    device_id, _ = await add_device(h)
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "Firefox"}, device_id=device_id)
    assert info.value.code == "resource_not_approved"
    assert info.value.details["approved"] == ["Google Chrome", "Visual Studio Code"]


async def test_invalid_and_unknown_tool_arguments_are_refused_without_echo(h: Harness) -> None:
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "launch_app", {"app": "chrome", "token": "sk-SECRETVALUE123"})
    assert info.value.code == "invalid_arguments"
    assert "SECRETVALUE" not in json.dumps(info.value.details)
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "does_not_exist", {})
    assert info.value.code == "invalid_arguments"


async def test_sensitive_memory_is_refused_before_a_task_is_stored(h: Harness) -> None:
    with pytest.raises(ProposalRefusedError) as info:
        await propose(h, "save_memory", {"category": "fact", "content": "my password is hunter2-long"})
    assert info.value.code == "sensitive_content"
    async with h.factory() as db:
        assert (await db.scalars(select(Task).where(Task.user_id == h.user_id))).all() == []


async def test_concurrency_limit_queues_and_drains_when_a_slot_frees(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    async with h.factory() as db:
        db.add(UserPreference(user_id=h.user_id, settings={"automation": {"max_concurrent_tasks": 1}}))
        await db.commit()
    first = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    second = await propose(h, "open_url", {"url": "https://www.youtube.com"}, device_id=device_id)
    assert first.status == "running"
    assert second.status == "queued" and second.current_step == "waiting_for_capacity"
    assert len(h.hub.commands) == 1

    await h.engine.on_agent_result(device_id, result_for(h.hub.commands[0][1].id, "succeeded", output={"summary": "ok"}))
    assert (await fetch(h, second.id)).status == "running"
    assert len(h.hub.commands) == 2


async def test_running_task_times_out_on_sweep(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    past = utc_now() - timedelta(seconds=h.app.state.settings.device_command_timeout_seconds + 5)
    await backdate(h, task.id, started_at=past, run_deadline_at=past + timedelta(seconds=h.app.state.settings.device_command_timeout_seconds))
    await h.engine.sweep_once()
    timed = await fetch(h, task.id)
    assert timed.status == "failed"
    assert timed.error["code"] == "timeout"
    assert [n.kind for n in await notifications_of(h)].count("task_failed") == 1


async def test_queued_task_expires_after_the_queue_ttl_with_device_offline(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await backdate(h, task.id, created_at=utc_now() - timedelta(seconds=h.app.state.settings.task_queue_ttl_seconds + 5))
    await h.engine.sweep_once()
    expired = await fetch(h, task.id)
    assert expired.status == "failed"
    assert expired.error["code"] == "device_offline"
    assert h.hub.commands == []


async def test_device_disconnect_keeps_running_task_and_records_an_event(h: Harness) -> None:
    device_id, _ = await add_device(h, permissions=allow("open_url"))
    h.hub.online.add(device_id)
    task = await propose(h, "open_url", {"site": "YouTube"}, device_id=device_id)
    await h.engine.on_device_disconnected(device_id)
    assert (await fetch(h, task.id)).status == "running"
    assert "device_disconnected" in await event_types(h, task.id)


async def test_another_users_task_is_not_found_for_any_action(h: Harness) -> None:
    device_id, _ = await add_device(h)
    task = await propose(h, "launch_app", {"app": "chrome"}, device_id=device_id)
    stranger = await _insert_user(h.factory, "stranger@example.com")
    with pytest.raises(NotFoundError):
        await h.engine.approve(stranger, task.id)
    with pytest.raises(NotFoundError):
        await h.engine.cancel(stranger, task.id)
    with pytest.raises(NotFoundError):
        await h.engine.retry(stranger, task.id)
    assert (await fetch(h, task.id)).status == "awaiting_approval"


# ---- HTTP surface -------------------------------------------------------------------------

async def _owner_device(h: Harness, owner_id: str, name: str = "Laptop", **kwargs: Any) -> uuid.UUID:
    device_id, _ = await add_device(h, name=name, user_id=uuid.UUID(owner_id), **kwargs)
    return device_id


async def test_tasks_api_is_scoped_and_lists_counts(client: AsyncClient, app: Any, settings: Any) -> None:
    owner = await create_owner(client)
    h = await build_harness(app, settings)
    foreign_task = await propose(h, "list_devices", {})
    response = await client.get(f"/api/v1/tasks/{foreign_task.id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"

    created = await client.post(
        "/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client)
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "completed" and body["source"] == "manual"
    assert body["actions"] == {"approve": False, "deny": False, "cancel": False, "retry": False}

    listing = (await client.get("/api/v1/tasks")).json()
    assert listing["total"] == 1
    assert listing["counts"]["completed"] == 1 and listing["counts"]["queued"] == 0
    detail = (await client.get(f"/api/v1/tasks/{body['id']}")).json()
    assert [event["type"] for event in detail["events"]] == ["created", "completed"]
    assert owner["id"]


async def test_tasks_api_refuses_ambiguous_device_with_candidates(client: AsyncClient, app: Any, settings: Any) -> None:
    owner = await create_owner(client)
    h = await build_harness(app, settings)
    await _owner_device(h, owner["id"], "Laptop Work")
    await _owner_device(h, owner["id"], "Laptop Home")
    response = await client.post(
        "/api/v1/tasks",
        json={"tool_name": "launch_app", "arguments": {"app": "chrome"}},
        headers=csrf_headers(client),
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "needs_device_selection"
    assert len(error["details"]["candidates"]) == 2
    assert (await client.get("/api/v1/tasks")).json()["total"] == 0


async def test_tasks_api_approve_dispatches_through_the_hub(client: AsyncClient, app: Any, settings: Any) -> None:
    owner = await create_owner(client)
    h = await build_harness(app, settings)
    device_id = await _owner_device(h, owner["id"])
    h.hub.online.add(device_id)
    h.app.state.task_engine = h.engine
    h.app.state.device_hub = h.hub

    created = await client.post(
        "/api/v1/tasks",
        json={"tool_name": "launch_app", "arguments": {"app": "Chrome"}, "device_id": str(device_id)},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "awaiting_approval"
    assert task["device"]["connection_status"] == "online"
    assert task["actions"]["approve"] is True

    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve", headers=csrf_headers(client))
    assert approved.status_code == 200
    assert approved.json()["status"] == "running"
    assert len(h.hub.commands) == 1


async def test_tasks_api_rejects_unknown_fields_and_bad_filters(client: AsyncClient) -> None:
    await create_owner(client)
    extra = await client.post(
        "/api/v1/tasks",
        json={"tool_name": "list_devices", "arguments": {}, "shell": "rm"},
        headers=csrf_headers(client),
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_error"
    bad_status = await client.get("/api/v1/tasks?status=paused")
    assert bad_status.status_code == 422


async def test_emergency_stop_endpoints_require_confirmation_and_block_automation(client: AsyncClient) -> None:
    await create_owner(client)
    wrong = await client.post("/api/v1/tasks/emergency-stop", json={"confirm": "yes"}, headers=csrf_headers(client))
    assert wrong.status_code == 422
    stopped = await client.post("/api/v1/tasks/emergency-stop", json={"confirm": "STOP"}, headers=csrf_headers(client))
    assert stopped.status_code == 200
    assert stopped.json() == {"stopped": True, "cancelled": 0}

    blocked = await client.post(
        "/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client)
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "emergency_stop_active"

    resumed = await client.post("/api/v1/tasks/emergency-resume", headers=csrf_headers(client))
    assert resumed.json() == {"stopped": False}


async def test_retry_endpoint_rejects_completed_tasks(client: AsyncClient) -> None:
    await create_owner(client)
    created = await client.post(
        "/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client)
    )
    response = await client.post(f"/api/v1/tasks/{created.json()['id']}/retry", headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_state_conflict"
