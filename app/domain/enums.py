"""
Domain enums used across routes, services, and models.

These enums are designed to be compatible with the existing database schema,
which stores values as strings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from app.error_values import Null, Option, Some


TEnum = TypeVar("TEnum", bound=StrEnum)


def parse_enum(enum_cls: type[TEnum], value: object) -> Option[TEnum]:
    """Best-effort conversion of a raw DB or string value into an enum member.

    Handles three cases without raising:

    * ``None`` → :class:`~app.error_values.Null`
    * Already the correct enum type → :class:`~app.error_values.Some` wrapping it
    * Convertible string → :class:`~app.error_values.Some` wrapping the member
    * Unrecognised string → :class:`~app.error_values.Null`

    Args:
        enum_cls: The :class:`~enum.StrEnum` subclass to parse into.
        value: The raw value from a database column or request parameter.

    Returns:
        :class:`~app.error_values.Some` containing the parsed enum member, or
        :class:`~app.error_values.Null` if the value cannot be mapped.
    """
    if value is None:
        return Null()
    if isinstance(value, enum_cls):
        return Some(value)
    try:
        return Some(enum_cls(str(value)))
    except Exception:
        return Null()


class MatchNoteTarget(StrEnum):
    """The entity that a :class:`~app.models.match.MatchNote` is associated with."""

    TEAM1 = "team1"
    TEAM2 = "team2"
    MATCH = "match"
    PLAYER = "player"


class RegistrationStatus(StrEnum):
    """Lifecycle status of an individual player's registration in an event."""

    PENDING_TEAM_APPROVAL = "PENDING_TEAM_APPROVAL"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class UserType(StrEnum):
    """Account type for a Flask-Login user.

    Members compare equal to their bare-string DB representation, which
    keeps storage shape compatible with the legacy ``user_type`` columns.
    """

    PLAYER = "player"
    TEAM = "team"


class TeamRegistrationStatus(StrEnum):
    """Lifecycle status of a team's registration in an event.

    PENDING is a transient scratch state used by the cap-enforcement
    insert-and-recount pattern in RegistrationService. Rows are never
    committed in PENDING state - they are either promoted to CONFIRMED
    or rolled back via savepoint.
    """

    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"


class MatchStatus(StrEnum):
    """Match statuses.
    NOT_STARTED: initial state

    TIME_FINALIZED: start time will not be pushed back any
    further. match is guaranteed not to be skipped.

    READY_TO_START: all ref and playing teams are known; game will
    start as soon as everyone is present.

    IN_PROGRESS: match has been started but not finished

    COMPLETED: match is done! both start and end stamps exist.

    SKIPPED: match has been skipped (effectively completed). start and
    end stamps are equal and are the time that the match was marked
    skipped.
    """

    NOT_STARTED = "NOT_STARTED"
    TIME_FINALIZED = "TIME_FINALIZED"
    READY_TO_START = "READY_TO_START"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class ScheduleType(StrEnum):
    """Scheduling strategy for computing match start times.

    Attributes:
        STATIC: Times are fixed and never recalculated automatically.
        SAFE: Times are recalculated conservatively, preventing cascading
            delays.
        FAST: Times are recalculated aggressively, scheduling matches as
            early as possible.
        BREAK: A scheduled break (no match played).
        JOIN: A synchronisation point that waits for multiple preceding
            matches to complete before advancing.
    """

    STATIC = "STATIC"
    SAFE = "SAFE"
    FAST = "FAST"
    BREAK = "BREAK"
    JOIN = "JOIN"


class SetType(StrEnum):
    """Scoring mode used for matches in a tournament.

    Attributes:
        SETS: Winner is determined by number of sets won.
        STONES: Winner is determined by total stone count (points).
    """

    SETS = "SETS"
    STONES = "STONES"


class SideCompType(StrEnum):
    """Allowed side competition types."""

    DUELING = "DUELING"
    CHAIN_BREAKING = "CHAIN_BREAKING"
    OTHER = "OTHER"


class WinnerSide(StrEnum):
    """Identifies which team won a completed match.

    Attributes:
        TEAM1: The first team in the match won.
        TEAM2: The second team in the match won.
    """

    TEAM1 = "TEAM1"
    TEAM2 = "TEAM2"


class BracketPortMode(StrEnum):
    """How a bracket match-block input is rendered on the canvas.

    Attributes:
        LABEL: Show the slot's ``_initial`` value as a net-style label
            (explicit team, tag, or match winner/loser token).
        NET: Draw a wire from another match's winner/loser output into
            this input. Only valid when the slot references a match
            winner or loser.
    """

    LABEL = "LABEL"
    NET = "NET"
