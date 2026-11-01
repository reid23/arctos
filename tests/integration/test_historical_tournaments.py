"""HTTP tests for historical tournaments: records editor, claims, rosters, and listings."""

import pytest

from models import Player, Team, db
from tests.utils import login_as


@pytest.fixture
def organizer(test_db):
    player = Player(id="organizer", name="Organizer", pw_hash="x")
    player.set_password("pw")
    db.session.add(player)
    db.session.commit()
    return player


@pytest.fixture
def real_team(test_db):
    team = Team(id="rigor", name="Rigor Mortis", pw_hash="x")
    team.set_password("pw")
    db.session.add(team)
    db.session.commit()
    return team


@pytest.fixture
def roster_player(test_db):
    player = Player(id="runner", name="Runner", pw_hash="x")
    player.set_password("pw")
    db.session.add(player)
    db.session.commit()
    return player


@pytest.fixture
def historical_url(client, organizer):
    login_as(client, organizer)
    response = client.post(
        "/_api/create-historical-tournament",
        json={
            "name": "Old Cup",
            "url": "old-cup",
            "location": "Berlin",
            "start_date": "2019-06-01",
            "end_date": "2019-06-02",
            "teams": ["Alpha", "Beta", "Gamma"],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["url"]


def editor_state(client, url) -> dict:
    response = client.get(f"/_api/tournaments/{url}/records")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def team_id_named(state, name) -> str:
    return next(team["id"] for team in state["teams"] if team["name"] == name)


def publish(client, url):
    response = client.put(
        f"/_api/tournaments/{url}/records/details",
        json={"name": "Old Cup", "location": "Berlin", "start_date": "2019-06-01", "end_date": "", "published": True},
    )
    assert response.status_code == 200, response.get_data(as_text=True)


@pytest.mark.integration
def test_create_and_open_records_editor(client, historical_url):
    state = editor_state(client, historical_url)

    assert state["tournament"]["historical"] is True
    assert state["tournament"]["published"] is False
    assert [team["name"] for team in state["teams"]] == ["Alpha", "Beta", "Gamma"]
    assert all(team["placeholder"] for team in state["teams"])


@pytest.mark.integration
def test_records_editor_is_organizer_only(client, historical_url, roster_player):
    login_as(client, roster_player)

    response = client.get(f"/_api/tournaments/{historical_url}/records")

    assert response.status_code == 403


@pytest.mark.integration
def test_saving_a_match_returns_the_refreshed_editor_state(client, historical_url):
    response = client.post(
        f"/_api/tournaments/{historical_url}/records/matches",
        json={
            "name": "Final",
            "team1": "Alpha",
            "team2": "Beta",
            "referees": ["Gamma"],
            "winner": "TEAM2",
            "points": [{"set": 1, "winner": "TEAM2"}, {"set": 1, "winner": "TEAM1"}],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    [match] = body["matches"]
    assert match["uuid"] == body["match_uuid"]
    assert match["winner"] == "TEAM2"
    assert len(match["points"]) == 2
    assert next(team for team in body["teams"] if team["name"] == "Gamma")["match_count"] == 1


@pytest.mark.integration
def test_invalid_match_returns_a_readable_error(client, historical_url):
    response = client.post(
        f"/_api/tournaments/{historical_url}/records/matches",
        json={"name": "Final", "team1": "Alpha", "team2": "Alpha", "winner": "TEAM1"},
    )

    assert response.status_code == 400
    assert "same team on both sides" in response.get_json()["error"]


@pytest.mark.integration
def test_deleting_a_match(client, historical_url):
    saved = client.post(
        f"/_api/tournaments/{historical_url}/records/matches",
        json={"name": "Final", "team1": "Alpha", "team2": "Beta", "winner": "TEAM1"},
    ).get_json()

    response = client.delete(f"/_api/tournaments/{historical_url}/records/matches/{saved['match_uuid']}")

    assert response.status_code == 200
    assert response.get_json()["matches"] == []


@pytest.mark.integration
def test_published_historical_tournament_is_flagged_on_the_homepage(client, historical_url):
    publish(client, historical_url)
    client.get("/_api/logout")

    response = client.get("/_api/tournaments")

    [listed] = [t for t in response.get_json()["tournaments"] if t["url"] == historical_url]
    assert listed["historical"] is True
    assert response.get_json()["team_counts"][historical_url] == 3


@pytest.mark.integration
def test_placeholders_are_hidden_from_the_team_list(client, historical_url, real_team):
    response = client.get("/_api/teams")

    listed = [team["id"] for team in response.get_json()["teams"]]
    assert "rigor" in listed
    assert not any(team_id.startswith("_") for team_id in listed)


@pytest.mark.integration
def test_general_settings_route_refuses_historical_tournaments(client, historical_url):
    response = client.post(f"/_api/{historical_url}/update-settings", data={"name": "Renamed"})

    assert response.status_code == 400
    assert "records editor" in response.get_json()["error"]


@pytest.mark.integration
def test_team_claims_a_placeholder(client, historical_url, real_team):
    state = editor_state(client, historical_url)
    alpha = team_id_named(state, "Alpha")
    invited = client.put(
        f"/_api/tournaments/{historical_url}/records/teams/{alpha}/claim-invitation", json={"team_id": "rigor"}
    ).get_json()
    invited_alpha = next(team for team in invited["teams"] if team["id"] == alpha)
    assert invited_alpha["claim_invitation"] == {"team_id": "rigor", "team_name": "Rigor Mortis"}
    publish(client, historical_url)

    login_as(client, real_team)
    [claim] = client.get(f"/_api/me/invitations?tournament={historical_url}").get_json()["team_claims"]
    assert claim["placeholder_name"] == "Alpha"
    accepted = client.post(f"/_api/me/invitations/claims/{claim['id']}/accept")
    assert accepted.get_json()["tournament_url"] == historical_url

    detail = client.get(f"/_api/tournaments/{historical_url}").get_json()
    claimed = next(team for team in detail["teams_with_counts"] if team["pseudonym"] == "Alpha")
    assert claimed["team_id"] == "rigor"
    assert claimed["placeholder"] is False
    assert detail["is_current_team_registered"] is True
    profile = client.get("/_api/teams/rigor").get_json()
    assert any(
        registration["event"] == historical_url and registration["historical"]
        for registration in profile["registrations"]
    )


@pytest.mark.integration
def test_player_accepts_a_roster_invitation(client, historical_url, roster_player):
    alpha = team_id_named(editor_state(client, historical_url), "Alpha")
    roster = client.post(
        f"/_api/tournaments/{historical_url}/rosters/{alpha}/players",
        json={"player_id": "runner", "jersey_number": "7"},
    ).get_json()
    assert [(player["player_id"], player["status"]) for player in roster["players"]] == [("runner", "INVITED")]

    publish(client, historical_url)
    login_as(client, roster_player)
    [invitation] = client.get("/_api/me/invitations").get_json()["roster_invitations"]
    assert (invitation["team_name"], invitation["jersey_number"]) == ("Alpha", "7")
    response = client.post(f"/_api/me/invitations/rosters/{invitation['id']}/accept")
    assert response.status_code == 200

    assert client.get("/_api/me/invitations").get_json()["roster_invitations"] == []


@pytest.mark.integration
def test_only_players_respond_to_roster_invitations(client, historical_url, real_team):
    login_as(client, real_team)

    response = client.post("/_api/me/invitations/rosters/1/accept")

    assert response.status_code == 403


@pytest.mark.integration
def test_unpublished_records_stay_off_profiles(client, historical_url, real_team, roster_player):
    from models import Player

    state = editor_state(client, historical_url)
    alpha = team_id_named(state, "Alpha")
    beta = team_id_named(state, "Beta")
    publish(client, historical_url)
    client.put(f"/_api/tournaments/{historical_url}/records/teams/{alpha}/claim-invitation", json={"team_id": "rigor"})
    login_as(client, real_team)
    [claim] = client.get("/_api/me/invitations").get_json()["team_claims"]
    client.post(f"/_api/me/invitations/claims/{claim['id']}/accept")

    login_as(client, roster_player)
    requested = client.post(
        f"/_api/tournaments/{historical_url}/roster-requests",
        json={"team_id": "rigor", "jersey_name": "Runner", "jersey_number": "9"},
    )
    assert requested.status_code == 200
    assert client.get("/_api/players/runner").get_json()["registrations"] == []

    login_as(client, real_team)
    [request] = client.get("/_api/me/invitations").get_json()["incoming_roster_requests"]
    approved = client.post(f"/_api/tournaments/{historical_url}/rosters/rigor/requests/{request['id']}/approve")
    assert approved.status_code == 200
    assert any(
        row["event"] == historical_url and row["historical"]
        for row in client.get("/_api/teams/rigor").get_json()["registrations"]
    )
    assert any(
        row["event"] == historical_url and row["historical"]
        for row in client.get("/_api/players/runner").get_json()["registrations"]
    )

    login_as(client, Player.query.get("organizer"))
    unpublished = client.put(
        f"/_api/tournaments/{historical_url}/records/details",
        json={"name": "Old Cup", "location": "Berlin", "start_date": "2019-06-01", "end_date": "", "published": False},
    )
    assert unpublished.status_code == 200

    login_as(client, real_team)
    assert all(row["event"] != historical_url for row in client.get("/_api/teams/rigor").get_json()["registrations"])
    assert all(row["event"] != historical_url for row in client.get("/_api/players/runner").get_json()["registrations"])
    assert client.get(f"/_api/teams/{beta}").status_code == 404

    login_as(client, Player.query.get("organizer"))
    assert client.get(f"/_api/teams/{beta}").status_code == 200


@pytest.mark.integration
def test_bracket_diagram_uses_recorded_links(client, historical_url):
    semi = client.post(
        f"/_api/tournaments/{historical_url}/records/matches",
        json={"name": "Semi 1", "team1": "Alpha", "team2": "Beta", "winner": "TEAM1"},
    ).get_json()
    final = client.post(
        f"/_api/tournaments/{historical_url}/records/matches",
        json={"name": "Final", "team1": "Semi 1::winner", "team2": "Gamma", "winner": "TEAM1"},
    ).get_json()

    assert (
        client.post(
            f"/_api/tournaments/{historical_url}/bracket-placements/add",
            json={"match": semi["match_uuid"], "x_pos": 40, "y_pos": 40},
        ).status_code
        == 200
    )
    placed = client.post(
        f"/_api/tournaments/{historical_url}/bracket-placements/add",
        json={"match": final["match_uuid"], "x_pos": 400, "y_pos": 40},
    )
    assert placed.status_code == 200
    final_placement = next(row for row in placed.get_json()["placements"] if row["match"] == final["match_uuid"])
    assert final_placement["team1"] == "NET"

    matches = client.get(f"/_api/tournaments/{historical_url}/bracket-matches").get_json()["matches"]
    recorded = next(match for match in matches if match["uuid"] == final["match_uuid"])
    assert recorded["team1_initial"] == "Semi 1::winner"
