"""Transition table, terminal states, the dispatch-revert edge, the late-result edge and refusal status mapping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Task, TaskEvent
from app.services.task_states import (
    ALLOWED_TRANSITIONS,
    DISPATCH_REVERT,
    InvalidTransitionError,
    ProposalRefusedError,
    allowed_next,
    apply_late_result,
    assert_transition,
    can_revert_dispatch,
    can_transition,
    is_terminal,
)

STATUSES = ("queued", "running", "awaiting_approval", "completed", "failed", "cancelled")
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class _RecordingSession:
    """Collects the rows that record_event adds. The late-result function never flushes, so nothing else is needed."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _task(status: str, error: dict[str, Any] | None) -> Task:
    return Task(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source="chat",
        title="Open YouTube",
        tool_name="open_url",
        arguments={},
        risk_level="low",
        status=status,
        error=error,
        result=None,
        attempt=1,
        max_attempts=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_transition_table_matches_the_brief_exactly() -> None:
    assert ALLOWED_TRANSITIONS["queued"] == {"running", "awaiting_approval", "cancelled", "failed"}
    assert ALLOWED_TRANSITIONS["awaiting_approval"] == {"queued", "cancelled", "failed"}
    assert ALLOWED_TRANSITIONS["running"] == {"completed", "failed", "cancelled"}
    assert set(ALLOWED_TRANSITIONS) == set(STATUSES)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for status in ("completed", "failed", "cancelled"):
        assert is_terminal(status)
        assert allowed_next(status) == frozenset()
        for target in STATUSES:
            assert not can_transition(status, target)
    for status in ("queued", "running", "awaiting_approval"):
        assert not is_terminal(status)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("completed", "running"),
        ("running", "queued"),
        ("queued", "completed"),
        ("awaiting_approval", "running"),
        ("awaiting_approval", "completed"),
        ("failed", "queued"),
        ("cancelled", "running"),
        ("running", "awaiting_approval"),
    ],
)
def test_forbidden_transitions_raise(source: str, target: str) -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition(source, target)


def test_allowed_transitions_do_not_raise() -> None:
    assert_transition("queued", "running")
    assert_transition("awaiting_approval", "queued")
    assert_transition("running", "completed")


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        allowed_next("paused")


def test_dispatch_revert_is_the_only_extra_edge() -> None:
    assert DISPATCH_REVERT == ("running", "queued")
    assert can_revert_dispatch("running", "queued")
    assert not can_revert_dispatch("queued", "running")
    assert not can_revert_dispatch("running", "completed")
    assert not can_transition("running", "queued")


def test_refusal_codes_map_to_stable_http_statuses() -> None:
    assert ProposalRefusedError("device_not_found", "x").status_code == 404
    assert ProposalRefusedError("needs_device_selection", "x").status_code == 409
    assert ProposalRefusedError("emergency_stop_active", "x").status_code == 409
    assert ProposalRefusedError("permission_denied", "x").status_code == 403
    assert ProposalRefusedError("invalid_arguments", "x").status_code == 422
    assert ProposalRefusedError("something_new", "x").status_code == 409


def test_refusal_carries_code_and_details() -> None:
    refusal = ProposalRefusedError("needs_device_selection", "Choose.", details={"candidates": [{"id": "1"}]})
    assert refusal.code == "needs_device_selection"
    assert refusal.details == {"candidates": [{"id": "1"}]}


# ---- late results: the one named edge out of failed ---------------------------------------


def test_failed_still_has_no_entry_in_the_transition_table() -> None:
    # The late-result edge is reached only through apply_late_result, never through the table.
    assert ALLOWED_TRANSITIONS["failed"] == frozenset()
    assert not can_transition("failed", "completed")
    with pytest.raises(InvalidTransitionError):
        assert_transition("failed", "completed")


def test_apply_late_result_completes_a_task_that_timed_out() -> None:
    task = _task("failed", {"code": "timeout", "message": "The device did not report a result in time.", "details": {}})
    session = _RecordingSession()
    result = {"summary": "Opened YouTube.", "output": {"summary": "Opened YouTube."}}

    apply_late_result(cast(AsyncSession, session), task, result=result, summary="Opened YouTube.", now=NOW)

    assert task.status == "completed"
    assert task.result == result
    assert task.error is None
    assert task.finished_at == NOW and task.updated_at == NOW
    events = [row for row in session.added if isinstance(row, TaskEvent)]
    assert [event.type for event in events] == ["late_result", "completed"]
    assert events[0].data == {"status": "succeeded", "reported_after": "timeout"}
    assert events[1].message == "Opened YouTube."


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("failed", {"code": "execution_failed", "message": "The device refused.", "details": {}}),
        ("failed", None),
        ("cancelled", {"code": "cancelled_by_user", "message": "Cancelled by the user.", "details": {}}),
        ("completed", None),
        ("running", None),
        ("queued", None),
    ],
)
def test_apply_late_result_refuses_every_other_source(status: str, error: dict[str, Any] | None) -> None:
    task = _task(status, error)
    session = _RecordingSession()

    with pytest.raises(InvalidTransitionError):
        apply_late_result(cast(AsyncSession, session), task, result={"summary": "x"}, summary="x", now=NOW)

    # A refused call changes nothing: no status, error, result or timeline row.
    assert task.status == status
    assert task.error == error
    assert task.result is None
    assert session.added == []
