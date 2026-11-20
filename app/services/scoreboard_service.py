"""Scoreboard state of a tournament field, live and after the fact.

Backs the public scoreboard (``/_api/scoreboard-state``, consumed by OBS
overlays), the public API's field scoreboard, and the offline scoreboard
overlay renderer. While a match is in progress on the field, the state
carries that match, its points, and the per-set scores; while the field is
idle, it carries the previous and next matches instead.

The live scoreboard and the offline renderer both build their active-match
state with :meth:`ScoreboardService.active_match_state`, so a rendered frame
shows exactly what the live overlay showed at that moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.domain.enums import STRUCTURAL_SCHEDULE_TYPES, MatchStatus, SetType, WinnerSide
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.utils.datetime_helpers import now_utc_naive, to_iso_z

# Stones tick down on global beat boundaries (multiples of this period since the
# Unix epoch). Must match the frontend scoreboard's beat.
STONES_BEAT = timedelta(seconds=1.5)
UNIX_EPOCH = datetime(1970, 1, 1)
DEFAULT_STONES_PER_SET = 100


@dataclass(frozen=True)
class ScoreboardSide:
    """How one side of a match is displayed on the scoreboard."""

    name: str | None
    shortname: str | None
    photo: str | None


@dataclass(frozen=True)
class ScoreboardMatch:
    match: "Match"
    team1: ScoreboardSide
    team2: ScoreboardSide


@dataclass(frozen=True)
class FieldScoreboard:
    """Scoreboard state of one field.

    Attributes:
        active: The match in progress, or ``None`` when the field is idle.
        points: The active match's points ordered by stamp.
        set_scores: The active match's ``set number -> (team1, team2)``
            points, ordered by set number.
        previous: Most recently completed match, only computed when idle
            (see :meth:`ScoreboardService.previous_and_next`).
        next: The match after *previous*, only computed when idle.
    """

    active: ScoreboardMatch | None
    points: list = field(default_factory=list)
    set_scores: dict[int, tuple[int, int]] = field(default_factory=dict)
    previous: ScoreboardMatch | None = None
    next: ScoreboardMatch | None = None


def set_scores(points) -> dict[int, tuple[int, int]]:
    """Points won by each side per set, ordered by set number.

    Rerolled points count for neither side; points without a set number are
    ignored.
    """
    scores: dict[int, list[int]] = {}
    for point in points:
        if not point.set_number:
            continue
        score = scores.setdefault(point.set_number, [0, 0])
        if point.rerolled:
            continue
        if point.winner == WinnerSide.TEAM1:
            score[0] += 1
        elif point.winner == WinnerSide.TEAM2:
            score[1] += 1
    return {number: (score[0], score[1]) for number, score in sorted(scores.items())}


@dataclass(frozen=True)
class ScoreboardTimelineEntry:
    """A scoreboard state and the (naive UTC) moment it starts being shown."""

    effective_at: datetime
    state: dict[str, Any]


def team_fields(team1: ScoreboardSide, team2: ScoreboardSide) -> dict[str, Any]:
    """The ``team1_*`` / ``team2_*`` fields of the ``scoreboard-state`` shape."""
    return {
        "team1_name": team1.name,
        "team2_name": team2.name,
        "team1_photo": team1.photo,
        "team2_photo": team2.photo,
        "team1_shortname": team1.shortname,
        "team2_shortname": team2.shortname,
    }


def _beat_index(moment: datetime) -> int:
    return (moment - UNIX_EPOCH) // STONES_BEAT


def _stones_elapsed(start: datetime, end: datetime) -> int:
    return max(_beat_index(end) - _beat_index(start), 0)


def _stones_remaining_at(started_points: list, moment: datetime) -> int | None:
    """Stones remaining at *moment*, mirroring the frontend scoreboard.

    During a point the count ticks down from that point's ``stones_at_start``;
    between points it holds at whatever the last completed point ended on.

    Args:
        started_points: Points with ``stamp <= moment``, ordered by ``stamp``.
        moment: Naive UTC time to evaluate at.
    """
    if started_points:
        latest = started_points[-1]
        ongoing = latest.end_stamp is None or latest.end_stamp > moment
        if ongoing and latest.stones_at_start is not None:
            return max(latest.stones_at_start - _stones_elapsed(latest.stamp, moment), 0)
    for point in reversed(started_points):
        if point.stones_at_start is not None and point.end_stamp is not None and point.end_stamp <= moment:
            return max(point.stones_at_start - _stones_elapsed(point.stamp, point.end_stamp), 0)
    return None


def _side(tournament, team_id: str | None, initial: str | None) -> ScoreboardSide:
    """Registration pseudonym, else team name, else the slot's initial."""
    from app.services.registration_resolver import team_registration_for_tournament
    from models import Team

    if not team_id:
        return ScoreboardSide(name=initial, shortname=None, photo=None)
    team = Team.query.get(team_id)
    registration = team_registration_for_tournament(tournament, team_id) if tournament else None
    if registration and registration.pseudonym:
        name = registration.pseudonym
    else:
        name = team.name if team else initial
    return ScoreboardSide(
        name=name,
        shortname=registration.shortname if registration and registration.shortname else None,
        photo=team.profile_photo if team and team.profile_photo else None,
    )


