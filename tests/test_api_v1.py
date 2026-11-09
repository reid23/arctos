"""Integration tests for the public API (``/api/v1``): auth, logging, visibility, and read endpoints."""

import base64
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest

from app.domain.enums import MatchStatus, Pompfen, ScheduleType, SetType
from models import (
    TO,
    Field,
    Injury,
    League,
    Match,
    Player,
    PlayerRegistration,
    Point,
    SideComp,
    SideCompRegistration,
    SideCompResult,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from tests.utils import issue_access_token, login_as, make_registrable_config, mark_paid

pytestmark = pytest.mark.integration

BASE = datetime(2026, 5, 1, 12, 0, 0)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(test_db, tournament, team, team_registration, player):
    """A published tournament with matches on two fields, an unpublished one, and a TO."""
    rivals = Team(id="rivals", name="Rivals Account", pw_hash="x", email="rivals@example.com")
    db.session.add(rivals)
    db.session.add(
        TeamRegistration(
            event=tournament.url, team=rivals.id, pseudonym="The Rivals", shortname="RIV", status="CONFIRMED"
        )
    )
    player.email = "player@example.com"
    paid_registration = PlayerRegistration(
        event=tournament.url,
        player=player.id,
        team=team.id,
        jersey_name="Testy",
        jersey_number="7",
        status="CONFIRMED",
    )
    db.session.add(paid_registration)
    mark_paid(paid_registration, 2500)

    organizer = Player(id="organizer", name="Organizer", pw_hash="x")
    db.session.add(organizer)
    secret = Tournament(
        url="secret-cup",
        name="Secret Cup",
        start_date=BASE,
        published=False,
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(secret)
    db.session.flush()
    db.session.add(TO(user_id=organizer.id, user_type="player", event=secret.url))
    db.session.add(TO(user_id=organizer.id, user_type="player", event=tournament.url))
    db.session.add(Field(event=secret.url, name="Hidden Field"))
    db.session.add(Field(event=tournament.url, name="North / Main"))

    def game(name, field, status, start, **extra):
        match = Match(
            name=name,
            event=tournament.url,
            field=field,
            team1=team.id,
            team2=rivals.id,
            scheduled_start_time=start,
            nominal_start_time=start,
            nominal_length=30,
            schedule_type=ScheduleType.STATIC,
            set_type=extra.pop("set_type", SetType.SETS),
            status=status,
            **extra,
        )
        db.session.add(match)
        db.session.flush()
        return match

    live = game("Live Game", "Field 1", MatchStatus.IN_PROGRESS, BASE)
    db.session.add(
        Point(match=live.uuid, winner="TEAM1", set_number=1, stamp=BASE, end_stamp=BASE + timedelta(minutes=1))
    )
    db.session.add(Point(match=live.uuid, winner=None, set_number=1, stamp=BASE + timedelta(minutes=2)))
    finished = game(
        "Finished Game",
        "Field 2",
        MatchStatus.COMPLETED,
        BASE - timedelta(hours=1),
        completed_time=BASE - timedelta(minutes=30),
        match_winner="TEAM2",
    )
    db.session.add(Point(match=finished.uuid, winner="TEAM2", set_number=1, stamp=BASE - timedelta(hours=1)))
    upcoming = game("Upcoming Game", "Field 2", MatchStatus.NOT_STARTED, BASE + timedelta(hours=1))

    db.session.add(Injury(player=player.id, message="Sprained ankle", show=True, stamp=BASE))
    db.session.add(Injury(player=player.id, message="Private concussion", show=False, stamp=BASE))

    side_competition = SideComp(event=tournament.url, name="Duels", type="DUELING", track_losses=True)
    db.session.add(side_competition)
    db.session.flush()
    duelist = SideCompRegistration(
        comp=side_competition.id, player=player.id, entry_number=1, weapon=Pompfen.LONG.value
    )
    # Logging a result for an unregistered player leaves an incomplete registration.
    unregistered = SideCompRegistration(comp=side_competition.id, player=organizer.id, entry_number=2)
    db.session.add_all([duelist, unregistered])
    db.session.flush()
    for points in (1, 1, -1):
        db.session.add(SideCompResult(comp=side_competition.id, player=duelist.id, points=points, ref=organizer.id))
    db.session.add(SideCompResult(comp=side_competition.id, player=unregistered.id, points=1, ref=organizer.id))
    db.session.commit()
    return {
        "tournament": tournament,
        "secret": secret,
        "organizer": organizer,
        "player": player,
        "team": team,
        "live": live,
        "finished": finished,
        "upcoming": upcoming,
        "side_competition": side_competition,
    }


@pytest.fixture
def token(world):
    """A client-credentials token: no user, no scopes."""
    return issue_access_token()


def test_requests_without_a_bearer_token_are_rejected(client, world):
    response = client.get("/api/v1/tournaments")
    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_token"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert client.get("/api/v1/tournaments", headers=_bearer("not-a-token")).status_code == 401
    login_as(client, world["player"])
    assert client.get("/api/v1/tournaments").status_code == 401


def test_every_route_requires_a_bearer_token(app, client, world):
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1"):
            continue
        url = re.sub(r"<int:[^>]+>", "1", rule.rule)
        url = re.sub(r"<[^>]+>", "x", url)
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            response = client.open(url, method=method)
            assert response.status_code == 401, f"{method} {rule.rule}"


def test_each_request_is_logged(client, world, caplog):
    token = issue_access_token(world["player"], client_name="Scoreboard App")
    caplog.set_level(logging.INFO, logger="arctos.api")
    assert client.get("/api/v1/tournaments", headers=_bearer(token)).status_code == 200
    [record] = [r for r in caplog.records if r.name == "arctos.api"]
    fields = record.api_request
    assert fields["client_name"] == "Scoreboard App"
    assert fields["client_id"]
    assert (fields["user_id"], fields["user_type"]) == ("test_player", "player")
    assert (fields["method"], fields["path"], fields["status_code"]) == ("GET", "/api/v1/tournaments", 200)
    assert fields["duration_ms"] >= 0


def test_rejected_requests_are_logged_without_a_client(client, world, caplog):
    caplog.set_level(logging.INFO, logger="arctos.api")
    client.get("/api/v1/tournaments")
    [record] = [r for r in caplog.records if r.name == "arctos.api"]
    assert record.api_request["client_id"] is None
    assert record.api_request["status_code"] == 401


def test_me(client, world, token):
    user_token = issue_access_token(world["player"])
    assert client.get("/api/v1/me", headers=_bearer(user_token)).get_json() == {
        "id": "test_player",
        "name": "Test Player",
        "type": "player",
    }
    response = client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"


def test_unpublished_tournament_is_only_visible_to_its_organizers_with_footage_scope(client, world, token):
    organizer = world["organizer"]
    hidden = {
        "client credentials": token,
        "organizer without scope": issue_access_token(organizer),
        "organizer with injuries scope": issue_access_token(organizer, scope="injuries"),
        "non-organizer with footage scope": issue_access_token(world["player"], scope="footage"),
    }
    for label, hidden_token in hidden.items():
        response = client.get("/api/v1/tournaments/secret-cup", headers=_bearer(hidden_token))
        assert response.status_code == 404, label
        assert response.get_json() == {"error": "Tournament not found"}
        listed = client.get("/api/v1/tournaments", headers=_bearer(hidden_token)).get_json()["tournaments"]
        assert [t["id"] for t in listed] == ["test-tournament"], label

    organizer_token = issue_access_token(organizer, scope="footage")
    detail = client.get("/api/v1/tournaments/secret-cup", headers=_bearer(organizer_token))
    assert detail.status_code == 200
    assert detail.get_json()["tournament"]["published"] is False
    fields = client.get("/api/v1/tournaments/secret-cup/fields", headers=_bearer(organizer_token)).get_json()
    assert [f["name"] for f in fields["fields"]] == ["Hidden Field"]
    listed = client.get("/api/v1/tournaments", headers=_bearer(organizer_token)).get_json()["tournaments"]
    assert {t["id"] for t in listed} == {"test-tournament", "secret-cup"}


def test_unpublished_league_events_are_hidden(client, world, token):
    league = League(
        url="summer", name="Summer League", published=True, registrable_config_id=make_registrable_config().id
    )
    db.session.add(league)
    db.session.flush()
    for url, published in (("summer-1", True), ("summer-2", False)):
        db.session.add(Tournament(url=url, name=url, start_date=BASE, published=published, league_id=league.url))
    db.session.commit()
    detail = client.get("/api/v1/leagues/summer", headers=_bearer(token)).get_json()
    assert [t["id"] for t in detail["tournaments"]] == ["summer-1"]
    assert client.get("/api/v1/tournaments/summer-2", headers=_bearer(token)).status_code == 404


def test_schedule_follows_schedule_published(client, world, token):
    world["tournament"].schedule_published = False
    db.session.commit()
    response = client.get("/api/v1/tournaments/test-tournament/matches", headers=_bearer(token))
    assert response.status_code == 403
    assert response.get_json() == {"error": "Schedule not published"}

    organizer_token = issue_access_token(world["organizer"], scope="footage")
    response = client.get("/api/v1/tournaments/test-tournament/matches", headers=_bearer(organizer_token))
    assert response.status_code == 200
    assert len(response.get_json()["matches"]) == 3


def test_matches_list_and_filters(client, world, token):
    url = "/api/v1/tournaments/test-tournament/matches"
    matches = client.get(url, headers=_bearer(token)).get_json()["matches"]
    assert [m["name"] for m in matches] == ["Finished Game", "Live Game", "Upcoming Game"]
    finished = matches[0]
    assert finished["team1"] == {"id": "test_team", "name": "Test Team Pseudonym", "shortname": None}
    assert finished["team2"] == {"id": "rivals", "name": "The Rivals", "shortname": "RIV"}
    assert finished["winner"] == "TEAM2"
    assert finished["sets"] == [{"number": 1, "team1_score": 0, "team2_score": 1}]
    assert finished["start_time"] == "2026-05-01T11:00:00Z"

    completed = client.get(f"{url}?status=completed", headers=_bearer(token)).get_json()["matches"]
    assert [m["name"] for m in completed] == ["Finished Game"]
    on_field = client.get(f"{url}?field=Field%202", headers=_bearer(token)).get_json()["matches"]
    assert [m["name"] for m in on_field] == ["Finished Game", "Upcoming Game"]
    assert client.get(f"{url}?status=bogus", headers=_bearer(token)).status_code == 400


def test_match_detail_has_points(client, world, token):
    response = client.get(f"/api/v1/tournaments/test-tournament/matches/{world['live'].uuid}", headers=_bearer(token))
    match = response.get_json()["match"]
    assert match["status"] == "IN_PROGRESS"
    assert [(p["winner"], p["end_stamp"] is None) for p in match["points"]] == [("TEAM1", False), (None, True)]
    assert all(p["id"] for p in match["points"])
    missing = client.get("/api/v1/tournaments/test-tournament/matches/nope", headers=_bearer(token))
    assert missing.status_code == 404


def test_fields(client, world, token):
    response = client.get("/api/v1/tournaments/test-tournament/fields", headers=_bearer(token))
    fields = response.get_json()["fields"]
    assert [f["name"] for f in fields] == ["Field 1", "Field 2", "North / Main"]
    assert all(set(f) == {"id", "name"} and isinstance(f["id"], int) for f in fields)


def test_scoreboard_in_progress_includes_points_for_sets_matches(client, world, token):
    response = client.get("/api/v1/tournaments/test-tournament/fields/Field%201/scoreboard", headers=_bearer(token))
    assert response.status_code == 200
    match = response.get_json()["match"]
    assert match["id"] == world["live"].uuid
    assert match["team1"] == {"name": "Test Team Pseudonym", "shortname": None}
    assert match["team2"] == {"name": "The Rivals", "shortname": "RIV"}
    assert match["set_type"] == "SETS"
    assert match["stones"] is None
    assert match["sets"] == [{"number": 1, "team1_score": 1, "team2_score": 0}]
    assert match["points"] == [
        {
            "stamp": "2026-05-01T12:00:00Z",
            "end_stamp": "2026-05-01T12:01:00Z",
            "set_number": 1,
            "winner": "TEAM1",
            "rerolled": False,
            "stones_at_start": None,
        },
        {
            "stamp": "2026-05-01T12:02:00Z",
            "end_stamp": None,
            "set_number": 1,
            "winner": None,
            "rerolled": False,
            "stones_at_start": None,
        },
    ]


def test_scoreboard_stones(client, world, token):
    live = world["live"]
    live.set_type = SetType.STONES
    live.stones_per_set = 50
    live.stones_remaining = 20
    db.session.query(Point).filter_by(match=live.uuid).update({"stones_at_start": 30})
    db.session.commit()
    match = client.get(
        "/api/v1/tournaments/test-tournament/fields/Field%201/scoreboard", headers=_bearer(token)
    ).get_json()["match"]
    assert match["stones"] == {"per_set": 50, "remaining": 20}
    assert [p["stones_at_start"] for p in match["points"]] == [30, 30]


def test_scoreboard_idle_field(client, world, token):
    response = client.get("/api/v1/tournaments/test-tournament/fields/Field%202/scoreboard", headers=_bearer(token))
    body = response.get_json()
    assert body["match"] is None
    assert body["previous"]["id"] == world["finished"].uuid
    assert body["previous"]["winner"] == "TEAM2"
    assert body["next"]["id"] == world["upcoming"].uuid
    assert body["next"]["team2"] == {"name": "The Rivals", "shortname": "RIV"}


def test_scoreboard_tz_offset_hides_matches_from_other_days(client, world, token):
    url = "/api/v1/tournaments/test-tournament/fields/Field%202/scoreboard"
    response = client.get(f"{url}?tz_offset_minutes=-420", headers=_bearer(token))
    assert response.get_json() == {"match": None, "previous": None, "next": None}
    invalid = client.get(f"{url}?tz_offset_minutes=pacific", headers=_bearer(token))
    assert invalid.status_code == 400


def test_scoreboard_field_names_are_url_encoded(client, world, token):
    url = f"/api/v1/tournaments/test-tournament/fields/{quote('North / Main', safe='')}/scoreboard"
    response = client.get(url, headers=_bearer(token))
    assert response.status_code == 200
    assert response.get_json() == {"match": None, "previous": None, "next": None}
    unknown = client.get("/api/v1/tournaments/test-tournament/fields/Nowhere/scoreboard", headers=_bearer(token))
    assert unknown.status_code == 404


def test_internal_scoreboard_state_is_unchanged(client, world):
    live = client.get("/_api/scoreboard-state?tournament=test-tournament&field=Field%201").get_json()
    assert live["has_active_match"] is True
    assert (live["team1_name"], live["team2_shortname"]) == ("Test Team Pseudonym", "RIV")
    assert live["scores_by_set"] == {"1": {"team1_score": 1, "team2_score": 0}}
    assert live["sets"] == [1]
    assert live["stones_info"] is None and live["points_for_stones"] is None
    idle = client.get("/_api/scoreboard-state?tournament=test-tournament&field=Field%202").get_json()
    assert idle["has_active_match"] is False
    assert idle["prev_match"]["winner"] == "TEAM2"
    assert idle["next_match"]["team2_name"] == "The Rivals"
    other_day = client.get(
        "/_api/scoreboard-state?tournament=test-tournament&field=Field%202&tz_offset_minutes=0"
    ).get_json()
    assert (other_day["prev_match"], other_day["next_match"]) == (None, None)


def test_injuries_respect_privacy_and_scope(client, world, token):
    player = world["player"]
    url = f"/api/v1/players/{player.id}/injuries"

    def messages(access_token):
        return sorted(i["message"] for i in client.get(url, headers=_bearer(access_token)).get_json()["injuries"])

    public_only = ["Sprained ankle"]
    assert messages(token) == public_only
    assert messages(issue_access_token(player)) == public_only
    assert messages(issue_access_token(world["organizer"], scope="injuries")) == public_only
    assert messages(issue_access_token(player, scope="injuries")) == ["Private concussion", "Sprained ankle"]


def test_rosters_hide_private_data(client, world, token):
    players = client.get("/api/v1/tournaments/test-tournament/players", headers=_bearer(token)).get_json()["players"]
    assert players == [
        {
            "id": "test_player",
            "name": "Test Player",
            "jersey_name": "Testy",
            "jersey_number": "7",
            "profile_photo": None,
            "team": {"id": "test_team", "name": "Test Team Pseudonym", "shortname": None},
        }
    ]
    for path in (
        "/api/v1/tournaments/test-tournament/players",
        "/api/v1/tournaments/test-tournament/teams",
        "/api/v1/players/test_player",
        "/api/v1/teams/rivals",
        "/api/v1/teams/test_team",
    ):
        body = client.get(path, headers=_bearer(token)).get_data(as_text=True)
        for secret in ("example.com", "1234567890", "amount_paid", "paid", "waiver", "email", "phone"):
            assert secret not in body, (path, secret)


def test_teams_and_results(client, world, token):
    teams = client.get("/api/v1/tournaments/test-tournament/teams", headers=_bearer(token)).get_json()["teams"]
    assert [(t["id"], t["name"], t["player_count"]) for t in teams] == [
        ("test_team", "Test Team Pseudonym", 1),
        ("rivals", "The Rivals", 0),
    ]
    standings = client.get("/api/v1/tournaments/test-tournament/results", headers=_bearer(token)).get_json()
    by_team = {row["team"]["id"]: row for row in standings["standings"]}
    assert (by_team["rivals"]["matches_won"], by_team["test_team"]["matches_lost"]) == (1, 1)


def test_player_and_team_profiles(client, world, token):
    player = client.get("/api/v1/players/test_player", headers=_bearer(token)).get_json()["player"]
    assert player["registrations"] == [
        {
            "tournament": {"id": "test-tournament", "name": "Test Tournament"},
            "league": None,
            "team": {"id": "test_team", "name": "Test Team Pseudonym", "shortname": None},
            "jersey_name": "Testy",
            "jersey_number": "7",
        }
    ]
    team = client.get("/api/v1/teams/test_team", headers=_bearer(token)).get_json()["team"]
    [registration] = team["registrations"]
    assert registration["name"] == "Test Team Pseudonym"
    assert [p["id"] for p in registration["players"]] == ["test_player"]
    assert client.get("/api/v1/players/nobody", headers=_bearer(token)).status_code == 404


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/api/v1/tournaments", "tournaments"),
        ("/api/v1/tournaments?when=upcoming", "tournaments"),
        ("/api/v1/tournaments?when=past", "tournaments"),
        ("/api/v1/tournaments/test-tournament", "tournament"),
        ("/api/v1/tournaments/test-tournament/fields", "fields"),
        ("/api/v1/tournaments/test-tournament/teams", "teams"),
        ("/api/v1/tournaments/test-tournament/players", "players"),
        ("/api/v1/tournaments/test-tournament/players?team=test_team", "players"),
        ("/api/v1/tournaments/test-tournament/matches", "matches"),
        ("/api/v1/tournaments/test-tournament/results", "standings"),
        ("/api/v1/tournaments/test-tournament/side-competitions", "side_competitions"),
        ("/api/v1/leagues", "leagues"),
        ("/api/v1/players", "players"),
        ("/api/v1/players?search=test", "players"),
        ("/api/v1/players/test_player", "player"),
        ("/api/v1/players/test_player/injuries", "injuries"),
        ("/api/v1/teams", "teams"),
        ("/api/v1/teams/test_team", "team"),
    ],
)
def test_read_endpoints_smoke(client, world, token, path, key):
    response = client.get(path, headers=_bearer(token))
    assert response.status_code == 200, response.get_json()
    assert key in response.get_json()


