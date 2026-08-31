"""
Service for match "action" endpoints (JSON).

The route handlers should be thin: validate request shape, call into this service,
then map Result -> JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.error_values import Err, Ok, Result, allow_Q, option
from app.exceptions import (
    ArctosError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.domain.enums import MatchStatus
from app.utils.helpers import can_head_ref_match

if TYPE_CHECKING:  # pragma: no cover
    from models import Match, Point


@dataclass(frozen=True)
class MatchActionsService:
    """Service for in-match actions (add/update/delete points, update stones/sets).

    All methods are static; the class acts as a typed namespace.  Each public
    method returns a :class:`~app.error_values.Result` so callers can map
    errors to HTTP responses without raising exceptions.
    """

    @staticmethod
    @allow_Q
    def _require_match(tournament_url: str, match_id: str) -> Result["Match", ArctosError]:
        """Fetch and validate a match belongs to *tournament_url*.

        Args:
            tournament_url: Expected tournament URL slug.
            match_id: UUID of the match to fetch.

        Returns:
            :class:`~app.error_values.Ok` wrapping the match, or
            :class:`~app.error_values.Err` with
            :class:`~app.exceptions.ValidationError` /
            :class:`~app.exceptions.NotFoundError`.
        """
        from models import Match

        if not match_id:
            return Err(ValidationError("Match ID required", status_code=400))
        match = option(Match.query.get(match_id)).ok_or(NotFoundError("Match not found", status_code=404)).Q()
        if match.event != tournament_url:
            return Err(NotFoundError("Match not found", status_code=404))
        return Ok(match)

    @staticmethod
    @allow_Q
    def _require_point(point_id: str) -> Result["Point", ArctosError]:
        """Fetch a :class:`~app.models.match.Point` by its UUID.

        Args:
            point_id: UUID of the point to fetch.

        Returns:
            :class:`~app.error_values.Ok` wrapping the point, or
            :class:`~app.error_values.Err` with
            :class:`~app.exceptions.ValidationError` /
            :class:`~app.exceptions.NotFoundError`.
        """
        from models import Point

        if not point_id:
            return Err(ValidationError("Point ID required", status_code=400))
        point = option(Point.query.get(point_id)).ok_or(NotFoundError("Point not found", status_code=404)).Q()
        return Ok(point)

    @staticmethod
    def _require_head_ref(tournament_url: str, user_id: str, *, match) -> Result[None, ArctosError]:
        """Verify that *user_id* has head-ref permission for *match*.

        Args:
            tournament_url: Tournament URL slug.
            user_id: ID of the player to check.
            match: The :class:`~app.models.match.Match` instance to check
                against, or ``None`` for a general check.

        Returns:
            :class:`~app.error_values.Ok` wrapping ``None`` on success, or
            :class:`~app.error_values.Err` with
            :class:`~app.exceptions.UnauthorizedError`.
        """
        if not can_head_ref_match(tournament_url, user_id, match=match):
            return Err(UnauthorizedError("Not authorized", status_code=403))
        return Ok(None)

    @staticmethod
    @allow_Q
    def get_points(tournament_url: str, user_id: str, *, match_id: str) -> Result[dict, ArctosError]:
        """Return all scored points for a match.

        Args:
            tournament_url: Tournament URL slug scoping the match.
            user_id: Requesting player's ID (must be a head ref).
            match_id: UUID of the target match.

        Returns:
            :class:`~app.error_values.Ok` wrapping
            ``{"points": [...]}``, or :class:`~app.error_values.Err`.
        """
        from models import Point

        match = MatchActionsService._require_match(tournament_url, match_id).Q()
        # Keep legacy behavior: this endpoint historically returned 200 for errors,
        # so the route can override status if desired.
        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        points = Point.query.filter_by(match=match_id).order_by(Point.stamp).all()
        points_data: list[dict[str, Any]] = []
        for p in points:
            points_data.append(
                {
                    "uuid": p.uuid,
                    "set_number": p.set_number,
                    "winner": p.winner,
                    "rerolled": p.rerolled,
                    "stamp": p.stamp.isoformat() if p.stamp else None,
                    "end_stamp": p.end_stamp.isoformat() if p.end_stamp else None,
                    "stones_at_start": (p.stones_at_start if match.set_type == "STONES" else None),
                }
            )
        return Ok({"points": points_data})

    @staticmethod
    @allow_Q
    def add_point(
        tournament_url: str,
        user_id: str,
        *,
        match_id: str,
        set_number: int | str | None,
        timestamp_ms: int | float | None,
        stones_at_start: int | None,
    ) -> Result[dict, ArctosError]:
        """Record a new scored point in the match.

        Attaches camera stream timestamp metadata when the field has camera
        information.

        Args:
            tournament_url: Tournament URL slug scoping the match.
            user_id: Requesting player's ID (must be a head ref).
            match_id: UUID of the target match.
            set_number: Which set this point belongs to (1-indexed).
            timestamp_ms: Unix timestamp in milliseconds when the point
                was scored.
            stones_at_start: Stones remaining at the start of this point
                (``STONES`` mode only).

        Returns:
            :class:`~app.error_values.Ok` wrapping the new point's UUID and
            metadata, or :class:`~app.error_values.Err`.
        """
        from models import Point, db
        from app.utils.datetime_helpers import to_iso_z

        match = MatchActionsService._require_match(tournament_url, match_id).Q()
        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        try:
            set_number_i = int(set_number) if set_number is not None else 1
        except (ValueError, TypeError):
            return Err(ValidationError("set_number must be an integer", status_code=400))

        if timestamp_ms is None:
            return Err(ValidationError("timestamp required", status_code=400))
        try:
            timestamp_f = float(timestamp_ms)
        except (ValueError, TypeError):
            return Err(ValidationError("timestamp must be a number", status_code=400))

        new_point = Point(
            match=match_id,
            set_number=set_number_i,
            stamp=datetime.fromtimestamp(timestamp_f / 1000, tz=timezone.utc),
        )

        if match.set_type == "STONES":
            if stones_at_start is not None and isinstance(stones_at_start, int):
                new_point.stones_at_start = stones_at_start
            else:
                new_point.stones_at_start = match.stones_remaining

        db.session.add(new_point)
        db.session.commit()

        return Ok(
            {
                "point_id": new_point.uuid,
                "set_number": new_point.set_number,
                "stamp": to_iso_z(new_point.stamp).unwrap_or(None),
                "end_stamp": to_iso_z(new_point.end_stamp).unwrap_or(None),
                "stones_at_start": (new_point.stones_at_start if match.set_type == "STONES" else None),
            }
        )

    @staticmethod
    @allow_Q
    def update_point(
        tournament_url: str,
        user_id: str,
        *,
        point_id: str,
        data: dict,
    ) -> Result[dict, ArctosError]:
        """Update mutable fields on an existing point.

        Supported keys in *data*: ``winner``, ``rerolled``, ``notes``,
        ``set_number``, ``end_stamp``.

        Args:
            tournament_url: Tournament URL slug scoping the point's match.
            user_id: Requesting player's ID (must be a head ref).
            point_id: UUID of the point to update.
            data: Dictionary of field names to new values.

        Returns:
            :class:`~app.error_values.Ok` wrapping the updated point dict,
            or :class:`~app.error_values.Err`.
        """
        from models import Match, db

        point = MatchActionsService._require_point(point_id).Q()
        match = Match.query.get(point.match)
        if not match or match.event != tournament_url:
            return Err(NotFoundError("Match not found", status_code=404))

        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        if "winner" in data:
            point.winner = data["winner"] if data["winner"] != "none" else None
        if "rerolled" in data:
            point.rerolled = data["rerolled"]
        if "notes" in data:
            point.notes = data["notes"]
        if "set_number" in data:
            point.set_number = data["set_number"]
        if "end_stamp" in data:
            point.end_stamp = datetime.fromisoformat(data["end_stamp"].replace("Z", "+00:00"))

        db.session.commit()

        return Ok(
            {
                "point_id": point_id,
                "winner": point.winner,
                "rerolled": point.rerolled,
                "notes": point.notes,
                "set_number": point.set_number,
                "end_stamp": point.end_stamp.isoformat() if point.end_stamp else None,
                "nstones": point.nstones,
            }
        )

    @staticmethod
    @allow_Q
    def delete_point(tournament_url: str, user_id: str, *, point_id: str) -> Result[dict, ArctosError]:
        """Permanently delete a scored point from a match.

        Args:
            tournament_url: Tournament URL slug scoping the point's match.
            user_id: Requesting player's ID (must be a head ref).
            point_id: UUID of the point to delete.

        Returns:
            :class:`~app.error_values.Ok` wrapping ``{"point_id": ...}``,
            or :class:`~app.error_values.Err`.
        """
        from models import Match, db

        point = MatchActionsService._require_point(point_id).Q()
        match = Match.query.get(point.match)
        if not match or match.event != tournament_url:
            return Err(NotFoundError("Match not found", status_code=404))

        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        db.session.delete(point)
        db.session.commit()
        return Ok({"point_id": point_id})

    @staticmethod
    @allow_Q
    def update_stones(
        tournament_url: str,
        user_id: str,
        *,
        match_id: str,
        stones_remaining: int | str | None,
    ) -> Result[dict, ArctosError]:
        """Update the stones-remaining count for a ``STONES``-mode match.

        Args:
            tournament_url: Tournament URL slug scoping the match.
            user_id: Requesting player's ID (must be a head ref).
            match_id: UUID of the target match.
            stones_remaining: New stones-remaining value (coerced to int).

        Returns:
            :class:`~app.error_values.Ok` wrapping
            ``{"stones_remaining": int}``, or
            :class:`~app.error_values.Err`.
        """
        from models import db

        if not match_id or stones_remaining is None:
            return Err(ValidationError("Match ID and stones_remaining required", status_code=400))
        try:
            stones_remaining_i = int(stones_remaining)
        except (ValueError, TypeError):
            return Err(ValidationError("stones_remaining must be an integer", status_code=400))

        match = MatchActionsService._require_match(tournament_url, match_id).Q()
        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        match.stones_remaining = stones_remaining_i
        db.session.commit()
        return Ok({"stones_remaining": stones_remaining_i})

    @staticmethod
    @allow_Q
    def update_set(
        tournament_url: str,
        user_id: str,
        *,
        point_id: str,
        set_number: int | str | None,
    ) -> Result[dict, ArctosError]:
        """Reassign a point to a different set number.

        Args:
            tournament_url: Tournament URL slug scoping the point's match.
            user_id: Requesting player's ID (must be a head ref).
            point_id: UUID of the point to reassign.
            set_number: Target set number (coerced to int).

        Returns:
            :class:`~app.error_values.Ok` wrapping the point ID and new set
            number, or :class:`~app.error_values.Err`.
        """
        from models import Match, db

        if not point_id or set_number is None:
            return Err(ValidationError("Point ID and set_number required", status_code=400))
        try:
            set_number_i = int(set_number)
        except (ValueError, TypeError):
            return Err(ValidationError("set_number must be an integer", status_code=400))

        point = MatchActionsService._require_point(point_id).Q()
        match = Match.query.get(point.match)
        if not match or match.event != tournament_url:
            return Err(NotFoundError("Match not found", status_code=404))

        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        point.set_number = set_number_i
        db.session.commit()
        return Ok({"point_id": point_id, "set_number": set_number_i})

    @staticmethod
    @allow_Q
    def complete_match(tournament_url: str, user_id: str, *, match_id: str) -> Result[dict, ArctosError]:
        """Transition a match's status to ``COMPLETED``.

        Args:
            tournament_url: Tournament URL slug scoping the match.
            user_id: Requesting player's ID (must be a head ref).
            match_id: UUID of the match to complete.

        Returns:
            :class:`~app.error_values.Ok` wrapping match ID and new status,
            or :class:`~app.error_values.Err`.
        """
        from models import db

        match = MatchActionsService._require_match(tournament_url, match_id).Q()
        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()

        match.status = MatchStatus.COMPLETED
        db.session.commit()
        return Ok({"match_id": match_id, "status": MatchStatus.COMPLETED})
