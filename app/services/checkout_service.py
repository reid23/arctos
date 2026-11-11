"""Paying registration fees online through Stripe Checkout.

When a config takes online payments and charges a fee, registering
requires an entry: either one the registrant already holds, or one bought
through checkout. Starting a checkout validates the registration form,
creates a Stripe Checkout Session as a direct charge on the organizer's
payout account, and records a :class:`~app.models.payments.CheckoutHold`
with the form. An open team hold takes a place under the team cap until
the session expires.

Fulfillment runs from the checkout webhook and from the payer's return
page, whichever comes first; the hold's status makes it idempotent. It
commits the payment and a ``STRIPE`` entry before registering, so a
registration that then fails (registration filled up, waiver changed)
leaves the registrant holding an ``AVAILABLE`` entry and an explanation.
A pay-now hold covers an existing unpaid registration instead.

:func:`start_checkout` flushes but never commits; :func:`fulfill` and
:func:`apply_session` commit, since a recorded payment must survive a
failed registration.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa
from flask import current_app

from app.domain.enums import CheckoutHoldStatus, PaidEntrySource, UserType
from app.error_values import Err, Ok, Result, Some, allow_Q
from app.exceptions import ArctosError, RegistrationClosedError, ValidationError
from app.services import paid_entry_service
from app.services._common import Scope
from app.services.payout_account_service import accepts_online_payments, require_gateway, stripe_failure
from app.utils.datetime_helpers import now_utc_naive
from app.utils.name_validation import team_pseudonym_char_error
from app.utils.result_helpers import public_error_message
from app.utils.stripe_gateway import CheckoutSession, StripeGateway, StripeGatewayError
from app.utils.user_helpers import is_team
from models import CheckoutHold, Payment, RegistrableConfig, db

logger = logging.getLogger(__name__)

# Stripe's minimum session lifetime; also how long a team hold keeps a place.
CHECKOUT_LIFETIME = timedelta(minutes=30)
BASIS_POINTS = 10_000
PAYMENT_FAILED_EVENT = "checkout.session.async_payment_failed"

SETUP_INCOMPLETE_MESSAGE = "The organizer's payment setup is incomplete. Contact them to register."
PAYMENT_REQUIRED_MESSAGE = "Pay the registration fee to register."
ALREADY_PREPAID_MESSAGE = (
    "You already had a prepaid entry when this payment completed, so it wasn't needed. Ask the organizer for a refund."
)
WITHDRAWN_MESSAGE = "The registration was withdrawn before the payment completed. The prepaid entry is waiting for you."
PAYMENT_FAILED_MESSAGE = "The payment didn't go through. Nothing was charged."


def _scope_filters(scope: Scope) -> dict[str, str]:
    return {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}


def _holder_filters(kind: UserType, holder_id: str) -> dict[str, str]:
    return {"holder_team": holder_id} if kind == UserType.TEAM else {"holder_player": holder_id}


def scope_of(hold: CheckoutHold) -> Scope:
    return Scope.league(hold.league_id) if hold.league_id else Scope.event(hold.event)


def online_fee_cents(config: RegistrableConfig | None, kind: UserType) -> int:
    """The fee registrants of *kind* pay online, or 0 when they don't pay online."""
    if config is None or not config.online_payments_enabled:
        return 0
    fee = config.team_reg_fee if kind == UserType.TEAM else config.player_reg_fee
    return int((Decimal(fee or 0) * 100).quantize(Decimal(1)))


def require_payment_settled(
    scope: Scope, config: RegistrableConfig | None, kind: UserType, holder_id: str
) -> Result[None, ArctosError]:
    """Refuse a registration that must be paid online first and isn't covered by a held entry."""
    if online_fee_cents(config, kind) == 0 or paid_entry_service.held_entry(scope, kind, holder_id).is_some():
        return Ok(None)
    if not accepts_online_payments(config):
        return Err(ArctosError(SETUP_INCOMPLETE_MESSAGE, status_code=409))
    return Err(ArctosError(PAYMENT_REQUIRED_MESSAGE, status_code=402))