def test_league_endpoints_smoke(client, world, token):
    league = League(
        url="winter", name="Winter League", published=True, registrable_config_id=make_registrable_config().id
    )
    db.session.add(league)
    db.session.flush()
    db.session.add(Tournament(url="winter-1", name="Winter 1", start_date=BASE, published=True, league_id=league.url))
    db.session.add(TeamRegistration(league_id=league.url, team="test_team", pseudonym="Winter Testers"))
    db.session.commit()
    for path, key in (
        ("/api/v1/leagues", "leagues"),
        ("/api/v1/leagues/winter", "league"),
        ("/api/v1/leagues/winter/teams", "teams"),
        ("/api/v1/leagues/winter/players", "players"),
        ("/api/v1/leagues/winter/results", "standings"),
        ("/api/v1/tournaments?league=winter", "tournaments"),
        ("/api/v1/tournaments/winter-1/teams", "teams"),
    ):
        response = client.get(path, headers=_bearer(token))
        assert response.status_code == 200, path
        assert key in response.get_json()
    teams = client.get("/api/v1/tournaments/winter-1/teams", headers=_bearer(token)).get_json()["teams"]
    assert [t["name"] for t in teams] == ["Winter Testers"]


def test_side_competitions(client, world, token):
    [side_competition] = client.get(
        "/api/v1/tournaments/test-tournament/side-competitions", headers=_bearer(token)
    ).get_json()["side_competitions"]
    assert side_competition["name"] == "Duels"
    assert side_competition["type"] == "DUELING"
    assert side_competition["registrant_count"] == 1
    assert side_competition["track_losses"] is True
    assert side_competition["results_available"] is True
    assert "registrants" not in side_competition


