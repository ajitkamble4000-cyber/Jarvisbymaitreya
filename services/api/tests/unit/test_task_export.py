"""CSV task export: spreadsheet formula neutralising, the column set, and line endings."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from app.models.tasks import Task
from app.services.task_export import COLUMNS, csv_cell, tasks_to_csv


def _task(**overrides: object) -> Task:
    values: dict[str, object] = {
        "id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "created_at": datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        "finished_at": None,
        "source": "chat",
        "title": "Open Chrome",
        "tool_name": "launch_app",
        "status": "completed",
        "risk_level": "medium",
        "error": None,
        "arguments": {"secret_path": "C:\\Users\\sam\\key.txt"},
        "result": {"output": "C:\\Users\\sam\\key.txt"},
    }
    values.update(overrides)
    return Task(**values)


def test_formula_like_cells_get_a_leading_apostrophe() -> None:
    for value in ("=HYPERLINK(\"http://x\")", "+1+1", "-2", "@SUM(A1)", "\tcmd", "\rcmd"):
        assert csv_cell(value) == "'" + value


def test_ordinary_and_empty_cells_are_unchanged() -> None:
    assert csv_cell("Open Chrome") == "Open Chrome"
    assert csv_cell("2026-10-21") == "2026-10-21"
    assert csv_cell(None) == ""


def test_csv_has_the_fixed_columns_and_neutralises_a_hostile_title() -> None:
    text = tasks_to_csv([_task(title="=cmd|' /C calc'!A0")])
    rows = list(csv.reader(io.StringIO(text)))
    assert tuple(rows[0]) == COLUMNS
    row = dict(zip(COLUMNS, rows[1], strict=True))
    assert row["title"] == "'=cmd|' /C calc'!A0"
    assert row["created_at"] == "2026-10-21T09:00:00+00:00"
    assert row["finished_at"] == ""
    assert row["error_code"] == ""


def test_arguments_and_results_are_never_exported() -> None:
    text = tasks_to_csv([_task()])
    assert "key.txt" not in text
    assert "secret_path" not in text
    assert "output" not in text


def test_error_code_is_taken_from_the_error_envelope() -> None:
    row = list(csv.reader(io.StringIO(tasks_to_csv([_task(error={"code": "timeout", "message": "late", "details": {}})]))))[1]
    assert row[COLUMNS.index("error_code")] == "timeout"
    odd = list(csv.reader(io.StringIO(tasks_to_csv([_task(error="not a dict")]))))[1]
    assert odd[COLUMNS.index("error_code")] == ""


def test_lines_end_with_crlf_and_commas_inside_titles_are_quoted() -> None:
    text = tasks_to_csv([_task(title="Plan, then build")])
    assert text.endswith("\r\n")
    assert '"Plan, then build"' in text
