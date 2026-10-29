"""HTTP name validation preserves error responses and stored records."""

import pytest

from models import TO, League, Match, TeamRegistration, db
from tests.utils import login_as, make_registrable_config


@pytest.fixture
def organizer(client, tournament, player):
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    login_as(client, player)


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", ["matches", "break-groups"])
def test_create_match_rejects_reserved_punctuation(client, tournament, organizer, endpoint):
    response = client.post(
        f"/_api/tournaments/{tournament.url}/{endpoint}",
        json={"name": "Final/Replay"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": 'Match names cannot contain "/".'}
    assert Match.query.filter_by(event=tournament.url).count() == 0


@pytest.mark.integration
def test_add_match_form_rejects_reserved_punctuation(client, tournament, organizer):
    response = client.post(
        f"/_api/{tournament.url}/add-match",
        data={"match_name": "Final, Replay"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"success": False, "error": 'Match names cannot contain ",".'}
    assert Match.query.filter_by(event=tournament.url).count() == 0


@pytest.mark.integration
def test_update_match_form_rejects_reserved_punctuation(client, tournament, organizer):
    match = Match(name="Final", event=tournament.url, schedule_type="STATIC", set_type="SETS")
    db.session.add(match)
    db.session.commit()

    response = client.post(
        f"/_api/{tournament.url}/update-match",
        data={"match_id": match.uuid, "match_name": "Final::winner"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"success": False, "error": 'Match names cannot contain "::".'}
    db.session.refresh(match)
    assert match.name == "Final"


@pytest.mark.integration
def test_team_registration_rejects_reserved_punctuation(client, tournament, team):
    login_as(client, team)

    response = client.post(
        f"/_api/{tournament.url}/register-team",
        data={"pseudonym": "North, South"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"success": False, "error": 'Team pseudonyms cannot contain ",".'}
    assert TeamRegistration.query.filter_by(event=tournament.url, team=team.id).count() == 0


@pytest.mark.integration
@pytest.mark.parametrize("scope", ["tournaments", "leagues"])
@pytest.mark.parametrize(
    ("pseudonym", "message"),
    [
        ("North::South", 'Team pseudonyms cannot contain "::".'),
        (" ", "Team name is required"),
    ],
)
def test_team_registration_edit_rejects_invalid_name(client, tournament, team, scope, pseudonym, message):
    if scope == "leagues":
        config = make_registrable_config(team_registration_open=True)
        league = League(url="test-league", name="Test League", published=True, registrable_config_id=config.id)
        db.session.add(league)
        db.session.flush()
        registration = TeamRegistration(league_id=league.url, team=team.id, pseudonym="Original", status="CONFIRMED")
        event_url = league.url
    else:
        registration = TeamRegistration(event=tournament.url, team=team.id, pseudonym="Original", status="CONFIRMED")
        event_url = tournament.url
    db.session.add(registration)
    db.session.commit()
    login_as(client, team)

    response = client.put(
        f"/_api/{scope}/{event_url}/registrations/team/me",
        json={"pseudonym": pseudonym},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": message}
    db.session.refresh(registration)
    assert registration.pseudonym == "Original"
