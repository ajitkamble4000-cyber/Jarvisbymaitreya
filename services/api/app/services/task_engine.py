"""Task engine: validation, policy, approvals, dispatch to devices, results, timeouts and emergency stop.

Constructor and public method signatures are a contract shared with ``devices.hub.DeviceHub`` and the
chat and scheduler services (see docs/architecture.md).

Ordering rules that keep the state honest:
* A ``running`` row is committed before the command leaves the process, so a fast agent result always
  finds its task. If the hub refuses the send, the task returns to ``queued`` (``DISPATCH_REVERT``).
* Refusals raise ``ProposalRefusedError`` and create no task. Server tools run before their task row is
  written and are recorded as completed or failed. Nothing is recorded as executed on a device until an
  agent ``result`` says so.
* Domain events are published only after the change that produced them has been committed.
* A result for a task that already ended follows the late-result rules (docs/phase2/late-results.md). Only
  ``apply_late_result`` may move a failed task to completed.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationAppError
from app.core.time import to_iso, utc_now
from app.database.session import session_scope
from app.devices.protocol import AgentProgress, AgentResult, build_cancel, build_command
from app.models.conversation import Conversation, Message
from app.models.devices import Device
from app.models.identity import UserPreference
from app.models.tasks import Task, TaskEvent
from app.schemas.devices import filter_capabilities
from app.scheduler.timing import instant_for_local
from app.security.crypto import SecretBox, SecretDecryptionError
from app.services.audit import record_audit
from app.services.chat import post_late_result_message, post_task_outcome_message
from app.services.device_resolver import (
    connection_status,
    dispatch_refusal,
    effective_mode,
    ensure_capability,
    paired_devices,
    permission_mode,
    resolve_device,
    resolve_target,
)
from app.services.events import EventBus
from app.services.memory_service import load_settings_document, save_from_tool
from app.services.notifications import notify
from app.services.task_states import (
    TIMEOUT_ERROR_CODE,
    ProposalRefusedError,
    apply_late_result,
    apply_transition,
    record_event,
    revert_dispatch,
)
from app.tools.args import ToolArgumentError, validate_tool_arguments
from app.tools.base import ExecutionTarget, ToolRegistry, ToolSpec
from app.tools.server_handlers import get_server_handler

log = logging.getLogger("jarvis.tasks")

SOURCES: frozenset[str] = frozenset({"chat", "schedule", "manual", "api"})
SWEEP_INTERVAL_SECONDS = 5.0
TITLE_MAX = 200
DESCRIPTION_MAX = 2000
SUMMARY_MAX = 500
ERROR_MESSAGE_MAX = 500
DEFAULT_AGENT_ERROR = "execution_failed"
AGENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "device_offline",
        "device_not_found",
        "needs_device_selection",
        "device_revoked",
        "permission_denied",
        "capability_missing",
        "resource_not_approved",
        "invalid_arguments",
        "approval_expired",
        "approval_denied",
        "cancelled_by_user",
        "timeout",
        "not_installed",
        "execution_failed",
        "unsupported_platform",
        "agent_error",
        "check_failed",
        # Protocol-level rejections reported by the agent (docs/desktop-protocol.md section 4).
        "expired",
        "replayed",
        "unsupported_action",
        "signature_invalid",
    }
)


class _Dispatch(StrEnum):
    DISPATCHED = "dispatched"
    WAITING_DEVICE = "waiting_device"
    WAITING_CAPACITY = "waiting_capacity"
    FINISHED = "finished"


class HubLike(Protocol):
    """The subset of ``DeviceHub`` the engine uses. Tests supply a double with the same methods."""

    def is_online(self, device_id: uuid.UUID) -> bool: ...

    async def send_command(self, device_id: uuid.UUID, message: Any) -> bool: ...

    async def send_cancel(self, device_id: uuid.UUID, message: Any) -> bool: ...


@dataclass(frozen=True)
class AutomationPolicy:
    emergency_stopped: bool
    max_concurrent_tasks: int
    # automation.default_task_timeout_seconds, or None when the stored document does not carry a valid value.
    default_task_timeout_seconds: int | None
    disabled_tools: frozenset[str]
    include_task_details: bool
    notify_completed: bool
    notify_failed: bool
    notify_approval: bool

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> AutomationPolicy:
        automation = _section(doc, "automation")
        notifications = _section(doc, "notifications")
        raw_max = automation.get("max_concurrent_tasks", 3)
        max_concurrent = raw_max if isinstance(raw_max, int) and not isinstance(raw_max, bool) and 1 <= raw_max <= 10 else 3
        raw_timeout = automation.get("default_task_timeout_seconds")
        task_timeout = (
            raw_timeout
            if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool) and 10 <= raw_timeout <= 1800
            else None
        )
        disabled = automation.get("disabled_tools")
        return cls(
            # Fail safe: any truthy stored value counts as stopped.
            emergency_stopped=bool(automation.get("emergency_stopped")),
            max_concurrent_tasks=max_concurrent,
            default_task_timeout_seconds=task_timeout,
            disabled_tools=frozenset(str(name) for name in disabled) if isinstance(disabled, list) else frozenset(),
            include_task_details=notifications.get("include_task_details") is True,
            notify_completed=notifications.get("on_task_completed", True) is not False,
            notify_failed=notifications.get("on_task_failed", True) is not False,
            notify_approval=notifications.get("on_approval_required", True) is not False,
        )


def _section(doc: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = doc.get(name)
    return value if isinstance(value, Mapping) else {}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def write_emergency_flag(db: AsyncSession, user_id: uuid.UUID, value: bool) -> None:
    """Set ``automation.emergency_stopped``. Uses the settings helper when present, else writes the document."""
    try:
        module = importlib.import_module("app.services.settings_service")
    except ImportError:
        module = None
    setter = getattr(module, "set_emergency_stopped", None) if module is not None else None
    if setter is not None:
        await _maybe_await(setter(db, user_id, value))
        return
    row = await db.get(UserPreference, user_id)
    if row is None:
        row = UserPreference(user_id=user_id, settings={})
        db.add(row)
    document = dict(row.settings or {})
    automation = dict(_section(document, "automation"))
    automation["emergency_stopped"] = value
    document["automation"] = automation
    row.settings = document


def _validated(spec: ToolSpec, arguments: Any) -> BaseModel:
    try:
        return validate_tool_arguments(spec.args_model, arguments)
    except ToolArgumentError as exc:
        if exc.code == "sensitive_content":
            raise ProposalRefusedError(
                "sensitive_content", "That looks like a password or key, so it was not saved."
            ) from None
        raise ProposalRefusedError(
            "invalid_arguments", "The arguments are not valid.", details={"fields": exc.fields}
        ) from None


def _stored(args: BaseModel) -> dict[str, Any]:
    return args.model_dump(mode="json", exclude_none=True)


def _clip(text: str, limit: int) -> str:
    return text.strip()[:limit]


def _default_title(spec: ToolSpec, arguments: Mapping[str, Any], labels: Mapping[str, str], device_name: str | None) -> str:
    on_device = f" on {device_name}" if device_name else ""
    name = spec.name
    if name == "open_url":
        return f"Open {labels.get('site') or 'a web page'}{on_device}"
    if name == "launch_app":
        return f"Launch {labels.get('app') or arguments.get('app')}{on_device}"
    if name == "open_folder":
        return f"Open folder {labels.get('folder') or arguments.get('folder')}{on_device}"
    if name == "open_project":
        return f"Open project {labels.get('project')} in {labels.get('app')}{on_device}"
    if name == "system_info":
        return f"Check system information{on_device}"
    if name == "read_file":
        return f"Read {labels.get('file') or arguments.get('file')}{on_device}"
    if name == "app_availability":
        return f"Check whether {labels.get('app') or arguments.get('app')} is installed{on_device}"
    if name == "run_project_check":
        return f"Run check {labels.get('check') or arguments.get('check')}{on_device}"
    if name == "save_memory":
        return "Save a memory"
    if name == "create_reminder":
        return "Create a reminder"
    return spec.title


def _result_summary(title: str, output: Mapping[str, Any]) -> str:
    summary = output.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _clip(summary, SUMMARY_MAX)
    return f"{title} completed."


def _reported_summary(output: Mapping[str, Any]) -> str | None:
    """The summary the device actually sent, or None. Never a generated sentence."""
    summary = output.get("summary")
    return _clip(summary, SUMMARY_MAX) if isinstance(summary, str) and summary.strip() else None


def _error_code(task: Task) -> str | None:
    error = task.error if isinstance(task.error, Mapping) else {}
    code = error.get("code")
    return code if isinstance(code, str) else None


def _agent_error(error: Any) -> tuple[str, str]:
    default_message = "The device could not complete the action."
    if not isinstance(error, Mapping):
        return DEFAULT_AGENT_ERROR, default_message
    raw_code = error.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code in AGENT_ERROR_CODES else DEFAULT_AGENT_ERROR
    raw_message = error.get("message")
    message = _clip(raw_message, ERROR_MESSAGE_MAX) if isinstance(raw_message, str) and raw_message.strip() else default_message
    return code, message


def _device_secret(box: SecretBox, device: Device) -> str | None:
    if not device.secret_ciphertext:
        return None
    try:
        return box.decrypt(device.secret_ciphertext)
    except SecretDecryptionError:
        return None


def _task_brief(task: Task) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status,
        "tool_name": task.tool_name,
        "created_at": to_iso(task.created_at),
    }


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def reminder_instant(when: str, *, timezone_name: str, now: datetime) -> datetime:
    """UTC instant for ``when``. HH:MM means the next occurrence in the user's timezone.

    Wall-clock times use the scheduler's DST rules, so a time in a spring gap runs at the first valid
    instant, the same as a scheduled job.
    """
    if len(when) == 5 and when[2] == ":":
        hour, minute = int(when[:2]), int(when[3:])
        if hour >= 24 or minute >= 60:
            raise ValueError("when is not a valid time")
        zone = _zone(timezone_name)
        local_today = now.astimezone(zone).date()
        instant = instant_for_local(local_today, time(hour, minute), zone)
        if instant <= now.astimezone(UTC):
            instant = instant_for_local(local_today + timedelta(days=1), time(hour, minute), zone)
        return instant
    return datetime.fromisoformat(when).astimezone(UTC)


class TaskEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        secret_box: SecretBox,
        events: EventBus,
        tools: ToolRegistry,
        devices: Any,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._secret_box = secret_box
        self._events = events
        self._tools = tools
        self._devices: HubLike = devices
        # Serialises capacity checks and the running transition within this process.
        self._dispatch_lock = asyncio.Lock()
        self._sweep_task: asyncio.Task[None] | None = None

    # ---- lifecycle --------------------------------------------------------------------

    async def start(self) -> None:
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(self._run_sweeps(), name="jarvis-task-sweeps")

    async def stop(self) -> None:
        task, self._sweep_task = self._sweep_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run_sweeps(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await self.sweep_once()
            except Exception:  # one failed sweep must not stop the loop; the next one retries
                log.exception("Task sweep failed.")

    # ---- proposals ---------------------------------------------------------------------

    async def propose(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        tool_name: str,
        arguments: dict,
        source: str,
        conversation_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        title: str | None = None,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> Task:
        """Validate, apply policy and create the task. Refusals raise ``ProposalRefusedError`` and create nothing.

        Server tools with no approval run now and the task is created as completed or failed. Device tasks
        are created as ``awaiting_approval`` or ``queued``. A queued task is dispatched before this returns
        when the device is online and a slot is free. The caller commits any work it adds afterwards.
        """
        spec = self._tools.get(tool_name)
        if spec is None:
            raise ProposalRefusedError("invalid_arguments", "Unknown tool.")
        if source not in SOURCES:
            raise ProposalRefusedError("invalid_arguments", "Unknown task source.")
        if idempotency_key:
            existing = await self._by_idempotency(db, user_id, idempotency_key)
            if existing is not None:
                return existing
        args = _validated(spec, arguments)
        policy = await self._policy(db, user_id)
        if policy.emergency_stopped:
            raise ProposalRefusedError("emergency_stop_active", "Emergency stop is on. Resume automation to continue.")
        if spec.name in policy.disabled_tools:
            raise ProposalRefusedError("tool_disabled", "This tool is switched off in settings.")
        if conversation_id is not None:
            found = await db.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            )
            if found is None:
                raise ProposalRefusedError("conversation_not_found", "Conversation not found.")

        if spec.target is ExecutionTarget.SERVER:
            if spec.approval_required:
                raise ProposalRefusedError("unsupported", "This server tool needs approval, which is not supported yet.")
            return await self._run_server_proposal(
                db,
                user_id=user_id,
                spec=spec,
                args=args,
                source=source,
                conversation_id=conversation_id,
                title=title,
                description=description,
                idempotency_key=idempotency_key,
            )
        return await self._propose_device(
            db,
            user_id=user_id,
            spec=spec,
            args=args,
            source=source,
            conversation_id=conversation_id,
            device_id=device_id,
            title=title,
            description=description,
            idempotency_key=idempotency_key,
            policy=policy,
        )

    async def _by_idempotency(self, db: AsyncSession, user_id: uuid.UUID, key: str) -> Task | None:
        return await db.scalar(select(Task).where(Task.user_id == user_id, Task.idempotency_key == key))

    async def _policy(self, db: AsyncSession, user_id: uuid.UUID) -> AutomationPolicy:
        return AutomationPolicy.from_document(await load_settings_document(db, user_id))

    async def _run_server_proposal(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        spec: ToolSpec,
        args: BaseModel,
        source: str,
        conversation_id: uuid.UUID | None,
        title: str | None,
        description: str,
        idempotency_key: str | None,
    ) -> Task:
        stored = _stored(args)
        now = utc_now()
        status = "completed"
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        try:
            output = await self.run_server_tool(db, user_id=user_id, tool_name=spec.name, arguments=stored)
        except AppError as exc:
            status = "failed"
            error = {"code": exc.code, "message": exc.message, "details": {}}
        else:
            result = {"summary": _result_summary(spec.title, output), "output": output}
        task = Task(
            user_id=user_id,
            conversation_id=conversation_id,
            source=source,
            title=_clip(title or _default_title(spec, stored, {}, None), TITLE_MAX),
            description=_clip(description, DESCRIPTION_MAX),
            tool_name=spec.name,
            arguments=stored,
            risk_level=spec.risk.value,
            status=status,
            result=result,
            error=error,
            attempt=0,
            max_attempts=1,
            started_at=now,
            finished_at=now,
            created_at=now,
            updated_at=now,
            idempotency_key=idempotency_key,
        )
        if not await self._add_task(db, task, user_id, idempotency_key):
            return await self._by_idempotency(db, user_id, idempotency_key or "") or task
        record_event(db, task, "created", "Task created.", {"source": source}, now=now)
        record_event(
            db,
            task,
            status,
            "Completed." if status == "completed" else (error or {}).get("message", "Failed."),
            {"code": error["code"]} if error else None,
            now=now,
        )
        return task

    async def _add_task(self, db: AsyncSession, task: Task, user_id: uuid.UUID, idempotency_key: str | None) -> bool:
        """Insert inside a savepoint so a concurrent duplicate idempotency key cannot break the session."""
        try:
            async with db.begin_nested():
                db.add(task)
                await db.flush()
        except IntegrityError:
            if idempotency_key is None:
                raise
            return False
        return True

    async def _propose_device(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        spec: ToolSpec,
        args: BaseModel,
        source: str,
        conversation_id: uuid.UUID | None,
        device_id: uuid.UUID | None,
        title: str | None,
        description: str,
        idempotency_key: str | None,
        policy: AutomationPolicy,
    ) -> Task:
        device = await resolve_device(
            db,
            user_id=user_id,
            hub=self._devices,
            device_id=device_id,
            device_text=getattr(args, "device", None),
        )
        ensure_capability(device, spec)
        mode = effective_mode(permission_mode(device, spec.required_capability), spec.risk)
        if mode == "deny":
            raise ProposalRefusedError("permission_denied", "This capability is turned off for the device.")
        stored = _stored(args)
        target = resolve_target(device, spec, stored)
        needs_approval = spec.approval_required or mode == "ask"
        now = utc_now()
        task = Task(
            user_id=user_id,
            conversation_id=conversation_id,
            source=source,
            title=_clip(title or _default_title(spec, stored, target.labels, device.name), TITLE_MAX),
            description=_clip(description, DESCRIPTION_MAX),
            tool_name=spec.name,
            arguments=stored,
            risk_level=spec.risk.value,
            status="awaiting_approval" if needs_approval else "queued",
            device_id=device.id,
            required_permissions=[spec.required_capability] if spec.required_capability else [],
            current_step="Waiting for approval" if needs_approval else None,
            attempt=0,
            max_attempts=1,
            approval_expires_at=(
                now + timedelta(seconds=self._settings.task_approval_ttl_seconds) if needs_approval else None
            ),
            created_at=now,
            updated_at=now,
            idempotency_key=idempotency_key,
        )
        if not await self._add_task(db, task, user_id, idempotency_key):
            return await self._by_idempotency(db, user_id, idempotency_key or "") or task
        record_event(db, task, "created", "Task created.", {"source": source, "device_id": str(device.id)}, now=now)

        if needs_approval:
            record_event(db, task, "awaiting_approval", "Waiting for approval.", now=now)
            if policy.notify_approval:
                notify(
                    db,
                    self._events,
                    user_id=user_id,
                    kind="approval_required",
                    severity="warning",
                    title="Approval needed",
                    body=f"Jarvis wants to: {task.title}",
                    related_type="task",
                    related_id=str(task.id),
                )
            await db.commit()
            self._publish_approval(task)
            return task

        record_event(db, task, "queued", "Queued for the device.", now=now)
        await db.commit()
        self._publish_task(task)
        await self._dispatch(db, task, device)
        return task

    # ---- approvals and user actions -----------------------------------------------------

    async def approve(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        expired = False
        async with session_scope(self._session_factory) as db:
            task = await self._owned(db, user_id, task_id, lock=True)
            if task.status != "awaiting_approval":
                raise ConflictError("This task is not waiting for approval.", code="task_state_conflict")
            now = utc_now()
            if task.approval_expires_at is not None and task.approval_expires_at <= now:
                _expire_approval(db, task, now)
                expired = True
            else:
                record_event(db, task, "approved", "Approved by the user.", now=now)
                apply_transition(db, task, "queued", message="Approved. Waiting to dispatch.", now=now)
                record_audit(
                    db,
                    action="tasks.approve",
                    user_id=user_id,
                    target_type="task",
                    target_id=str(task.id),
                    data={"tool": task.tool_name},
                )
                await db.commit()
                await self._dispatch_task(db, task)
        self._publish_task(task)
        if expired:
            # The expiry is committed. Tell the user in the conversation and answer with a conflict.
            await self._post_outcome(user_id, task_id)
            raise ConflictError(
                "The approval window closed before the task was approved.", code="approval_expired"
            )
        return await self._load(user_id, task_id)

    async def deny(self, user_id: uuid.UUID, task_id: uuid.UUID, reason: str | None) -> Task:
        async with session_scope(self._session_factory) as db:
            task = await self._owned(db, user_id, task_id, lock=True)
            if task.status != "awaiting_approval":
                raise ConflictError("This task is not waiting for approval.", code="task_state_conflict")
            task.error = {
                "code": "approval_denied",
                "message": "Denied by the user.",
                "details": {"reason": reason} if reason else {},
            }
            apply_transition(
                db,
                task,
                "cancelled",
                message="Denied by the user.",
                data={"code": "approval_denied"},
            )
            record_audit(
                db, action="tasks.deny", user_id=user_id, target_type="task", target_id=str(task.id), data={"tool": task.tool_name}
            )
        self._publish_task(task)
        await self._post_outcome(user_id, task_id)
        return await self._load(user_id, task_id)

    async def cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        request_device_cancel = False
        async with session_scope(self._session_factory) as db:
            task = await self._owned(db, user_id, task_id, lock=True)
            if task.status in ("queued", "awaiting_approval"):
                _cancel_now(db, task, "Cancelled by the user.")
            elif task.status == "running":
                request_device_cancel = True
                if not task.cancel_requested:
                    task.cancel_requested = True
                    record_event(db, task, "cancel_requested", "Cancel requested.", now=utc_now())
            else:
                raise ConflictError("This task has already finished.", code="task_state_conflict")
            record_audit(
                db, action="tasks.cancel", user_id=user_id, target_type="task", target_id=str(task.id), data={"status": task.status}
            )
        if request_device_cancel:
            await self._request_device_cancel(user_id, task_id)
        self._publish_task(task)
        await self._post_outcome(user_id, task_id)
        return await self._load(user_id, task_id)

    async def retry(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        """Create a new task with the same tool and arguments. Permission and approval apply again."""
        async with session_scope(self._session_factory) as db:
            old = await self._owned(db, user_id, task_id)
            if old.status not in ("failed", "cancelled"):
                raise ConflictError("Only failed or cancelled tasks can be retried.", code="task_state_conflict")
            spec = self._tools.get(old.tool_name)
            if spec is None:
                raise ProposalRefusedError("invalid_arguments", "This tool no longer exists.")
            if spec.target is ExecutionTarget.DEVICE and old.device_id is None:
                raise ProposalRefusedError("device_not_found", "The device for this task no longer exists.")
            new = await self.propose(
                db,
                user_id=user_id,
                tool_name=old.tool_name,
                arguments=dict(old.arguments or {}),
                source=old.source,
                conversation_id=old.conversation_id,
                device_id=old.device_id,
                title=old.title,
                description=f"Retry of task {old.id}.",
            )
            new_id = new.id
        return await self._load(user_id, new_id)

    async def emergency_stop(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Stop automation. Queued and awaiting-approval tasks are cancelled now. A running task is only asked
        to stop: it stays ``running`` until the device confirms or the timeout runs, so it is counted as
        ``cancel_requested`` and not as ``cancelled``."""
        affected: list[uuid.UUID] = []
        running: list[uuid.UUID] = []
        async with session_scope(self._session_factory) as db:
            await write_emergency_flag(db, user_id, True)
            # Row locks: a dispatch that is already sending a command finishes first, and then this stop
            # sees the task as running and asks the device to stop it.
            rows = (
                await db.scalars(
                    select(Task)
                    .where(
                        Task.user_id == user_id,
                        Task.status.in_(("queued", "awaiting_approval", "running")),
                    )
                    .order_by(Task.id)
                    .with_for_update()
                )
            ).all()
            now = utc_now()
            cancelled = 0
            for task in rows:
                affected.append(task.id)
                if task.status == "running":
                    running.append(task.id)
                    if not task.cancel_requested:
                        task.cancel_requested = True
                        record_event(db, task, "cancel_requested", "Emergency stop requested cancellation.", now=now)
                else:
                    cancelled += 1
                    _cancel_now(db, task, "Stopped by emergency stop.", now=now)
            record_audit(
                db,
                action="tasks.emergency_stop",
                user_id=user_id,
                actor_type="user",
                data={"cancelled": cancelled, "cancel_requested": len(running)},
            )
        for task_id in running:
            await self._request_device_cancel(user_id, task_id)
        for task_id in affected:
            self._publish_task(await self._load(user_id, task_id))
            await self._post_outcome(user_id, task_id)
        return {"stopped": True, "cancelled": cancelled, "cancel_requested": len(running)}

    async def emergency_resume(self, user_id: uuid.UUID) -> dict[str, Any]:
        async with session_scope(self._session_factory) as db:
            await write_emergency_flag(db, user_id, False)
            record_audit(db, action="tasks.emergency_resume", user_id=user_id, actor_type="user")
        return {"stopped": False}

    # ---- server tools -------------------------------------------------------------------

    async def run_server_tool(self, db: AsyncSession, *, user_id: uuid.UUID, tool_name: str, arguments: dict) -> dict:
        spec = self._tools.get(tool_name)
        if spec is None or spec.target is not ExecutionTarget.SERVER:
            raise ProposalRefusedError("invalid_arguments", "Not a server tool.")
        args = _validated(spec, arguments)
        handlers: dict[str, Callable[[AsyncSession, uuid.UUID, BaseModel], Any]] = {
            "list_devices": self._tool_list_devices,
            "list_tasks": self._tool_list_tasks,
            "get_task": self._tool_get_task,
            "cancel_task": self._tool_cancel_task,
            "save_memory": self._tool_save_memory,
            "create_reminder": self._tool_create_reminder,
            "send_reminder": self._tool_send_reminder,
        }
        handler = handlers.get(tool_name) or get_server_handler(tool_name)
        if handler is None:
            raise ProposalRefusedError("unsupported", "This server tool is not available.")
        return await handler(db, user_id, args)

    async def _tool_list_devices(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        devices = await paired_devices(db, user_id)
        return {
            "devices": [
                {
                    "device_id": str(device.id),
                    "name": device.name,
                    "device_type": device.device_type,
                    "connection_status": connection_status(device, self._devices),
                    "is_default": device.is_default,
                    "capabilities": filter_capabilities(device.capabilities or []),
                }
                for device in devices
            ]
        }

    async def _tool_list_tasks(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        status = getattr(args, "status", None)
        limit = int(getattr(args, "limit", 10))
        stmt = select(Task).where(Task.user_id == user_id)
        if status:
            stmt = stmt.where(Task.status == status)
        rows = (await db.scalars(stmt.order_by(Task.created_at.desc(), Task.id).limit(limit))).all()
        return {"tasks": [_task_brief(task) for task in rows]}

    async def _tool_get_task(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        task = await db.scalar(select(Task).where(Task.id == args.task_id, Task.user_id == user_id))
        if task is None:
            raise NotFoundError("Task not found.", code="task_not_found")
        return {
            **_task_brief(task),
            "current_step": task.current_step,
            "result": task.result,
            "error": task.error,
            "finished_at": to_iso(task.finished_at),
        }

    async def _tool_cancel_task(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        task_id = args.task_id
        await self.cancel(user_id, task_id)
        status = await db.scalar(select(Task.status).where(Task.id == task_id, Task.user_id == user_id))
        if status is None:
            raise NotFoundError("Task not found.", code="task_not_found")
        if status == "running":
            # The device has to confirm the stop. Until it does, the task is still running.
            summary = "Cancel requested. The device has not confirmed the stop yet."
        else:
            summary = f"The task is now {status}."
        return {"task_id": str(task_id), "status": status, "summary": summary}

    async def _tool_save_memory(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        memory = await save_from_tool(
            db,
            user_id=user_id,
            category=args.category,
            content=args.content,
        )
        saved = _clip(str(args.content), 200)
        return {"memory_id": str(memory.id), "category": memory.category, "summary": f"Saved a memory: {saved}"}

    async def _tool_create_reminder(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        document = await load_settings_document(db, user_id)
        stored_zone = document.get("timezone")
        timezone_name = stored_zone if isinstance(stored_zone, str) and stored_zone else "UTC"
        now = utc_now()
        try:
            run_at = reminder_instant(args.when, timezone_name=timezone_name, now=now)
        except ValueError:
            raise ValidationAppError("The reminder time is not valid.", code="invalid_arguments") from None
        if run_at <= now:
            raise ValidationAppError("The reminder time must be in the future.", code="invalid_arguments")
        create_schedule, schedule_model = _scheduling_api()
        payload = {
            "name": "Reminder",
            "description": "",
            "tool_name": "send_reminder",
            "arguments": {"message": args.message},
            "schedule": {"kind": "once", "run_at": to_iso(run_at), "timezone": timezone_name},
            "retry": {"max_attempts": 1, "backoff_seconds": 60},
            "max_runtime_seconds": 60,
        }
        try:
            data = schedule_model.model_validate(payload)
        except ValidationError:
            raise AppError(
                "The reminder could not be scheduled.", code="unavailable", status_code=503
            ) from None
        job = await _maybe_await(create_schedule(db, user_id=user_id, data=data))
        await db.flush()
        local_run = run_at.astimezone(_zone(timezone_name))
        when_text = local_run.strftime("%Y-%m-%d %H:%M %Z")
        return {
            "schedule_id": str(job.id),
            "run_at": to_iso(run_at),
            "timezone": timezone_name,
            "summary": f"Reminder set for {when_text}.",
        }

    async def _tool_send_reminder(self, db: AsyncSession, user_id: uuid.UUID, args: BaseModel) -> dict:
        row = notify(
            db,
            self._events,
            user_id=user_id,
            kind="reminder",
            severity="info",
            title="Reminder",
            body=str(args.message),
            related_type="reminder",
        )
        await db.flush()
        return {"notification_id": str(row.id)}

    # ---- device events (DeviceEventHandler) ---------------------------------------------

    async def on_agent_result(self, device_id: uuid.UUID, message: AgentResult) -> None:
        try:
            command_id = uuid.UUID(message.command_id)
        except ValueError:
            log.info("Ignoring an agent result with an invalid command id.")
            return
        late_handled = False
        follow_up: Message | None = None
        async with session_scope(self._session_factory) as db:
            # Row lock: a timeout sweep, a cancel or a second result for the same task waits, then sees the final status.
            task = await db.scalar(
                select(Task).where(Task.command_id == command_id, Task.device_id == device_id).with_for_update()
            )
            if task is None:
                log.info("Ignoring an agent result for an unknown command.")
                return
            if task.status != "running":
                late_handled, follow_up = await self._record_late_result(db, task, device_id, message)
                if not late_handled:
                    return
            else:
                await self._record_running_result(db, task, device_id, message)
        self._publish_task(task)
        if late_handled:
            # The late result has been committed. Nothing else changed capacity or outcome, so no drain.
            self._publish_message(task.user_id, follow_up)
            return
        await self._post_outcome(task.user_id, task.id)
        await self._drain(user_id=task.user_id)

    async def _record_running_result(
        self, db: AsyncSession, task: Task, device_id: uuid.UUID, message: AgentResult
    ) -> None:
        """The normal path: a result for a running task completes, fails or cancels it."""
        policy = await self._policy(db, task.user_id)
        spec = self._tools.get(task.tool_name)
        now = utc_now()
        if message.status == "succeeded":
            output = dict(message.output or {})
            summary = _result_summary(spec.title if spec is not None else task.tool_name, output)
            task.result = {"summary": summary, "output": output}
            task.error = None
            apply_transition(db, task, "completed", message=summary, now=now)
            if policy.notify_completed:
                _notify_outcome(db, self._events, task, policy, kind="task_completed")
        elif message.status == "failed":
            code, text = _agent_error(message.error)
            task.error = {"code": code, "message": text, "details": {}}
            apply_transition(db, task, "failed", message=text, data={"code": code}, now=now)
            if policy.notify_failed:
                _notify_outcome(db, self._events, task, policy, kind="task_failed")
        else:
            text = "Cancelled on the device."
            task.error = {"code": "cancelled_by_user", "message": text, "details": {}}
            apply_transition(db, task, "cancelled", message=text, data={"code": "cancelled_by_user"}, now=now)
        record_audit(
            db,
            action="tasks.result",
            user_id=task.user_id,
            actor_type="device",
            actor_id=str(device_id),
            target_type="task",
            target_id=str(task.id),
            data={"tool": task.tool_name, "status": task.status},
        )

    async def _record_late_result(
        self, db: AsyncSession, task: Task, device_id: uuid.UUID, message: AgentResult
    ) -> tuple[bool, Message | None]:
        """A result for a task that already ended. Returns ``(handled, follow_up)``; ``handled`` False means nothing changed.

        Rules (docs/phase2/late-results.md):
        * failed with ``timeout``, then ``succeeded``: completed, with the output stored and a follow-up message.
        * failed with ``timeout``, then ``failed``: stays failed, timeline only.
        * cancelled by the user (a command was sent), then ``succeeded``: stays cancelled, timeline and follow-up.
        * Any other result, or a second result after one was already recorded, is ignored.
        """
        now = utc_now()
        already_recorded = await db.scalar(
            select(TaskEvent.id).where(TaskEvent.task_id == task.id, TaskEvent.type == "late_result").limit(1)
        )
        error_code = _error_code(task)
        if already_recorded is None and task.command_id is not None:
            if task.status == "failed" and error_code == TIMEOUT_ERROR_CODE and message.status == "succeeded":
                output = dict(message.output or {})
                spec = self._tools.get(task.tool_name)
                summary = _result_summary(spec.title if spec is not None else task.tool_name, output)
                apply_late_result(db, task, result={"summary": summary, "output": output}, summary=summary, now=now)
                follow_up = await post_late_result_message(
                    db,
                    task,
                    reported_after=TIMEOUT_ERROR_CODE,
                    summary=_reported_summary(output),
                    unconfirmed=output.get("verified") is False,
                )
                self._late_audit(db, task, device_id, message, "timeout", follow_up)
                return True, follow_up
            if task.status == "failed" and error_code == TIMEOUT_ERROR_CODE and message.status == "failed":
                record_event(
                    db,
                    task,
                    "late_result",
                    "The device reported a failure after the time limit. The task stays failed.",
                    {"status": "failed"},
                    now=now,
                )
                self._late_audit(db, task, device_id, message, "timeout", None)
                return True, None
            if task.status == "cancelled" and error_code == "cancelled_by_user" and message.status == "succeeded":
                output = dict(message.output or {})
                record_event(
                    db,
                    task,
                    "late_result",
                    "The operation completed even though it was cancelled.",
                    {"status": "succeeded", "reported_after": "cancel"},
                    now=now,
                )
                follow_up = await post_late_result_message(
                    db,
                    task,
                    reported_after="cancel",
                    summary=_reported_summary(output),
                    unconfirmed=output.get("verified") is False,
                )
                self._late_audit(db, task, device_id, message, "cancel", follow_up)
                return True, follow_up
        log.info("Ignoring an agent result for a task that already finished.")
        return False, None

    def _late_audit(
        self,
        db: AsyncSession,
        task: Task,
        device_id: uuid.UUID,
        message: AgentResult,
        reported_after: str,
        follow_up: Message | None,
    ) -> None:
        """Audit a late result. ``follow_up`` is the chat message written for it, when there is one."""
        record_audit(
            db,
            action="tasks.late_result",
            user_id=task.user_id,
            actor_type="device",
            actor_id=str(device_id),
            target_type="task",
            target_id=str(task.id),
            data={
                "tool": task.tool_name,
                "status": message.status,
                "reported_after": reported_after,
                "task_status": task.status,
                "follow_up": follow_up is not None,
            },
        )

    async def on_agent_progress(self, device_id: uuid.UUID, message: AgentProgress) -> None:
        try:
            command_id = uuid.UUID(message.command_id)
        except ValueError:
            return
        async with session_scope(self._session_factory) as db:
            task = await db.scalar(select(Task).where(Task.command_id == command_id, Task.device_id == device_id))
            if task is None or task.status != "running":
                return
            step = _clip(message.step, 200)
            changed = task.current_step != step
            task.current_step = step
            if message.progress is not None:
                task.progress = {"value": float(message.progress), "step": step}
            if changed or message.progress is not None:
                record_event(
                    db,
                    task,
                    "progress",
                    step,
                    {"value": message.progress} if message.progress is not None else {},
                    now=utc_now(),
                )
        self._publish_task(task)

    async def on_device_connected(self, device_id: uuid.UUID) -> None:
        await self._drain(device_id=device_id)

    async def on_device_disconnected(self, device_id: uuid.UUID) -> None:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.scalars(select(Task).where(Task.device_id == device_id, Task.status == "running"))
            ).all()
            now = utc_now()
            for task in rows:
                record_event(
                    db,
                    task,
                    "device_disconnected",
                    "The device disconnected. The task stays running until it reports a result or times out.",
                    now=now,
                )

    # ---- sweeps (timeouts, expiry, queue draining) ---------------------------------------

    async def sweep_once(self) -> None:
        """One pass of the timeout loop: expire approvals, expire queued tasks, time out running tasks,
        then dispatch queued work for online devices."""
        now = utc_now()
        changed: list[tuple[uuid.UUID, uuid.UUID]] = []
        freed_users: set[uuid.UUID] = set()
        policies: dict[uuid.UUID, AutomationPolicy] = {}

        async with session_scope(self._session_factory) as db:
            expired_approvals = (
                await db.scalars(
                    select(Task)
                    .where(
                        Task.status == "awaiting_approval",
                        Task.approval_expires_at.is_not(None),
                        Task.approval_expires_at <= now,
                    )
                    .with_for_update()
                )
            ).all()
            for task in expired_approvals:
                _expire_approval(db, task, now)
                changed.append((task.user_id, task.id))

            queue_cutoff = now - timedelta(seconds=self._settings.task_queue_ttl_seconds)
            stale_queued = (
                await db.scalars(
                    select(Task).where(Task.status == "queued", Task.created_at <= queue_cutoff).with_for_update()
                )
            ).all()
            for task in stale_queued:
                online = task.device_id is not None and self._devices.is_online(task.device_id)
                if online:
                    code, text = "timeout", "The task did not start before its queue time limit."
                else:
                    code, text = "device_offline", "The device stayed offline past the queue time limit."
                policy = await self._cached_policy(db, task.user_id, policies)
                task.error = {"code": code, "message": text, "details": {}}
                apply_transition(db, task, "failed", message=text, data={"code": code}, now=now)
                record_event(db, task, "expired", text, now=now)
                _notify_failure(db, self._events, task, policy)
                changed.append((task.user_id, task.id))

            # Rows dispatched before run_deadline_at existed have no stored deadline: they keep the configured device timeout.
            command_cutoff = now - timedelta(seconds=self._settings.device_command_timeout_seconds)
            timed_out = (
                await db.scalars(
                    select(Task)
                    .where(
                        Task.status == "running",
                        or_(
                            Task.run_deadline_at <= now,
                            and_(
                                Task.run_deadline_at.is_(None),
                                Task.started_at.is_not(None),
                                Task.started_at <= command_cutoff,
                            ),
                        ),
                    )
                    .with_for_update()
                )
            ).all()
            for task in timed_out:
                if task.cancel_requested:
                    # The user asked to stop and the device never confirmed it. The task stays cancelled
                    # (docs/phase2/late-results.md). A later success is reported as an action that finished after a cancel.
                    text = "Cancel requested. The device did not confirm the stop in time, so the action may still finish."
                    task.error = {"code": "cancelled_by_user", "message": text, "details": {}}
                    apply_transition(db, task, "cancelled", message=text, data={"code": "cancelled_by_user"}, now=now)
                else:
                    text = "The device did not report a result in time."
                    task.error = {"code": "timeout", "message": text, "details": {}}
                    apply_transition(db, task, "failed", message=text, data={"code": "timeout"}, now=now)
                    policy = await self._cached_policy(db, task.user_id, policies)
                    _notify_failure(db, self._events, task, policy)
                changed.append((task.user_id, task.id))
                freed_users.add(task.user_id)

        for user_id, task_id in changed:
            self._publish_task(await self._load(user_id, task_id))
            await self._post_outcome(user_id, task_id)

        for user_id in freed_users:
            await self._drain(user_id=user_id)
        for user_id in await self._users_with_online_queue():
            await self._drain(user_id=user_id)

    async def _cached_policy(
        self, db: AsyncSession, user_id: uuid.UUID, cache: dict[uuid.UUID, AutomationPolicy]
    ) -> AutomationPolicy:
        if user_id not in cache:
            cache[user_id] = await self._policy(db, user_id)
        return cache[user_id]

    async def _users_with_online_queue(self) -> list[uuid.UUID]:
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    select(Task.user_id, Task.device_id).where(Task.status == "queued", Task.device_id.is_not(None))
                )
            ).all()
        return sorted({user for user, device in rows if device is not None and self._devices.is_online(device)}, key=str)

    # ---- dispatch -----------------------------------------------------------------------

    async def _drain(self, *, user_id: uuid.UUID | None = None, device_id: uuid.UUID | None = None) -> None:
        """Dispatch queued tasks, oldest first, for online devices, until the concurrency limit is reached."""
        async with self._session_factory() as session:
            stmt = select(Task.id, Task.device_id).where(Task.status == "queued", Task.device_id.is_not(None))
            if user_id is not None:
                stmt = stmt.where(Task.user_id == user_id)
            if device_id is not None:
                stmt = stmt.where(Task.device_id == device_id)
            rows = (await session.execute(stmt.order_by(Task.created_at, Task.id))).all()
        for task_id, dev_id in rows:
            if dev_id is None or not self._devices.is_online(dev_id):
                continue
            async with session_scope(self._session_factory) as db:
                task = await db.get(Task, task_id)
                if task is None or task.status != "queued":
                    continue
                device = await db.get(Device, dev_id)
                if device is None:
                    await self._fail_queued(db, task, "device_not_found", "The device no longer exists.")
                    continue
                outcome = await self._dispatch(db, task, device)
            if outcome is _Dispatch.WAITING_CAPACITY:
                break

    async def _owned(self, db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, *, lock: bool = False) -> Task:
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        if lock:
            # Row lock: a concurrent approve, deny, cancel, dispatch or result waits for this transaction.
            stmt = stmt.with_for_update()
        task = await db.scalar(stmt)
        if task is None:
            raise NotFoundError("Task not found.", code="task_not_found")
        return task

    async def _load(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        async with self._session_factory() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
        if task is None:
            raise NotFoundError("Task not found.", code="task_not_found")
        return task

    async def _dispatch_task(self, db: AsyncSession, task: Task) -> _Dispatch:
        if task.device_id is None:
            await self._fail_queued(db, task, "device_not_found", "The device no longer exists.")
            return _Dispatch.FINISHED
        device = await db.scalar(select(Device).where(Device.id == task.device_id, Device.user_id == task.user_id))
        if device is None:
            await self._fail_queued(db, task, "device_not_found", "The device no longer exists.")
            return _Dispatch.FINISHED
        return await self._dispatch(db, task, device)

    async def _dispatch(self, db: AsyncSession, task: Task, device: Device) -> _Dispatch:
        """Send one queued task to its device, or leave it queued with the reason it is waiting.

        The caller's copy of the task may be stale, so every decision that moves the task (status,
        emergency stop, policy, capacity) is made again under ``_dispatch_lock`` on a row locked with
        ``FOR UPDATE``. The command is sent while the lock is held, so a cancel for the same task waits
        until the command has left the process.
        """
        if task.status != "queued":
            return _Dispatch.FINISHED
        spec = self._tools.get(task.tool_name)
        if spec is None or spec.target is not ExecutionTarget.DEVICE or spec.device_action is None:
            await self._fail_queued(db, task, "unsupported_platform", "This tool cannot run on a device.")
            return _Dispatch.FINISHED
        refusal = dispatch_refusal(device, spec)
        if refusal is not None:
            await self._fail_queued(db, task, refusal[0], refusal[1])
            return _Dispatch.FINISHED
        try:
            target = resolve_target(device, spec, task.arguments or {})
        except ProposalRefusedError as exc:
            await self._fail_queued(db, task, exc.code, exc.message)
            return _Dispatch.FINISHED
        if not self._devices.is_online(device.id):
            await self._wait(db, task, "waiting_for_device")
            return _Dispatch.WAITING_DEVICE
        secret = _device_secret(self._secret_box, device)
        if secret is None:
            await self._fail_queued(
                db, task, "execution_failed", "The device credentials could not be read. Pair the device again."
            )
            return _Dispatch.FINISHED

        async with self._dispatch_lock:
            await db.refresh(task, with_for_update=True)
            if task.status != "queued":
                # Approved, cancelled, stopped or dispatched by another caller while this one was waiting.
                await db.commit()
                return _Dispatch.FINISHED
            policy = await self._policy(db, task.user_id)
            if policy.emergency_stopped:
                _cancel_now(db, task, "Stopped by emergency stop.")
                await db.commit()
                self._publish_task(task)
                return _Dispatch.FINISHED
            now = utc_now()
            queue_cutoff = now - timedelta(seconds=self._settings.task_queue_ttl_seconds)
            if task.created_at <= queue_cutoff:
                await db.commit()
                await self._fail_queued(db, task, "timeout", "The task did not start before its queue time limit.")
                return _Dispatch.FINISHED
            if spec.name in policy.disabled_tools:
                await self._fail_queued(db, task, "tool_disabled", "This tool is switched off in settings.")
                return _Dispatch.FINISHED
            if await self._needs_fresh_approval(db, task, spec, device):
                # Permission became "ask" after the task was queued. The user must approve it again.
                await self._return_to_approval(db, task, now)
                return _Dispatch.FINISHED
            running = await self._count_running(db, task.user_id)
            if running >= policy.max_concurrent_tasks:
                await db.commit()
                await self._wait(db, task, "waiting_for_capacity")
                return _Dispatch.WAITING_CAPACITY
            command = build_command(
                secret,
                action=spec.device_action,
                args=target.wire_args,
                ttl_seconds=self._settings.device_command_ttl_seconds,
            )
            task.command_id = uuid.UUID(command.id)
            task.attempt += 1
            task.started_at = now
            # The timeout is read here, once per dispatch. Without a valid stored setting the configured device timeout applies.
            timeout_seconds = policy.default_task_timeout_seconds or self._settings.device_command_timeout_seconds
            task.run_deadline_at = now + timedelta(seconds=timeout_seconds)
            task.current_step = "Sent to the device"
            record_event(
                db,
                task,
                "dispatched",
                f"Sent to {device.name}.",
                {"device_id": str(device.id), "attempt": task.attempt},
                now=now,
            )
            apply_transition(db, task, "running", message="Running on the device.", now=now)
            record_audit(
                db,
                action="tasks.dispatch",
                user_id=task.user_id,
                actor_type="system",
                target_type="task",
                target_id=str(task.id),
                data={"tool": task.tool_name, "device_id": str(device.id)},
            )
            await db.commit()
            self._publish_task(task)

            if await self._devices.send_command(device.id, command):
                return _Dispatch.DISPATCHED
            revert_dispatch(db, task, message="The device did not accept the command. Waiting for the device.")
            await db.commit()
        self._publish_task(task)
        return _Dispatch.WAITING_DEVICE

    async def _needs_fresh_approval(self, db: AsyncSession, task: Task, spec: ToolSpec, device: Device) -> bool:
        """True when a queued task now needs approval it does not have (permission set to ask after it was queued)."""
        mode = effective_mode(permission_mode(device, spec.required_capability), spec.risk)
        if not (spec.approval_required or mode == "ask"):
            return False
        approved = await db.scalar(
            select(TaskEvent.id).where(TaskEvent.task_id == task.id, TaskEvent.type == "approved").limit(1)
        )
        return approved is None

    async def _return_to_approval(self, db: AsyncSession, task: Task, now: datetime) -> None:
        task.approval_expires_at = now + timedelta(seconds=self._settings.task_approval_ttl_seconds)
        task.current_step = "Waiting for approval"
        apply_transition(db, task, "awaiting_approval", message="Waiting for approval again.", now=now)
        await db.commit()
        self._publish_approval(task)

    async def _wait(self, db: AsyncSession, task: Task, step: str) -> None:
        if task.current_step == step:
            return
        task.current_step = step
        task.updated_at = utc_now()
        await db.commit()
        self._publish_task(task)

    async def _count_running(self, db: AsyncSession, user_id: uuid.UUID) -> int:
        value = await db.scalar(
            select(func.count(Task.id)).where(
                Task.user_id == user_id,
                Task.status == "running",
                Task.device_id.is_not(None),
            )
        )
        return int(value or 0)

    async def _fail_queued(self, db: AsyncSession, task: Task, code: str, message: str) -> None:
        policy = await self._policy(db, task.user_id)
        task.error = {"code": code, "message": message, "details": {}}
        apply_transition(db, task, "failed", message=message, data={"code": code})
        _notify_failure(db, self._events, task, policy)
        await db.commit()
        self._publish_task(task)
        await self._post_outcome(task.user_id, task.id)

    async def _request_device_cancel(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Ask the agent to stop a running command. Without a confirmed send, cancel now and say so."""
        target: tuple[uuid.UUID, str, str] | None = None
        async with session_scope(self._session_factory) as db:
            task = await db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
            if task is None or task.status != "running":
                return
            device = await db.get(Device, task.device_id) if task.device_id else None
            if device is not None and device.user_id == user_id and task.command_id is not None:
                secret = _device_secret(self._secret_box, device)
                if secret is not None:
                    target = (device.id, str(task.command_id), secret)
        sent = False
        if target is not None and self._devices.is_online(target[0]):
            message = build_cancel(target[2], command_id=target[1])
            # Under the dispatch lock, so a cancel never overtakes the command it refers to.
            async with self._dispatch_lock:
                sent = await self._devices.send_cancel(target[0], message)
        if sent:
            return
        async with session_scope(self._session_factory) as db:
            task = await db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
            if task is None or task.status != "running":
                return
            _cancel_now(
                db,
                task,
                "Cancelled by the user.",
                note="The device did not confirm the cancel. The operation may still have completed.",
            )
        self._publish_task(task)
        await self._post_outcome(user_id, task_id)
        await self._drain(user_id=user_id)

    # ---- publishing ---------------------------------------------------------------------

    def _publish_task(self, task: Task) -> None:
        self._events.publish(task.user_id, "task.updated", {"task_id": str(task.id), "status": task.status})

    async def _post_outcome(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Writes the real outcome of a finished task into its conversation, then announces the new message.

        Safe to call for any task: nothing is written unless the task is terminal, and a second call
        writes nothing new. Called after each terminal transition has been committed.
        """
        async with session_scope(self._session_factory) as db:
            task = await db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
            message = await post_task_outcome_message(db, task) if task is not None else None
        self._publish_message(user_id, message)

    def _publish_message(self, user_id: uuid.UUID, message: Message | None) -> None:
        """Announces a committed chat message to the user's open clients."""
        if message is not None and message.conversation_id is not None:
            self._events.publish(
                user_id,
                "chat.message",
                {"conversation_id": str(message.conversation_id), "message_id": str(message.id)},
            )

    def _publish_approval(self, task: Task) -> None:
        self._events.publish(task.user_id, "approval.requested", {"task_id": str(task.id), "title": task.title})
        self._publish_task(task)


def _cancel_now(db: AsyncSession, task: Task, message: str, *, note: str | None = None, now: datetime | None = None) -> None:
    details: dict[str, Any] = {"note": note} if note else {}
    task.error = {"code": "cancelled_by_user", "message": message, "details": details}
    apply_transition(db, task, "cancelled", message=message, data={"code": "cancelled_by_user"}, now=now)


def _expire_approval(db: AsyncSession, task: Task, now: datetime) -> None:
    text = "The approval window closed before the task was approved."
    task.error = {"code": "approval_expired", "message": text, "details": {}}
    apply_transition(db, task, "cancelled", message="Approval expired.", data={"code": "approval_expired"}, now=now)
    record_event(db, task, "expired", text, {"code": "approval_expired"}, now=now)


def _notify_failure(db: AsyncSession, events: EventBus, task: Task, policy: AutomationPolicy) -> None:
    if policy.notify_failed:
        _notify_outcome(db, events, task, policy, kind="task_failed")


def _notify_outcome(db: AsyncSession, events: EventBus, task: Task, policy: AutomationPolicy, *, kind: str) -> None:
    """Notification text never includes task arguments unless the user asked for details."""
    completed = kind == "task_completed"
    if policy.include_task_details:
        body = task.title
    else:
        body = "Jarvis finished a task." if completed else "A task could not be completed."
    notify(
        db,
        events,
        user_id=task.user_id,
        kind=kind,
        severity="success" if completed else "error",
        title="Task completed" if completed else "Task failed",
        body=body,
        related_type="task",
        related_id=str(task.id),
    )


def _scheduling_api() -> tuple[Callable[..., Any], type[BaseModel]]:
    """Import the scheduling service lazily. Missing service means an honest 503, never a fake reminder."""
    unavailable = AppError("Reminders need the scheduling service, which is not available.", code="unavailable", status_code=503)
    try:
        service = importlib.import_module("app.services.schedule_service")
        schemas = importlib.import_module("app.schemas.schedules")
    except ImportError as exc:
        raise unavailable from exc
    create_schedule = getattr(service, "create_schedule", None)
    schedule_model = getattr(schemas, "ScheduleCreate", None)
    if create_schedule is None or schedule_model is None:
        raise unavailable
    return create_schedule, schedule_model


__all__ = [
    "AutomationPolicy",
    "SOURCES",
    "TaskEngine",
    "reminder_instant",
    "write_emergency_flag",
]
