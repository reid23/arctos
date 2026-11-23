"""Mapping Stripe's v2 account objects and Checkout Sessions onto the gateway's records."""

import pytest
import stripe

from app.utils.stripe_gateway import CheckoutSession, ConnectedAccount, _checkout_session, _connected_account

pytestmark = pytest.mark.unit


def _account(**fields) -> stripe.StripeObject:
    return stripe.StripeObject.construct_from({"id": "acct_test", "object": "v2.core.account"} | fields, "sk_test")


def _merchant(card_payments: str, payouts: str) -> dict:
    return {
        "merchant": {
            "capabilities": {
                "card_payments": {"status": card_payments},
                "stripe_balance": {"payouts": {"status": payouts}},
            }
        }
    }


def test_new_account_awaiting_onboarding():
    account = _account(
        configuration=_merchant("restricted", "restricted"),
        defaults={"currency": "usd"},
        requirements={"entries": [{"awaiting_action_from": "user"}, {"awaiting_action_from": "stripe"}]},
    )

    assert _connected_account(account) == ConnectedAccount("acct_test", False, False, False, "usd")


def test_onboarded_account_with_payouts_still_restricted():
    account = _account(
        configuration=_merchant("active", "restricted"),
        defaults={"currency": "usd"},
        requirements={"entries": [{"awaiting_action_from": "stripe"}]},
    )

    assert _connected_account(account) == ConnectedAccount("acct_test", True, False, True, "usd")


def test_missing_sections_mean_nothing_is_enabled():
    account = _account(configuration=None, defaults=None, requirements=None)

    assert _connected_account(account) == ConnectedAccount("acct_test", False, False, True, None)


def _session(**fields) -> stripe.StripeObject:
    base = {
        "id": "cs_test",
        "object": "checkout.session",
        "url": None,
        "status": "complete",
        "payment_status": "paid",
        "amount_total": 1000,
        "currency": "usd",
    }
    return stripe.StripeObject.construct_from(base | fields, "sk_test")


@pytest.mark.parametrize(
    "payment_intent",
    ["pi_test", {"id": "pi_test", "object": "payment_intent"}],
    ids=["id", "expanded"],
)
def test_checkout_session_reads_the_payment_intent_id(payment_intent):
    session = _checkout_session(_session(payment_intent=payment_intent), "acct_test")

    assert session == CheckoutSession("cs_test", "acct_test", None, "complete", "paid", "pi_test", 1000, "usd")
    assert session.paid


def test_open_checkout_session_is_unpaid():
    session = _checkout_session(_session(status="open", payment_status="unpaid", payment_intent=None), "acct_test")

    assert (session.payment_intent_id, session.paid) == (None, False)
