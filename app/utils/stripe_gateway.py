"""The only module that talks to Stripe.

Services reach Stripe through :func:`gateway`, which is ``None`` when
``STRIPE_SECRET_KEY`` is unset; online payments are then unavailable.
Tests install a fake with the same methods under :data:`EXTENSION_KEY`.

Connected accounts are Accounts v2 accounts with Managed Risk: Stripe
collects requirements through hosted onboarding, charges its fees to the
account, and absorbs unrecoverable negative balances. Owners use the full
Stripe Dashboard. Registration payments are direct charges on the owner's
account.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import stripe
from flask import current_app

EXTENSION_KEY = "stripe_gateway"
ACCOUNT_INCLUDES = ["configuration.merchant", "defaults", "requirements"]


class StripeGatewayError(Exception):
    """Stripe rejected a request or could not be reached."""


class InvalidWebhookError(Exception):
    """A webhook's payload or signature did not verify."""


@dataclass(frozen=True)
class ConnectedAccount:
    stripe_account_id: str
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    default_currency: str | None


@dataclass(frozen=True)
class AccountEvent:
    """A verified thin event; ``stripe_account_id`` is set when it concerns a connected account."""

    type: str
    stripe_account_id: str | None


@dataclass(frozen=True)
class CheckoutSession:
    """A Checkout Session on a connected account.

    ``status`` is ``"open"``, ``"complete"``, or ``"expired"``;
    ``payment_status`` is ``"paid"`` once money has moved, which can lag
    completion for delayed payment methods.
    """

    id: str
    stripe_account_id: str
    url: str | None
    status: str
    payment_status: str
    payment_intent_id: str | None
    amount_total: int
    currency: str

    @property
    def paid(self) -> bool:
        return self.payment_status in ("paid", "no_payment_required")


@dataclass(frozen=True)
class CheckoutEvent:
    """A verified snapshot event about a Checkout Session on a connected account."""

    type: str
    session: CheckoutSession | None


def gateway() -> StripeGateway | None:
    return current_app.extensions.get(EXTENSION_KEY)


def _capability_active(capability) -> bool:
    return capability is not None and capability.status == "active"


def _connected_account(account) -> ConnectedAccount:
    merchant = account.configuration.merchant if account.configuration is not None else None
    capabilities = merchant.capabilities if merchant is not None else None
    balance = capabilities.stripe_balance if capabilities is not None else None
    entries = (account.requirements.entries if account.requirements is not None else None) or []
    return ConnectedAccount(
        stripe_account_id=account.id,
        charges_enabled=_capability_active(capabilities.card_payments if capabilities is not None else None),
        payouts_enabled=_capability_active(balance.payouts if balance is not None else None),
        details_submitted=not any(entry.awaiting_action_from == "user" for entry in entries),
        default_currency=account.defaults.currency if account.defaults is not None else None,
    )


def _checkout_session(session, stripe_account_id: str) -> CheckoutSession:
    payment_intent = session.payment_intent
    return CheckoutSession(
        id=session.id,
        stripe_account_id=stripe_account_id,
        url=session.url,
        status=session.status,
        payment_status=session.payment_status,
        payment_intent_id=payment_intent
        if isinstance(payment_intent, str) or payment_intent is None
        else payment_intent.id,
        amount_total=session.amount_total or 0,
        currency=session.currency,
    )


@contextmanager
def _stripe_errors():
    try:
        yield
    except stripe.StripeError as error:
        raise StripeGatewayError(str(error)) from error


