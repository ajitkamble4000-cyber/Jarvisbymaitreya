"""The chat's outcome sentence must not call an unverified or only-requested action done."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.chat import outcome_texts


def _task(tool_name: str, output: dict[str, Any], summary: str) -> SimpleNamespace:
    return SimpleNamespace(
        title="Open Google Chrome on Laptop",
        status="completed",
        tool_name=tool_name,
        result={"summary": summary, "output": output},
        error=None,
    )


def test_a_verified_completion_still_says_done() -> None:
    _, reply = outcome_texts(_task("launch_app", {"verified": True}, "Started Google Chrome (pid 4242)."))
    assert reply.startswith("Done: Open Google Chrome on Laptop.")


def test_an_unverified_launch_is_not_reported_as_done() -> None:
    _, reply = outcome_texts(_task("launch_app", {"verified": False}, "Started Google Chrome (pid 4242)."))
    assert reply.startswith("Not confirmed yet: ")
    assert "Done:" not in reply


def test_a_cancel_that_the_device_has_not_confirmed_is_not_reported_as_done() -> None:
    tool, reply = outcome_texts(
        _task("cancel_task", {"status": "running"}, "Cancel requested. The device has not confirmed the stop yet.")
    )
    assert tool.startswith("Cancel requested.")
    assert reply.startswith("Not confirmed yet: ")
