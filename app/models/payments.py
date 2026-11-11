"""Prepaid registration entries, the payments behind them, and where money goes.

Defines :class:`PaidEntry` (the right to one registration in one event or
league), :class:`PaidEntryEvent` (its append-only history),
:class:`Payment` (money recorded for a registration or entry),
:class:`CheckoutHold` (a Stripe checkout in progress), and
:class:`PayoutAccount` (a player's Stripe connected account).

A registration is paid exactly when an ``APPLIED`` entry points at it.
Entries outlive deregistration: they become ``AVAILABLE`` and are applied
again on re-registration, or move to another holder by transfer. Write
them only through :mod:`app.services.paid_entry_service`.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.domain.enums import CheckoutHoldStatus, PaidEntryAction, PaidEntrySource, PaidEntryStatus
from app.models.base import db
from app.models.constants import (
    AUTH_STRING_LEN,
    SHORT_CODE_LEN,
    SHORT_LABEL_LEN,
    SHORT_NAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
)
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

    payments = db.relationship("Payment", viewonly=True)

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
    """Money recorded for a registration or an entry.

    An organizer's manual payment belongs to one registration. A Stripe
    payment belongs to the entry it bought, which may cover different
    registrations over time; it has no entry when the payer already held
    one by the time the payment completed.

    Attributes:
        id: Auto-increment primary key.
        team_registration_id: Team registration a manual payment is for.
        player_registration_id: Player registration a manual payment is for.
        entry_id: Entry this payment bought, if any. A partial payment has
            none.
        amount_cents: Amount in cents.
        method: How payment was made (e.g. ``"cash"``).
        reference: Transaction id, cheque number, etc.
        notes: Free-text notes. Organizer-only.
        stripe_checkout_session_id: Stripe Checkout Session that took a
            Stripe payment.
        stripe_payment_intent_id: Stripe PaymentIntent behind that session.
        stripe_account_id: Connected account the payment was charged on.
        currency: Lowercase ISO currency code of a Stripe payment.
        application_fee_cents: The platform's fee from a Stripe payment.
        payer_user_id: Account that paid through Stripe.
        payer_user_type: ``"player"`` or ``"team"`` for ``payer_user_id``.
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
    stripe_checkout_session_id = db.Column(db.String(AUTH_STRING_LEN), nullable=True, unique=True)
    stripe_payment_intent_id = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    stripe_account_id = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    currency = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    application_fee_cents = db.Column(db.Integer, default=0, nullable=False)
    payer_user_id = db.Column(db.String(USER_ID_LEN), nullable=True)
    payer_user_type = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_utc_naive, onupdate=now_utc_naive, nullable=False)

    __table_args__ = (
        db.CheckConstraint(
            "(stripe_checkout_session_id IS NULL"
            " AND (team_registration_id IS NULL) != (player_registration_id IS NULL))"
            " OR (stripe_checkout_session_id IS NOT NULL"
            " AND team_registration_id IS NULL AND player_registration_id IS NULL)",
            name="ck_payments_one_registration",
        ),
        db.CheckConstraint("amount_cents >= 0", name="ck_payments_amount_nonneg"),
    )


