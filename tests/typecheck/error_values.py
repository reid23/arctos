"""Static API contracts checked by ``just typecheck``.

``assert_type`` verifies inferred types. Expected errors have precise ignores;
mypy's unused-ignore check fails if those invalid calls become accepted.
"""

from typing import assert_type

from app.error_values import Err, Null, Ok, Option, Result, Some, allow_Q, option
from app.utils.name_validation import match_name_char_error, team_pseudonym_char_error


def render(value: int) -> str:
    return str(value)


def parse(value: str) -> Result[int, str]:
    try:
        return Ok(int(value))
    except ValueError:
        return Err("Expected an integer")


def positive(value: int) -> Option[int]:
    if value > 0:
        return Some(value)
    return Null()


def option_contract(value: Option[int], raw: int | None) -> None:
    assert_type(option(raw), Option[int])
    assert_type(value.map(render), Option[str])
    assert_type(value.and_then(positive), Option[int])
    assert_type(value.unwrap_or("missing"), int | str)
    assert_type(value.ok_or("missing"), Result[int, str])
    assert_type(value.ok_or_else(lambda: "missing"), Result[int, str])
    assert_type(match_name_char_error("Final"), Option[str])
    assert_type(team_pseudonym_char_error("Team"), Option[str])

    value.map(str.upper)  # type: ignore[arg-type]
    value.and_then(render)  # type: ignore[arg-type]


def result_contract(value: Result[int, str], text: Result[str, str]) -> None:
    assert_type(value.map(render), Result[str, str])
    assert_type(value.map_err(len), Result[int, int])
    assert_type(text.and_then(parse), Result[int, str])
    assert_type(value.unwrap_or("missing"), int | str)
    assert_type(value.unwrap_err_or(0), str | int)

    value.map(str.upper)  # type: ignore[arg-type]
    value.map_err(render)  # type: ignore[arg-type]
    value.and_then(render)  # type: ignore[arg-type]
    value.and_then(positive)  # type: ignore[arg-type]


def different_error(value: str) -> Result[int, ValueError]:
    return Err(ValueError(value))


def error_contract(value: Result[str, str]) -> None:
    value.and_then(different_error)  # type: ignore[arg-type]


@allow_Q
def decorated_option(raw: int | None, *, minimum: int = 0) -> Option[str]:
    value = option(raw).Q()
    if value < minimum:
        return Null()
    return Some(str(value))


@allow_Q
def decorated_result(raw: str, *, increment: int = 0) -> Result[int, str]:
    value = parse(raw).Q()
    return Ok(value + increment)


def decorator_contract() -> None:
    assert_type(decorated_option(0, minimum=0), Option[str])
    assert_type(decorated_result("0", increment=1), Result[int, str])
    decorated_option("0")  # type: ignore[arg-type]
    decorated_result("0", increment="1")  # type: ignore[arg-type]
    decorated_result("0", 1)  # type: ignore[misc]


@allow_Q  # type: ignore[arg-type]
def invalid_return() -> int:
    return 1
