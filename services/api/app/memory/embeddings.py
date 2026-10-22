"""pgvector support for memory embeddings: the column type, vector literals and cosine distance.

The column is ``vector(768)`` (see the ``add memory embeddings`` migration). Vectors reach PostgreSQL
only as bound parameters, rendered as ``[v1,v2,...]`` text and cast with ``CAST(... AS vector(768))``.
The pgvector Python package is not used, and no SQL is ever built from user text.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Float, bindparam, cast
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import UserDefinedType

# Fixed by the database column. Other sizes are refused, never truncated or padded.
VECTOR_DIMENSIONS = 768


def check_embedding_dimensions(dimensions: int) -> int:
    """Returns ``dimensions`` when it matches the column, otherwise raises ``ValueError``."""
    if isinstance(dimensions, bool) or dimensions != VECTOR_DIMENSIONS:
        raise ValueError(f"Embedding dimensions must be {VECTOR_DIMENSIONS} to match the memory column.")
    return dimensions


def vector_literal(values: Sequence[float]) -> str:
    """Formats a vector as the pgvector text literal ``[v1,v2,...]``.

    Raises ``ValueError`` for the wrong length, for non-numeric values and for NaN or infinity.
    """
    if len(values) != VECTOR_DIMENSIONS:
        raise ValueError(f"A memory embedding must have {VECTOR_DIMENSIONS} values, got {len(values)}.")
    parts: list[str] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Embedding values must be numbers.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Embedding values must be finite numbers.")
        parts.append(repr(number))
    return "[" + ",".join(parts) + "]"


def parse_vector_literal(text: str) -> list[float]:
    """Reads a pgvector text literal back into floats. Raises ``ValueError`` when it is malformed."""
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        raise ValueError("Malformed vector literal.")
    body = stripped[1:-1]
    values = [float(part) for part in body.split(",")] if body else []
    if len(values) != VECTOR_DIMENSIONS:
        raise ValueError("Vector literal has the wrong length.")
    return values


class Vector(UserDefinedType[list[float]]):
    """SQLAlchemy type for a pgvector ``vector(768)`` column. Python side: a list of floats."""

    cache_ok = True

    def get_col_spec(self, **kwargs: Any) -> str:
        return f"vector({VECTOR_DIMENSIONS})"

    def bind_processor(self, dialect: Dialect) -> Any:
        def process(value: Any) -> str | None:
            if value is None:
                return None
            return vector_literal(value)

        return process

    def result_processor(self, dialect: Dialect, coltype: object) -> Any:
        def process(value: Any) -> list[float] | None:
            if value is None:
                return None
            if isinstance(value, str):
                return parse_vector_literal(value)
            return [float(item) for item in value]

        return process


def cosine_distance(column: Any, query: Sequence[float]) -> ColumnElement[float]:
    """Cosine distance (pgvector ``<=>``) between a stored vector column and a query vector.

    The query vector is a bound parameter. Smaller is closer; NULL when the stored vector is NULL.
    """
    bound = cast(bindparam("query_vector", list(query), type_=Vector(), unique=True), Vector())
    return column.op("<=>", return_type=Float)(bound)