class CheckoutHold(db.Model):  # type: ignore[misc]
    """A Stripe Checkout Session in progress for one registrant.

    The hold keeps the registration form so the registration can complete
    when the payment does, and, for a new team registration, holds a place
    under the team cap until ``expires_at``. A pay-now hold names the
    existing registration it pays for instead. At most one hold per
    registrant and scope is ``OPEN``. Write holds only through
    :mod:`app.services.checkout_service`.

    Attributes:
        id: Auto-increment primary key.
        stripe_session_id: Stripe's ``cs_...`` id.
        stripe_account_id: Connected account the session charges on.
        event: Tournament URL slug, or ``None`` for league holds.
        league_id: League URL slug, or ``None`` for event holds.
        kind: ``"player"`` or ``"team"``.
        holder_player: Player being registered.
        holder_team: Team being registered.
        payer_user_id: Account that started the checkout.
        payer_user_type: ``"player"`` or ``"team"`` for ``payer_user_id``.
        form_payload: Registration form fields for a new registration.
        team_registration_id: Unpaid team registration a pay-now hold is for.
        player_registration_id: Unpaid player registration a pay-now hold
            is for.
        amount_cents: Amount charged, in cents.
        currency: Lowercase ISO currency code.
        application_fee_cents: The platform's fee.
        status: :class:`~app.domain.enums.CheckoutHoldStatus`.
        failure_message: Why the registration couldn't complete after
            payment, shown to the payer.
        created_at: When the checkout started.
        expires_at: When Stripe expires the session.
        completed_at: When the payment was recorded.
    """

    __tablename__ = "checkout_holds"

    id = db.Column(db.Integer, primary_key=True)
    stripe_session_id = db.Column(db.String(AUTH_STRING_LEN), nullable=False, unique=True)
    stripe_account_id = db.Column(db.String(AUTH_STRING_LEN), nullable=False)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=True)
    league_id = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("leagues.url"), nullable=True)
    kind = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    holder_player = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=True, index=True)
    holder_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=True, index=True)
    payer_user_id = db.Column(db.String(USER_ID_LEN), nullable=False)
    payer_user_type = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    form_payload = db.Column(db.JSON, nullable=True)
    team_registration_id = db.Column(db.Integer, db.ForeignKey("team_registrations.id"), nullable=True)
    player_registration_id = db.Column(db.Integer, db.ForeignKey("player_registrations.id"), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    application_fee_cents = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.Enum(CheckoutHoldStatus), nullable=False)
    failure_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL AND league_id IS NOT NULL)",
            name="ck_checkout_holds_event_league_mutual_exclusive",
        ),
        db.CheckConstraint(
            "(kind = 'player' AND holder_player IS NOT NULL AND holder_team IS NULL"
            " AND team_registration_id IS NULL)"
            " OR (kind = 'team' AND holder_team IS NOT NULL AND holder_player IS NULL"
            " AND player_registration_id IS NULL)",
            name="ck_checkout_holds_kind_holder",
        ),
        db.CheckConstraint("amount_cents > 0", name="ck_checkout_holds_amount_positive"),
        db.Index(
            "uq_checkout_holds_open_holder_scope",
            "kind",
            sa.func.coalesce(holder_player, holder_team),
            sa.func.coalesce(event, league_id),
            unique=True,
            sqlite_where=sa.text("status = 'OPEN'"),
        ),
    )

    @property
    def holder(self) -> str:
        return self.holder_player if self.kind == "player" else self.holder_team

    @property
    def pays_existing_registration(self) -> bool:
        return self.team_registration_id is not None or self.player_registration_id is not None


class PayoutAccount(db.Model):  # type: ignore[misc]
    """A player's Stripe connected account, which receives online registration fees.

    The status flags mirror Stripe's account object. Stripe is the source
    of truth; account webhooks and onboarding returns refresh them through
    :mod:`app.services.payout_account_service`.

    Attributes:
        id: Auto-increment primary key.
        owner_player: Player who owns the account. One account per player.
        stripe_account_id: Stripe's ``acct_...`` id.
        charges_enabled: Whether the account can accept card payments.
        payouts_enabled: Whether Stripe can pay out to the owner's bank.
        details_submitted: Whether the owner finished Stripe onboarding.
        default_currency: Lowercase ISO currency code, once Stripe knows it.
        created_at: When the account was created.
        updated_at: When the status flags last changed.
    """

    __tablename__ = "payout_accounts"

    id = db.Column(db.Integer, primary_key=True)
    owner_player = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False, unique=True)
    stripe_account_id = db.Column(db.String(AUTH_STRING_LEN), nullable=False, unique=True)
    charges_enabled = db.Column(db.Boolean, default=False, nullable=False)
    payouts_enabled = db.Column(db.Boolean, default=False, nullable=False)
    details_submitted = db.Column(db.Boolean, default=False, nullable=False)
    default_currency = db.Column(db.String(SHORT_CODE_LEN), nullable=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_utc_naive, onupdate=now_utc_naive, nullable=False)

    owner = db.relationship("Player")
