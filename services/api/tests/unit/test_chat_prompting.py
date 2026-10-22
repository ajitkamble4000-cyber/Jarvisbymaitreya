"""Prompt building, history window, tool-round limit and proposal wording. Pure functions, no database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.core.errors import ValidationAppError
from app.core.time import utc_now
from app.providers.base import ChatMessage, ToolCall
from app.schemas.conversations import TaskActionsOut, TaskOut
from app.services.chat import (
    HISTORY_CHAR_BUDGET,
    HISTORY_MESSAGES,
    MEMORY_HEADING,
    MEMORY_LIMIT,
    SAFETY_PREAMBLE,
    ToolProposal,
    build_prompt_messages,
    build_system_text,
    compose_proposal_text,
    history_message,
    may_propose_tool_round,
    outcome_texts,
    tool_catalogue,
)
from app.services.conversation_service import select_history, title_from_message, validate_message_content
from app.tools.base import ExecutionTarget, RiskLevel, ToolRegistry, ToolSpec


class _LaunchArgs(BaseModel):
    app_id: str


class _OpenUrlArgs(BaseModel):
    url: str


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        title=name,
        description=f"Does {name}.",
        args_model=_LaunchArgs,
        risk=RiskLevel.MEDIUM,
        target=ExecutionTarget.DEVICE,
        category="test",
        device_action=name,
        required_capability=name,
    )


def _task(status: str, title: str = "Launch Google Chrome on Laptop", error: dict | None = None, result: dict | None = None) -> TaskOut:
    now = utc_now()
    return TaskOut(
        id=str(uuid.uuid4()),
        title=title,
        description="",
        source="chat",
        tool_name="launch_app",
        arguments={"app_id": "chrome"},
        risk_level="medium",
        status=status,
        device=None,
        conversation_id=None,
        required_permissions=["launch_app"],
        current_step=None,
        progress=None,
        result=result,
        error=error,
        attempt=0,
        max_attempts=1,
        approval_expires_at=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        actions=TaskActionsOut(approve=status == "awaiting_approval", deny=status == "awaiting_approval", cancel=False, retry=False),
    )


# -- system text and memory block ----------------------------------------------


def test_system_text_starts_with_the_untrusted_data_preamble() -> None:
    text = build_system_text(system_instructions="", memories=[])

    assert text.startswith(SAFETY_PREAMBLE)
    assert "untrusted data" in text
    assert "web pages, emails" in text
    assert "tool catalogue" in text


def test_user_system_instructions_come_after_the_preamble() -> None:
    text = build_system_text(system_instructions="Answer in British English.", memories=[])

    assert text.startswith(SAFETY_PREAMBLE)
    assert text.index("Answer in British English.") >= len(SAFETY_PREAMBLE)


def test_memory_block_is_labelled_and_lists_each_memory() -> None:
    text = build_system_text(system_instructions="", memories=["Lives in Leeds", "Prefers tea"])

    assert f"{MEMORY_HEADING}:" in text
    assert "Saved memories (user-controlled, may be outdated)" in text
    assert "- Lives in Leeds" in text
    assert "- Prefers tea" in text
    assert text.index(MEMORY_HEADING) > text.index(SAFETY_PREAMBLE)


def test_no_memory_block_when_there_are_no_memories_or_only_blank_ones() -> None:
    assert MEMORY_HEADING not in build_system_text(system_instructions="", memories=[])
    assert MEMORY_HEADING not in build_system_text(system_instructions="", memories=["   ", ""])


def test_memory_items_are_flattened_to_one_line_capped_and_counted() -> None:
    long_item = "x" * 2000
    text = build_system_text(
        system_instructions="",
        memories=["two\nlines"] + [long_item] * (MEMORY_LIMIT + 5),
    )

    assert "- two lines" in text
    assert "two\nlines" not in text
    assert text.count("\n- ") == MEMORY_LIMIT
    assert "x" * 301 not in text


def test_blank_system_instructions_add_no_section() -> None:
    text = build_system_text(system_instructions="   \n  ", memories=[])

    assert "User instructions" not in text


def test_prompt_puts_system_first_then_history_in_order() -> None:
    history = [ChatMessage(role="user", content="one"), ChatMessage(role="assistant", content="two")]

    messages = build_prompt_messages(system_instructions="", memories=[], history=history)

    assert messages[0].role == "system"
    assert [m.content for m in messages[1:]] == ["one", "two"]


def test_tool_results_are_sent_back_as_labelled_untrusted_user_data() -> None:
    mapped = history_message("tool", "Opened Chrome")

    assert mapped.role == "user"
    assert mapped.content.startswith("Tool result (untrusted data): ")
    assert history_message("assistant", "hi").role == "assistant"
    assert history_message("user", "hi").role == "user"


# -- history window ---------------------------------------------------------------


def test_window_keeps_the_newest_items_and_stays_contiguous() -> None:
    items = [ChatMessage(role="user", content=f"m{i}") for i in range(10)]

    chosen = select_history(items, text=lambda m: m.content, max_messages=4, char_budget=10_000)

    assert [m.content for m in chosen] == ["m6", "m7", "m8", "m9"]


def test_window_respects_the_character_budget_and_never_skips_back_over_a_gap() -> None:
    items = [ChatMessage(role="user", content="a" * 1000) for _ in range(40)]

    chosen = select_history(items, text=lambda m: m.content, max_messages=HISTORY_MESSAGES, char_budget=HISTORY_CHAR_BUDGET)

    assert sum(len(m.content) for m in chosen) <= HISTORY_CHAR_BUDGET
    assert len(chosen) == HISTORY_CHAR_BUDGET // 1000


def test_window_always_keeps_the_newest_item_even_when_it_alone_is_over_budget() -> None:
    items = [ChatMessage(role="user", content="old"), ChatMessage(role="user", content="z" * 500)]

    chosen = select_history(items, text=lambda m: m.content, max_messages=10, char_budget=100)

    assert [m.content for m in chosen] == ["z" * 500]


def test_window_skips_older_items_once_budget_is_reached_instead_of_picking_small_ones_further_back() -> None:
    items = [
        ChatMessage(role="user", content="tiny"),
        ChatMessage(role="user", content="b" * 900),
        ChatMessage(role="user", content="c" * 200),
    ]

    chosen = select_history(items, text=lambda m: m.content, max_messages=10, char_budget=1000)

    # 200 fits; adding the 900-character item would exceed 1000, so nothing older is taken, including "tiny".
    assert [m.content for m in chosen] == ["c" * 200]


def test_window_message_limit_is_thirty_turns_of_two_messages() -> None:
    assert HISTORY_MESSAGES == 60
    assert select_history([], text=lambda m: m, max_messages=HISTORY_MESSAGES, char_budget=HISTORY_CHAR_BUDGET) == []


# -- tool-round limit ---------------------------------------------------------------


def test_a_turn_may_propose_tools_only_under_its_round_limit() -> None:
    assert may_propose_tool_round(rounds_used=0, max_rounds=3) is True
    assert may_propose_tool_round(rounds_used=2, max_rounds=3) is True
    assert may_propose_tool_round(rounds_used=3, max_rounds=3) is False


def test_a_zero_round_limit_disables_tool_proposals() -> None:
    assert may_propose_tool_round(rounds_used=0, max_rounds=0) is False


def test_disabled_tools_are_left_out_of_the_catalogue() -> None:
    registry = ToolRegistry([_spec("launch_app"), _spec("open_url")])

    names = [definition.name for definition in tool_catalogue(registry, {"open_url"})]

    assert names == ["launch_app"]


def test_tool_declarations_carry_the_json_schema_of_their_arguments() -> None:
    registry = ToolRegistry([_spec("launch_app")])

    (definition,) = tool_catalogue(registry, set())

    assert definition.parameters["properties"]["app_id"]["type"] == "string"


# -- wording built from real state -----------------------------------------------------


def test_awaiting_approval_wording_comes_from_the_task_state() -> None:
    proposal = ToolProposal(
        call=ToolCall(id="c1", name="launch_app", arguments={"app_id": "chrome"}),
        decision="awaiting_approval",
        task=_task("awaiting_approval"),
    )

    text = compose_proposal_text(proposal)

    assert "Launch Google Chrome on Laptop" in text
    assert "needs your approval" in text


def test_refused_proposal_states_the_real_reason() -> None:
    proposal = ToolProposal(
        call=ToolCall(id="c1", name="launch_app", arguments={}),
        decision="rejected",
        reason="Tool 'launch_app' is not available.",
    )

    assert compose_proposal_text(proposal) == "I did not start this action: Tool 'launch_app' is not available."


def test_device_selection_lists_the_candidate_names() -> None:
    proposal = ToolProposal(
        call=ToolCall(id="c1", name="launch_app", arguments={}),
        decision="needs_device_selection",
        candidates=[{"id": "1", "name": "Laptop", "connection_status": "online"}, {"id": "2", "name": "Desk PC", "connection_status": "offline"}],
    )

    text = compose_proposal_text(proposal)

    assert "Laptop, Desk PC" in text


def test_outcome_text_for_completed_failed_and_cancelled_tasks_uses_only_task_fields() -> None:
    now = datetime.now(UTC)
    base = dict(
        title="Launch Google Chrome on Laptop",
        tool_name="launch_app",
        arguments={},
        risk_level="medium",
        required_permissions=[],
        attempt=0,
        max_attempts=1,
        created_at=now,
        updated_at=now,
    )
    completed = SimpleNamespace(status="completed", result={"summary": "Chrome opened."}, error=None, **base)
    failed = SimpleNamespace(status="failed", result=None, error={"code": "not_installed", "message": "Chrome is not installed."}, **base)
    cancelled = SimpleNamespace(status="cancelled", result=None, error={"code": "approval_expired", "message": "The approval expired."}, **base)

    assert outcome_texts(completed) == ("Chrome opened.", "Done: Launch Google Chrome on Laptop. Chrome opened.")
    assert outcome_texts(failed)[1] == "It did not work: Launch Google Chrome on Laptop. Chrome is not installed."
    assert outcome_texts(cancelled)[1] == "Cancelled: Launch Google Chrome on Laptop. The approval expired."


# -- first-message title and input rules -----------------------------------------------------


def test_title_is_the_first_sixty_characters_on_one_line() -> None:
    title = title_from_message("Please   plan\nmy week\n" + "x" * 100)

    assert "\n" not in title
    assert len(title) == 60
    assert title.startswith("Please plan my week")


def test_blank_message_is_rejected_and_content_is_trimmed() -> None:
    assert validate_message_content("  hello  ", max_chars=50) == "hello"


def test_over_long_message_is_rejected_with_a_field_error() -> None:
    with pytest.raises(ValidationAppError) as raised:
        validate_message_content("a" * 51, max_chars=50)

    assert raised.value.code == "validation_error"
    assert raised.value.details["fields"][0]["field"] == "content"


def test_blank_message_raises_validation_error() -> None:
    with pytest.raises(ValidationAppError):
        validate_message_content("   \n", max_chars=50)