class StripeGateway:
    def __init__(self, secret_key: str, account_webhook_secret: str, checkout_webhook_secret: str):
        self._client = stripe.StripeClient(secret_key)
        self._account_webhook_secret = account_webhook_secret
        self._checkout_webhook_secret = checkout_webhook_secret

    def create_connected_account(self, *, email: str | None) -> ConnectedAccount:
        params = {
            "dashboard": "full",
            "identity": {"country": "US"},
            "defaults": {"responsibilities": {"fees_collector": "stripe", "losses_collector": "stripe"}},
            "configuration": {"merchant": {"capabilities": {"card_payments": {"requested": True}}}},
            "include": ACCOUNT_INCLUDES,
        }
        if email:
            params["contact_email"] = email
        with _stripe_errors():
            return _connected_account(self._client.v2.core.accounts.create(params))

    def retrieve_connected_account(self, stripe_account_id: str) -> ConnectedAccount:
        with _stripe_errors():
            account = self._client.v2.core.accounts.retrieve(stripe_account_id, {"include": ACCOUNT_INCLUDES})
        return _connected_account(account)

    def onboarding_url(self, stripe_account_id: str, *, return_url: str, refresh_url: str) -> str:
        with _stripe_errors():
            link = self._client.v2.core.account_links.create(
                {
                    "account": stripe_account_id,
                    "use_case": {
                        "type": "account_onboarding",
                        "account_onboarding": {"return_url": return_url, "refresh_url": refresh_url},
                    },
                }
            )
        return link.url

    def account_event(self, payload: bytes, signature: str | None) -> AccountEvent:
        try:
            notification = self._client.parse_event_notification(payload, signature, self._account_webhook_secret)
        except (ValueError, KeyError, stripe.SignatureVerificationError) as error:
            raise InvalidWebhookError(str(error)) from error
        related = getattr(notification, "related_object", None)
        account_id = related.id if related is not None and related.type == "v2.core.account" else None
        return AccountEvent(type=notification.type, stripe_account_id=account_id)

    def create_checkout_session(
        self,
        stripe_account_id: str,
        *,
        amount_cents: int,
        currency: str,
        product_name: str,
        application_fee_cents: int,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
        expires_at: datetime,
    ) -> CheckoutSession:
        """Start a direct charge on *stripe_account_id*; the platform keeps *application_fee_cents*."""
        params = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency,
                        "unit_amount": amount_cents,
                        "product_data": {"name": product_name},
                    },
                }
            ],
            "payment_intent_data": {"description": product_name},
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": int(expires_at.replace(tzinfo=timezone.utc).timestamp()),
        }
        if application_fee_cents > 0:
            params["payment_intent_data"]["application_fee_amount"] = application_fee_cents
        if customer_email:
            params["customer_email"] = customer_email
        with _stripe_errors():
            session = self._client.v1.checkout.sessions.create(params, {"stripe_account": stripe_account_id})
        return _checkout_session(session, stripe_account_id)

    def retrieve_checkout_session(self, stripe_account_id: str, session_id: str) -> CheckoutSession:
        with _stripe_errors():
            session = self._client.v1.checkout.sessions.retrieve(
                session_id, options={"stripe_account": stripe_account_id}
            )
        return _checkout_session(session, stripe_account_id)

    def expire_checkout_session(self, stripe_account_id: str, session_id: str) -> CheckoutSession:
        """Expire the session if it is still open; return its state either way."""
        session = self.retrieve_checkout_session(stripe_account_id, session_id)
        if session.status != "open":
            return session
        try:
            with _stripe_errors():
                expired = self._client.v1.checkout.sessions.expire(
                    session_id, options={"stripe_account": stripe_account_id}
                )
            return _checkout_session(expired, stripe_account_id)
        except StripeGatewayError:
            # The payer may have completed it in the meantime.
            return self.retrieve_checkout_session(stripe_account_id, session_id)

    def checkout_event(self, payload: bytes, signature: str | None) -> CheckoutEvent:
        try:
            event = self._client.construct_event(payload, signature, self._checkout_webhook_secret)
        except (ValueError, KeyError, stripe.SignatureVerificationError) as error:
            raise InvalidWebhookError(str(error)) from error
        account_id = getattr(event, "account", None)
        session = event.data.object if event.type.startswith("checkout.session.") else None
        return CheckoutEvent(
            type=event.type,
            session=_checkout_session(session, account_id) if session is not None and account_id else None,
        )
