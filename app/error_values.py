"""Represent expected failures and absent values explicitly.

``Result[T, E]`` contains ``Ok(value)`` or ``Err(error)``. ``Option[T]``
contains ``Some(value)`` or ``Null()``. Use ``option(value)`` at boundaries
where Python libraries return ``None`` for absence. Values such as ``0``,
``False``, empty strings, and empty collections remain present values.

Handle variants with pattern matching, transform their values with ``map``
or ``map_err``, and chain operations with ``and_then``. Check presence or
success with ``is_some()``, ``is_null()``, ``is_ok()``, or ``is_err()``.
Converting an Option or Result to a boolean raises ``TypeError``.

Use ``unwrap`` and ``expect`` only to assert an invariant: the absent or
error variant must indicate a programming error. The same rule applies to
``unwrap_err`` and ``expect_err``. Handle expected absence and failures
with pattern matching, a fallback, or propagation instead.

Inside an ``@allow_Q`` function, ``.Q()`` extracts a present or successful
value and returns early for ``Null`` or ``Err``. Use Option propagation
only in functions returning Option, and Result propagation only in
functions returning Result with a compatible error type. Convert an
Option with ``ok_or`` or ``ok_or_else`` before propagating into a Result::

    @allow_Q
    def require_count(raw_count: int | None) -> Result[int, str]:
        count = option(raw_count).ok_or("Count is required").Q()
        return Ok(count)

Python type checkers preserve the decorated function's signature, but
cannot verify the return values carried by propagation exceptions. Use
``and_then`` when each step's return type needs static checking.
"""

from abc import ABC, abstractmethod
from functools import wraps
from typing import Any, Callable, NoReturn, overload


def option[T](val: T | None) -> "Option[T]":
    """Convert a nullable Python value into an :class:`Option`.

    Args:
        val: Any value, or ``None``.

    Returns:
        :class:`Some` wrapping *val* when *val* is not ``None``,
        otherwise :class:`Null`.
    """
    return Null() if val is None else Some(val)