def held_team_places(scope: Scope, *, excluding_team: str | None = None) -> int:
    """Open, unexpired checkouts for new team registrations, which each hold a place under the cap."""
    query = CheckoutHold.query.filter_by(
        **_scope_filters(scope), kind=UserType.TEAM.value, status=CheckoutHoldStatus.OPEN, team_registration_id=None
    ).filter(CheckoutHold.expires_at > now_utc_naive())
    if excluding_team is not None:
        query = query.filter(CheckoutHold.holder_team != excluding_team)
    return query.count()


def open_hold(scope: Scope, kind: UserType, holder_id: str) -> CheckoutHold | None:
    return CheckoutHold.query.filter_by(
        **_scope_filters(scope), kind=kind.value, **_holder_filters(kind, holder_id), status=CheckoutHoldStatus.OPEN
    ).first()


def checkout_mode(scope: Scope, config: RegistrableConfig | None, kind: UserType, holder_id: str) -> str:
    """How *holder_id* pays to register: ``"none"`` (no online payment needed),
    ``"checkout"``, or ``"unavailable"`` (online payment is needed but the
    organizer can't accept it)."""
    if online_fee_cents(config, kind) == 0 or paid_entry_service.held_entry(scope, kind, holder_id).is_some():
        return "none"
    match paid_entry_service.active_registration(scope, kind, holder_id):
        case Some(registration) if registration.paid:
            return "none"
    return "checkout" if accepts_online_payments(config) else "unavailable"


def _registration_open(config: RegistrableConfig, kind: UserType) -> bool:
    return bool(config.team_registration_open if kind == UserType.TEAM else config.player_registration_open)


