from __future__ import annotations

from typing import Any, Callable, TypeVar

from flask import jsonify

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError
from app.utils.responses import json_error, json_success


def public_error_message(err: Any) -> str:
    """Convert an error value into a safe end-user message string.

    For :class:`~app.exceptions.ArctosError` instances the human-readable
    ``message`` is returned when ``public`` is ``True``; otherwise a generic
    fallback is used.  All other error types are coerced to ``str``.

    Args:
        err: The error value to convert.

    Returns:
        A string safe to show in an API response body.
    """
    if isinstance(err, ArctosError):
        return err.message if err.public else "Request failed"
    return str(err) or "Request failed"


T = TypeVar("T")
E = TypeVar("E")


def json_from_result(
    res: Result[T, E],
    *,
    ok_to_payload: Callable[[T], dict] = lambda v: v if isinstance(v, dict) else {"value": v},
    ok_status_code: int = 200,
    err_status_code: int | None = None,
):
    """Convert a :class:`~app.error_values.Result` into a JSON HTTP response.

    Args:
        res: The result to convert.
        ok_to_payload: Callable that converts the success value to a dict
            merged into the JSON body.  Defaults to passing dicts through
            unchanged and wrapping other values as ``{"value": v}``.
        ok_status_code: HTTP status code for success responses (default 200).
        err_status_code: HTTP status code for error responses.  When ``None``,
            uses the ``status_code`` attribute of
            :class:`~app.exceptions.ArctosError` errors, or ``200`` for all
            others (historical compatibility).

    Returns:
        A ``(Response, status_code)`` tuple from :func:`~app.utils.responses.json_success`
        or :func:`~app.utils.responses.json_error`.
    """
    match res:
        case Ok(val):
            return json_success(ok_to_payload(val), status_code=ok_status_code)
        case Err(err):
            status = 200
            if err_status_code is not None:
                status = err_status_code
            elif isinstance(err, ArctosError):
                status = err.status_code
            return json_error(public_error_message(err), status_code=status)


def result_response(res: Result[T, E], ok_to_payload: Callable[[T], dict], ok_status_code: int = 200):
    """Convert a :class:`~app.error_values.Result` into a JSON response without a ``success`` key.

    ``Ok`` becomes ``ok_to_payload(value)``; ``Err`` becomes ``{"error": "..."}``
    with the error's ``status_code`` (400 for errors that aren't an
    :class:`~app.exceptions.ArctosError`).
    """
    match res:
        case Ok(value):
            return jsonify(ok_to_payload(value)), ok_status_code
        case Err(err):
            status_code = err.status_code if isinstance(err, ArctosError) else 400
            return jsonify({"error": public_error_message(err)}), status_code
