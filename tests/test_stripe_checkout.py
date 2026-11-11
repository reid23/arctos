"""Paying registration fees through Stripe Checkout, from starting a checkout to registering."""

import json
from datetime import timedelta

import pytest

from app.domain.enums import CheckoutHoldStatus, PaidEntrySource, PaidEntryStatus, RegistrationStatus
from app.services import checkout_service
from models import (
    TO,
    CheckoutHold,
    PaidEntry,
    Payment,
    PayoutAccount,
    Player,
    PlayerRegistration,
    Team,
    TeamManager,
    TeamRegistration,
    db,
)
from tests.stripe_fakes import VALID_SIGNATURE
from tests.utils import login_as

pytestmark = pytest.mark.integration

CHECKOUT_URL = "/_api/payments/checkout/events/test-tournament"
WEBHOOK_URL = "/_api/payments/webhooks/checkout"


@pytest.fixture
def paid_tournament(tournament, stripe_gateway):
    """The test tournament taking $10 per team and $5 per player online."""
    organizer = Player(id="organizer", name="Organizer", pw_hash="x")
    db.session.add(organizer)
    status = stripe_gateway.create_connected_account(email=None)
    stripe_gateway.finish_onboarding(status.stripe_account_id)
    account = PayoutAccount(
        owner_player=organizer.id,
        stripe_account_id=status.stripe_account_id,
        charges_enabled=True,
        payouts_enabled=True,
        details_submitted=True,
        default_currency="usd",
    )
    db.session.add(account)
    db.session.add(TO(user_id=organizer.id, user_type="player", event=tournament.url))
    config = tournament.registrable_config
    config.team_reg_fee = 10
    config.player_reg_fee = 5
    config.online_payments_enabled = True
    config.payout_account = account
    db.session.commit()
    return tournament


def _team(team_id: str) -> Team:
    team = Team(id=team_id, name=team_id.title(), pw_hash="x")
    db.session.add(team)
    db.session.commit()
    return team


def _start_team_checkout(client, pseudonym="Paid Team", **extra):
    return client.post(
        CHECKOUT_URL,
        json={"registrant": "team", "form": {"pseudonym": pseudonym}, "return_path": "/test-tournament/register"}
        | extra,
    )


def _start_player_checkout(client, **form):
    return client.post(
        CHECKOUT_URL,
        json={"registrant": "player", "form": form, "return_path": "/test-tournament/register"},
    )


def _post_checkout_event(client, event_type, session_id, signature=VALID_SIGNATURE):
    return client.post(
        WEBHOOK_URL,
        data=json.dumps({"type": event_type, "session": session_id}),
        headers={"Stripe-Signature": signature},
    )


def _team_registration(team_id) -> TeamRegistration | None:
    db.session.expire_all()
    return TeamRegistration.query.filter_by(event="test-tournament", team=team_id).first()


def test_paid_registration_requires_checkout(client, team, paid_tournament):
    login_as(client, team)

    response = client.post("/_api/test-tournament/register-team", data={"pseudonym": "Unpaid"})

    assert response.status_code == 400
    assert response.get_json()["error"] == checkout_service.PAYMENT_REQUIRED_MESSAGE
    assert _team_registration(team.id) is None
    options = client.get(f"{CHECKOUT_URL}?registrant=team").get_json()
    assert options == {"mode": "checkout", "amount_cents": 1000}


def test_lost_charges_block_paid_registration(client, team, paid_tournament, stripe_gateway):
    paid_tournament.registrable_config.payout_account.charges_enabled = False
    db.session.commit()
    login_as(client, team)

    response = client.post("/_api/test-tournament/register-team", data={"pseudonym": "Blocked"})

    assert response.get_json()["error"] == checkout_service.SETUP_INCOMPLETE_MESSAGE
    assert client.get(f"{CHECKOUT_URL}?registrant=team").get_json()["mode"] == "unavailable"
    assert _start_team_checkout(client).status_code == 409


def test_fees_without_online_payments_register_unpaid(client, team, paid_tournament):
    paid_tournament.registrable_config.online_payments_enabled = False
    db.session.commit()
    login_as(client, team)

    assert client.get(f"{CHECKOUT_URL}?registrant=team").get_json() == {"mode": "none", "amount_cents": 0}
    assert client.post("/_api/test-tournament/register-team", data={"pseudonym": "Later"}).status_code == 200
    assert not _team_registration(team.id).paid


def test_checkout_charges_the_organizer_account_and_keeps_the_form(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)

    response = _start_team_checkout(client, pseudonym="Paid Team")

    assert response.status_code == 200
    assert response.get_json()["url"] == "https://checkout.stripe.test/cs_1"
    request = stripe_gateway.checkout_requests[0]
    assert request["stripe_account_id"] == "acct_1"
    assert (request["amount_cents"], request["currency"], request["application_fee_cents"]) == (1000, "usd", 0)
    assert request["success_url"].endswith("/checkout/{CHECKOUT_SESSION_ID}")
    assert request["cancel_url"].endswith("/test-tournament/register")
    hold = CheckoutHold.query.one()
    assert (hold.status, hold.holder_team, hold.form_payload["pseudonym"]) == (
        CheckoutHoldStatus.OPEN,
        team.id,
        "Paid Team",
    )
    assert _team_registration(team.id) is None