def test_side_competition_detail_hides_incomplete_registrations(client, world, token):
    side_competition_id = world["side_competition"].id
    detail = client.get(
        f"/api/v1/tournaments/test-tournament/side-competitions/{side_competition_id}", headers=_bearer(token)
    ).get_json()
    assert detail["registrant_count"] == 1
    [registrant] = detail["registrants"]
    assert registrant["player"] == {"id": "test_player", "name": "Test Player"}
    assert (registrant["entry_number"], registrant["weapon"]) == (1, "LONG")


def test_side_competition_results(client, world, token):
    side_competition_id = world["side_competition"].id
    results = client.get(
        f"/api/v1/tournaments/test-tournament/side-competitions/{side_competition_id}/results",
        headers=_bearer(token),
    ).get_json()
    assert results["finalized"] is False
    [table] = results["tables"]
    assert (table["id"], table["track_losses"]) == ("all", True)
    [row] = table["rows"]
    assert row["player"] == {"id": "test_player", "name": "Test Player"}
    assert row["team"]["id"] == world["team"].id
    assert (row["rank"], row["wins"], row["losses"]) == (1, 2, 1)
    assert row["win_percentage"] == pytest.approx(200 / 3)


def test_hidden_side_competition_results_are_not_found(client, world, token):
    world["side_competition"].only_show_top_n_results = 0
    db.session.commit()
    response = client.get(
        f"/api/v1/tournaments/test-tournament/side-competitions/{world['side_competition'].id}/results",
        headers=_bearer(token),
    )
    assert response.status_code == 404


