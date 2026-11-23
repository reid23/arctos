"""Integration tests for organizer registration management in the public API (``manage`` scope)."""

from datetime import datetime, timezone

import pytest

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from models import TO, League, Player, PlayerRegistration, Team, TeamRegistration, Tournament, db
from tests.utils import issue_access_token, make_registrable_config

pytestmark = pytest.mark.integration


@pytest.fixture
def world(test_db):
    """A published standalone tournament and league, each with one organizer, a team, and a player."""
    tournament = Tournament(
        url="spring-open",
        name="Spring Open",
        start_date=datetime.now(timezone.utc),
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    league = League(url="metro", name="Metro", published=True, registrable_config_id=make_registrable_config().id)
    organizer = Player(id="organizer", name="Organizer", pw_hash="x")
    bystander = Player(id="bystander", name="Bystander", pw_hash="x")
    player = Player(id="jsmith", name="Jane Smith", pw_hash="x")
    team = Team(id="bears", name="Bears", pw_hash="x")
    db.session.add_all([tournament, league, organizer, bystander, player, team])
    db.session.flush()
    db.session.add(TO(user_id=organizer.id, user_type="player", event=tournament.url))
    db.session.add(TO(user_id=organizer.id, user_type="player", league_id=league.url))
    db.session.add(
        TeamRegistration(
            league_id=league.url, team=team.id, pseudonym="Metro Bears", status=TeamRegistrationStatus.CONFIRMED
        )
    )
    db.session.add(
        PlayerRegistration(league_id=league.url, player=player.id, team=team.id, status=RegistrationStatus.CONFIRMED)
    )
    db.session.commit()
    return {"organizer": organizer, "bystander": bystander}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_tournament_registration_lifecycle(client, world):
    headers = _bearer(issue_access_token(world["organizer"], scope="manage"))
    base = "/api/v1/tournaments/spring-open/registrations"

    team = client.post(f"{base}/teams", headers=headers, json={"team_id": "bears", "name": "Spring Bears"})
    assert team.status_code == 201, team.get_json()
    assert team.get_json()["registration"]["name"] == "Spring Bears"
    player = client.post(
        f"{base}/players",
        headers=headers,
        json={"player_id": "jsmith", "team_id": "bears", "jersey_name": "SMITH", "jersey_number": "7"},
    )
    assert player.status_code == 201, player.get_json()
    player_registration_id = player.get_json()["registration"]["id"]

    paid = client.patch(
        f"{base}/players/{player_registration_id}",
        headers=headers,
        json={"paid": True, "amount_paid": 12.5, "payment_method": "cash"},
    )
    assert paid.status_code == 200, paid.get_json()
    assert paid.get_json()["registration"]["payment"]["amount_paid"] == 12.5

    listing = client.get(base, headers=headers).get_json()
    assert [row["team"]["id"] for row in listing["teams"]] == ["bears"]
    [listed_player] = listing["players"]
    assert listed_player["team"]["name"] == "Spring Bears"
    assert listed_player["payment"]["paid"] is True
    assert listed_player["payment"]["payment_method"] == "cash"
    assert listed_player["waiver_status"] is None

    assert client.delete(f"{base}/teams/bears", headers=headers).status_code == 204
    listing = client.get(base, headers=headers).get_json()
    assert listing["teams"] == []
    assert [(player["player"]["id"], player["team"]) for player in listing["players"]] == [("jsmith", None)]
    assert client.delete(f"{base}/teams/bears", headers=headers).status_code == 404


def test_league_registration_payment_and_deregistration(client, world):
    headers = _bearer(issue_access_token(world["organizer"], scope="manage"))
    base = "/api/v1/leagues/metro/registrations"

    listing = client.get(base, headers=headers).get_json()
    [team] = listing["teams"]
    assert team["name"] == "Metro Bears"
    assert team["payment"]["paid"] is False

    paid = client.patch(f"{base}/teams/{team['id']}", headers=headers, json={"paid": True, "amount_paid": 40})
    assert paid.status_code == 200
    assert TeamRegistration.query.get(team["id"]).paid is True

    rejected = client.patch(f"{base}/teams/{team['id']}", headers=headers, json={"paid": "yes"})
    assert rejected.status_code == 400

    assert client.delete(f"{base}/players/jsmith", headers=headers).status_code == 204
    assert PlayerRegistration.query.filter_by(player="jsmith").one().status == RegistrationStatus.CANCELLED


@pytest.mark.parametrize(
    "path",
    ["/api/v1/tournaments/spring-open/registrations", "/api/v1/leagues/metro/registrations"],
)
def test_registrations_need_manage_scope_and_organizer(client, world, path):
    assert client.get(path, headers=_bearer(issue_access_token(world["organizer"], scope="footage"))).status_code == 403
    assert client.get(path, headers=_bearer(issue_access_token(scope="manage"))).status_code == 403
    bystander = client.get(path, headers=_bearer(issue_access_token(world["bystander"], scope="manage")))
    assert bystander.status_code == 403
    assert "organizers" in bystander.get_json()["error"]


def test_unpublished_tournament_is_hidden_from_non_organizers(client, world):
    Tournament.query.get("spring-open").published = False
    db.session.commit()
    path = "/api/v1/tournaments/spring-open/registrations"
    assert client.get(path, headers=_bearer(issue_access_token(world["bystander"], scope="manage"))).status_code == 404
    assert client.get(path, headers=_bearer(issue_access_token(world["organizer"], scope="manage"))).status_code == 200
