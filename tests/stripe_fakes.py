"""An in-memory stand-in for :class:`~app.utils.stripe_gateway.StripeGateway`."""

import dataclasses
import json

from app.utils.stripe_gateway import (
    AccountEvent,
    CheckoutEvent,
    CheckoutSession,
    ConnectedAccount,
    InvalidWebhookError,
    StripeGatewayError,
)

VALID_SIGNATURE = "valid-signature"


def _verified(payload, signature) -> dict:
    if signature != VALID_SIGNATURE:
        raise InvalidWebhookError("No signatures found matching the expected signature")
    return json.loads(payload)


class FakeStripeGateway:
    def __init__(self):
        self.accounts: dict[str, ConnectedAccount] = {}
        self.fail_links = False
        self.return_urls: list[str] = []
        self.sessions: dict[str, CheckoutSession] = {}
        self.checkout_requests: list[dict] = []

    def create_connected_account(self, *, email):
        account = ConnectedAccount(f"acct_{len(self.accounts) + 1}", False, False, False, None)
        self.accounts[account.stripe_account_id] = account
        return account

    def retrieve_connected_account(self, stripe_account_id):
        return self.accounts[stripe_account_id]

    def onboarding_url(self, stripe_account_id, *, return_url, refresh_url):
        if self.fail_links:
            raise StripeGatewayError("Stripe is down")
        self.return_urls.append(return_url)
        return f"https://connect.stripe.test/{stripe_account_id}"

    def account_event(self, payload, signature):
        data = _verified(payload, signature)
        return AccountEvent(type=data["type"], stripe_account_id=data.get("account"))

    def create_checkout_session(self, stripe_account_id, **request):
        session_id = f"cs_{len(self.sessions) + 1}"
        session = CheckoutSession(
            id=session_id,
            stripe_account_id=stripe_account_id,
            url=f"https://checkout.stripe.test/{session_id}",
            status="open",
            payment_status="unpaid",
            payment_intent_id=None,
            amount_total=request["amount_cents"],
            currency=request["currency"],
        )
        self.sessions[session_id] = session
        self.checkout_requests.append({"stripe_account_id": stripe_account_id, **request})
        return session

    def retrieve_checkout_session(self, stripe_account_id, session_id):
        return self.sessions[session_id]

    def expire_checkout_session(self, stripe_account_id, session_id):
        if self.sessions[session_id].status == "open":
            self.sessions[session_id] = dataclasses.replace(self.sessions[session_id], status="expired")
        return self.sessions[session_id]

    def checkout_event(self, payload, signature):
        data = _verified(payload, signature)
        return CheckoutEvent(type=data["type"], session=self.sessions.get(data["session"]))

    def restrict_charges(self, stripe_account_id):
        self.accounts[stripe_account_id] = dataclasses.replace(self.accounts[stripe_account_id], charges_enabled=False)

    def finish_onboarding(self, stripe_account_id):
        self.accounts[stripe_account_id] = dataclasses.replace(
            self.accounts[stripe_account_id],
            charges_enabled=True,
            payouts_enabled=True,
            details_submitted=True,
            default_currency="usd",
        )

    def pay(self, session_id, *, payment_status="paid"):
        """The payer completes checkout. A delayed payment method completes ``"unpaid"``."""
        self.sessions[session_id] = dataclasses.replace(
            self.sessions[session_id],
            status="complete",
            payment_status=payment_status,
            payment_intent_id=f"pi_{session_id}",
        )
