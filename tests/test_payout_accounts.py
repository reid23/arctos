"""Payout accounts: Stripe onboarding, account webhooks, and attaching an account to an event or league."""

import json
from datetime import datetime, timezone

import pytest

from app.services import payout_account_service
from models import TO, League, PayoutAccount, Player, Tournament, db
from tests.stripe_fakes import VALID_SIGNATURE
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


def _player(player_id: str) -> Player:
    player = Player(id=player_id, name=player_id.title(), pw_hash="x")
    db.session.add(player)
    db.session.commit()
    return player


def _ready_account(stripe_gateway, player: Player) -> PayoutAccount:
    status = stripe_gateway.create_connected_account(email=None)
    stripe_gateway.finish_onboarding(status.stripe_account_id)
    account = PayoutAccount(
        owner_player=player.id,
        stripe_account_id=status.stripe_account_id,
        charges_enabled=True,
        payouts_enabled=True,
        details_submitted=True,
    )
    db.session.add(account)
    db.session.commit()
    return account


def _make_organizer(player: Player, *, event=None, league_id=None) -> None:
    db.session.add(TO(user_id=player.id, user_type="player", event=event, league_id=league_id))
    db.session.commit()


def _event_settings_url(tournament) -> str:
    return f"/_api/payments/settings/events/{tournament.url}"


def test_payout_account_reports_unavailable_without_stripe(client, player):
    login_as(client, player)

    assert client.get("/_api/payments/payout-account").get_json() == {"available": False, "account": None}
    response = client.post("/_api/payments/payout-account/onboarding")
    assert response.status_code == 404
    assert PayoutAccount.query.count() == 0


def test_onboarding_creates_one_account_and_returns_to_profile(client, player, stripe_gateway):
    login_as(client, player)

    first = client.post("/_api/payments/payout-account/onboarding")
    second = client.post("/_api/payments/payout-account/onboarding")

    assert first.status_code == 200
    assert first.get_json()["url"] == "https://connect.stripe.test/acct_1"
    assert second.get_json()["url"] == "https://connect.stripe.test/acct_1"
    account = PayoutAccount.query.one()
    assert (account.owner_player, account.stripe_account_id) == (player.id, "acct_1")
    assert stripe_gateway.return_urls[0].endswith(f"/players/{player.id}/edit#payouts")


def test_onboarding_keeps_new_account_when_link_fails(client, player, stripe_gateway):
    stripe_gateway.fail_links = True
    login_as(client, player)

    response = client.post("/_api/payments/payout-account/onboarding")

    assert response.status_code == 502
    assert response.get_json()["error"] == payout_account_service.STRIPE_FAILURE_MESSAGE
    db.session.expire_all()
    assert PayoutAccount.query.one().stripe_account_id == "acct_1"


def test_team_accounts_cannot_receive_payouts(client, team, stripe_gateway):
    login_as(client, team)

    assert client.get("/_api/payments/payout-account").status_code == 403
    assert client.post("/_api/payments/payout-account/onboarding").status_code == 403


def test_refresh_mirrors_stripe_after_onboarding(client, player, stripe_gateway):
    login_as(client, player)
    client.post("/_api/payments/payout-account/onboarding")
    stripe_gateway.finish_onboarding("acct_1")

    response = client.post("/_api/payments/payout-account/refresh")

    assert response.get_json()["account"] == {
        "id": PayoutAccount.query.one().id,
        "owner": {"id": player.id, "name": player.name},
        "charges_enabled": True,
        "payouts_enabled": True,
        "details_submitted": True,
    }
    assert PayoutAccount.query.one().default_currency == "usd"


CAPABILITY_EVENT = "v2.core.account[configuration.merchant].capability_status_updated"


def _post_account_event(client, event: dict, signature: str = VALID_SIGNATURE):
    return client.post(
        "/_api/payments/webhooks/accounts",
        data=json.dumps(event),
        headers={"Stripe-Signature": signature},
        content_type="application/json",
    )


def test_account_webhook_rereads_the_account_from_stripe(client, player, stripe_gateway):
    account = _ready_account(stripe_gateway, player)
    stripe_gateway.restrict_charges(account.stripe_account_id)

    response = _post_account_event(client, {"type": CAPABILITY_EVENT, "account": account.stripe_account_id})

    assert response.status_code == 200
    db.session.expire_all()
    assert not PayoutAccount.query.one().charges_enabled


def test_account_webhook_rejects_bad_signatures(client, player, stripe_gateway):
    account = _ready_account(stripe_gateway, player)
    stripe_gateway.restrict_charges(account.stripe_account_id)

    response = _post_account_event(
        client, {"type": CAPABILITY_EVENT, "account": account.stripe_account_id}, signature="forged"
    )

    assert response.status_code == 400
    db.session.expire_all()
    assert PayoutAccount.query.one().charges_enabled


