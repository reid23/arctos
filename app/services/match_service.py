"""High-level match lifecycle operations: start, end, finalise.

Routes call into ``MatchService`` for any non-trivial transition.  The
service orchestrates the model mutations, eligibility checks, dual-write
roster updates, and downstream schedule recomputation that follow each
transition; it does not own match-action endpoints (those live in
``match_actions_service``).

Like the other services in this package, ``MatchService`` is a
``@dataclass(frozen=True)`` with ``@staticmethod`` methods - call them as
``MatchService.start_match(...)`` - and returns ``Result[T, ArctosError]``
rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, List, Optional

from app.error_values import Err, Ok, Result, allow_Q
from app.utils.datetime_helpers import now_utc_naive
from app.exceptions import (
    ArctosError,
    NotFoundError,
    ValidationError,
)

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
        from models import Match, db
        from app.services._common import get_tournament_or_err
        from app.domain.enums import MatchStatus
        from app.utils.scheduling import recompute_all_match_times

        if not match_id:
            return Err(ValidationError("Match ID required"))

        match = Match.query.get(match_id)
        if not match or match.event != tournament_url:
            return Err(NotFoundError("Match not found"))

        from app.services.match_start_eligibility import get_can_start_and_reasons

        can_start, block_reasons, _ = get_can_start_and_reasons(tournament_url, match, user)
        if not can_start:
            msg = block_reasons[0] if block_reasons else "Cannot start this match."
            return Err(ValidationError(msg))

        raw_team1 = (team1_players_csv or "").strip()
        raw_team2 = (team2_players_csv or "").strip()
        team1_players = [pid for pid in (raw_team1.split(",") if raw_team1 else []) if pid]
        team2_players = [pid for pid in (raw_team2.split(",") if raw_team2 else []) if pid]

        overlap = set(team1_players) & set(team2_players)
        if overlap:
            return Err(ValidationError("A player cannot be selected for both teams"))

        tournament_obj = get_tournament_or_err(tournament_url).Q()
        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament_obj)
        max_roster = getattr(cfg, "max_team_size_field", None) if cfg else None
        try:
            max_roster = int(max_roster) if max_roster is not None else None
        except Exception:
            max_roster = None
        if max_roster and (len(team1_players) > max_roster or len(team2_players) > max_roster):
            return Err(ValidationError("Too many players selected for a team"))

        team1_players = _dedup(team1_players)
        team2_players = _dedup(team2_players)

        # Mutations start here (after validation)
        match.status = MatchStatus.IN_PROGRESS
        # Use UTC time (stored as naive in DB, treated as UTC)
        match.confirmed_start_time = now_utc_naive()

        match.initial_notes = match_notes or ""
        from app.services.dual_write import set_match_players

        set_match_players(match, team1_players, team2_players)
        match.started_by = user.id
        match.started_at = now_utc_naive()

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

        db.session.commit()

        # Update predicted times first
        try:
            recompute_all_match_times(tournament_url)
            db.session.commit()
        except Exception:
            # Preserve existing behavior: don't fail match start if scheduling update fails
            pass

        return Ok(match)