def _scoreboard_match(tournament, match) -> ScoreboardMatch:
    return ScoreboardMatch(
        match=match,
        team1=_side(tournament, match.team1, match.team1_initial),
        team2=_side(tournament, match.team2, match.team2_initial),
    )


@dataclass(frozen=True)
class ScoreboardService:
    @staticmethod
    def field_state(tournament_url: str, field_name: str, tz_offset_minutes: int | None = None) -> FieldScoreboard:
        """Scoreboard state of the field named *field_name* in *tournament_url*.

        Args:
            tz_offset_minutes: Viewer's offset from UTC; see :meth:`previous_and_next`.
        """
        from models import Match, Point, Tournament

        tournament = Tournament.query.get(tournament_url)
        active = Match.query.filter_by(event=tournament_url, field=field_name, status=MatchStatus.IN_PROGRESS).first()
        if active:
            points = Point.query.filter_by(match=active.uuid).order_by(Point.stamp).all()
            return FieldScoreboard(
                active=_scoreboard_match(tournament, active),
                points=points,
                set_scores=set_scores(points),
            )

        field_matches = Match.query.filter_by(event=tournament_url, field=field_name).all()
        previous, upcoming = ScoreboardService.previous_and_next(field_matches, now_utc_naive(), tz_offset_minutes)
        return FieldScoreboard(
            active=None,
            previous=_scoreboard_match(tournament, previous) if previous else None,
            next=_scoreboard_match(tournament, upcoming) if upcoming else None,
        )

    @staticmethod
    def previous_and_next(
        field_matches: list, now: datetime, tz_offset_minutes: int | None
    ) -> tuple["Match | None", "Match | None"]:
        """The matches an idle field's scoreboard shows as previous and next.

        Previous is the most recently completed match. Next follows
        previous's ``next_match`` chain past breaks, joins, and finished
        matches; when the chain doesn't lead to one (static matches are never
        chained), next is the first unfinished match after previous by
        ``scheduled_start_time``. Before any match has completed, next is the
        field's first unfinished match. Breaks, joins, and skipped matches are
        never shown.

        Args:
            field_matches: Every match on the field, including breaks and joins.
            now: Current naive UTC time.
            tz_offset_minutes: Viewer's offset from UTC (``local = utc + offset``).
                When given, a match is only shown if it falls on the viewer's
                current local day: previous by ``completed_time``, next by
                ``nominal_start_time``.
        """
        matches_by_uuid = {match.uuid: match for match in field_matches}
        games = sorted(
            (match for match in field_matches if match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES),
            key=lambda match: match.scheduled_start_time or datetime.max,
        )

        def is_upcoming(match) -> bool:
            return match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES and match.status not in (
                MatchStatus.COMPLETED,
                MatchStatus.SKIPPED,
            )

        def next_in_chain(match):
            visited = {match.uuid}
            link = match.next_match
            while link in matches_by_uuid and link not in visited:
                visited.add(link)
                linked = matches_by_uuid[link]
                if is_upcoming(linked):
                    return linked
                link = linked.next_match
            return None

        completed = [match for match in games if match.status == MatchStatus.COMPLETED and match.completed_time]
        previous = max(completed, key=lambda match: match.completed_time, default=None)
        if previous is None:
            upcoming = next((match for match in games if is_upcoming(match)), None)
        else:
            upcoming = next_in_chain(previous) or next(
                (match for match in games[games.index(previous) + 1 :] if is_upcoming(match)), None
            )

        if tz_offset_minutes is not None:
            offset = timedelta(minutes=tz_offset_minutes)
            today = (now + offset).date()

            def is_today(moment: datetime | None) -> bool:
                return moment is not None and (moment + offset).date() == today

            if previous and not is_today(previous.completed_time):
                previous = None
            if upcoming and not is_today(upcoming.nominal_start_time):
                upcoming = None
        return previous, upcoming

    @staticmethod
    def active_match_state(
        scoreboard_match: ScoreboardMatch,
        started_points: list,
        scored_points: list,
        stones_remaining: int | None,
    ) -> dict[str, Any]:
        """The ``scoreboard-state`` active-match state, without timing fields.

        Args:
            scoreboard_match: The match being shown.
            started_points: Points whose set should appear as a column.
            scored_points: Points eligible to count toward the score; rerolled
                points and points without a winner are ignored.
            stones_remaining: Stones-remaining value to show (STONES matches only).

        Returns:
            Dict with ``has_active_match``, ``match_id``, the team fields,
            ``sets``, ``scores_by_set`` (keyed by set number) and ``stones_info``.
        """
        match = scoreboard_match.match
        sets = sorted({p.set_number for p in started_points if p.set_number})
        scores = set_scores(scored_points)
        scores_by_set = {}
        for number in sets:
            team1_score, team2_score = scores.get(number, (0, 0))
            scores_by_set[number] = {"team1_score": team1_score, "team2_score": team2_score}
        stones_info = None
        if match.set_type == SetType.STONES:
            stones_info = {
                "stones_per_set": match.stones_per_set or DEFAULT_STONES_PER_SET,
                "stones_remaining": stones_remaining,
            }
        return {
            "has_active_match": True,
            "match_id": match.uuid,
            **team_fields(scoreboard_match.team1, scoreboard_match.team2),
            "scores_by_set": scores_by_set,
            "sets": sets,
            "stones_info": stones_info,
        }

    @staticmethod
    def timeline(scoreboard_match: ScoreboardMatch, points: list) -> Result[list[ScoreboardTimelineEntry], ArctosError]:
        """Every distinct scoreboard state the match went through, in order.

        At time ``t`` a point's set is shown once ``stamp <= t``; it counts
        toward the score once ``end_stamp <= t`` (rerolled points never count,
        since when the reroll happened isn't recorded). For STONES matches the
        stones count changes on each beat boundary crossed during a point.

        The first entry is effective from the match start (``started_at``, or
        the first point's ``stamp`` if that is earlier or ``started_at`` is
        unset). Consecutive identical states are collapsed.

        Each state has the same shape as the live ``scoreboard-state``
        active-match response, with ``timestamp`` set to the entry's
        effective time.

        Returns:
            ``Ok`` with the entries, or ``Err(ValidationError)`` when the match
            has neither a start time nor any points.
        """
        match = scoreboard_match.match
        stamped = sorted((p for p in points if p.stamp is not None), key=lambda p: p.stamp)
        start_candidates = [moment for moment in (match.started_at, stamped[0].stamp if stamped else None) if moment]
        if not start_candidates:
            return Err(ValidationError("Match has not started, so there is no scoreboard to render"))
        start = min(start_candidates)
        is_stones = match.set_type == SetType.STONES

        change_times = {start}
        for point in stamped:
            change_times.add(point.stamp)
            if point.end_stamp is not None:
                change_times.add(point.end_stamp)
            if is_stones and point.stones_at_start is not None:
                start_beat = _beat_index(point.stamp)
                last_beat = start_beat + point.stones_at_start
                if point.end_stamp is not None:
                    last_beat = min(last_beat, _beat_index(point.end_stamp))
                change_times.update(UNIX_EPOCH + beat * STONES_BEAT for beat in range(start_beat + 1, last_beat + 1))

        stones_before_first_point = next(
            (p.stones_at_start for p in stamped if p.stones_at_start is not None),
            match.stones_per_set or DEFAULT_STONES_PER_SET,
        )
        distinct_states: list[tuple[datetime, dict[str, Any]]] = []
        for moment in sorted(change_times):
            started = [p for p in stamped if p.stamp <= moment]
            scored = [p for p in started if p.end_stamp is not None and p.end_stamp <= moment]
            stones_remaining = None
            if is_stones:
                stones_remaining = _stones_remaining_at(started, moment)
                if stones_remaining is None:
                    stones_remaining = stones_before_first_point
            state = ScoreboardService.active_match_state(scoreboard_match, started, scored, stones_remaining)
            if distinct_states and distinct_states[-1][1] == state:
                continue
            distinct_states.append((moment, state))

        return Ok(
            [
                ScoreboardTimelineEntry(
                    effective_at=moment,
                    state={**state, "points_for_stones": None, "timestamp": to_iso_z(moment).unwrap()},
                )
                for moment, state in distinct_states
            ]
        )

    @staticmethod
    def match_timeline(tournament, match_id: str) -> Result[list[ScoreboardTimelineEntry], ArctosError]:
        """Load a match of *tournament* and compute its :meth:`timeline`.

        Returns:
            ``Err(NotFoundError)`` when the match doesn't belong to
            *tournament*; otherwise the result of :meth:`timeline`.
        """
        from models import Match, Point

        match = Match.query.filter_by(uuid=match_id, event=tournament.url).first()
        if match is None:
            return Err(NotFoundError("Match not found"))
        points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()
        return ScoreboardService.timeline(_scoreboard_match(tournament, match), points)
