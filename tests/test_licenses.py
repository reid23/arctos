"""Organizers enter a license once; the key never comes back out."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.license_service import LicenseService
from models import League, License, Player, TO, Tournament, db
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


def event_key(teams=4):
    return LicenseService.issue("event", teams).unwrap()


def row_for(key):
    return License.query.filter_by(key_id=key.split(".", 1)[0]).one()


def organize(client, player, tournament):
    db.session.add(TO(user_id=player.id, user_type="player", event=tournament.url))
    db.session.commit()
    login_as(client, player)


def other_event(slug):
    event = Tournament(
        url=slug,
        name=slug,
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=1),
        location="Elsewhere",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(event)
    db.session.commit()
    return event


def test_organizer_can_enter_a_key_and_responses_do_not_contain_it(client, player, tournament):
    organize(client, player, tournament)
    key = event_key()
    key_id, secret = key.split(".", 1)
    response = client.post(f"/_api/{tournament.url}/license", json={"key": key})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    text = response.get_data(as_text=True)
    assert body["team_allowance"] == 4
    assert body["amount_paid_cents"] == 2000
    assert secret not in text
    assert key not in text
    assert "key_hash" not in text

    detail = client.get(f"/_api/tournaments/{tournament.url}")
    assert detail.status_code == 200
    payload = detail.get_json()["tournament"]
    detail_text = detail.get_data(as_text=True)
    assert payload["licensed"] is True
    assert payload["license_team_allowance"] == 4
    assert payload["license_price_cents"] == 500
    assert "amount_paid_cents" not in detail_text
    assert secret not in detail_text
    assert key_id not in detail_text
    assert "key_hash" not in detail_text


def test_anonymous_and_non_organizer_cannot_enter_a_key(client, player, tournament):
    key = event_key(1)
    anonymous = client.post(f"/_api/{tournament.url}/license", json={"key": key})
    assert anonymous.status_code == 401
    assert row_for(key).tournament_url is None

    outsider = Player(id="outsider", name="Outsider", phone="1234567890")
    outsider.set_password("testpass")
    db.session.add(outsider)
    db.session.commit()
    login_as(client, outsider)
    rejected = client.post(f"/_api/{tournament.url}/license", json={"key": key})
    assert rejected.status_code == 403
    assert key.split(".", 1)[1] not in rejected.get_data(as_text=True)
    assert row_for(key).tournament_url is None
    assert row_for(key).league_url is None


def test_empty_key_is_rejected(client, player, tournament):
    organize(client, player, tournament)
    response = client.post(f"/_api/{tournament.url}/license", json={"key": "  "})
    assert response.status_code == 400
    assert response.get_json()["error"] == "Enter a license key."
    assert License.query.count() == 0


def test_removing_or_deleting_the_event_frees_the_key(client, player, tournament):
    other = other_event("second-event")
    db.session.add(TO(user_id=player.id, user_type="player", event=other.url))
    organize(client, player, tournament)
    key = event_key(2)

    assert client.post(f"/_api/{tournament.url}/license", json={"key": key}).status_code == 200
    conflict = client.post(f"/_api/{other.url}/license", json={"key": key})
    assert conflict.status_code == 400
    assert conflict.get_json()["error"] == "This license is already used on another event."

    removed = client.delete(f"/_api/{tournament.url}/license")
    assert removed.status_code == 200
    assert removed.get_json()["success"] is True
    reused = client.post(f"/_api/{other.url}/license", json={"key": key})
    assert reused.status_code == 200, reused.get_data(as_text=True)
    assert row_for(key).tournament_url == other.url

    deleted = client.post(f"/_api/{other.url}/delete", data={"confirm_url": other.url})
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert row_for(key).tournament_url is None
    assert row_for(key).redeemed_at is None
    again = client.post(f"/_api/{tournament.url}/license", json={"key": key})
    assert again.status_code == 200, again.get_data(as_text=True)


def test_league_organizer_enters_a_league_key(client, player):
    row = League(
        url="paid-league",
        name="Paid League",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(TO(user_id=player.id, user_type="player", league_id=row.url))
    db.session.commit()
    login_as(client, player)
    key = LicenseService.issue("league", 3).unwrap()
    secret = key.split(".", 1)[1]

    response = client.post(f"/_api/leagues/{row.url}/license", json={"key": key})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["team_allowance"] == 3
    assert body["amount_paid_cents"] == 3000
    assert secret not in response.get_data(as_text=True)

    detail = client.get(f"/_api/leagues/{row.url}").get_json()["league"]
    assert detail["licensed"] is True
    assert detail["license_team_allowance"] == 3
    assert detail["license_price_cents"] == 1000
    assert "amount_paid_cents" not in client.get(f"/_api/leagues/{row.url}").get_data(as_text=True)