def _text(form: dict, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


@allow_Q
def _registration_form(scope: Scope, parent, config: RegistrableConfig, kind: UserType, holder_id: str, form: dict):
    """The registration fields to keep on the hold, once they pass the checks registration will repeat."""
    from app.services.registration_service import RegistrationService

    scope_label = "league" if scope.is_league else "tournament"
    if not _registration_open(config, kind):
        return Err(RegistrationClosedError(f"Registration is not open for this {scope_label}"))
    if kind == UserType.TEAM:
        pseudonym = _text(form, "pseudonym")
        if not pseudonym:
            return Err(ValidationError("Team pseudonym is required"))
        match team_pseudonym_char_error(pseudonym):
            case Some(error):
                return Err(ValidationError(error))
        if config.n_max_teams is not None:
            if RegistrationService._taken_team_places(scope, parent, holder_id) >= config.n_max_teams:
                return Err(ValidationError(f"Maximum number of teams ({config.n_max_teams}) already registered"))
        return Ok({"pseudonym": pseudonym, "shortname": _text(form, "shortname") or None})

    payload = {
        "team": _text(form, "team") or None,
        "jersey_number": _text(form, "jersey_number"),
        "jersey_name": _text(form, "jersey_name"),
        "waiver_legal_name_signature": "",
        "signed_waiver_sha256": None,
    }
    if config.waiver_filepath:
        signature = _text(form, "waiver_legal_name_signature")
        if not signature:
            return Err(ValidationError("Waiver signature is required"))
        if not config.waiver_sha256:
            return Err(ValidationError("Waiver is missing its checksum"))
        payload["waiver_legal_name_signature"] = signature
        payload["signed_waiver_sha256"] = config.waiver_sha256
    return Ok(payload)


@allow_Q
def _settle_previous_hold(stripe: StripeGateway, scope: Scope, kind: UserType, holder_id: str):
    """Expire the registrant's earlier checkout, or report that it was paid after all."""
    hold = open_hold(scope, kind, holder_id)
    if hold is None:
        return Ok(None)
    try:
        session = stripe.expire_checkout_session(hold.stripe_account_id, hold.stripe_session_id)
    except StripeGatewayError as error:
        return stripe_failure("expire_checkout", error)
    if session.status == "expired":
        hold.status = CheckoutHoldStatus.EXPIRED
        db.session.flush()
        return Ok(None)
    if session.paid:
        fulfill(hold, session)
        return Err(ValidationError("Your earlier payment went through. Refresh to see your registration."))
    return Err(ValidationError("Your earlier payment is still processing. Try again once it finishes."))


def _product_name(parent, kind: UserType, holder_name: str) -> str:
    label = "Team registration" if kind == UserType.TEAM else "Player registration"
    return f"{label}: {parent.name} ({holder_name})"


@allow_Q
def start_checkout(
    scope: Scope,
    parent,
    config: RegistrableConfig,
    kind: UserType,
    holder,
    *,
    payer,
    form: dict,
    success_url: str,
    cancel_url: str,
) -> Result[str, ArctosError]:
    """Start paying *holder*'s fee, returning the Stripe Checkout URL.

    Pays for the holder's existing unpaid registration when there is one;
    otherwise validates *form* to register them once the payment completes.
    *success_url* may contain ``{CHECKOUT_SESSION_ID}``.
    """
    stripe = require_gateway().Q()
    amount_cents = online_fee_cents(config, kind)
    if amount_cents == 0:
        return Err(ValidationError("This registration doesn't take online payment."))
    if not accepts_online_payments(config):
        return Err(ArctosError(SETUP_INCOMPLETE_MESSAGE, status_code=409))
    if paid_entry_service.held_entry(scope, kind, holder.id).is_some():
        return Err(ValidationError("You already have a prepaid entry. Register without paying."))
    _settle_previous_hold(stripe, scope, kind, holder.id).Q()

    registration = paid_entry_service.active_registration(scope, kind, holder.id).unwrap_or(None)
    if registration is not None and registration.paid:
        return Err(ValidationError("This registration is already paid."))
    form_payload = (
        None if registration is not None else _registration_form(scope, parent, config, kind, holder.id, form).Q()
    )

    account = config.payout_account
    currency = account.default_currency or "usd"
    application_fee_cents = amount_cents * current_app.config["STRIPE_PLATFORM_FEE_BPS"] // BASIS_POINTS
    expires_at = now_utc_naive() + CHECKOUT_LIFETIME
    try:
        session = stripe.create_checkout_session(
            account.stripe_account_id,
            amount_cents=amount_cents,
            currency=currency,
            product_name=_product_name(parent, kind, holder.name),
            application_fee_cents=application_fee_cents,
            customer_email=getattr(payer, "email", None),
            success_url=success_url,
            cancel_url=cancel_url,
            expires_at=expires_at,
        )
    except StripeGatewayError as error:
        return stripe_failure("create_checkout", error)

    hold = CheckoutHold(
        stripe_session_id=session.id,
        stripe_account_id=account.stripe_account_id,
        **_scope_filters(scope),
        kind=kind.value,
        **_holder_filters(kind, holder.id),
        payer_user_id=payer.id,
        payer_user_type=str(UserType.TEAM if is_team(payer) else UserType.PLAYER),
        form_payload=form_payload,
        team_registration_id=registration.id if registration is not None and kind == UserType.TEAM else None,
        player_registration_id=registration.id if registration is not None and kind == UserType.PLAYER else None,
        amount_cents=amount_cents,
        currency=currency,
        application_fee_cents=application_fee_cents,
        status=CheckoutHoldStatus.OPEN,
        expires_at=expires_at,
    )
    db.session.add(hold)
    db.session.flush()
    logger.info(
        "checkout started: hold=%s session=%s kind=%s holder=%s amount_cents=%s",
        hold.id,
        session.id,
        kind,
        holder.id,
        amount_cents,
    )
    return Ok(session.url)


def _record_failure(hold_id: int, message: str) -> None:
    hold = db.session.get(CheckoutHold, hold_id)
    hold.failure_message = message
    db.session.commit()
    logger.info("checkout registration failed: hold=%s reason=%s", hold_id, message)


def _register(hold: CheckoutHold, scope: Scope) -> Result:
    from app.services.registration_service import RegistrationService

    form = hold.form_payload or {}
    if hold.kind == UserType.TEAM.value:
        return RegistrationService.register_team(
            scope,
            hold.holder_team,
            form.get("pseudonym", ""),
            shortname=form.get("shortname"),
            allow_closed_registration=True,
        )
    return RegistrationService.register_player(
        scope,
        hold.holder_player,
        form.get("team"),
        jersey_number=form.get("jersey_number", ""),
        jersey_name=form.get("jersey_name", ""),
        waiver_legal_name_signature=form.get("waiver_legal_name_signature", ""),
        signed_waiver_sha256=form.get("signed_waiver_sha256"),
        allow_closed_registration=True,
    )


def _complete_registration(hold: CheckoutHold) -> None:
    scope = scope_of(hold)
    kind = UserType(hold.kind)
    match paid_entry_service.active_registration(scope, kind, hold.holder):
        case Some(registration):
            paid_entry_service.cover_registration(registration, source_if_new=None)
            db.session.commit()
            return
    if hold.pays_existing_registration:
        _record_failure(hold.id, WITHDRAWN_MESSAGE)
        return
    hold_id = hold.id
    result = _register(hold, scope)
    if result.is_err():
        db.session.rollback()
        _record_failure(hold_id, public_error_message(result.unwrap_err()))


def fulfill(hold: CheckoutHold, session: CheckoutSession) -> CheckoutHold:
    """Record *session*'s payment and a ``STRIPE`` entry, then register the holder. Commits.

    Does nothing once the hold has left ``OPEN``, so the webhook and the
    return page may both call it.
    """
    claimed = db.session.execute(
        sa.update(CheckoutHold)
        .where(CheckoutHold.id == hold.id, CheckoutHold.status == CheckoutHoldStatus.OPEN)
        .values(status=CheckoutHoldStatus.COMPLETED, completed_at=now_utc_naive())
        .execution_options(synchronize_session=False)
    ).rowcount
    db.session.refresh(hold)
    if not claimed:
        return hold

    scope = scope_of(hold)
    kind = UserType(hold.kind)
    payment = Payment(
        amount_cents=session.amount_total,
        method="Stripe",
        reference=session.payment_intent_id,
        stripe_checkout_session_id=session.id,
        stripe_payment_intent_id=session.payment_intent_id,
        stripe_account_id=hold.stripe_account_id,
        currency=session.currency,
        application_fee_cents=hold.application_fee_cents,
        payer_user_id=hold.payer_user_id,
        payer_user_type=hold.payer_user_type,
    )
    if paid_entry_service.held_entry(scope, kind, hold.holder).is_some():
        hold.failure_message = ALREADY_PREPAID_MESSAGE
    else:
        entry = paid_entry_service.create_entry(scope, kind, hold.holder, source=PaidEntrySource.STRIPE)
        payment.entry_id = entry.id
    db.session.add(payment)
    db.session.commit()
    logger.info(
        "checkout paid: hold=%s session=%s amount_cents=%s entry=%s",
        hold.id,
        session.id,
        session.amount_total,
        payment.entry_id,
    )
    if hold.failure_message is None:
        _complete_registration(hold)
    db.session.refresh(hold)
    return hold


def apply_session(hold: CheckoutHold, session: CheckoutSession, *, payment_failed: bool = False) -> CheckoutHold:
    """Bring *hold* up to date with Stripe's view of its session. Commits.

    *payment_failed* is for a delayed payment method that Stripe reports
    as failed after the session completed.
    """
    if hold.status != CheckoutHoldStatus.OPEN:
        return hold
    if session.paid:
        return fulfill(hold, session)
    if session.status == "expired" or payment_failed:
        hold.status = CheckoutHoldStatus.EXPIRED
        if payment_failed:
            hold.failure_message = PAYMENT_FAILED_MESSAGE
        db.session.commit()
        logger.info("checkout expired: hold=%s session=%s failed=%s", hold.id, session.id, payment_failed)
    return hold


@allow_Q
def sync(hold: CheckoutHold) -> Result[CheckoutHold, ArctosError]:
    """Re-read an open hold's session from Stripe, for the payer's return page."""
    if hold.status != CheckoutHoldStatus.OPEN:
        return Ok(hold)
    stripe = require_gateway().Q()
    try:
        session = stripe.retrieve_checkout_session(hold.stripe_account_id, hold.stripe_session_id)
    except StripeGatewayError as error:
        return stripe_failure("retrieve_checkout", error)
    return Ok(apply_session(hold, session))


def apply_checkout_event(event_type: str, session: CheckoutSession | None) -> None:
    """Apply a verified checkout webhook event. Sessions without a hold are ignored."""
    if session is None:
        return
    hold = CheckoutHold.query.filter_by(stripe_session_id=session.id).first()
    if hold is None:
        logger.info("stripe checkout event for unknown session: type=%s session=%s", event_type, session.id)
        return
    apply_session(hold, session, payment_failed=event_type == PAYMENT_FAILED_EVENT)
