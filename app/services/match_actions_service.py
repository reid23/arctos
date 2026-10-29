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
        correction: dict | None = None,
    ) -> Result[dict, ArctosError]:
        """Save a count, optionally rebasing an active point on the same global beat.

        A manual correction carries the observed timestamp and expected active/latest
        point IDs. Retrying that unchanged payload produces the same starting anchor;
        the original point timestamp (and therefore duration) is never rewritten.
        Legacy end-point/rerun calls omit ``correction`` and keep count-only behavior.
        """
        import math

        from sqlalchemy import update

        from app.utils.datetime_helpers import now_utc_naive
        from models import Match, Point, db

        if not match_id or stones_remaining is None:
            return Err(ValidationError("Match ID and stones_remaining required", status_code=400))
        if isinstance(stones_remaining, bool):
            return Err(ValidationError("stones_remaining must be a nonnegative integer", status_code=400))
        try:
            stones_remaining_i = int(stones_remaining)
            if str(stones_remaining_i) != str(stones_remaining).strip() or not 0 <= stones_remaining_i <= 2_147_483_647:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            return Err(ValidationError("stones_remaining must be a nonnegative integer", status_code=400))

        match = MatchActionsService._require_match(tournament_url, match_id).Q()
        MatchActionsService._require_head_ref(tournament_url, user_id, match=match).Q()
        if correction is None:
            match.stones_remaining = stones_remaining_i
            db.session.commit()
            return Ok({"stones_remaining": stones_remaining_i})

        if (
            not isinstance(correction, dict)
            or not {"point_id", "latest_point_id", "observed_at_ms"} <= correction.keys()
        ):
            return Err(ValidationError("Correction requires point state and observation time", status_code=400))
        if match.set_type != "STONES":
            return Err(ValidationError("Stone corrections require a stones match", status_code=400))
        observed_ms = correction["observed_at_ms"]
        if (
            isinstance(observed_ms, bool)
            or not isinstance(observed_ms, (int, float))
            or (isinstance(observed_ms, float) and not math.isfinite(observed_ms))
        ):
            return Err(ValidationError("Invalid correction observation time", status_code=400))
        now = now_utc_naive()
        now_ms = now.replace(tzinfo=timezone.utc).timestamp() * 1000
        if observed_ms > now_ms + 5000 or observed_ms < now_ms - 300_000:
            return Err(
                ValidationError("Correction time is stale. Check the current count and try again.", status_code=409)
            )

        # Acquire the match write lock before reading point state, including on SQLite.
        # All updates below are committed together; validation never changes the count.
        db.session.execute(update(Match).where(Match.uuid == match_id).values(stones_remaining=Match.stones_remaining))
        db.session.refresh(match)
        points = (
            Point.query.filter_by(match=match_id)
            .order_by(Point.stamp, Point.uuid)
            .populate_existing()
            .with_for_update()
            .all()
        )
        latest = points[-1] if points else None
        active = [point for point in points if point.end_stamp is None]
        expected_active = correction["point_id"]
        expected_latest = correction["latest_point_id"]
        actual_active = active[0] if len(active) == 1 else None
        if (
            len(active) > 1
            or expected_active != (actual_active.uuid if actual_active else None)
            or expected_latest != (latest.uuid if latest else None)
        ):
            db.session.rollback()
            return Err(
                ValidationError("The point changed. Check the current count before correcting it.", status_code=409)
            )
        payload: dict[str, Any] = {"stones_remaining": stones_remaining_i, "corrected_point": None}
        if actual_active is not None:
            if actual_active.stamp is None:
                db.session.rollback()
                return Err(ValidationError("The point has no start time", status_code=409))
            stamp_ms = actual_active.stamp.replace(tzinfo=timezone.utc).timestamp() * 1000
            if observed_ms < stamp_ms:
                db.session.rollback()
                return Err(ValidationError("Correction time is before the point started", status_code=400))
            elapsed_observed = max(math.floor(observed_ms / 1500) - math.floor(stamp_ms / 1500), 0)
            elapsed_now = max(math.floor(now_ms / 1500) - math.floor(stamp_ms / 1500), 0)
            anchor = stones_remaining_i + elapsed_observed
            if anchor > 2_147_483_647:
                db.session.rollback()
                return Err(ValidationError("Stone count is too large", status_code=400))
            if "expected_anchor" in correction and actual_active.stones_at_start not in (
                correction["expected_anchor"],
                anchor,
            ):
                db.session.rollback()
                return Err(
                    ValidationError(
                        "The stone count changed. Check the current count before correcting it.", status_code=409
                    )
                )
            actual_active.stones_at_start = anchor
            payload["stones_remaining"] = max(anchor - elapsed_now, 0)
            payload["corrected_point"] = {"uuid": actual_active.uuid, "stones_at_start": anchor}
        elif "expected_remaining" in correction and match.stones_remaining not in (
            correction["expected_remaining"],
            stones_remaining_i,
        ):
            db.session.rollback()
            return Err(
                ValidationError(
                    "The stone count changed. Check the current count before correcting it.", status_code=409
                )
            )
        match.stones_remaining = payload["stones_remaining"]
        db.session.commit()
        return Ok(payload)

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
