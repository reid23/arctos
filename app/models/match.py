"""SQLAlchemy models for matches, scored points, and match notes."""

from __future__ import annotations

import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import validates

from app.domain.enums import (
    MatchStatus,
    ScheduleType,
    WinnerSide,
    SetType,
    parse_enum,
    MatchNoteTarget,
)
from app.models.base import db
from app.models.constants import (
    LONG_NAME_LEN,
    LONG_URL_LEN,
    SHORT_CODE_LEN,
    SHORT_NAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
    UUID_LEN,
)
from app.error_values import Some
from app.utils.datetime_helpers import now_utc_naive


class Match(db.Model):
    """A single scheduled match between two teams at a tournament.

    Matches progress through a lifecycle tracked by :attr:`status`.  Dynamic
    scheduling (``SAFE`` / ``FAST``) recomputes :attr:`nominal_start_time`
    based on predecessor match outcomes and the :attr:`schedule_type`.

    Referees and team rosters are stored in the ``match_referees`` and
    ``match_players`` join tables; access them through the helpers in
    :mod:`app.services.dual_write` rather than by attribute on this model.

    Attributes:
        uuid: UUID primary key, auto-generated.
        name: Human-readable match name.
        event: Tournament URL slug (FK).
        team1: ID of the first team, or ``None`` while unresolved.
        team2: ID of the second team, or ``None`` while unresolved.
        team1_initial: ASS expression or label for team1 before resolution.
        team2_initial: ASS expression or label for team2 before resolution.
        field: Name of the field (court) where the match takes place.
        scheduled_start_time: Originally-scheduled start time. Stable across
            dynamic recomputation, so time-based dependency edges between
            matches don't drift as :attr:`nominal_start_time` is updated.
        nominal_start_time: Scheduled start time (may be updated dynamically).
        confirmed_start_time: Actual start time once the match begins.
        completed_time: Time the match ended.
        nominal_length: Planned duration in minutes.
        schedule_type: One of the :class:`~app.domain.enums.ScheduleType` values.
        set_type: Scoring mode (:class:`~app.domain.enums.SetType`).
        ribbon: ``True`` for ribbon (exhibition) games not counted in standings.
        nsets: Number of sets required to win the match.
        status: Current lifecycle state (:class:`~app.domain.enums.MatchStatus`).
        initial_notes: Free-text notes set when the match is created.
        started_by: User ID of whoever started the match.
        started_at: Timestamp when the match was started.
        stones_per_set: Number of stones per set for STONES-mode matches.
        stones_remaining: Stones remaining in the current set.
        finalized_by: User ID of whoever finalised the match.
        final_notes: Free-text notes submitted at finalisation.
        match_winner: Which side won (:class:`~app.domain.enums.WinnerSide`).
        team1_signature: Signature data submitted by team1 at finalisation.
        team2_signature: Signature data submitted by team2 at finalisation.
        finalized_at: Timestamp when the match was finalised.
        ready_to_start: Flag set when all preconditions are met (dynamic mode).
        ready_to_start_at: Timestamp when :attr:`ready_to_start` was set.
        camera_stream_starts: JSON object mapping camera index to stream start
            time (ISO format).
        previous_match: UUID of the preceding match in a chain.
        next_match: UUID of the following match in a chain.
        skip_condition: DSL expression evaluated to decide if this match should
            be skipped automatically.
    """

    __tablename__ = "matches"
    __table_args__ = (
        db.Index(
            "unique_with_field",
            "name",
            "event",
            "field",
            unique=True,
            sqlite_where=sa.text("schedule_type IN ('BREAK', 'JOIN')"),
        ),
        db.Index(
            "unique_without_field",
            "name",
            "event",
            unique=True,
            sqlite_where=sa.text("schedule_type NOT IN ('BREAK', 'JOIN')"),
        ),
    )

    uuid = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(LONG_NAME_LEN), nullable=False)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=False)
    team1 = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"))
    team2 = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"))
    team1_initial = db.Column(db.String(LONG_NAME_LEN))
    team2_initial = db.Column(db.String(LONG_NAME_LEN))
    field = db.Column(db.String(SHORT_NAME_LEN))
    scheduled_start_time = db.Column(db.DateTime)
    nominal_start_time = db.Column(db.DateTime)
    confirmed_start_time = db.Column(db.DateTime)
    completed_time = db.Column(db.DateTime)
    nominal_length = db.Column(db.Integer)  # minutes
    schedule_type = db.Column(db.Enum(ScheduleType), default=ScheduleType.STATIC)  # STATIC, SAFE, FAST, BREAK, JOIN
    set_type = db.Column(db.Enum(SetType), default=SetType.SETS)  # SETS, STONES (only for non-BREAK/JOIN matches)
    ribbon = db.Column(db.Boolean, default=False)  # True if this is a ribbon game (not counted in results)
    nsets = db.Column(db.Integer)
    status = db.Column(db.Enum(MatchStatus), default=MatchStatus.NOT_STARTED)  # NOT_STARTED, IN_PROGRESS, COMPLETED
    initial_notes = db.Column(db.Text)  # notes (initial match notes, distinct from MatchNote objects)
    started_by = db.Column(db.String(USER_ID_LEN))  # user ID who started the match
    started_at = db.Column(db.DateTime)  # when match started
    stones_per_set = db.Column(db.Integer)  # for STONES matches
    stones_remaining = db.Column(db.Integer)  # for STONES matches
    finalized_by = db.Column(db.String(USER_ID_LEN))  # user ID who finalized the match
    final_notes = db.Column(db.Text)  # final notes
    match_winner = db.Column(db.Enum(WinnerSide))  # 'TEAM1' or 'TEAM2'
    team1_signature = db.Column(db.Text)  # signature data
    team2_signature = db.Column(db.Text)  # signature data
    finalized_at = db.Column(db.DateTime)  # when match was finalized
    ready_to_start = db.Column(db.Boolean, default=False)  # flag for dynamic scheduling
    ready_to_start_at = db.Column(db.DateTime)  # when ready_to_start was set
    camera_stream_starts = db.Column(db.Text)  # JSON object mapping camera_index to stream start time (ISO format)
    previous_match = db.Column(db.String(UUID_LEN), db.ForeignKey("matches.uuid"), nullable=True)
    next_match = db.Column(db.String(UUID_LEN), db.ForeignKey("matches.uuid"), nullable=True)
    skip_condition = db.Column(db.Text, default="false")  # DSL expression that determines if match should be skipped

    # Relationships
    previous_match_obj = db.relationship(
        "Match",
        foreign_keys=[previous_match],
        remote_side=[uuid],
        post_update=True,
        backref="previous_of",
    )
    next_match_obj = db.relationship(
        "Match",
        foreign_keys=[next_match],
        remote_side=[uuid],
        post_update=True,
        backref="next_of",
    )

    @validates("name")
    def _strip_name(self, key: str, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def get_skip_condition_dependencies(self) -> dict[str, set[str]]:
        """
        Analyze the skip condition to determine which matches it depends on.

        Returns:
            Dictionary with keys:
            - "direct": Set of match names that must be completed (winner/loser determined)
              for this skip condition to evaluate fully
            - "skip_condition": Set of match names whose status must be known for (is-skipped MATCH)
              to evaluate

        Example:
            If skip_condition is "(== 0 (losses [Match1::winner]))", this will return:
            {"direct": {"Match1"}, "skip_condition": set()}

            If skip_condition is "(is-skipped {Match2})", this will return:
            {"direct": set(), "skip_condition": {"Match2"}}
        """
        from app.utils.dsl_dependency_analyzer import MatchDependencyAnalyzer

        if not self.skip_condition:
            return {"direct": set(), "skip_condition": set()}

        analyzer = MatchDependencyAnalyzer(self.event)
        return analyzer.analyze(self.skip_condition)

    team1_registration = db.relationship(
        "TeamRegistration",
        primaryjoin="and_(Match.team1 == foreign(TeamRegistration.team), Match.event == TeamRegistration.event)",
        uselist=False,
        viewonly=True,
    )
    team2_registration = db.relationship(
        "TeamRegistration",
        primaryjoin="and_(Match.team2 == foreign(TeamRegistration.team), Match.event == TeamRegistration.event)",
        uselist=False,
        viewonly=True,
    )

    @property
    def winner_team_id(self) -> str | None:
        """Return the team ID of the match winner, or ``None`` if undecided."""
        match parse_enum(WinnerSide, getattr(self, "match_winner", None)):
            case Some(WinnerSide.TEAM1):
                return self.team1
            case Some(WinnerSide.TEAM2):
                return self.team2
            case _:
                return None

    @property
    def loser_team_id(self) -> str | None:
        """Return the team ID of the match loser, or ``None`` if undecided."""
        match parse_enum(WinnerSide, getattr(self, "match_winner", None)):
            case Some(WinnerSide.TEAM1):
                return self.team2
            case Some(WinnerSide.TEAM2):
                return self.team1
            case _:
                return None

    @property
    def is_time_finalized(self) -> bool:
        """True when start time is locked: status is TIME_FINALIZED or any later state (READY_TO_START, IN_PROGRESS, COMPLETED, SKIPPED)."""
        if self.status is None:
            return False
        return self.status != MatchStatus.NOT_STARTED

    def finalize(self) -> None:
        """Mark this match as finalised and set completion metadata.

        Sets :attr:`finalized_at` to the current UTC time.  For ``JOIN``
        and ``BREAK`` schedule types the method also transitions the match
        to ``COMPLETED`` and calculates :attr:`completed_time`:

        * ``JOIN``: completed at :attr:`nominal_start_time`.
        * ``BREAK``: completed at ``nominal_start_time + nominal_length``.

        For all other schedule types the caller is responsible for setting
        :attr:`status` and :attr:`completed_time`.
        """
        self.finalized_at = now_utc_naive()
        if self.schedule_type in (ScheduleType.JOIN, ScheduleType.BREAK):
            self.confirmed_start_time = self.nominal_start_time
            self.status = MatchStatus.COMPLETED
            self.completed_time = (
                self.nominal_start_time
                if self.schedule_type == ScheduleType.JOIN
                else self.nominal_start_time + timedelta(minutes=self.nominal_length)
            )


class Point(db.Model):
    """A single scored point (or stone set) within a match.

    Each ``Point`` record captures who won the point, when it was scored,
    and optional footage / stream metadata for video review.

    Attributes:
        uuid: UUID primary key, auto-generated.
        match: UUID FK of the parent :class:`Match`.
        winner: Winning side (``"TEAM1"`` or ``"TEAM2"``).
        rerolled: ``True`` if this point was rerolled (overridden).
        stamp: Timestamp when the point was scored.
        end_stamp: Timestamp when the point ended (for duration tracking).
        footage: URL or path to the footage clip for this point.
        length: Duration of this point as a :class:`~datetime.timedelta`.
        nstones: Number of stones scored (``STONES`` mode only).
        stones_at_start: Stones remaining at the start of this point.
        rerollreason: Reason text explaining why the point was rerolled.
        set_number: Which set this point belongs to (1-indexed).
        notes: Free-text notes for this specific point.
    """

    __tablename__ = "points"

    uuid = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    match = db.Column(db.String(UUID_LEN), db.ForeignKey("matches.uuid"), nullable=False)
    winner = db.Column(db.String(SHORT_CODE_LEN))  # TEAM1, TEAM2
    rerolled = db.Column(db.Boolean, default=False)
    stamp = db.Column(db.DateTime, default=now_utc_naive)
    end_stamp = db.Column(db.DateTime)
    footage = db.Column(db.String(LONG_URL_LEN))
    length = db.Column(db.Interval)
    nstones = db.Column(db.Integer)
    stones_at_start = db.Column(db.Integer)  # Stones remaining when this point started (for STONES matches)
    rerollreason = db.Column(db.Text)
    set_number = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)


