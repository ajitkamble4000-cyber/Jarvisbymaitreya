"""The calculator plugin: arithmetic results, every refused construct, and the numeric limits. No database is needed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.plugins.calculator import (
    DIVISION_BY_ZERO,
    EXPONENT_TOO_LARGE,
    INVALID_EXPRESSION,
    RESULT_TOO_LARGE,
    CalculateArgs,
    CalculationError,
    evaluate,
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("2 ** 3 ** 2", 512),  # ** is right-associative
        ("-2 ** 2", -4),  # unary minus binds looser than **
        ("(-2) ** 2", 4),
        ("7 // 2", 3),
        ("-7 // 2", -4),  # floor division rounds toward negative infinity
        ("7 % 3", 1),
        ("-7 % 3", 2),
        ("10 / 4", 2.5),
        ("+5", 5),
        ("--3", 3),
        ("((1))", 1),
        ("1_000 + 1", 1001),
        ("2.5 * 4", 10.0),
        ("  1 + 1  ", 2),
        ("2 ** 0.5", 2**0.5),
        ("0 ** 0", 1),
        ("2 ** -1", 0.5),
        ("1 ** 1000", 1),
        ("10 ** 18", 10**18),  # exactly at the limit is allowed
        ("1000000000 * 1000000000", 10**18),
    ],
)
def test_arithmetic_results(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "__import__('os').system('id')",
        "().__class__",
        "''.join(['a'])",
        "open('x')",
        "print(1)",
        "x",
        "abs(-1)",
        "[1, 2]",
        "{1: 2}",
        "(1, 2)",
        "a[0]",
        "'abc'",
        "b'abc'",
        "lambda: 1",
        "1 if True else 2",
        "1 < 2",
        "1 == 1",
        "not 1",
        "1 and 2",
        "True",
        "False",
        "None",
        "1j",
        "~1",
        "1 & 1",
        "1 | 1",
        "1 << 2",
        "1 >> 1",
        "1 @ 1",
        "f'{1}'",
        "[x for x in range(3)]",
        "1; import os",
        "1\n+1",
        "1\t+ 1",
        "1 +",
        "",
        "   ",
        "\x00",
    ],
)
def test_forbidden_constructs_are_refused(expression: str) -> None:
    with pytest.raises(CalculationError) as raised:
        evaluate(expression)
    assert raised.value.code == INVALID_EXPRESSION


def test_arguments_refuse_code_at_proposal_time() -> None:
    with pytest.raises(ValidationError):
        CalculateArgs(expression="__import__('os')")
    with pytest.raises(ValidationError):
        CalculateArgs(expression="1 +")


def test_arguments_limit_length_to_200_characters() -> None:
    assert CalculateArgs(expression="1" + "+1" * 99).expression
    with pytest.raises(ValidationError):
        CalculateArgs(expression="1" + "+1" * 100)  # 201 characters
    with pytest.raises(ValidationError):
        CalculateArgs(expression="")


def test_arguments_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        CalculateArgs.model_validate({"expression": "1 + 1", "eval": True})


def test_division_by_zero_is_an_error_code() -> None:
    for expression in ("1 / 0", "1 // 0", "1 % 0", "1 / (2 - 2)", "1.5 / 0.0", "0 ** -1"):
        with pytest.raises(CalculationError) as raised:
            evaluate(expression)
        assert raised.value.code == DIVISION_BY_ZERO, expression


def test_exponent_above_1000_is_refused() -> None:
    for expression in ("2 ** 1001", "2 ** -1001", "1.5 ** 1001", "9 ** 9 ** 9"):
        with pytest.raises(CalculationError) as raised:
            evaluate(expression)
        assert raised.value.code == EXPONENT_TOO_LARGE, expression


def test_exponent_at_the_cap_still_reaches_the_magnitude_check() -> None:
    # 1 ** 1000 is within the cap. 2 ** 1000 is not, and must be refused without building the integer.
    assert evaluate("1 ** 1000") == 1
    with pytest.raises(CalculationError) as raised:
        evaluate("2 ** 1000")
    assert raised.value.code == RESULT_TOO_LARGE


@pytest.mark.parametrize(
    "expression",
    [
        "2 ** 60",
        "10 ** 19",
        "1000000000 * 1000000001",
        "999999999999999999999",  # a literal above the cap
        "1e400",  # a literal that overflows to infinity
        "1e300 * 1e300",
        "1e18 * 10",
    ],
)
def test_results_above_the_cap_are_refused(expression: str) -> None:
    with pytest.raises(CalculationError) as raised:
        evaluate(expression)
    assert raised.value.code == RESULT_TOO_LARGE


def test_tiny_exponents_are_allowed() -> None:
    # 0.5 ** 1000 is about 1e-301, well inside the exponent cap, so 2 ** that is about 1.0.
    assert evaluate("2 ** 0.5 ** 1000") == pytest.approx(1.0)


def test_intermediate_values_above_the_cap_are_refused() -> None:
    with pytest.raises(CalculationError) as raised:
        evaluate("(10 ** 18 * 10) / 10")
    assert raised.value.code == RESULT_TOO_LARGE


def test_a_negative_base_with_a_fractional_exponent_is_not_a_real_number() -> None:
    with pytest.raises(CalculationError) as raised:
        evaluate("(-8) ** 0.5")
    assert raised.value.code == INVALID_EXPRESSION


def test_deep_nesting_is_refused_not_crashed() -> None:
    with pytest.raises(CalculationError) as raised:
        evaluate("(" * 250 + "1" + ")" * 250)
    assert raised.value.code == INVALID_EXPRESSION
    with pytest.raises(CalculationError) as raised:
        evaluate("-" * 199 + "x")
    assert raised.value.code == INVALID_EXPRESSION
