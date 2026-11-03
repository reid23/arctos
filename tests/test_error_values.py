"""Tests for the error-as-values module (Option / Result / allow_Q)."""

from typing import Callable, NoReturn

import pytest

from app.error_values import Err, Null, Ok, Option, PropagationError, Result, Some, allow_Q, option

pytestmark = pytest.mark.unit


def unexpected_callback(_: object) -> NoReturn:
    raise AssertionError("The callback must not run for this variant")


def test_option_ok_or_some() -> None:
    """Some.ok_or returns Ok wrapping the contained value."""
    res = Some(123).ok_or("nope")
    match res:
        case Ok(v):
            assert v == 123
        case Err(_):
            raise AssertionError("Expected Ok")


def test_option_ok_or_null() -> None:
    """Null.ok_or returns Err wrapping the provided error value."""
    empty: Option[int] = Null()
    res = empty.ok_or("nope")
    match res:
        case Err(e):
            assert e == "nope"
        case Ok(_):
            raise AssertionError("Expected Err")


def test_option_ok_or_else_is_lazy() -> None:
    """Some.ok_or_else never calls the factory; Null.ok_or_else does call it."""
    calls: list[str] = []

    def make_error() -> str:
        calls.append("error created")
        return "err"

    res = Some("x").ok_or_else(make_error)
    assert calls == []
    assert res.unwrap() == "x"

    empty: Option[str] = Null()
    res2 = empty.ok_or_else(make_error)
    assert calls == ["error created"]
    assert res2.unwrap_err() == "err"


def test_option_helper() -> None:
    """option() converts None to Null and non-None values to Some."""
    assert option(None).is_null()
    assert option(5).unwrap() == 5


@pytest.mark.parametrize("value", [0, False, "", [], {}])
def test_option_preserves_falsy_values(value: object) -> None:
    result = option(value)
    assert isinstance(result, Some)
    assert result.val is value
    assert result.unwrap_or("fallback") is value
    assert result.ok_or("missing").unwrap() is value


@pytest.mark.parametrize("value", [0, False, "", [], {}, None])
def test_result_and_some_maps_preserve_falsy_payloads(value: object) -> None:
    success: Result[object, str] = Ok(value)
    present: Option[object] = Some(value)
    calls: list[object] = []

    def transform(item: object) -> tuple[object]:
        calls.append(item)
        return (item,)

    mapped_success = success.map(transform)
    mapped_present = present.map(transform)
    assert isinstance(mapped_success, Ok)
    assert isinstance(mapped_present, Some)
    assert mapped_success.val[0] is value
    assert mapped_present.val[0] is value
    assert calls == [value, value]
    assert success.unwrap_or("fallback") is value


def test_option_map_can_contain_none() -> None:
    """Only option() treats None as absence; map preserves its callback output."""
    mapped = Some(1).map(lambda _: None)
    assert isinstance(mapped, Some)
    assert mapped.val is None


def test_null_combinators_skip_callbacks() -> None:
    empty: Option[int] = Null()
    mapped: Option[str] = empty.map(unexpected_callback)
    chained: Option[str] = empty.and_then(unexpected_callback)
    assert isinstance(mapped, Null)
    assert isinstance(chained, Null)


def test_error_combinators_skip_success_callbacks() -> None:
    error = ValueError("invalid count")
    failure: Result[int, ValueError] = Err(error)
    mapped: Result[str, ValueError] = failure.map(unexpected_callback)
    chained: Result[str, ValueError] = failure.and_then(unexpected_callback)
    assert isinstance(mapped, Err)
    assert isinstance(chained, Err)
    assert mapped.val is error
    assert chained.val is error


def test_map_err_skips_success_and_transforms_falsy_error() -> None:
    payload: list[int] = []
    success: Result[list[int], str] = Ok(payload)
    mapped_success: Result[list[int], int] = success.map_err(unexpected_callback)
    assert isinstance(mapped_success, Ok)
    assert mapped_success.val is payload

    calls: list[str] = []

    def count_characters(message: str) -> int:
        calls.append(message)
        return len(message)

    failure: Result[str, str] = Err("")
    mapped_error = failure.map_err(count_characters)
    assert isinstance(mapped_error, Err)
    assert mapped_error.val == 0
    assert calls == [""]


@pytest.mark.parametrize("next_option", [Some(""), Null()])
def test_option_and_then_returns_callback_option(next_option: Option[str]) -> None:
    calls: list[int] = []

    def next_step(value: int) -> Option[str]:
        calls.append(value)
        return next_option

    assert Some(0).and_then(next_step) is next_option
    assert calls == [0]


