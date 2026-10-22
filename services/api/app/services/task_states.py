"""Task lifecycle: the allowed status transitions, the refusal error for policy decisions, and the
helpers that apply a transition and write its ``TaskEvent``.

Terminal states (completed, failed, cancelled) have no outgoing transitions, with one named exception:
``apply_late_result`` moves a task that timed out to completed when the device reports success afterwards.
Tasks are created directly in their starting state (queued, awaiting_approval, or a final state for server
tools); creation is not a transition.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.time import utc_now
from app.models.tasks import TERMINAL_TASK_STATUSES, Task, TaskEvent

ALLOWED_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "queued": frozenset({"running", "awaiting_approval", "cancelled", "failed"}),
        "awaiting_approval": frozenset({"queued", "cancelled", "failed"}),
        "running": frozenset({"completed", "failed", "cancelled"}),
        "completed": frozenset(),
        "failed": frozenset(),
        "cancelled": frozenset(),
    }
)

# The one edge that leaves ``running`` without an agent result: the backend could not hand the
# command to the device, so the task goes back to waiting. It is not part of ALLOWED_TRANSITIONS
# because a command was never delivered, so no execution can have happened.
DISPATCH_REVERT: Final[tuple[str, str]] = ("running", "queued")


class InvalidTransitionError(ValueError):
    """A status change that the lifecycle does not allow."""


def is_terminal(status: str) -> bool:
    return status in TERMINAL_TASK_STATUSES


def allowed_next(status: str) -> frozenset[str]:
    if status not in ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(f"Unknown task status: {status}")
    return ALLOWED_TRANSITIONS[status]


def can_transition(source: str, target: str) -> bool:
    return target in allowed_next(source)


def assert_transition(source: str, target: str) -> None:
    if not can_transition(source, target):
        raise InvalidTransitionError(f"A task cannot move from {source} to {target}.")


def can_revert_dispatch(source: str, target: str) -> bool:
    return (source, target) == DISPATCH_REVERT


_REFUSAL_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "invalid_arguments": 422,
        "sensitive_content": 422,
        "resource_not_approved": 422,
        "conversation_not_found": 404,
        "device_not_found": 404,
        "permission_denied": 403,
        "emergency_stop_active": 409,
        "tool_disabled": 409,
        "device_revoked": 409,
        "capability_missing": 409,
        "needs_device_selection": 409,
        "unsupported": 409,
        "settings_unavailable": 503,
        "unavailable": 503,
    }
)


class ProposalRefusedError(AppError):
    """Policy refused a proposal. No task is created. ``code`` is stable; ``details`` never carries secrets.

    ``needs_device_selection`` carries ``details.candidates``: a list of ``{id, name, connection_status}``.
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=code,
            status_code=_REFUSAL_STATUS.get(code, 409),
            details=details or {},
        )


def record_event(
    db: AsyncSession,
    task: Task,
    event_type: str,
    message: str = "",
    data: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Append a timeline row. ``data`` must be JSON-safe and must never hold task arguments or secrets."""
    db.add(
        TaskEvent(
            task_id=task.id,
            user_id=task.user_id,
            type=event_type,
            message=message[:500],
            data=dict(data or {}),
            created_at=now or utc_now(),
        )
    )


def apply_transition(
    db: AsyncSession,
    task: Task,
    target: str,
    *,
    message: str = "",
    data: Mapping[str, Any] | None = None,
    event_type: str | None = None,
    now: datetime | None = None,
) -> None:
    """Move ``task`` to ``target`` and write the status event (type defaults to the new status)."""
    assert_transition(task.status, target)
    moment = now or utc_now()
    task.status = target
    task.updated_at = moment
    if is_terminal(target):
        task.finished_at = moment
    record_event(db, task, event_type or target, message or f"Status is now {target}.", data, now=moment)


TIMEOUT_ERROR_CODE: Final[str] = "timeout"


def apply_late_result(
    db: AsyncSession,
    task: Task,
    *,
    result: Mapping[str, Any],
    summary: str,
    now: datetime | None = None,
) -> None:
    """The one edge out of ``failed``: a success reported after the device time limit completes the task.

    Only a task that failed with error code ``timeout`` can take it. ``result`` is stored, ``error`` is cleared,
    and the timeline gets a ``late_result`` event followed by the ``completed`` status event. Normal transitions
    never reach this function, so terminal states stay terminal on every other path.
    """
    error = task.error if isinstance(task.error, Mapping) else {}
    if task.status != "failed" or error.get("code") != TIMEOUT_ERROR_CODE:
        raise InvalidTransitionError("Only a task that timed out can be completed by a late result.")
    moment = now or utc_now()
    task.result = dict(result)
    task.error = None
    task.status = "completed"
    task.updated_at = moment
    task.finished_at = moment
    record_event(
        db,
        task,
        "late_result",
        "The device reported success after the time limit. The task is now completed.",
        {"status": "succeeded", "reported_after": TIMEOUT_ERROR_CODE},
        now=moment,
    )
    record_event(db, task, "completed", summary, now=moment)


def revert_dispatch(db: AsyncSession, task: Task, *, message: str, now: datetime | None = None) -> None:
    """Return a task whose command could not be delivered to ``queued``. Restores the attempt counter."""
    source, target = DISPATCH_REVERT
    if task.status != source:
        raise InvalidTransitionError(f"Only a running task can be reverted, not {task.status}.")
    moment = now or utc_now()
    task.status = target
    task.command_id = None
    task.started_at = None
    task.run_deadline_at = None
    task.attempt = max(0, task.attempt - 1)
    task.current_step = "waiting_for_device"
    task.updated_at = moment
    record_event(db, task, "queued", message, {"reason": "dispatch_not_accepted"}, now=moment)