def test_side_competitions_of_other_tournaments_are_not_found(client, world, token):
    side_competition_id = world["side_competition"].id
    for path in ("", "/results"):
        response = client.get(
            f"/api/v1/tournaments/secret-cup/side-competitions/{side_competition_id}{path}",
            headers=_bearer(issue_access_token(world["organizer"], scope="footage")),
        )
        assert response.status_code == 404


def test_unknown_api_paths_return_json_errors(client, world, token):
    response = client.get("/api/v1/nope", headers=_bearer(token))
    assert response.status_code == 404
    assert response.get_json() == {"error": "Not found"}


def test_cors_preflight_allows_put(client, world):
    response = client.options(
        "/api/v1/tournaments/t/matches/m/footage/uploads/u/chunks/0",
        headers={"Origin": "https://app.example", "Access-Control-Request-Method": "PUT"},
    )
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "PUT" in response.headers["Access-Control-Allow-Methods"]


def test_client_credentials_quick_start(client, world, monkeypatch):
    monkeypatch.setenv("AUTHLIB_INSECURE_TRANSPORT", "1")
    login_as(client, world["player"])
    registered = client.post(
        "/_api/oauth/clients",
        json={"name": "Stats Site", "redirect_uris": ["https://stats.example/cb"], "confidential": True},
    ).get_json()
    credentials = f"{registered['client']['client_id']}:{registered['client_secret']}"
    issued = client.post(
        "/api/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"},
    ).get_json()
    response = client.get("/api/v1/tournaments", headers=_bearer(issued["access_token"]))
    assert [t["id"] for t in response.get_json()["tournaments"]] == ["test-tournament"]


def test_tournament_detail_lists_organizers(client, world, token):
    tournament = client.get("/api/v1/tournaments/test-tournament", headers=_bearer(token)).get_json()["tournament"]
    assert tournament["organizers"] == [{"id": "organizer", "name": "Organizer", "type": "player"}]
    assert tournament["start_date"].endswith("Z")
    assert set(tournament["registration"]) >= {"team_registration_open", "max_teams"}