@pytest.mark.parametrize("next_result", [Ok(""), Err("invalid")])
def test_result_and_then_returns_callback_result(next_result: Result[str, str]) -> None:
    calls: list[int] = []

    def next_step(value: int) -> Result[str, str]:
        calls.append(value)
        return next_result

    success: Result[int, str] = Ok(0)
    assert success.and_then(next_step) is next_result
    assert calls == [0]


@pytest.mark.parametrize("value", [Some(0), Some(1), Null(), Ok(False), Ok(True), Err("")])
@pytest.mark.parametrize(
    "operation",
    [bool, lambda value: not value, lambda value: value and "next", lambda value: value or "fallback"],
    ids=["bool", "not", "and", "or"],
)
def test_variants_reject_boolean_conversion(value: object, operation: Callable[[object], object]) -> None:
    with pytest.raises(TypeError, match="has no truth value"):
        operation(value)


@pytest.mark.parametrize("value", [Some(0), Some(1), Null(), Ok(False), Ok(True), Err("")])
def test_variants_reject_implicit_branching(value: object) -> None:
    with pytest.raises(TypeError, match="has no truth value"):
        if value:
            pytest.fail("A variant must not choose a branch through truthiness")


def test_variant_checks_are_explicit_booleans() -> None:
    assert Some(False).is_some() is True
    assert Some(False).is_null() is False
    assert Null().is_some() is False
    assert Null().is_null() is True
    assert Ok(False).is_ok() is True
    assert Ok(False).is_err() is False
    assert Err("").is_ok() is False
    assert Err("").is_err() is True


@pytest.mark.parametrize("failed", [False, True])
def test_result_q_returns_early_only_for_errors(failed: bool) -> None:
    error = ValueError("missing count")
    source: Result[int, ValueError] = Err(error) if failed else Ok(0)
    calls: list[int] = []

    @allow_Q
    def calculate(*, multiplier: int) -> Result[str, ValueError]:
        """Format the scaled count."""
        count = source.Q()
        calls.append(count)
        return Ok(str(count * multiplier))

    result = calculate(multiplier=2)
    assert calculate.__name__ == "calculate"
    assert calculate.__doc__ == "Format the scaled count."
    if failed:
        assert isinstance(result, Err)
        assert result.val is error
        assert calls == []
    else:
        assert isinstance(result, Ok)
        assert result.val == "0"
        assert calls == [0]


@pytest.mark.parametrize("missing", [False, True])
def test_option_q_returns_early_only_for_null(missing: bool) -> None:
    source: Option[bool] = Null() if missing else Some(False)
    calls: list[bool] = []

    @allow_Q
    def read_flag() -> Option[bool]:
        flag = source.Q()
        calls.append(flag)
        return Some(flag)

    result = read_flag()
    if missing:
        assert isinstance(result, Null)
        assert calls == []
    else:
        assert isinstance(result, Some)
        assert result.val is False
        assert calls == [False]


def test_option_can_be_converted_before_result_propagation() -> None:
    @allow_Q
    def require_count(raw_count: int | None) -> Result[int, str]:
        count = option(raw_count).ok_or("Count is required").Q()
        return Ok(count)

    assert require_count(None).unwrap_err() == "Count is required"
    assert require_count(0).unwrap() == 0


def test_nested_q_stops_at_the_nearest_decorated_function() -> None:
    calls: list[str] = []

    @allow_Q
    def inner() -> Result[int, str]:
        failure: Result[int, str] = Err("invalid")
        return Ok(failure.Q())

    @allow_Q
    def outer() -> Result[int, str]:
        inner_result = inner()
        calls.append("inner returned")
        count = inner_result.Q()
        calls.append("outer continued")
        return Ok(count)

    assert outer().unwrap_err() == "invalid"
    assert calls == ["inner returned"]


def test_q_requires_a_decorated_caller() -> None:
    with pytest.raises(PropagationError, match="@allow_Q"):
        Null().Q()
    with pytest.raises(PropagationError, match="@allow_Q"):
        Err("invalid").Q()


def test_combinators_and_allow_q_preserve_unexpected_exceptions() -> None:
    error = RuntimeError("unexpected failure")

    def fail(_: object) -> NoReturn:
        raise error

    @allow_Q
    def calculate() -> Result[int, str]:
        raise error

    success: Result[int, str] = Ok(0)
    failure: Result[int, str] = Err("")
    for operation in (
        lambda: Some(0).map(fail),
        lambda: Some(0).and_then(fail),
        lambda: success.map(fail),
        lambda: success.and_then(fail),
        lambda: failure.map_err(fail),
        calculate,
    ):
        with pytest.raises(RuntimeError) as raised:
            operation()
        assert raised.value is error