def test_platform_fee_comes_from_config(client, app, team, paid_tournament, stripe_gateway, monkeypatch):
    monkeypatch.setitem(app.config, "STRIPE_PLATFORM_FEE_BPS", 250)
    login_as(client, team)

    _start_team_checkout(client)

    assert stripe_gateway.checkout_requests[0]["application_fee_cents"] == 25
    assert CheckoutHold.query.one().application_fee_cents == 25


def test_checkout_validates_the_form_first(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)

    response = _start_team_checkout(client, pseudonym="  ")

    assert response.status_code == 400
    assert stripe_gateway.checkout_requests == []
    assert CheckoutHold.query.count() == 0


def test_webhook_registers_the_paid_team_once(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client, pseudonym="Paid Team")
    stripe_gateway.pay("cs_1")

    first = _post_checkout_event(client, "checkout.session.completed", "cs_1")
    second = _post_checkout_event(client, "checkout.session.completed", "cs_1")

    assert first.status_code == second.status_code == 200
    registration = _team_registration(team.id)
    assert (registration.status, registration.pseudonym, registration.paid) == ("CONFIRMED", "Paid Team", True)
    assert registration.amount_paid == 10
    entry = PaidEntry.query.one()
    assert (entry.source, entry.status) == (PaidEntrySource.STRIPE, PaidEntryStatus.APPLIED)
    payment = Payment.query.one()
    assert (payment.entry_id, payment.amount_cents, payment.stripe_payment_intent_id) == (entry.id, 1000, "pi_cs_1")
    assert (payment.payer_user_id, payment.payer_user_type) == (team.id, "team")
    assert CheckoutHold.query.one().status == CheckoutHoldStatus.COMPLETED