def test_account_webhook_ignores_unknown_accounts_and_other_objects(client, stripe_gateway):
    assert _post_account_event(client, {"type": CAPABILITY_EVENT, "account": "acct_elsewhere"}).status_code == 200
    assert _post_account_event(client, {"type": "v1.billing.meter.no_meter_found"}).status_code == 200
    assert PayoutAccount.query.count() == 0


def test_only_organizers_see_payment_settings(client, player, tournament, stripe_gateway):
    login_as(client, player)

    assert client.get(_event_settings_url(tournament)).status_code == 403


def test_organizer_attaches_own_account_and_enables_online_payments(client, player, tournament, stripe_gateway):
    _make_organizer(player, event=tournament.url)
    account = _ready_account(stripe_gateway, player)
    login_as(client, player)

    before = client.get(_event_settings_url(tournament)).get_json()
    response = client.put(_event_settings_url(tournament), json={"enabled": True, "payout_account_id": account.id})

    assert before["available"] and not before["enabled"] and before["account"] is None
    assert before["own_account"]["id"] == account.id
    body = response.get_json()
    assert response.status_code == 200
    assert body["enabled"] and body["accepting"]
    assert body["account"]["owner"] == {"id": player.id, "name": player.name}
    db.session.expire_all()
    assert tournament.registrable_config.payout_account_id == account.id


def test_online_payments_need_a_ready_account(client, player, tournament, stripe_gateway):
    _make_organizer(player, event=tournament.url)
    login_as(client, player)
    client.post("/_api/payments/payout-account/onboarding")
    account = PayoutAccount.query.one()

    enable = client.put(_event_settings_url(tournament), json={"enabled": True, "payout_account_id": account.id})
    attach_only = client.put(_event_settings_url(tournament), json={"enabled": False, "payout_account_id": account.id})

    assert enable.status_code == 400
    assert attach_only.status_code == 200
    assert attach_only.get_json()["account"]["id"] == account.id


def test_organizer_cannot_attach_someone_elses_account(client, player, tournament, stripe_gateway):
    _make_organizer(player, event=tournament.url)
    other = _ready_account(stripe_gateway, _player("other_organizer"))
    login_as(client, player)

    response = client.put(_event_settings_url(tournament), json={"enabled": True, "payout_account_id": other.id})

    assert response.status_code == 403
    db.session.expire_all()
    assert tournament.registrable_config.payout_account_id is None


def test_co_organizer_must_detach_before_swapping_accounts(client, player, tournament, stripe_gateway):
    co_organizer = _player("co_organizer")
    _make_organizer(player, event=tournament.url)
    _make_organizer(co_organizer, event=tournament.url)
    first = _ready_account(stripe_gateway, player)
    second = _ready_account(stripe_gateway, co_organizer)
    tournament.registrable_config.payout_account_id = first.id
    tournament.registrable_config.online_payments_enabled = True
    db.session.commit()
    login_as(client, co_organizer)

    seen = client.get(_event_settings_url(tournament)).get_json()
    swap = client.put(_event_settings_url(tournament), json={"enabled": True, "payout_account_id": second.id})
    detach = client.put(_event_settings_url(tournament), json={"enabled": False, "payout_account_id": None})
    attach = client.put(_event_settings_url(tournament), json={"enabled": True, "payout_account_id": second.id})

    assert seen["account"]["owner"]["name"] == player.name
    assert swap.status_code == 409
    assert "Player" in swap.get_json()["error"]
    assert detach.status_code == 200 and detach.get_json()["account"] is None
    assert attach.status_code == 200 and attach.get_json()["account"]["id"] == second.id


def test_lost_charges_stop_accepting_without_turning_payments_off(client, player, tournament, stripe_gateway):
    _make_organizer(player, event=tournament.url)
    account = _ready_account(stripe_gateway, player)
    tournament.registrable_config.payout_account_id = account.id
    tournament.registrable_config.online_payments_enabled = True
    db.session.commit()
    stripe_gateway.restrict_charges(account.stripe_account_id)
    _post_account_event(client, {"type": CAPABILITY_EVENT, "account": account.stripe_account_id})
    login_as(client, player)

    settings = client.get(_event_settings_url(tournament)).get_json()

    assert settings["enabled"] and not settings["accepting"]


def test_league_events_take_payment_settings_from_the_league(client, player, stripe_gateway):
    cfg = make_registrable_config()
    db.session.add(League(url="paying-league", name="Paying League", published=True, registrable_config_id=cfg.id))
    db.session.add(
        Tournament(
            url="league-week-1",
            name="League Week 1",
            start_date=datetime.now(timezone.utc),
            league_id="paying-league",
        )
    )
    db.session.commit()
    _make_organizer(player, league_id="paying-league")
    account = _ready_account(stripe_gateway, player)
    login_as(client, player)

    event = client.get("/_api/payments/settings/events/league-week-1")
    league = client.put(
        "/_api/payments/settings/leagues/paying-league", json={"enabled": True, "payout_account_id": account.id}
    )

    assert event.status_code == 400
    assert league.status_code == 200 and league.get_json()["accepting"]
