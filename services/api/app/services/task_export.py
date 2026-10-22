"""Task list export as CSV.

The columns are chosen to be safe to share: ids, times, titles, tool names, statuses and error codes. The
export never includes task arguments, results, device output or the event timeline, because those can hold
file paths, URLs and command text. Every query is scoped to the owner.

Spreadsheet programs read a cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return as a
formula. Such a cell gets a leading apostrophe, so the file cannot run anything when it is opened.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.tasks import Task
from app.services.csv_export import csv_cell, to_csv

__all__ = ["COLUMNS", "FILENAME", "csv_cell", "tasks_to_csv"]

COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "finished_at",
    "source",
    "title",
    "tool_name",
    "status",
    "risk_level",
    "error_code",
)
FILENAME = "jarvis-tasks.csv"
def _timestamp(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""  # type: ignore[union-attr]


def tasks_to_csv(tasks: Sequence[Task]) -> str:
    """The CSV text for ``tasks``, with a header row and CRLF line endings (RFC 4180)."""
    rows = []
    for task in tasks:
        error = task.error if isinstance(task.error, dict) else {}
        rows.append(
            (
                task.id,
                _timestamp(task.created_at),
                _timestamp(task.finished_at),
                task.source,
                task.title,
                task.tool_name,
                task.status,
                task.risk_level,
                error.get("code"),
            )
        )
    return to_csv(COLUMNS, rows)
