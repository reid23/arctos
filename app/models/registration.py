"""SQLAlchemy models for team and player registrations."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.base import db
from app.models.constants import (
    SHA256_HEX_LEN,
    SHORT_CODE_LEN,
    SHORT_LABEL_LEN,
    SHORT_NAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
)
from app.domain.enums import RegistrationStatus, TeamRegistrationStatus


class TeamRegistration(db.Model):  # type: ignore[misc]
    """A team's registration in a tournament or league.

    Tracks the team's pseudonym (display name for this event), confirmation
    status, and payment details.  Either ``event`` or ``league_id`` is set,
    never both.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug, or ``None`` for league registrations.
        league_id: League URL slug, or ``None`` for event registrations.
        team: ID of the registering team.
        pseudonym: Team display name specific to this event / league.
        status: Registration status
            (:class:`~app.domain.enums.TeamRegistrationStatus`).
        registered_at: Timestamp of initial registration.
        paid: Whether the team registration fee has been paid.
        amount_paid: Amount paid so far (may be partial).
        paid_at: Timestamp of the most recent payment, or ``None``.
        payment_method: How payment was made (e.g. ``"cash"``, ``"stripe"``).
        payment_reference: Transaction ID, cheque number, etc.
        payment_notes: Free-text payment notes.
    """

    __tablename__ = "team_registrations"
    __table_args__ = (
        db.UniqueConstraint("event", "team", name="uq_team_reg_event_team"),
        db.UniqueConstraint("league_id", "team", name="uq_team_reg_league_team"),
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR "
            "(event IS NULL AND league_id IS NOT NULL)",
            name="ck_team_reg_event_league_mutual_exclusive",
        ),
        db.Index("ix_team_registrations_event", "event"),
        db.Index("ix_team_registrations_league_id", "league_id"),
        db.Index("ix_team_registrations_team", "team"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True
    )
    league_id = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True
    )
    team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False)
    pseudonym = db.Column(
        db.String(SHORT_NAME_LEN), nullable=False
    )  # Team name for this tournament
    status = db.Column(
        db.Enum(TeamRegistrationStatus),
        default=TeamRegistrationStatus.CONFIRMED,
        nullable=False,
    )  # CONFIRMED, CANCELLED
    registered_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    # Payment fields
    paid = db.Column(db.Boolean, default=False, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_method = db.Column(
        db.String(SHORT_LABEL_LEN)
    )  # e.g., cash, check, venmo, stripe
    payment_reference = db.Column(db.String(SHORT_NAME_LEN))  # txn id, check #, etc
    payment_notes = db.Column(db.Text)


class PlayerRegistration(db.Model):  # type: ignore[misc]
    """An individual player's registration in a tournament or league.

    Links a :class:`~app.models.user.Player` to an event (optionally via a
    :class:`~app.models.user.Team`), tracking jersey details, payment, and
    waiver signature.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug, or ``None`` for league registrations.
        league_id: League URL slug, or ``None`` for event registrations.
        player: ID of the registering player.
        team: ID of the team the player is registering under, or ``None`` for
            unattached players.
        jersey_number: Jersey number string for this event.
        jersey_name: Name printed on the player's jersey for this event.
        status: Registration lifecycle status
            (:class:`~app.domain.enums.RegistrationStatus`).
        registered_at: Timestamp of initial registration.
        paid: Whether the player registration fee has been paid.
        amount_paid: Amount paid so far.
        paid_at: Timestamp of the most recent payment, or ``None``.
        payment_method: How payment was made.
        payment_reference: Transaction ID or other reference.
        payment_notes: Free-text payment notes.
        waiver_legal_name_signature: The player's legal name as typed in the
            waiver signature field.  Never expose outside player/TO contexts.
        waiver_legal_name_signature_sha256: SHA-256 hex digest of the waiver
            file bytes at the time of signing.
        waiver_signature_submitted_at: Server timestamp of waiver submission.
    """

    __tablename__ = "player_registrations"
    __table_args__ = (
        db.UniqueConstraint("event", "player", name="uq_player_reg_event_player"),
        db.UniqueConstraint("league_id", "player", name="uq_player_reg_league_player"),
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR "
            "(event IS NULL AND league_id IS NOT NULL)",
            name="ck_player_reg_event_league_mutual_exclusive",
        ),
        db.Index("ix_player_registrations_event", "event"),
        db.Index("ix_player_registrations_league_id", "league_id"),
        db.Index("ix_player_registrations_player", "player"),
        db.Index("ix_player_registrations_team", "team"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True
    )
    league_id = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True
    )
    player = db.Column(
        db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False
    )
    team = db.Column(
        db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=True
    )  # null for unattached
    jersey_number = db.Column(db.String(SHORT_CODE_LEN))
    jersey_name = db.Column(
        db.String(SHORT_NAME_LEN)
    )  # Player name for this tournament
    status = db.Column(
        db.Enum(RegistrationStatus),
        default=RegistrationStatus.PENDING_TEAM_APPROVAL,
        nullable=False,
    )
    registered_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    # Payment fields
    paid = db.Column(db.Boolean, default=False, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_method = db.Column(db.String(SHORT_LABEL_LEN))
    payment_reference = db.Column(db.String(SHORT_NAME_LEN))
    payment_notes = db.Column(db.Text)

    # signature of the current waiver.
    # Never send this field to non-player/non-TO contexts.
    waiver_legal_name_signature = db.Column(db.Text)
    # SHA-256 of the waiver file at the moment the player signed.
    waiver_legal_name_signature_sha256 = db.Column(db.String(SHA256_HEX_LEN))
    # Server timestamp when the signature was submitted.
    waiver_signature_submitted_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=True,
    )