class MatchNote(db.Model):
    """A referee or head-ref note attached to a match.

    Notes can be targeted at team1, team2, the match overall, or a specific
    player.  They may also reference a specific scored :class:`Point` and
    optionally link to a :class:`PenaltyType`.

    Attributes:
        uuid: UUID primary key, auto-generated.
        match: UUID FK of the parent :class:`Match`.
        text: Note content text.
        target: Which entity the note addresses
            (:class:`~app.domain.enums.MatchNoteTarget`).
        created_by: Player ID of the note author.
        created_at: Timestamp when the note was created.
        player_id: Optional player ID the note concerns.
        point_id: Optional UUID of the :class:`Point` this note relates to.
        penalty_type_id: Optional FK to a :class:`PenaltyType`.
    """

    __tablename__ = "match_notes"

    uuid = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    match = db.Column(db.String(UUID_LEN), db.ForeignKey("matches.uuid"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    target = db.Column(db.Enum(MatchNoteTarget, values_callable=lambda obj: [e.value for e in obj]))
    created_by = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc_naive)
    # Optional link to a specific player
    player_id = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"))
    # Optional link to a specific point
    point_id = db.Column(db.String(UUID_LEN), db.ForeignKey("points.uuid"))
    # Optional link to penalty type
    penalty_type_id = db.Column(db.Integer, db.ForeignKey("penalty_types.id"))

    # Relationships
    match_obj = db.relationship("Match", backref="match_notes")
    creator = db.relationship("Player", foreign_keys=[created_by])
    player = db.relationship("Player", foreign_keys=[player_id])
    point_obj = db.relationship("Point", foreign_keys=[point_id], backref="point_notes")
    penalty_type = db.relationship("PenaltyType")
