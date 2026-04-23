"""
Match operations service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, List, Optional

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import (
    ArctosError,
    NotFoundError,
    ValidationError,
)
from app.utils.match_1nf import replace_match_roster, replace_match_stream_starts

if TYPE_CHECKING:  # pragma: no cover
    from models import Match


def _dedup(seq: Iterable[str]) -> List[str]:
    """Return a deduplicated list preserving insertion order, skipping falsy values.

    Args:
        seq: Iterable of strings to deduplicate.

    Returns:
        A new list with duplicates and empty strings removed.
    """
    seen = set()
    out: List[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


@dataclass(frozen=True)
class MatchService:
    """Service encapsulating high-level match operations.

    All methods are static; the class acts as a namespace.  Each method
    returns a :class:`~app.error_values.Result` so that callers can handle
    errors without exceptions.
    """

    @staticmethod
    @allow_Q
    def start_match(
        tournament_url: str,
        match_id: str,
        user,
        *,
        team1_players_csv: str = "",
        team2_players_csv: str = "",
        match_notes: str = "",
        stones_per_set: Optional[str] = None,
    ) -> Result["Match", ArctosError]:
        """Validate and transition a match into the ``IN_PROGRESS`` state.

        Performs eligibility checks, deduplicates player rosters, records
        the starting timestamp and camera stream-start times, then triggers
        a schedule recomputation.

        Args:
            tournament_url: Tournament URL slug scoping the match.
            match_id: UUID of the match to start.
            user: Authenticated user initiating the start.
            team1_players_csv: Comma-separated player IDs for team1's
                field roster.
            team2_players_csv: Comma-separated player IDs for team2's
                field roster.
            match_notes: Optional free-text notes recorded at start.
            stones_per_set: Override stones per set for ``STONES``-mode
                matches; ``None`` falls back to the match's stored value.

        Returns:
            :class:`~app.error_values.Ok` wrapping the started
            :class:`~app.models.match.Match`, or
            :class:`~app.error_values.Err` wrapping a domain error.
        """
        from models import Match, Tournament, db
        from app.domain.enums import MatchStatus
        from app.utils.field_refs import resolve_match_field_obj, sync_match_field_ref
        from app.utils.scheduling import recompute_all_match_times

        if not match_id:
            return Err(ValidationError("Match ID required"))

        match = Match.query.get(match_id)
        if not match or match.event != tournament_url:
            return Err(NotFoundError("Match not found"))

        from app.services.match_start_eligibility import get_can_start_and_reasons

        can_start, block_reasons, _ = get_can_start_and_reasons(
            tournament_url, match, user
        )
        if not can_start:
            msg = block_reasons[0] if block_reasons else "Cannot start this match."
            return Err(ValidationError(msg))

        raw_team1 = (team1_players_csv or "").strip()
        raw_team2 = (team2_players_csv or "").strip()
        team1_players = [
            pid for pid in (raw_team1.split(",") if raw_team1 else []) if pid
        ]
        team2_players = [
            pid for pid in (raw_team2.split(",") if raw_team2 else []) if pid
        ]

        overlap = set(team1_players) & set(team2_players)
        if overlap:
            return Err(ValidationError("A player cannot be selected for both teams"))

        tournament_obj = Tournament.query.get(tournament_url)
        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament_obj)
        max_roster = getattr(cfg, "max_team_size_field", None) if cfg else None
        try:
            max_roster = int(max_roster) if max_roster is not None else None
        except Exception:
            max_roster = None
        if max_roster and (
            len(team1_players) > max_roster or len(team2_players) > max_roster
        ):
            return Err(ValidationError("Too many players selected for a team"))

        team1_players = _dedup(team1_players)
        team2_players = _dedup(team2_players)

        # Mutations start here (after validation)
        match.status = MatchStatus.IN_PROGRESS
        # Use UTC time (stored as naive in DB, treated as UTC)
        match.confirmed_start_time = datetime.now(timezone.utc).replace(tzinfo=None)

        match.initial_notes = match_notes or ""
        replace_match_roster(match, "team1", team1_players)
        replace_match_roster(match, "team2", team2_players)
        match.started_by = user.id
        match.started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        if match.set_type == "STONES":
            if stones_per_set:
                try:
                    spp = int(stones_per_set)
                except ValueError:
                    return Err(ValidationError("Invalid stones per set value"))
            else:
                spp = match.stones_per_set or 100
            match.stones_per_set = spp
            match.stones_remaining = spp

        # Get camera stream start times for all cameras on this field
        sync_match_field_ref(tournament_url, match)
        field_obj = resolve_match_field_obj(tournament_url, match)
        if field_obj and field_obj.camera:
            from app.utils.camera_helpers import get_all_camera_stream_starts

            stream_starts = get_all_camera_stream_starts(field_obj)
            if stream_starts:
                replace_match_stream_starts(match, stream_starts)

        db.session.commit()

        # Update predicted times first
        try:
            recompute_all_match_times(tournament_url)
            db.session.commit()
        except Exception:
            # Preserve existing behavior: don't fail match start if scheduling update fails
            pass

        return Ok(match)
