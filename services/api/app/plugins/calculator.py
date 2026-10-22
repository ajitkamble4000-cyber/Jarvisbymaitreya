"""Plugin ``calculator``: one read-only server tool, ``calculate``.

The expression is parsed with ``ast``. A whitelist walker accepts only numeric literals, the operators + - * / // % **,
unary + and -, and parentheses. Anything else (names, calls, attributes, subscripts, strings, lambdas, comparisons,
booleans) is refused. The values are then computed by a second walker over the same tree. ``eval``, ``exec`` and
``compile`` are never used.

Limits, chosen so that evaluation stays cheap for any input of at most 200 characters:
* Every intermediate and final value must be finite and at most 1e18 in absolute value.
* An exponent may be at most 1000 in absolute value.
* Division, floor division and modulo by zero are errors. So is a power that would give a complex number.

Expression errors at proposal time (syntax, forbidden nodes, length) refuse the call and create no task.
Evaluation errors (division by zero, limits) produce a failed task with a stable error code.

This is trusted code that runs in-process. See docs/plugins.md.
"""

from __future__ import annotations

import ast
import math
import re
from typing import Final
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.tools.args import ToolArgs
from app.tools.base import ExecutionTarget, RiskLevel, ToolSpec
from app.tools.server_handlers import ServerHandler

PLUGIN_NAME: Final = "calculator"
PLUGIN_VERSION: Final = "1.0.0"
TOOL_NAME: Final = "calculate"

MAX_EXPRESSION_LENGTH: Final = 200
MAX_EXPONENT: Final = 1000
MAX_MAGNITUDE: Final = 1e18
# log2 of the largest power the walker may compute before the magnitude check. Any power above this bound is
# refused before Python builds the integer, so no input can make evaluation allocate large numbers.
_LOG2_POWER_BOUND: Final = 61.0

INVALID_EXPRESSION: Final = "invalid_expression"
DIVISION_BY_ZERO: Final = "division_by_zero"
EXPONENT_TOO_LARGE: Final = "exponent_too_large"
RESULT_TOO_LARGE: Final = "result_too_large"

_INVALID_MESSAGE: Final = "The expression is not plain arithmetic. Use numbers, + - * / // % **, and parentheses."
_RESULT_MESSAGE: Final = "The result must stay within 1e18 in absolute value and be finite."
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_BINARY: Final[tuple[type[ast.operator], ...]] = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_UNARY: Final[tuple[type[ast.unaryop], ...]] = (ast.UAdd, ast.USub)

Number = int | float


class CalculationError(Exception):
    """An expression cannot be evaluated. ``code`` is one of the stable codes above and ``message`` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid() -> CalculationError:
    return CalculationError(INVALID_EXPRESSION, _INVALID_MESSAGE)


def _parse(expression: str) -> ast.expr:
    """Parse and check the shape of ``expression``. Nothing is evaluated here."""
    text = expression.strip()
    if not text or len(text) > MAX_EXPRESSION_LENGTH or _CONTROL.search(text):
        raise _invalid()
    try:
        tree = ast.parse(text, mode="eval")
        _check_shape(tree.body)
    except (SyntaxError, ValueError, RecursionError):
        raise _invalid() from None
    return tree.body


def _check_shape(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _invalid()
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY):
        _check_shape(node.operand)
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
        _check_shape(node.left)
        _check_shape(node.right)
        return
    raise _invalid()


def _bounded(value: Number) -> Number:
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculationError(RESULT_TOO_LARGE, _RESULT_MESSAGE)
    if abs(value) > MAX_MAGNITUDE:
        raise CalculationError(RESULT_TOO_LARGE, _RESULT_MESSAGE)
    return value


def _power(base: Number, exponent: Number) -> Number:
    if abs(exponent) > MAX_EXPONENT:
        raise CalculationError(EXPONENT_TOO_LARGE, f"Exponents may be at most {MAX_EXPONENT} in absolute value.")
    if base == 0:
        if exponent < 0:
            raise CalculationError(DIVISION_BY_ZERO, "Division by zero is not defined.")
        return base**exponent
    # |base| is not zero here, so the logarithm is defined.
    if exponent * math.log2(abs(base)) > _LOG2_POWER_BOUND:
        raise CalculationError(RESULT_TOO_LARGE, _RESULT_MESSAGE)
    try:
        result = base**exponent
    except OverflowError:
        raise CalculationError(RESULT_TOO_LARGE, _RESULT_MESSAGE) from None
    if isinstance(result, complex):
        raise CalculationError(INVALID_EXPRESSION, "The result is not a real number.")
    return result


def _apply(op: ast.operator, left: Number, right: Number) -> Number:
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Pow):
        return _power(left, right)
    if right == 0:
        raise CalculationError(DIVISION_BY_ZERO, "Division by zero is not defined.")
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    return left % right


def _value(node: ast.expr) -> Number:
    if isinstance(node, ast.Constant):
        literal = node.value
        if isinstance(literal, bool) or not isinstance(literal, (int, float)):
            raise _invalid()
        return _bounded(literal)
    if isinstance(node, ast.UnaryOp):
        operand = _value(node.operand)
        return _bounded(+operand if isinstance(node.op, ast.UAdd) else -operand)
    if isinstance(node, ast.BinOp):
        left = _value(node.left)
        right = _value(node.right)
        return _bounded(_apply(node.op, left, right))
    raise _invalid()


def evaluate(expression: str) -> Number:
    """Return the value of ``expression``. Raises ``CalculationError`` with a stable code when it cannot."""
    body = _parse(expression)
    try:
        return _value(body)
    except RecursionError:
        raise _invalid() from None


class CalculateArgs(ToolArgs):
    expression: str = Field(min_length=1, max_length=MAX_EXPRESSION_LENGTH)

    @field_validator("expression")
    @classmethod
    def _plain_arithmetic(cls, value: str) -> str:
        try:
            _parse(value)
        except CalculationError:
            raise ValueError("must be a plain arithmetic expression") from None
        return value


def _format(value: Number) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


async def _calculate(_db: AsyncSession, _user_id: UUID, args: BaseModel) -> dict:
    if not isinstance(args, CalculateArgs):
        raise ValidationAppError("The arguments are not valid.", code="invalid_arguments")
    expression = args.expression.strip()
    try:
        result = evaluate(expression)
    except CalculationError as exc:
        raise ValidationAppError(exc.message, code=exc.code) from None
    return {
        "summary": f"{expression} = {_format(result)}",
        "expression": expression,
        "result": result,
    }


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=TOOL_NAME,
            title="Calculate",
            description=(
                "Evaluate an arithmetic expression of at most 200 characters. Allowed: numbers, + - * / // % **, "
                "unary + and -, and parentheses. Names, function calls and other code are refused. Results and "
                "intermediate values must stay within 1e18, and exponents within 1000. Read-only."
            ),
            args_model=CalculateArgs,
            risk=RiskLevel.LOW,
            target=ExecutionTarget.SERVER,
            category="productivity",
            approval_required=False,
            example_commands=("What is 17 times 23?", "Calculate (2 + 3) ** 2 / 5."),
            notes="Provided by the calculator plugin. Expressions are parsed, never executed as code.",
        )
    ]


def server_handlers() -> dict[str, ServerHandler]:
    return {TOOL_NAME: _calculate}