def test_return_page_fulfills_before_the_webhook(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    stripe_gateway.pay("cs_1")

    response = client.post("/_api/payments/checkouts/cs_1/sync")
    _post_checkout_event(client, "checkout.session.completed", "cs_1")

    body = response.get_json()
    assert (body["status"], body["registered"], body["failure"]) == ("completed", True, None)
    assert body["scope"] == {"kind": "events", "slug": "test-tournament", "name": "Test Tournament"}
    assert Payment.query.count() == 1


def test_only_the_payer_or_registrant_sees_a_checkout(client, team, player, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)

    login_as(client, player)

    assert client.post("/_api/payments/checkouts/cs_1/sync").status_code == 404


def test_open_team_checkouts_hold_places_under_the_cap(client, team, paid_tournament, stripe_gateway):
    paid_tournament.registrable_config.n_max_teams = 1
    db.session.commit()
    other = _team("other_team")
    login_as(client, team)
    _start_team_checkout(client)

    login_as(client, other)
    blocked = _start_team_checkout(client, pseudonym="Other")
    CheckoutHold.query.one().expires_at -= checkout_service.CHECKOUT_LIFETIME + timedelta(minutes=1)
    db.session.commit()
    after_expiry = _start_team_checkout(client, pseudonym="Other")

    assert blocked.status_code == 400
    assert "Maximum number of teams (1)" in blocked.get_json()["error"]
    assert after_expiry.status_code == 200


def test_a_second_checkout_expires_the_first(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)

    response = _start_team_checkout(client, pseudonym="Renamed")

    assert response.get_json()["url"] == "https://checkout.stripe.test/cs_2"
    assert stripe_gateway.sessions["cs_1"].status == "expired"
    holds = {hold.stripe_session_id: hold.status for hold in CheckoutHold.query.all()}
    assert holds == {"cs_1": CheckoutHoldStatus.EXPIRED, "cs_2": CheckoutHoldStatus.OPEN}


def test_a_second_checkout_finds_the_first_was_paid(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    stripe_gateway.pay("cs_1")

    response = _start_team_checkout(client)

    assert response.status_code == 400
    assert "earlier payment went through" in response.get_json()["error"]
    assert _team_registration(team.id).paid
    assert len(stripe_gateway.sessions) == 1


def test_registration_closing_mid_checkout_is_honored(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    paid_tournament.registrable_config.team_registration_open = False
    db.session.commit()
    stripe_gateway.pay("cs_1")

    _post_checkout_event(client, "checkout.session.completed", "cs_1")

    assert _team_registration(team.id).paid


def test_failed_registration_leaves_a_prepaid_entry(client, player, paid_tournament, stripe_gateway):
    config = paid_tournament.registrable_config
    config.waiver_filepath = "waiver.pdf"
    config.waiver_sha256 = "a" * 64
    db.session.commit()
    login_as(client, player)
    _start_player_checkout(client, jersey_name="Pat", waiver_legal_name_signature="Pat Example")
    config.waiver_sha256 = "b" * 64
    db.session.commit()
    stripe_gateway.pay("cs_1")

    body = client.post("/_api/payments/checkouts/cs_1/sync").get_json()

    assert (body["status"], body["registered"]) == ("completed", False)
    assert "waiver changed" in body["failure"]
    entry = PaidEntry.query.one()
    assert (entry.status, entry.holder_player) == (PaidEntryStatus.AVAILABLE, player.id)
    assert client.get(f"{CHECKOUT_URL}?registrant=player").get_json()["mode"] == "none"
    registered = client.post(
        "/_api/test-tournament/register-player",
        data={"jersey_name": "Pat", "waiver_legal_name_signature": "Pat Example"},
    )
    assert registered.status_code == 200
    db.session.expire_all()
    assert PlayerRegistration.query.filter_by(player=player.id).one().paid


def test_player_who_picks_a_team_awaits_approval_after_paying(client, player, team, paid_tournament, stripe_gateway):
    login_as(client, player)
    _start_player_checkout(client, team=team.id, jersey_name="Pat")
    stripe_gateway.pay("cs_1")

    _post_checkout_event(client, "checkout.session.completed", "cs_1")

    db.session.expire_all()
    registration = PlayerRegistration.query.filter_by(player=player.id).one()
    assert (registration.status, registration.team, registration.paid) == (
        RegistrationStatus.PENDING_TEAM_APPROVAL,
        team.id,
        True,
    )


def test_pay_now_covers_an_existing_unpaid_registration(client, team, paid_tournament, stripe_gateway):
    config = paid_tournament.registrable_config
    config.online_payments_enabled = False
    config.n_max_teams = 1
    db.session.commit()
    login_as(client, team)
    client.post("/_api/test-tournament/register-team", data={"pseudonym": "Pay Later"})
    config.online_payments_enabled = True
    db.session.commit()

    response = _start_team_checkout(client)
    hold = CheckoutHold.query.one()
    held_places = checkout_service.held_team_places(checkout_service.scope_of(hold))
    stripe_gateway.pay("cs_1")
    _post_checkout_event(client, "checkout.session.completed", "cs_1")

    assert response.status_code == 200
    assert (hold.team_registration_id, hold.form_payload) == (_team_registration(team.id).id, None)
    assert held_places == 0
    registration = _team_registration(team.id)
    assert (registration.pseudonym, registration.paid) == ("Pay Later", True)


def test_already_paid_registrations_need_no_checkout(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    stripe_gateway.pay("cs_1")
    _post_checkout_event(client, "checkout.session.completed", "cs_1")

    assert client.get(f"{CHECKOUT_URL}?registrant=team").get_json()["mode"] == "none"
    assert _start_team_checkout(client).status_code == 400


def test_expired_and_failed_checkouts_release_their_hold(client, team, player, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    login_as(client, player)
    _start_player_checkout(client, jersey_name="Pat")
    stripe_gateway.expire_checkout_session("acct_1", "cs_1")
    stripe_gateway.pay("cs_2", payment_status="unpaid")

    _post_checkout_event(client, "checkout.session.expired", "cs_1")
    _post_checkout_event(client, "checkout.session.completed", "cs_2")
    still_processing = db.session.get(CheckoutHold, 2).status
    _post_checkout_event(client, "checkout.session.async_payment_failed", "cs_2")

    db.session.expire_all()
    assert still_processing == CheckoutHoldStatus.OPEN
    assert db.session.get(CheckoutHold, 1).status == CheckoutHoldStatus.EXPIRED
    failed = db.session.get(CheckoutHold, 2)
    assert (failed.status, failed.failure_message) == (
        CheckoutHoldStatus.EXPIRED,
        checkout_service.PAYMENT_FAILED_MESSAGE,
    )
    assert Payment.query.count() == 0


def test_checkout_webhook_rejects_bad_signatures(client, team, paid_tournament, stripe_gateway):
    login_as(client, team)
    _start_team_checkout(client)
    stripe_gateway.pay("cs_1")

    response = _post_checkout_event(client, "checkout.session.completed", "cs_1", signature="forged")

    assert response.status_code == 400
    assert _team_registration(team.id) is None


def test_managers_pay_for_their_team(client, player, team, paid_tournament, stripe_gateway):
    outsider = Player(id="outsider", name="Outsider", pw_hash="x")
    db.session.add_all([outsider, TeamManager(team_id=team.id, player_id=player.id)])
    db.session.commit()

    login_as(client, outsider)
    forbidden = _start_team_checkout(client, team_id=team.id)
    login_as(client, player)
    allowed = _start_team_checkout(client, team_id=team.id)

    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    hold = CheckoutHold.query.one()
    assert (hold.holder_team, hold.payer_user_id, hold.payer_user_type) == (team.id, player.id, "player")


def test_organizers_still_register_teams_without_payment(client, team, paid_tournament):
    organizer = db.session.get(Player, "organizer")
    login_as(client, organizer)

    response = client.post(
        "/_api/test-tournament/register-team-as-to",
        json={"team_id": team.id, "pseudonym": "Comped"},
    )

    assert response.status_code == 200
    registration = _team_registration(team.id)
    assert (registration.status, registration.paid) == ("CONFIRMED", True)
    assert Payment.query.count() == 0
