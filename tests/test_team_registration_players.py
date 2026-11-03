"""Expandable team lists use confirmed rosters from the displayed registration scope."""

from datetime import datetime

import pytest

from models import Injury, League, Player, PlayerRegistration, Team, TeamRegistration, Tournament, db
from tests.utils import make_registrable_config

pytestmark = pytest.mark.integration


@pytest.fixture(params=["event", "league"])
def roster_scope(request, tournament):
    if request.param == "event":
        return {"event": tournament.url}, tournament.url, [f"/_api/tournaments/{tournament.url}"]

    league = League(
        url="roster-season",
        name="Roster Season",
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(league)
    db.session.flush()
    tournament.league_id = league.url
    tournament.registrable_config_id = None
    db.session.commit()
    return (
        {"league_id": league.url},
        f"league:{league.url}",
        [f"/_api/leagues/{league.url}", f"/_api/tournaments/{tournament.url}"],
    )


def test_team_roster_matches_scope_counts_and_omits_private_registration_fields(client, team, roster_scope):
    scope, event_argument, detail_paths = roster_scope
    other_event = Tournament(
        url="other-roster-event",
        name="Other Event",
        start_date=datetime(2026, 1, 1),
        published=True,
        registrable_config_id=make_registrable_config().id,
    )
    other_team = Team(id="other-roster-team", name="Other Team")
    db.session.add_all([other_event, other_team])
    db.session.flush()
    db.session.add(TeamRegistration(**scope, team=team.id, pseudonym="Event Team", status="CONFIRMED"))
    player_specs = [
        ("confirmed", "CONFIRMED", team.id, scope),
        ("pending", "PENDING_TEAM_APPROVAL", team.id, scope),
        ("rejected", "REJECTED", team.id, scope),
        ("cancelled", "CANCELLED", team.id, scope),
        ("other-team", "CONFIRMED", other_team.id, scope),
        ("unattached", "CONFIRMED", None, scope),
        ("other-event", "CONFIRMED", team.id, {"event": other_event.url}),
    ]
    for player_id, status, team_id, registration_scope in player_specs:
        db.session.add(
            Player(
                id=player_id,
                name=f"Player {player_id}",
                profile_photo=f"uploads/{player_id}.png",
                email=f"{player_id}@private.example",
                phone="555-123-4567",
            )
        )
        db.session.flush()
        db.session.add(
            PlayerRegistration(
                **registration_scope,
                player=player_id,
                team=team_id,
                status=status,
                jersey_name="Jersey Name",
                jersey_number="00",
                paid=True,
                amount_paid=25,
                payment_notes="Private payment note",
                waiver_legal_name_signature="Private Legal Name",
            )
        )
    db.session.add(Injury(player="confirmed", message="Private injury", active=True, show=False))
    db.session.commit()

    response = client.get(f"/_api/teams/{team.id}/players", query_string={"event": event_argument})

    assert response.status_code == 200
    roster = response.get_json()
    assert roster == [
        {
            "registration": {"player": "confirmed", "jersey_name": "Jersey Name", "jersey_number": "00"},
            "player": {"id": "confirmed", "name": "Player confirmed", "profile_photo": "uploads/confirmed.png"},
        }
    ]
    for path in detail_paths:
        detail = client.get(path)
        assert detail.status_code == 200
        team_row = next(row for row in detail.get_json()["teams_with_counts"] if row["team_id"] == team.id)
        assert team_row["player_count"] == len(roster)


def test_confirmed_team_with_no_confirmed_players_returns_empty_roster(client, team, player, roster_scope):
    scope, event_argument, detail_paths = roster_scope
    db.session.add(TeamRegistration(**scope, team=team.id, pseudonym="Empty Team", status="CONFIRMED"))
    db.session.add(PlayerRegistration(**scope, team=team.id, player=player.id, status="PENDING_TEAM_APPROVAL"))
    db.session.commit()

    response = client.get(f"/_api/teams/{team.id}/players", query_string={"event": event_argument})

    assert response.status_code == 200
    assert response.get_json() == []
    for path in detail_paths:
        detail = client.get(path)
        assert detail.status_code == 200
        assert detail.get_json()["teams_with_counts"][0]["player_count"] == 0


@pytest.mark.parametrize("status", ["PENDING", "CANCELLED"])
def test_roster_requires_confirmed_team_registration(client, team, player, roster_scope, status):
    scope, event_argument, _ = roster_scope
    db.session.add(TeamRegistration(**scope, team=team.id, pseudonym="Unconfirmed Team", status=status))
    db.session.add(PlayerRegistration(**scope, team=team.id, player=player.id, status="CONFIRMED"))
    db.session.commit()

    response = client.get(f"/_api/teams/{team.id}/players", query_string={"event": event_argument})

    assert response.status_code == 404
    assert response.get_json() == {"error": "Not found"}
