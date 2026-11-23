"""Players' Stripe payout accounts and which one an event or league pays into.

A player has at most one :class:`~app.models.payments.PayoutAccount`.
Stripe owns its status; this module mirrors it on returns from onboarding
and on account webhooks. An organizer may attach only their own
account to a registration config, and must detach someone else's first.

Functions here flush but never commit; callers own the transaction.
"""

from __future__ import annotations

import logging

from app.error_values import Err, Ok, Option, Result, allow_Q, option
from app.exceptions import ArctosError, NotFoundError, UnauthorizedError, ValidationError
from app.utils.stripe_gateway import AccountEvent, ConnectedAccount, StripeGateway, StripeGatewayError, gateway
from app.utils.user_helpers import is_player
from models import PayoutAccount, Player, RegistrableConfig, db

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "Online payments are not available on this site."
STRIPE_FAILURE_MESSAGE = "Stripe could not be reached. Try again in a few minutes."


def require_gateway() -> Result[StripeGateway, ArctosError]:
    return option(gateway()).ok_or(NotFoundError(UNAVAILABLE_MESSAGE))


def stripe_failure(action: str, error: StripeGatewayError) -> Err:
    logger.warning("stripe request failed: action=%s error=%s", action, error)
    return Err(ArctosError(message=STRIPE_FAILURE_MESSAGE, status_code=502))


def payout_account_of(player_id: str) -> Option[PayoutAccount]:
    return option(PayoutAccount.query.filter_by(owner_player=player_id).first())


def _apply_status(account: PayoutAccount, status: ConnectedAccount) -> None:
    account.charges_enabled = status.charges_enabled
    account.payouts_enabled = status.payouts_enabled
    account.details_submitted = status.details_submitted
    account.default_currency = status.default_currency


@allow_Q
def onboarding_url(player: Player, *, return_url: str, refresh_url: str) -> Result[str, ArctosError]:
    """Create the player's connected account if needed, and a link to Stripe's hosted onboarding.

    A newly created account is flushed even when the link then fails, so
    the caller should commit either way rather than lose the Stripe account.
    """
    stripe = require_gateway().Q()
    account = payout_account_of(player.id).unwrap_or(None)
    if account is None:
        try:
            status = stripe.create_connected_account(email=player.email)
        except StripeGatewayError as error:
            return stripe_failure("create_account", error)
        account = PayoutAccount(owner_player=player.id, stripe_account_id=status.stripe_account_id)
        _apply_status(account, status)
        db.session.add(account)
        db.session.flush()
    try:
        return Ok(stripe.onboarding_url(account.stripe_account_id, return_url=return_url, refresh_url=refresh_url))
    except StripeGatewayError as error:
        return stripe_failure("onboarding_link", error)


@allow_Q
def refresh(account: PayoutAccount) -> Result[PayoutAccount, ArctosError]:
    """Re-read the account's status from Stripe, for when the owner returns from onboarding."""
    stripe = require_gateway().Q()
    try:
        status = stripe.retrieve_connected_account(account.stripe_account_id)
    except StripeGatewayError as error:
        return stripe_failure("retrieve_account", error)
    _apply_status(account, status)
    db.session.flush()
    return Ok(account)


def apply_account_event(event: AccountEvent) -> Result[PayoutAccount | None, ArctosError]:
    """Re-read a connected account that a verified thin event concerns.

    Thin events carry no account state, so this fetches it. Events about
    other objects and unknown accounts are ignored.
    """
    if event.stripe_account_id is None:
        return Ok(None)
    account = PayoutAccount.query.filter_by(stripe_account_id=event.stripe_account_id).first()
    if account is None:
        logger.info("stripe account event for unknown account: type=%s account=%s", event.type, event.stripe_account_id)
        return Ok(None)
    return refresh(account)


def accepts_online_payments(config: RegistrableConfig) -> bool:
    """Whether registrants can pay this config's fees online right now."""
    account = config.payout_account
    return bool(config.online_payments_enabled and account is not None and account.charges_enabled)


def update_online_payments(
    config: RegistrableConfig, *, enabled: bool, payout_account_id: int | None, actor
) -> Result[RegistrableConfig, ArctosError]:
    """Attach or detach a payout account and turn online payments on or off.

    Any organizer may detach the current account. Attaching requires the
    actor's own account, and no other account already attached.
    """
    if payout_account_id is None:
        config.payout_account = None
    elif payout_account_id != config.payout_account_id:
        own = payout_account_of(actor.id).unwrap_or(None) if is_player(actor) else None
        if own is None or own.id != payout_account_id:
            return Err(UnauthorizedError("You can only attach your own payout account."))
        if config.payout_account is not None:
            return Err(
                ValidationError(
                    f"Payments go to {config.payout_account.owner.name}'s payout account. Detach it first.",
                    status_code=409,
                )
            )
        config.payout_account = own
    if enabled and (config.payout_account is None or not config.payout_account.charges_enabled):
        return Err(ValidationError("Finish payout setup before turning on online payments."))
    config.online_payments_enabled = enabled
    db.session.flush()
    return Ok(config)