class Result[T, E](ABC):
    """Abstract base for a two-state computation result.

    A ``Result[T, E]`` is either an :class:`Ok` carrying a success value of
    type *T*, or an :class:`Err` carrying an error value of type *E*.  Callers
    inspect the variant with :meth:`is_ok` / :meth:`is_err` or via
    ``match``-statement destructuring (``Ok(val)`` / ``Err(val)``).
    """

    def __bool__(self) -> bool:
        """Require an explicit check of the result variant."""
        raise TypeError("Result has no truth value; use is_ok(), is_err(), or pattern matching")

    @abstractmethod
    def map[U](self, f: Callable[[T], U]) -> "Result[U, E]":
        """Transform an Ok value with *f*, leaving an Err value unchanged."""

    @abstractmethod
    def map_err[F](self, f: Callable[[E], F]) -> "Result[T, F]":
        """Transform an Err value with *f*, leaving an Ok value unchanged."""

    @abstractmethod
    def and_then[U](self, f: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        """Call *f* for Ok and return its result; propagate Err without calling *f*."""

    @abstractmethod
    def is_ok(self) -> bool:
        """Return ``True`` if this result is an :class:`Ok` variant."""

    @abstractmethod
    def is_err(self) -> bool:
        """Return ``True`` if this result is an :class:`Err` variant."""

    @abstractmethod
    def expect(self, msg: str) -> T:
        """Assert success and return the value, raising if the invariant fails.

        Args:
            msg: Message for the ``ValueError`` raised on an :class:`Err`.

        Returns:
            The contained success value.

        Raises:
            ValueError: If this is an :class:`Err` variant.
        """

    @abstractmethod
    def expect_err(self, msg: str) -> E:
        """Assert an error and return it, raising if the invariant fails.

        Args:
            msg: Message for the ``ValueError`` raised on an :class:`Ok`.

        Returns:
            The contained error value.

        Raises:
            ValueError: If this is an :class:`Ok` variant.
        """

    @abstractmethod
    def unwrap(self) -> T:
        """Assert success and return the value, raising if the invariant fails.

        Returns:
            The contained success value.

        Raises:
            ValueError: If this is an :class:`Err` variant.
        """

    @abstractmethod
    def unwrap_err(self) -> E:
        """Assert an error and return it, raising if the invariant fails.

        Returns:
            The contained error value.

        Raises:
            ValueError: If this is an :class:`Ok` variant.
        """

    @abstractmethod
    def unwrap_or[U](self, default: U) -> T | U:
        """Return the success value, or *default* if this is an error.

        Args:
            default: Fallback value returned when this is an :class:`Err`.

        Returns:
            The contained success value, or *default*.
        """

    @abstractmethod
    def unwrap_err_or[U](self, default: U) -> E | U:
        """Return the error value, or *default* if this is a success.

        Args:
            default: Fallback value returned when this is an :class:`Ok`.

        Returns:
            The contained error value, or *default*.
        """

    @abstractmethod
    def Q(self) -> T:
        """Propagate errors like Rust's ``?`` operator.

        If this is an :class:`Ok`, unwrap and return the contained value.
        If this is an :class:`Err`, raise :class:`PropagationError` which is
        caught by the :func:`allow_Q` decorator and returned as-is to the
        caller — effectively propagating the error up the call stack.

        Must be used inside a function decorated with :func:`allow_Q`.

        Returns:
            The contained success value.

        Raises:
            PropagationError: Caught by :func:`allow_Q`; do not handle
                manually.
        """


class Option[T](ABC):
    """Abstract base for an optional value.

    An ``Option[T]`` is either :class:`Some` wrapping a value of type *T*,
    or :class:`Null` representing the absence of a value — similar to
    ``std::optional`` in C++ or ``Option`` in Rust/Haskell.
    """

    def __bool__(self) -> bool:
        """Require an explicit check of the option variant."""
        raise TypeError("Option has no truth value; use is_some(), is_null(), or pattern matching")

    @abstractmethod
    def map[U](self, f: Callable[[T], U]) -> "Option[U]":
        """Transform a Some value with *f*; preserve Null without calling *f*."""

    @abstractmethod
    def and_then[U](self, f: Callable[[T], "Option[U]"]) -> "Option[U]":
        """Call *f* for Some and return its option; propagate Null without calling *f*."""

    @abstractmethod
    def unwrap(self) -> T:
        """Assert presence and return the value, raising if the invariant fails.

        Returns:
            The contained value.

        Raises:
            ValueError: If this is a :class:`Null` variant.
        """

    @abstractmethod
    def unwrap_or[U](self, default: U) -> T | U:
        """Return the contained value, or *default* if this is :class:`Null`.

        Args:
            default: Fallback value returned for :class:`Null`.

        Returns:
            The contained value, or *default*.
        """

    @abstractmethod
    def expect(self, msg: str) -> T:
        """Assert presence and return the value, raising if the invariant fails.

        Args:
            msg: Message for the ``ValueError`` raised on :class:`Null`.

        Returns:
            The contained value.

        Raises:
            ValueError: If this is a :class:`Null` variant.
        """

    @abstractmethod
    def is_some(self) -> bool:
        """Return ``True`` if this option holds a value."""

    @abstractmethod
    def is_null(self) -> bool:
        """Return ``True`` if this option is empty."""

    @abstractmethod
    def Q(self) -> T:
        """Propagate :class:`Null` like Rust's ``?`` operator.

        If this is :class:`Some`, unwrap and return the value.  If this is
        :class:`Null`, raise :class:`PropagationError` which is caught by the
        :func:`allow_Q` decorator and returned as :class:`Null` to the caller.

        Must be used inside a function decorated with :func:`allow_Q`.

        Returns:
            The contained value.

        Raises:
            PropagationError: Caught by :func:`allow_Q`; do not handle
                manually.
        """

    @abstractmethod
    def ok_or[E](self, err: E) -> Result[T, E]:
        """Convert this option into a ``Result``, using *err* for :class:`Null`.

        Args:
            err: The error value to use when this option is :class:`Null`.

        Returns:
            :class:`Ok` wrapping the value, or :class:`Err` wrapping *err*.
        """

    @abstractmethod
    def ok_or_else[E](self, f: Callable[[], E]) -> Result[T, E]:
        """Convert this option into a ``Result``, calling *f* for :class:`Null`.

        Args:
            f: Zero-argument callable that produces the error value when this
               option is :class:`Null`.

        Returns:
            :class:`Ok` wrapping the value, or :class:`Err` wrapping ``f()``.
        """


class Some[T](Option[T]):
    """An :class:`Option` variant that holds a value.

    Attributes:
        val: The wrapped value of type *T*.
    """

    __match_args__ = ("val",)

    def __init__(self, val: T) -> None:
        """Wrap *val* in a ``Some``.

        Args:
            val: The value to hold.
        """
        self.val = val

    def map[U](self, f: Callable[[T], U]) -> Option[U]:
        """Return Some containing the transformed value, including None."""
        return Some(f(self.val))

    def and_then[U](self, f: Callable[[T], Option[U]]) -> Option[U]:
        """Return the option produced by *f*."""
        return f(self.val)

    def unwrap(self) -> T:
        """Return the contained value."""
        return self.val

    def unwrap_or[U](self, default: U) -> T | U:
        """Return the contained value (ignores *default*)."""
        return self.val

    def expect(self, msg: str) -> T:
        """Return the contained value (ignores *msg*)."""
        return self.val

    def is_some(self) -> bool:
        """Always returns ``True``."""
        return True

    def is_null(self) -> bool:
        """Always returns ``False``."""
        return False

    def Q(self) -> T:
        """Return the contained value unchanged."""
        return self.val

    def ok_or[E](self, err: E) -> "Result[T, E]":
        """Return ``Ok(val)`` (ignores *err*)."""
        return Ok(self.val)

    def ok_or_else[E](self, f: Callable[[], E]) -> "Result[T, E]":
        """Return ``Ok(val)`` (never calls *f*)."""
        return Ok(self.val)


class Null[T](Option[T]):
    """An :class:`Option` variant that represents the absence of a value."""

    def map[U](self, f: Callable[[T], U]) -> Option[U]:
        """Return Null without calling *f*."""
        return Null()

    def and_then[U](self, f: Callable[[T], Option[U]]) -> Option[U]:
        """Return Null without calling *f*."""
        return Null()

    def unwrap(self) -> T:
        """Raise ``ValueError`` — no value is present.

        Raises:
            ValueError: Always.
        """
        raise ValueError("`.unwrap()` called on a `Null` value")

    def unwrap_or[U](self, default: U) -> T | U:
        """Return *default* because no value is present."""
        return default

    def expect(self, msg: str) -> T:
        """Raise ``ValueError`` with *msg*.

        Args:
            msg: Message for the raised exception.

        Raises:
            ValueError: Always.
        """
        raise ValueError(msg)

    def is_some(self) -> bool:
        """Always returns ``False``."""
        return False

    def is_null(self) -> bool:
        """Always returns ``True``."""
        return True

    def Q(self) -> NoReturn:
        """Propagate this ``Null`` via :class:`PropagationError`.

        Raises:
            PropagationError: Always; caught by :func:`allow_Q`.
        """
        raise PropagationError(self)

    def ok_or[E](self, err: E) -> "Result[T, E]":
        """Return ``Err(err)``."""
        return Err(err)

    def ok_or_else[E](self, f: Callable[[], E]) -> "Result[T, E]":
        """Return ``Err(f())``."""
        return Err(f())


class Ok[T, E](Result[T, E]):
    """A :class:`Result` variant representing a successful computation.

    Attributes:
        val: The success value of type *T*.
    """

    __match_args__ = ("val",)

    def __init__(self, val: T) -> None:
        """Wrap *val* in an ``Ok``.

        Args:
            val: The success value to hold.
        """
        self.val = val

    def map[U](self, f: Callable[[T], U]) -> Result[U, E]:
        """Return Ok containing the transformed success value."""
        return Ok(f(self.val))

    def map_err[F](self, f: Callable[[E], F]) -> Result[T, F]:
        """Preserve the success value without calling *f*."""
        return Ok(self.val)

    def and_then[U](self, f: Callable[[T], Result[U, E]]) -> Result[U, E]:
        """Return the result produced by *f*."""
        return f(self.val)

    def is_ok(self) -> bool:
        """Always returns ``True``."""
        return True

    def is_err(self) -> bool:
        """Always returns ``False``."""
        return False

    def expect(self, msg: str) -> T:
        """Return the contained success value (ignores *msg*)."""
        return self.val

    def expect_err(self, msg: str) -> E:
        """Raise ``ValueError`` with *msg* — this is not an error.

        Args:
            msg: Message for the raised exception.

        Raises:
            ValueError: Always.
        """
        e = ValueError(msg)
        e.add_note(f"Caused by call `Ok({self.val}).expect_err(...)`")
        raise e

    def unwrap(self) -> T:
        """Return the contained success value."""
        return self.val

    def unwrap_err(self) -> E:
        """Raise ``ValueError`` — this is not an error.

        Raises:
            ValueError: Always.
        """
        e = ValueError("`.unwrap_err()` called on `Ok` value")
        e.add_note(f"Caused by call `Ok({self.val}).unwrap_err()`")
        raise e

    def unwrap_or[U](self, default: U) -> T | U:
        """Return the contained success value (ignores *default*)."""
        return self.val

    def unwrap_err_or[U](self, default: U) -> E | U:
        """Return *default* because this is not an error."""
        return default

    def Q(self) -> T:
        """Return the contained success value unchanged."""
        return self.val


class Err[T, E](Result[T, E]):
    """A :class:`Result` variant representing a failed computation.

    Attributes:
        val: The error value of type *E*.
    """

    __match_args__ = ("val",)

    def __init__(self, err: E) -> None:
        """Wrap *err* in an ``Err``.

        Args:
            err: The error value to hold.
        """
        self.val = err

    def map[U](self, f: Callable[[T], U]) -> Result[U, E]:
        """Preserve the error value without calling *f*."""
        return Err(self.val)

    def map_err[F](self, f: Callable[[E], F]) -> Result[T, F]:
        """Return Err containing the transformed error value."""
        return Err(f(self.val))

    def and_then[U](self, f: Callable[[T], Result[U, E]]) -> Result[U, E]:
        """Propagate the error value without calling *f*."""
        return Err(self.val)

    def is_ok(self) -> bool:
        """Always returns ``False``."""
        return False

    def is_err(self) -> bool:
        """Always returns ``True``."""
        return True

    def expect(self, msg: str) -> T:
        """Raise the underlying error — no success value exists.

        If the error value is an :class:`Exception` it is re-raised directly;
        otherwise a ``ValueError`` with *msg* is raised.

        Args:
            msg: Message for the ``ValueError`` when the error value is not
                an exception.

        Raises:
            Exception: The contained error when it is already an exception.
            ValueError: When the contained error is not an exception.
        """
        if isinstance(self.val, Exception):
            self.val.add_note("Caused by .expect(...) call on `Err` value raised above")
            raise self.val
        else:
            e = ValueError(msg)
            e.add_note(f"Caused by call `Err({self.val}).expect(...)`")
            raise e

    def expect_err(self, msg: str) -> E:
        """Return the contained error value (ignores *msg*)."""
        return self.val

    def unwrap(self) -> T:
        """Raise the underlying error — no success value exists.

        Raises:
            Exception: The contained error when it is already an exception.
            ValueError: When the contained error is not an exception.
        """
        if isinstance(self.val, Exception):
            self.val.add_note("Caused by `.unwrap()` call on `Err` value raised above")
            raise self.val
        else:
            e = ValueError("`.unwrap()` called on `Err` value")
            e.add_note(f"Caused by call `Err({self.val}).unwrap()`")
            raise e

    def unwrap_err(self) -> E:
        """Return the contained error value."""
        return self.val

    def unwrap_or[U](self, default: U) -> T | U:
        """Return *default* because this is an error."""
        return default

    def unwrap_err_or[U](self, default: U) -> E | U:
        """Return the contained error value (ignores *default*)."""
        return self.val

    def Q(self) -> NoReturn:
        """Propagate this error via :class:`PropagationError`.

        Raises:
            PropagationError: Always; caught by :func:`allow_Q`.
        """
        raise PropagationError(self)


class PropagationError(Exception):
    """Internal sentinel used by the ``.Q()`` short-circuit mechanism.

    Raised by :meth:`Err.Q` and :meth:`Null.Q`; caught by the
    :func:`allow_Q` decorator, which returns ``self.val`` from the decorated
    function.  Should never escape to user-visible code.

    Attributes:
        val: The :class:`Err` or :class:`Null` value being propagated.
    """

    def __init__(self, val: Err[Any, Any] | Null[Any]) -> None:
        """Initialise with the error/null value to propagate.

        Args:
            val: The :class:`Err` or :class:`Null` instance being propagated.
        """
        self.val = val
        super().__init__("if you're seeing this it means you forgot the @allow_Q decorator")


@overload
def allow_Q[**P, T, E](f: Callable[P, Result[T, E]]) -> Callable[P, Result[T, E]]: ...


@overload
def allow_Q[**P, T](f: Callable[P, Option[T]]) -> Callable[P, Option[T]]: ...


def allow_Q[**P](
    f: Callable[P, Result[Any, Any] | Option[Any]],
) -> Callable[P, Result[Any, Any] | Option[Any]]:
    """Decorator that enables the ``.Q()`` short-circuit operator inside *f*.

    Wraps *f* so that any :class:`PropagationError` raised by a ``.Q()``
    call is caught and the propagated :class:`Err` / :class:`Null` value is
    returned in place of the normal return value — mirroring Rust's ``?``
    operator semantics.

    Args:
        f: The function to wrap. Its return annotation must be Result or
           Option. Each Q call must propagate that same family; Result
           errors must also match the function's declared error type.

    Returns:
        A wrapped callable that preserves the parameter and return types.
        The caller must ensure propagated values match that return type;
        Python type checkers cannot verify exception-based propagation.

    Example::

        @allow_Q
        def divide(a: int, b: int) -> Result[float, str]:
            result = safe_divide(a, b).Q()  # propagates Err if b == 0
            return Ok(result)
    """

    @wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Result[Any, Any] | Option[Any]:
        try:
            return f(*args, **kwargs)
        except PropagationError as e:
            return e.val

    return wrapper
