"""Public contact choices and private injury defaults preserve explicit intent."""

import pytest
from flask import g

from app.domain.enums import MatchStatus
from models import Injury, Match, Player, PlayerRegistration, Team, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("account_type", ["team", "player"])
def test_google_signup_does_not_publish_team_identity_email(client, account_type):
    with client.session_transaction() as session:
        session["google_oauth_data"] = {
            "user_type": account_type,
            "google_id": "fixture-google-id",
            "email": "private@example.test",
            "name": "Fixture account",
        }
    response = client.post(
        "/_api/google/complete-profile", json={"username": "fixture-account", "display_name": "Fixture account"}
    )
    assert response.status_code == 200
    account = db.session.get(Team if account_type == "team" else Player, "fixture-account")
    assert account.google_id == "fixture-google-id"
    assert account.email == (None if account_type == "team" else "private@example.test")
    with client.session_transaction() as session:
        assert "google_oauth_data" not in session


def test_team_contact_address_stays_public_until_owner_removes_it(client, team):
    team.email = "contact@example.test"
    db.session.commit()
    assert client.get(f"/_api/teams/{team.id}").get_json()["team"]["email"] == "contact@example.test"
    login_as(client, team)
    g.pop("_login_user", None)
    assert client.put(f"/_api/teams/{team.id}", json={"name": "New team name"}).status_code == 200
    assert team.email == "contact@example.test"
    assert client.put(f"/_api/teams/{team.id}", json={"email": ""}).status_code == 200
    assert not client.get(f"/_api/teams/{team.id}").get_json()["team"]["email"]


def test_new_injuries_are_private_and_existing_visibility_survives_edit(client, player):
    original = Injury(player=player.id, message="Previously public fixture", show=True, active=False)
    defaulted = Injury(player=player.id, message="Private by model default", active=True)
    db.session.add_all([original, defaulted])
    db.session.commit()
    assert defaulted.show is False
    login_as(client, player)
    created = client.post(f"/_api/players/{player.id}/injuries", json={"message": "Private fixture", "active": True})
    assert created.status_code == 200
    assert created.get_json()["show"] is False
    edited = client.put(f"/_api/players/{player.id}/injuries/{original.id}", json={"message": "Edited fixture"})
    assert edited.status_code == 200 and edited.get_json()["show"] is True
    owner_ids = {injury["id"] for injury in client.get(f"/_api/players/{player.id}").get_json()["injuries"]}
    assert owner_ids == {original.id, defaulted.id, created.get_json()["id"]}
    with client.session_transaction() as session:
        session.clear()
    client.delete_cookie("remember_token")
    g.pop("_login_user", None)
    public_ids = {injury["id"] for injury in client.get(f"/_api/players/{player.id}").get_json()["injuries"]}
    assert public_ids == {original.id}
    assert client.get(f"/_api/players/{player.id}/injuries").status_code in (302, 401)


def test_active_private_injury_remains_available_in_authorized_match_setup(
    client, player, tournament, head_ref_player, seeded_teams
):
    db.session.add(
        PlayerRegistration(player=player.id, event=tournament.url, team="team1", status="CONFIRMED", paid=True)
    )
    db.session.add_all(
        [
            Injury(player=player.id, message="Active private fixture", active=True, show=False),
            Injury(player=player.id, message="Healed private fixture", active=False, show=False),
        ]
    )
    match = Match(
        name="Fixture match",
        event=tournament.url,
        team1="team1",
        team2="team2",
        field="Field 1",
        schedule_type="SAFE",
        set_type="SETS",
        status=MatchStatus.READY_TO_START,
        nominal_length=60,
    )
    db.session.add(match)
    db.session.commit()
    login_as(client, head_ref_player)
    response = client.get(f"/_api/tournaments/{tournament.url}/start-match", query_string={"match_id": match.uuid})
    assert response.status_code == 200
    roster = {entry["id"]: entry for entry in response.get_json()["all_players"]}
    assert roster[player.id]["injuries"] == ["Active private fixture"]
