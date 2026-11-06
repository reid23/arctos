"""License keys are hashed, unbound until entered, and limited to one holder."""

from datetime import datetime

import pytest
from werkzeug.security import check_password_hash

from app.models.constants import AUTH_STRING_LEN
from app.models.license import LICENSE_KIND_EVENT, LICENSE_KIND_LEAGUE
from app.services.license_service import LicenseService
from app.services.public_league_service import PublicLeagueService
from app.services.public_page_service import PublicPageService
from models import League, License, Tournament, db
from tests.utils import make_registrable_config

pytestmark = pytest.mark.unit


def issue(kind, teams):
    return LicenseService.issue(kind, teams).unwrap()


def row_for(key):
    key_id, _secret = key.split(".", 1)
    return License.query.filter_by(key_id=key_id).one()


def standalone(slug):
    event = Tournament(
        url=slug,
        name=slug,
        start_date=datetime(2030, 6, 15),
        location="",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(event)
    db.session.commit()
    return event


def league(slug="paid-league"):
    row = League(
        url=slug,
        name="Paid League",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def league_event(parent, slug):
    event = Tournament(
        url=slug,
        name=slug,
        start_date=datetime(2030, 7, 1),
        location="",
        published=True,
        league_id=parent.url,
        registrable_config_id=None,
    )
    db.session.add(event)
    db.session.commit()
    return event


def test_issued_key_records_payment_and_stores_only_a_password_hash(test_db):
    key = issue(LICENSE_KIND_EVENT, 4)
    key_id, secret = key.split(".", 1)
    row = row_for(key)
    assert row.kind == LICENSE_KIND_EVENT
    assert row.amount_paid_cents == 2000
    assert row.team_allowance == 4
    assert row.tournament_url is None
    assert row.league_url is None
    assert row.redeemed_at is None
    assert secret not in row.key_hash
    assert key not in row.key_hash
    assert len(row.key_hash) <= AUTH_STRING_LEN
    assert check_password_hash(row.key_hash, secret)
    assert not check_password_hash(row.key_hash, "wrong-secret")
    assert key_id == row.key_id

    league_key = issue(LICENSE_KIND_LEAGUE, 3)
    assert row_for(league_key).amount_paid_cents == 3000
    assert row_for(league_key).team_allowance == 3
    assert row_for(league_key).kind == LICENSE_KIND_LEAGUE


@pytest.mark.parametrize("team_count", [0, True, 10_001])
def test_issue_rejects_unusable_team_counts(test_db, team_count):
    assert LicenseService.issue(LICENSE_KIND_EVENT, team_count).is_err()


def test_issue_rejects_unknown_kind(test_db):
    assert LicenseService.issue("vip", 2).is_err()


def test_redeem_is_idempotent_and_a_key_cannot_be_used_twice(test_db, tournament):
    key = issue(LICENSE_KIND_EVENT, 4)
    first = LicenseService.redeem_event(tournament.url, key).unwrap()
    second = LicenseService.redeem_event(tournament.url, key).unwrap()
    assert first.id == second.id
    assert License.query.count() == 1
    assert first.tournament_url == tournament.url
    assert first.redeemed_at is not None

    other = standalone("other-event")
    error = LicenseService.redeem_event(other.url, key).unwrap_err()
    assert str(error) == "This license is already used on another event."
    assert row_for(key).tournament_url == tournament.url

    LicenseService.clear_event(tournament.url).unwrap()
    moved = LicenseService.redeem_event(other.url, key).unwrap()
    assert moved.tournament_url == other.url
    assert moved.league_url is None
    assert License.query.filter_by(tournament_url=tournament.url).count() == 0


def test_event_and_league_keys_are_not_interchangeable(test_db, tournament):
    parent = league()
    event_key = issue(LICENSE_KIND_EVENT, 1)
    league_key = issue(LICENSE_KIND_LEAGUE, 2)

    event_error = LicenseService.redeem_event(tournament.url, league_key).unwrap_err()
    assert str(event_error) == "This license is for a league, not an event."
    assert row_for(league_key).tournament_url is None
    assert row_for(league_key).league_url is None

    league_error = LicenseService.redeem_league(parent.url, event_key).unwrap_err()
    assert str(league_error) == "This license is for an event, not a league."
    assert row_for(event_key).tournament_url is None


def test_league_event_does_not_accept_or_consume_an_event_key(test_db):
    parent = league()
    child = league_event(parent, "league-round")
    key = issue(LICENSE_KIND_EVENT, 1)
    error = LicenseService.redeem_event(child.url, key).unwrap_err()
    assert "league settings" in str(error)
    assert row_for(key).tournament_url is None
    assert row_for(key).league_url is None
    assert row_for(key).redeemed_at is None


@pytest.mark.parametrize(
    "key",
    ["nope", "zzzzzzzzzzzzzzzz.not-a-real-secret", ""],
)
def test_invalid_keys_are_rejected_without_a_redemption(test_db, tournament, key):
    error = LicenseService.redeem_event(tournament.url, key).unwrap_err()
    assert str(error) == "That license key is not valid."
    assert License.query.count() == 0


def test_wrong_secret_matches_the_invalid_key_message(test_db, tournament):
    key = issue(LICENSE_KIND_EVENT, 1)
    key_id, _secret = key.split(".", 1)
    error = LicenseService.redeem_event(tournament.url, f"{key_id}.wrong-secret").unwrap_err()
    assert str(error) == "That license key is not valid."
    assert row_for(key).tournament_url is None
    assert row_for(key).redeemed_at is None


def test_release_clears_the_holder_without_deleting_the_key(test_db, tournament):
    key = issue(LICENSE_KIND_EVENT, 2)
    LicenseService.redeem_event(tournament.url, key).unwrap()
    LicenseService.release_event(tournament.url)
    db.session.commit()
    stored = row_for(key)
    assert stored.tournament_url is None
    assert stored.league_url is None
    assert stored.redeemed_at is None
    assert License.query.count() == 1


def test_public_pages_show_a_direct_license_and_an_inherited_league_license(test_db, tournament):
    assert PublicPageService.event(tournament.url).unwrap().card.licensed is False
    LicenseService.redeem_event(tournament.url, issue(LICENSE_KIND_EVENT, 4)).unwrap()
    assert PublicPageService.event(tournament.url).unwrap().card.licensed is True

    parent = league()
    child = league_event(parent, "covered-round")
    assert PublicPageService.event(child.url).unwrap().card.licensed is False
    assert PublicLeagueService.league(parent.url).unwrap().card.licensed is False
    LicenseService.redeem_league(parent.url, issue(LICENSE_KIND_LEAGUE, 6)).unwrap()
    assert PublicLeagueService.league(parent.url).unwrap().card.licensed is True
    assert PublicPageService.event(child.url).unwrap().card.licensed is True
