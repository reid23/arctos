"""Prepaid registration entries and the payments behind them.

Defines :class:`PaidEntry` (the right to one registration in one event or
league), :class:`PaidEntryEvent` (its append-only history), and
:class:`Payment` (money recorded against a registration).

A registration is paid exactly when an ``APPLIED`` entry points at it.
Entries outlive deregistration: they become ``AVAILABLE`` and are applied
again on re-registration, or move to another holder by transfer. Write
them only through :mod:`app.services.paid_entry_service`.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.domain.enums import PaidEntryAction, PaidEntrySource, PaidEntryStatus
from app.models.base import db
from app.models.constants import SHORT_CODE_LEN, SHORT_LABEL_LEN, SHORT_NAME_LEN, URL_SLUG_LEN, USER_ID_LEN
from app.utils.datetime_helpers import now_utc_naive


class PaidEntry(db.Model):  # type: ignore[misc]
    """The right to one team or player registration in an event or league.

    An entry is not a balance: it covers one registration whatever the fee
    is when it is used.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug, or ``None`` for league entries.
        league_id: League URL slug, or ``None`` for event entries.
        kind: ``"player"`` or ``"team"``.
        holder_player: Player holding a player entry.
        holder_team: Team holding a team entry.
        team_registration_id: Team registration this entry covers while
            ``APPLIED``.
        player_registration_id: Player registration this entry covers while
            ``APPLIED``.
        status: :class:`~app.domain.enums.PaidEntryStatus`.
        source: :class:`~app.domain.enums.PaidEntrySource`.
        transferred_from: Previous holder while a transfer is unclaimed;
            that holder may revoke it and the current holder may decline it.
        reserves_team_slot: Whether this unclaimed team entry counts toward
            the scope's team cap.
        created_at: When the entry was created.
        applied_at: When the entry was last applied to a registration.
    """

    __tablename__ = "paid_entries"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True)
    league_id = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True)
    kind = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    holder_player = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=True, index=True)
    holder_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=True, index=True)
    team_registration_id = db.Column(db.Integer, db.ForeignKey("team_registrations.id"), nullable=True)
    player_registration_id = db.Column(db.Integer, db.ForeignKey("player_registrations.id"), nullable=True)
    status = db.Column(db.Enum(PaidEntryStatus), nullable=False)
    source = db.Column(db.Enum(PaidEntrySource), nullable=False)
    transferred_from = db.Column(db.String(USER_ID_LEN), nullable=True)
    reserves_team_slot = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    applied_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL AND league_id IS NOT NULL)",
            name="ck_paid_entries_event_league_mutual_exclusive",
        ),
        db.CheckConstraint(
            "(kind = 'player' AND holder_player IS NOT NULL AND holder_team IS NULL"
            " AND team_registration_id IS NULL AND NOT reserves_team_slot)"
            " OR (kind = 'team' AND holder_team IS NOT NULL AND holder_player IS NULL"
            " AND player_registration_id IS NULL)",
            name="ck_paid_entries_kind_holder",
        ),
        db.CheckConstraint(
            "(status = 'APPLIED') = (COALESCE(team_registration_id, player_registration_id) IS NOT NULL)",
            name="ck_paid_entries_applied_has_registration",
        ),
        db.Index(
            "uq_paid_entries_holder_scope",
            "kind",
            sa.func.coalesce(holder_player, holder_team),
            sa.func.coalesce(event, league_id),
            unique=True,
            sqlite_where=sa.text("status != 'VOIDED'"),
        ),
        db.Index("ix_paid_entries_team_registration_id", "team_registration_id"),
        db.Index("ix_paid_entries_player_registration_id", "player_registration_id"),
    )

    @property
    def holder(self) -> str:
        return self.holder_player if self.kind == "player" else self.holder_team


class PaidEntryEvent(db.Model):  # type: ignore[misc]
    """One change in a :class:`PaidEntry`'s life, kept for transaction history.

    Holder and actor ids are plain strings so history survives account
    changes; the entry's ``kind`` says whether holders are players or teams.

    Attributes:
        id: Auto-increment primary key.
        entry_id: The entry that changed.
        action: :class:`~app.domain.enums.PaidEntryAction`.
        from_holder: Holder before a transfer-like action.
        to_holder: Holder after a transfer-like action.
        actor_user_id: Account that made the change, or ``None`` when the
            holder acted through ordinary registration.
        actor_user_type: ``"player"`` or ``"team"`` for ``actor_user_id``.
        created_at: When the change happened.
    """

    __tablename__ = "paid_entry_events"

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("paid_entries.id"), nullable=False, index=True)
    action = db.Column(db.Enum(PaidEntryAction), nullable=False)
    from_holder = db.Column(db.String(USER_ID_LEN), nullable=True)
    to_holder = db.Column(db.String(USER_ID_LEN), nullable=True)
    actor_user_id = db.Column(db.String(USER_ID_LEN), nullable=True)
    actor_user_type = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)


class Payment(db.Model):  # type: ignore[misc]
    """Money recorded against one team or player registration.

    Attributes:
        id: Auto-increment primary key.
        team_registration_id: Team registration paid for.
        player_registration_id: Player registration paid for.
        entry_id: Entry this payment bought, if any. A partial payment has
            none.
        amount_cents: Amount in cents.
        method: How payment was made (e.g. ``"cash"``).
        reference: Transaction id, cheque number, etc.
        notes: Free-text notes. Organizer-only.
        created_at: When the payment was first recorded.
        updated_at: When the payment record last changed.
    """

    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    team_registration_id = db.Column(db.Integer, db.ForeignKey("team_registrations.id"), nullable=True, index=True)
    player_registration_id = db.Column(db.Integer, db.ForeignKey("player_registrations.id"), nullable=True, index=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("paid_entries.id"), nullable=True, index=True)
    amount_cents = db.Column(db.Integer, default=0, nullable=False)
    method = db.Column(db.String(SHORT_LABEL_LEN))
    reference = db.Column(db.String(SHORT_NAME_LEN))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_utc_naive, onupdate=now_utc_naive, nullable=False)

    __table_args__ = (
        db.CheckConstraint(
            "(team_registration_id IS NULL) != (player_registration_id IS NULL)",
            name="ck_payments_one_registration",
        ),
        db.CheckConstraint("amount_cents >= 0", name="ck_payments_amount_nonneg"),
    )
