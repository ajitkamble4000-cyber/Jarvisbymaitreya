"""CSV writing shared by the exports: fixed columns, RFC 4180 line endings, and spreadsheet formula neutralising.

Spreadsheet programs read a cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return as a formula.
Such a cell gets a leading apostrophe, so an opened file cannot run anything.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    """Text for one cell. ``None`` is empty. A value that a spreadsheet would read as a formula is neutralised."""
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def to_csv(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Header plus one line per row, with CRLF endings. Every cell passes through :func:`csv_cell`."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([csv_cell(value) for value in row])
    return buffer.getvalue()
