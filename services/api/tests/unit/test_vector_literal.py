"""Vector literal formatting, parsing and dimension checks for memory embeddings. No database."""

from __future__ import annotations

import math

import pytest
from sqlalchemy.dialects import postgresql

from app.memory.embeddings import (
    VECTOR_DIMENSIONS,
    Vector,
    check_embedding_dimensions,
    cosine_distance,
    parse_vector_literal,
    vector_literal,
)
from app.models.memory import Memory


def _vector(value: float = 0.5) -> list[float]:
    return [value] * VECTOR_DIMENSIONS


def test_column_is_768_dimensions_and_fixed() -> None:
    assert VECTOR_DIMENSIONS == 768
    assert Vector().get_col_spec() == "vector(768)"


def test_literal_has_brackets_commas_and_no_spaces() -> None:
    literal = vector_literal([1.0, -0.25] + [0.0] * (VECTOR_DIMENSIONS - 2))
    assert literal.startswith("[1.0,-0.25,0.0,")
    assert literal.endswith(",0.0]")
    assert " " not in literal


def test_literal_round_trips_through_parse() -> None:
    values = [((index % 7) - 3) / 7.0 for index in range(VECTOR_DIMENSIONS)]
    assert parse_vector_literal(vector_literal(values)) == values


def test_integers_are_accepted_and_stored_as_floats() -> None:
    literal = vector_literal([1] * VECTOR_DIMENSIONS)
    assert parse_vector_literal(literal) == [1.0] * VECTOR_DIMENSIONS


@pytest.mark.parametrize("length", [0, 1, VECTOR_DIMENSIONS - 1, VECTOR_DIMENSIONS + 1, 1536])
def test_wrong_length_is_rejected(length: int) -> None:
    with pytest.raises(ValueError, match="768"):
        vector_literal([0.1] * length)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(bad: float) -> None:
    values = _vector()
    values[10] = bad
    with pytest.raises(ValueError, match="finite"):
        vector_literal(values)


@pytest.mark.parametrize("bad", ["0.5", None, True])
def test_non_numeric_values_are_rejected(bad: object) -> None:
    values: list[object] = list(_vector())
    values[3] = bad
    with pytest.raises(ValueError):
        vector_literal(values)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "0.1,0.2", "(0.1,0.2)", "[0.1,0.2]"])
def test_malformed_or_short_literals_are_rejected_on_parse(text: str) -> None:
    with pytest.raises(ValueError):
        parse_vector_literal(text)


def test_dimension_check_accepts_only_the_column_size() -> None:
    assert check_embedding_dimensions(768) == 768
    for bad in (0, 64, 767, 769, 1536, 3072):
        with pytest.raises(ValueError, match="768"):
            check_embedding_dimensions(bad)
    with pytest.raises(ValueError):
        check_embedding_dimensions(True)  # type: ignore[arg-type]


def test_bind_and_result_processors_handle_null() -> None:
    dialect = postgresql.dialect()
    column_type = Vector()
    bind = column_type.bind_processor(dialect)
    result = column_type.result_processor(dialect, None)
    assert bind is not None and result is not None
    assert bind(None) is None
    assert result(None) is None
    assert bind(_vector())  # a literal string is produced
    assert result(vector_literal(_vector())) == _vector()


def test_cosine_distance_binds_the_query_vector() -> None:
    expression = cosine_distance(Memory.embedding, _vector())
    sql = str(expression.compile(dialect=postgresql.dialect()))
    assert "<=>" in sql
    assert "CAST(" in sql and "vector(768)" in sql
    # The vector itself is a bound parameter, never inlined into the SQL text.
    assert "0.5" not in sql
