"""Per-event team and player registrations.

Defines :class:`TeamRegistration` and :class:`PlayerRegistration`.
Both tables are scoped *either* to a single tournament (``event``) *or*
to a league (``league_id``), never both - a `CHECK` constraint
enforces the invariant. Use
:func:`app.services.registration_resolver.team_registrations_for_tournament`
(and friends) to query in a way that handles both scopes
transparently.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from app.models.base import db
from app.utils.datetime_helpers import now_utc_naive
from app.models.constants import (
    SHA256_HEX_LEN,
    SHORT_CODE_LEN,
    SHORT_NAME_LEN,
    SHORTNAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
)
from app.domain.enums import RegistrationStatus, TeamRegistrationStatus

CENTS_PER_UNIT = Decimal(100)
CURRENCY_QUANTUM = Decimal("0.01")


class PaidStateMixin:
    """Payment state derived from a registration's entry and payments.

    ``applied_entry`` and ``payments`` are view-only relationships, so after
    changing entries or payments in an open transaction, expire them on the
    registration before reading these properties.
    """

    @property
    def paid(self) -> bool:
        """Whether an applied :class:`~app.models.payments.PaidEntry` covers this registration."""
        return self.applied_entry is not None

    @property
    def amount_paid(self) -> Decimal:
        """Manual payments plus Stripe payments for the covering entry, in currency units."""
        entry_payments = self.applied_entry.payments if self.applied_entry is not None else []
        total_cents = sum(payment.amount_cents for payment in self.payments) + sum(
            payment.amount_cents for payment in entry_payments if payment.stripe_checkout_session_id is not None
        )
        return (Decimal(total_cents) / CENTS_PER_UNIT).quantize(CURRENCY_QUANTUM)

    @property
    def paid_at(self) -> datetime | None:
        """When the covering entry was applied, or ``None`` while unpaid."""
        return self.applied_entry.applied_at if self.applied_entry is not None else None


class TeamRegistration(PaidStateMixin, db.Model):  # type: ignore[misc]
    """A team's registration in a tournament or league.

    Tracks the team's pseudonym (display name for this event) and
    confirmation status. Payment state comes from :class:`PaidStateMixin`.
    Either ``event`` or ``league_id`` is set, never both.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug, or ``None`` for league registrations.
        league_id: League URL slug, or ``None`` for event registrations.
        team: ID of the registering team.
        pseudonym: Team display name specific to this event / league.
        shortname: Optional short alias used in space-constrained UI
            (schedule cells, bracket lines, match cards). ``None`` means
            "fall back to truncating the pseudonym".
        status: Registration status
            (:class:`~app.domain.enums.TeamRegistrationStatus`).
        registered_at: Timestamp of initial registration.
        applied_entry: The applied entry covering this registration.
        payments: Payments recorded against this registration.
    """

    __tablename__ = "team_registrations"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True)
    league_id = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True)
    team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False)
    pseudonym = db.Column(db.String(SHORT_NAME_LEN), nullable=False)  # Team name for this tournament
    shortname = db.Column(db.String(SHORTNAME_LEN), nullable=True)  # Optional short alias for layout-constrained UI
    status = db.Column(
        db.Enum(TeamRegistrationStatus), default=TeamRegistrationStatus.CONFIRMED
    )  # CONFIRMED, CANCELLED
    registered_at = db.Column(db.DateTime, default=now_utc_naive)
    applied_entry = db.relationship(
        "PaidEntry",
        primaryjoin="and_(PaidEntry.team_registration_id == TeamRegistration.id, PaidEntry.status == 'APPLIED')",
        foreign_keys="PaidEntry.team_registration_id",
        viewonly=True,
        uselist=False,
        lazy="joined",
    )
    payments = db.relationship(
        "Payment",
        primaryjoin="Payment.team_registration_id == TeamRegistration.id",
        foreign_keys="Payment.team_registration_id",
        viewonly=True,
    )

    __table_args__ = (
        # Exactly one of (event, league_id) must be set. Mirrors the same
        # invariant on Tournament/PenaltyType so every "scope" column pair
        # in the schema is enforced identically. A row with both set, or
        # neither set, is a data error and silently breaks any query that
        # filters by one or the other.
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL     AND league_id IS NOT NULL)",
            name="ck_team_registrations_event_league_mutual_exclusive",
        ),
    )


class PlayerRegistration(PaidStateMixin, db.Model):  # type: ignore[misc]
    """An individual player's registration in a tournament or league.

    Links a :class:`~app.models.user.Player` to an event (optionally via a
    :class:`~app.models.user.Team`), tracking jersey details and waiver
    signature. Payment state comes from :class:`PaidStateMixin`.

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
        applied_entry: The applied entry covering this registration.
        payments: Payments recorded against this registration.
        waiver_legal_name_signature: The player's legal name as typed in the
            waiver signature field.  Never expose outside player/TO contexts.
        waiver_legal_name_signature_sha256: SHA-256 hex digest of the waiver
            file bytes at the time of signing.
        waiver_signature_submitted_at: Server timestamp of waiver submission.
    """

    __tablename__ = "player_registrations"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True)
    league_id = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True)
    player = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False)
    team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=True)  # null for unattached
    jersey_number = db.Column(db.String(SHORT_CODE_LEN))
    jersey_name = db.Column(db.String(SHORT_NAME_LEN))  # Player name for this tournament
    status = db.Column(db.Enum(RegistrationStatus), default=RegistrationStatus.PENDING_TEAM_APPROVAL)
    registered_at = db.Column(db.DateTime, default=now_utc_naive)
    applied_entry = db.relationship(
        "PaidEntry",
        primaryjoin="and_(PaidEntry.player_registration_id == PlayerRegistration.id, PaidEntry.status == 'APPLIED')",
        foreign_keys="PaidEntry.player_registration_id",
        viewonly=True,
        uselist=False,
        lazy="joined",
    )
    payments = db.relationship(
        "Payment",
        primaryjoin="Payment.player_registration_id == PlayerRegistration.id",
        foreign_keys="Payment.player_registration_id",
        viewonly=True,
    )

    # signature of the current waiver.
    # Never send this field to non-player/non-TO contexts.
    waiver_legal_name_signature = db.Column(db.Text)
    # SHA-256 of the waiver file at the moment the player signed.
    waiver_legal_name_signature_sha256 = db.Column(db.String(SHA256_HEX_LEN))
    # Server timestamp when the signature was submitted.
    waiver_signature_submitted_at = db.Column(
        db.DateTime,
        default=now_utc_naive,
        nullable=True,
    )

    __table_args__ = (
        # Same exactly-one-of-(event, league_id) invariant as TeamRegistration.
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL     AND league_id IS NOT NULL)",
            name="ck_player_registrations_event_league_mutual_exclusive",
        ),
        # One settled or invited roster spot per event. A historical player may
        # also have a single REQUESTED row beside an invitation. Rejected and
        # cancelled rows are kept and are not unique.
        db.Index(
            "uq_player_registrations_player_event",
            "player",
            "event",
            unique=True,
            sqlite_where=sa.text("status NOT IN ('REQUESTED', 'REJECTED', 'CANCELLED')"),
        ),
        db.Index(
            "uq_player_registrations_player_event_requested",
            "player",
            "event",
            unique=True,
            sqlite_where=sa.text("status = 'REQUESTED'"),
        ),
    )
