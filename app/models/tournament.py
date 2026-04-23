"""SQLAlchemy models for tournaments and related entities (TOs, fields, tags)."""

from __future__ import annotations

from datetime import datetime

from app.models.base import db
from app.models.constants import (
    LONG_NAME_LEN,
    SHORT_CODE_LEN,
    SHORT_NAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
)


class Tournament(db.Model):
    """A Jugger tournament (event) managed through Arctos.

    A tournament belongs either to a :class:`~app.models.league.League`
    (when ``league_id`` is set) or stands alone (when ``registrable_config_id``
    is set directly).  Exactly one of these must be non-null, enforced by a
    database check constraint.

    Attributes:
        url: URL slug used as the primary key and public identifier.
        name: Human-readable tournament name.
        start_date: Date and time the tournament begins (UTC, naive).
        end_date: Date and time the tournament ends, or ``None``.
        location: Venue name or address.
        n_max_teams: Maximum number of registered teams allowed.
        max_team_size_roster: Maximum players on a team's full roster.
        max_team_size_field: Maximum players allowed on the field at once.
        max_field_size: Maximum total players per side on the field.
        schedule_published: Whether the match schedule is visible to the public.
        league_id: Foreign key to the parent league, or ``None`` for standalone
            tournaments.
        head_refs_allowed_list: Comma-separated player IDs permitted as head
            refs.
        head_refs_allow_reffing_teams: When ``True``, reffing teams and their
            members may also head-ref.
        head_refs_allow_anyone: When ``True``, any registered participant may
            head-ref.
        bracket: TOML string defining bracket visualisation config.
        about: Markdown description shown on the tournament homepage.
        published: Whether the tournament is publicly visible.
        registrable_config_id: FK to the registration config for standalone
            tournaments.
        registrable_config: Relationship to the
            :class:`~app.models.registrable_config.RegistrableConfig`.
    """

    __tablename__ = "tournaments"

    url = db.Column(db.String(URL_SLUG_LEN), primary_key=True)
    name = db.Column(db.String(LONG_NAME_LEN), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=True)
    location = db.Column(db.String(LONG_NAME_LEN))
    n_max_teams = db.Column(db.Integer)
    max_team_size_roster = db.Column(db.Integer)  # Maximum players on team roster
    max_team_size_field = db.Column(db.Integer)  # Maximum players on field at once
    max_field_size = db.Column(db.Integer)
    schedule_published = db.Column(db.Boolean, default=False)
    league_id = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True
    )
    head_refs_allowed_list = db.Column(
        db.Text
    )  # comma-separated list of allowed usernames
    head_refs_allow_reffing_teams = db.Column(
        db.Boolean, default=False
    )  # allow reffing teams and their members
    head_refs_allow_anyone = db.Column(
        db.Boolean, default=False
    )  # allow anyone registered
    bracket = db.Column(db.Text)  # TOML string defining bracket visualizations

    # Per-event fields (every tournament)
    about = db.Column(db.Text)
    published = db.Column(db.Boolean, default=False, nullable=False)

    # Registration config: used only when league_id is null (standalone tournament).
    # When league_id is set, use league's config. Mutual exclusivity enforced by constraint.
    registrable_config_id = db.Column(
        db.Integer,
        db.ForeignKey("registrable_configs.id", ondelete="CASCADE"),
        nullable=True,
    )
    registrable_config = db.relationship(
        "RegistrableConfig",
        backref="tournaments",
        foreign_keys=[registrable_config_id],
    )

    __table_args__ = (
        db.CheckConstraint(
            "(league_id IS NULL AND registrable_config_id IS NOT NULL) OR "
            "(league_id IS NOT NULL AND registrable_config_id IS NULL)",
            name="ck_tournament_reg_config_mutual_exclusive",
        ),
    )


class TO(db.Model):
    """A Tournament Organiser assignment linking a user to an event or league.

    Both players and teams may be TOs.  A TO record grants administrative
    permissions over the referenced tournament or league.

    Attributes:
        id: Auto-increment primary key.
        user_id: ID of the player or team acting as TO.
        user_type: ``"player"`` or ``"team"``.
        event: Tournament URL slug this TO manages, or ``None`` for league TOs.
        league_id: League URL slug this TO manages, or ``None`` for event TOs.
    """

    __tablename__ = "tos"
    __table_args__ = (
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR "
            "(event IS NULL AND league_id IS NOT NULL)",
            name="ck_to_event_league_mutual_exclusive",
        ),
        db.CheckConstraint(
            "user_type IN ('player', 'team')",
            name="ck_to_user_type_allowed_values",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(USER_ID_LEN), nullable=False)  # Player or Team ID
    user_type = db.Column(
        db.String(SHORT_CODE_LEN), nullable=False
    )  # 'player' or 'team'
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True
    )
    league_id = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True
    )


class Field(db.Model):
    """A playing field (court) within a tournament.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug this field belongs to.
        name: Display name for the field (e.g. ``"Field A"``).
        camera: JSON array of camera stream URLs, or a single URL string for
            backwards compatibility.
    """

    __tablename__ = "fields"
    __table_args__ = (db.UniqueConstraint("event", "name", name="uq_field_event_name"),)

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=False
    )
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    camera = db.Column(
        db.Text
    )  # JSON array of camera URLs (or single URL for backward compatibility)


class Tag(db.Model):
    """A schedule tag that associates a team with a named placeholder.

    Tags allow Arctos Schedule Script expressions to refer to a team
    symbolically (e.g. ``[GroupA::winner]``) rather than by their ID.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug this tag belongs to.
        name: Tag name used in ASS expressions.
        team: ID of the team assigned to this tag, or ``None`` if unresolved.
    """

    __tablename__ = "tags"
    __table_args__ = (db.UniqueConstraint("event", "name", name="uq_tag_event_name"),)

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=False
    )
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=True)
