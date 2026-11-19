"""Integration tests for organizer setup through the public API (``manage`` scope).

Tournaments, leagues, organizers, settings, fields, tags, script variables,
and building the schedule.
"""

import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from models import TO, Field, League, Match, Player, Tag, Team, TeamRegistration, Tournament, db
from tests.utils import issue_access_token, make_registrable_config

pytestmark = pytest.mark.integration


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def people(test_db):
    organizer = Player(id="organizer", name="Olive Organizer", pw_hash="x")
    bystander = Player(id="bystander", name="Bea Bystander", pw_hash="x")
    helper = Player(id="helper", name="Hal Helper", pw_hash="x")
    db.session.add_all([organizer, bystander, helper])
    db.session.commit()
    return {"organizer": organizer, "bystander": bystander, "helper": helper}


@pytest.fixture
def headers(people):
    return _bearer(issue_access_token(people["organizer"], scope="manage"))


@pytest.fixture
def organized(test_db, people):
    """A published and an unpublished tournament organized by ``organizer``."""
    for url, published in (("open-cup", True), ("secret-cup", False)):
        db.session.add(
            Tournament(
                url=url,
                name=url.replace("-", " ").title(),
                start_date=datetime(2026, 6, 1),
                end_date=datetime(2026, 6, 2),
                published=published,
                schedule_published=published,
                registrable_config_id=make_registrable_config().id,
            )
        )
        db.session.flush()
        db.session.add(TO(user_id="organizer", user_type="player", event=url))
    db.session.add(Field(event="open-cup", name="Field 1"))
    db.session.commit()


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"method": "post", "path": "/api/v1/tournaments", "json": {"id": "x", "name": "X"}},
        {"method": "patch", "path": "/api/v1/tournaments/open-cup", "json": {"name": "Renamed"}},
        {"method": "post", "path": "/api/v1/tournaments/open-cup/fields", "json": {"name": "Field 2"}},
        {"method": "put", "path": "/api/v1/tournaments/open-cup/schedule", "json": {"script": ""}},
        {"method": "post", "path": "/api/v1/leagues", "json": {"id": "x", "name": "X"}},
    ],
    ids=lambda kwargs: f"{kwargs['method']} {kwargs['path']}",
)
@pytest.mark.parametrize("token_kind", ["no manage scope", "client credentials"])
def test_writes_need_a_user_token_with_manage_scope(client, people, organized, request_kwargs, token_kind):
    token = (
        issue_access_token(people["organizer"], scope="footage")
        if token_kind == "no manage scope"
        else issue_access_token(scope="manage")
    )
    response = getattr(client, request_kwargs["method"])(
        request_kwargs["path"], headers=_bearer(token), json=request_kwargs["json"]
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"
    assert Tournament.query.get("x") is None
    assert Tournament.query.get("open-cup").name == "Open Cup"


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "settings"),
        ("patch", ""),
        ("post", "fields"),
        ("get", "tags"),
        ("get", "schedule/script"),
        ("post", "matches"),
        ("delete", ""),
    ],
)
def test_non_organizers_get_403_on_published_and_404_on_unpublished(client, people, organized, method, path):
    headers = _bearer(issue_access_token(people["bystander"], scope="manage"))
    for tournament_id, status_code in (("open-cup", 403), ("secret-cup", 404)):
        url = f"/api/v1/tournaments/{tournament_id}/{path}".rstrip("/") + "?confirm=" + tournament_id
        response = getattr(client, method)(url, headers=headers, json={"name": "Hijacked"})
        assert response.status_code == status_code, (tournament_id, response.get_json())
    assert Tournament.query.get("open-cup") is not None
    assert Field.query.filter_by(name="Hijacked").count() == 0


def test_manage_token_sees_unpublished_tournaments_it_organizes(client, headers, organized):
    listed = client.get("/api/v1/tournaments", headers=headers).get_json()["tournaments"]
    assert {t["id"] for t in listed} == {"open-cup", "secret-cup"}
    assert client.get("/api/v1/tournaments/secret-cup/matches", headers=headers).status_code == 200


def test_create_configure_and_delete_tournament(client, headers, people):
    created = client.post("/api/v1/tournaments", headers=headers, json={"id": "fall-open", "name": "Fall Open"})
    assert created.status_code == 201
    assert created.get_json()["tournament"]["id"] == "fall-open"
    assert TO.query.filter_by(event="fall-open", user_id="organizer").count() == 1

    duplicate = client.post("/api/v1/tournaments", headers=headers, json={"id": "fall-open", "name": "Again"})
    assert duplicate.status_code == 400

    updated = client.patch(
        "/api/v1/tournaments/fall-open",
        headers=headers,
        json={
            "location": "Central Park",
            "start_date": "2026-10-03",
            "end_date": "2026-10-04",
            "published": True,
            "registration": {"team_reg_fee": 40, "team_registration_open": True, "n_max_teams": 12},
        },
    )
    assert updated.status_code == 200, updated.get_json()
    settings = updated.get_json()["settings"]
    assert settings["name"] == "Fall Open"
    assert settings["location"] == "Central Park"
    assert settings["start_date"] == "2026-10-03"
    assert settings["published"] is True
    assert settings["registration"]["team_reg_fee"] == 40
    assert settings["registration"]["team_registration_open"] is True
    assert settings["registration"]["player_registration_open"] is False
    assert settings["registration"]["n_max_teams"] == 12
    assert settings["organizers"] == [{"id": "organizer", "name": "Olive Organizer", "type": "player"}]
    assert client.get("/api/v1/tournaments/fall-open/settings", headers=headers).get_json()["settings"] == settings

    bad_dates = client.patch("/api/v1/tournaments/fall-open", headers=headers, json={"end_date": "2026-10-01"})
    assert bad_dates.status_code == 400
    wrong_type = client.patch("/api/v1/tournaments/fall-open", headers=headers, json={"published": "yes"})
    assert wrong_type.status_code == 400
    assert Tournament.query.get("fall-open").end_date.date().isoformat() == "2026-10-04"

    unconfirmed = client.delete("/api/v1/tournaments/fall-open", headers=headers)
    assert unconfirmed.status_code == 400
    deleted = client.delete("/api/v1/tournaments/fall-open?confirm=fall-open", headers=headers)
    assert deleted.status_code == 204
    assert Tournament.query.get("fall-open") is None


def test_add_and_remove_organizers(client, headers, people, organized):
    added = client.post(
        "/api/v1/tournaments/open-cup/organizers", headers=headers, json={"user_id": "helper", "user_type": "player"}
    )
    assert added.status_code == 201
    assert {o["id"] for o in added.get_json()["organizers"]} == {"organizer", "helper"}

    missing = client.post(
        "/api/v1/tournaments/open-cup/organizers", headers=headers, json={"user_id": "nobody", "user_type": "player"}
    )
    assert missing.status_code == 404

    yourself = client.delete("/api/v1/tournaments/open-cup/organizers/player/organizer", headers=headers)
    assert yourself.status_code == 400
    removed = client.delete("/api/v1/tournaments/open-cup/organizers/player/helper", headers=headers)
    assert removed.status_code == 204
    assert TO.query.filter_by(event="open-cup", user_id="helper").count() == 0


def test_fields_tags_and_script_variables(client, headers, organized):
    base = "/api/v1/tournaments/open-cup"
    field = client.post(f"{base}/fields", headers=headers, json={"name": "Field 2"})
    assert field.status_code == 201
    field_id = field.get_json()["field"]["id"]
    assert client.post(f"{base}/fields", headers=headers, json={"name": "Field 2"}).status_code == 400
    renamed = client.patch(f"{base}/fields/{field_id}", headers=headers, json={"name": "Field B"})
    assert renamed.get_json()["field"] == {"id": field_id, "name": "Field B"}
    assert client.delete(f"{base}/fields/{field_id}", headers=headers).status_code == 204
    assert client.delete(f"{base}/fields/{field_id}", headers=headers).status_code == 404

    tag = client.post(f"{base}/tags", headers=headers, json={"name": "Seed 1"})
    assert tag.status_code == 201
    tag_id = tag.get_json()["tag"]["id"]
    assert client.post(f"{base}/tags", headers=headers, json={"name": "a::b"}).status_code == 400
    db.session.add(Team(id="seeded", name="Seeded", pw_hash="x"))
    db.session.commit()
    team = client.put(f"{base}/tags/{tag_id}/team", headers=headers, json={"team_id": "seeded"})
    assert team.get_json()["tag"]["team"] == "seeded"
    renamed_tag = client.put(f"{base}/tags/{tag_id}", headers=headers, json={"name": "Top Seed"})
    assert renamed_tag.get_json()["tag"]["name"] == "Top Seed"
    assert client.get(f"{base}/tags", headers=headers).get_json()["tags"] == [
        {"id": tag_id, "name": "Top Seed", "expression": None, "team": "seeded"}
    ]

    variable = client.post(f"{base}/script-variables", headers=headers, json={"name": "rounds", "expression": "3"})
    assert variable.status_code == 201, variable.get_json()
    variable_id = variable.get_json()["script_variable"]["id"]
    cyclic = client.post(f"{base}/script-variables", headers=headers, json={"name": "if", "expression": "1"})
    assert cyclic.status_code == 400
    changed = client.patch(f"{base}/script-variables/{variable_id}", headers=headers, json={"expression": "(+ 1 2)"})
    assert changed.get_json()["script_variable"] == {"id": variable_id, "name": "rounds", "expression": "(+ 1 2)"}
    assert client.get(f"{base}/script-variables", headers=headers).get_json()["script_variables"] == [
        {"id": variable_id, "name": "rounds", "expression": "(+ 1 2)"}
    ]
    assert client.delete(f"{base}/script-variables/{variable_id}", headers=headers).status_code == 204
    assert client.delete(f"{base}/tags/{tag_id}", headers=headers).status_code == 204
    assert Tag.query.filter_by(event="open-cup").count() == 0


def test_edit_matches_one_at_a_time(client, headers, organized):
    base = "/api/v1/tournaments/open-cup"
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
    opener = client.post(
        f"{base}/matches",
        headers=headers,
        json={
            "name": "Opener",
            "field": "Field 1",
            "schedule_type": "STATIC",
            "start_time": start.isoformat(),
            "length": 30,
        },
    )
    assert opener.status_code == 201, opener.get_json()
    opener_id = opener.get_json()["match"]["id"]
    follower = client.post(
        f"{base}/matches",
        headers=headers,
        json={
            "name": "Follower",
            "field": "Field 1",
            "schedule_type": "FAST",
            "previous_match_id": opener_id,
            "length": 30,
        },
    )
    assert follower.status_code == 201, follower.get_json()
    follower_id = follower.get_json()["match"]["id"]
    assert follower.get_json()["match"]["start_time"] == (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    duplicate = client.post(f"{base}/matches", headers=headers, json={"name": "Opener", "field": "Field 1"})
    assert duplicate.status_code == 400
    orphan = client.post(f"{base}/matches", headers=headers, json={"name": "Orphan", "schedule_type": "FAST"})
    assert orphan.status_code == 400

    bulk = client.post(f"{base}/matches/bulk-length", headers=headers, json={"match_ids": [opener_id], "length": 45})
    assert bulk.get_json() == {"updated": 1, "results": [{"match_id": opener_id, "status": "updated"}]}
    follower_after = client.get(f"{base}/matches/{follower_id}", headers=headers).get_json()["match"]
    assert follower_after["start_time"] == (start + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")

    edited = client.patch(
        f"{base}/matches/{follower_id}",
        headers=headers,
        json={"length": 20, "team1": "tag::Winners", "previous_match_id": opener_id},
    )
    assert edited.status_code == 200, edited.get_json()
    assert edited.get_json()["match"]["length_minutes"] == 20
    assert edited.get_json()["match"]["team1"] == {"id": None, "name": "tag::Winners", "shortname": None}
    unknown_type = client.patch(f"{base}/matches/{follower_id}", headers=headers, json={"schedule_type": "STATIC"})
    assert unknown_type.status_code == 400

    lunch = client.post(
        f"{base}/break-groups",
        headers=headers,
        json={"name": "Lunch", "schedule_type": "BREAK", "fields": ["Field 1"], "length": 60},
    )
    assert lunch.status_code == 201, lunch.get_json()
    assert lunch.get_json()["break_group"]["name"] == "Lunch"
    assert Match.query.get(lunch.get_json()["break_group"]["match_ids"][0]).previous_match == follower_id
    assert client.patch(f"{base}/break-groups/Lunch", headers=headers, json={"length": 30}).status_code == 204
    assert client.delete(f"{base}/break-groups/Lunch", headers=headers).status_code == 204

    pushed = client.post(
        f"{base}/schedule/push-back",
        headers=headers,
        json={"minutes": 10, "day": start.date().isoformat(), "tz_offset_minutes": 0},
    )
    assert pushed.get_json() == {"moved": 1}
    assert client.post(f"{base}/schedule/update-references", headers=headers).status_code == 204

    assert client.delete(f"{base}/matches/{opener_id}", headers=headers).status_code == 204
    assert client.delete(f"{base}/matches/{opener_id}", headers=headers).status_code == 404
    assert Match.query.filter_by(event="open-cup").count() == 1


def test_validate_expressions_and_schedule_warnings(client, headers, organized):
    base = "/api/v1/tournaments/open-cup"
    valid = client.post(f"{base}/schedule/validate", headers=headers, json={"expression": "(+ 1 2)"}).get_json()
    assert valid["valid"] is True
    assert valid["value"] == 3
    invalid = client.post(f"{base}/schedule/validate", headers=headers, json={"expression": "(+ 1"}).get_json()
    assert invalid["valid"] is False
    assert invalid["error"]
    assert client.post(f"{base}/schedule/validate", headers=headers, json={}).status_code == 400

    db.session.add(Match(name="Lonely", event="open-cup", field="Field 1", team1_initial="ghost-team"))
    db.session.commit()
    warnings = client.get(f"{base}/schedule/warnings", headers=headers).get_json()["warnings"]
    assert any(warning["kind"] == "unknown_team" for warning in warnings)


def test_invalid_schedule_script_changes_nothing(client, headers, organized):
    response = client.put(
        "/api/v1/tournaments/open-cup/schedule",
        headers=headers,
        json={"script": '[[matches]]\nname = "M1"\nfield = "Nowhere"\nschedule_type = "STATIC"\n'},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert Field.query.filter_by(event="open-cup").count() == 1
    assert Match.query.filter_by(event="open-cup").count() == 0


def test_leagues(client, headers, people):
    created = client.post("/api/v1/leagues", headers=headers, json={"id": "metro", "name": "Metro League"})
    assert created.status_code == 201
    assert created.get_json()["league"]["id"] == "metro"

    updated = client.patch(
        "/api/v1/leagues/metro",
        headers=headers,
        json={"about": "Weekly play.", "registration": {"payment_info": "Pay at the field", "player_reg_fee": 5}},
    )
    assert updated.status_code == 200, updated.get_json()
    settings = updated.get_json()["settings"]
    assert settings["about"] == "Weekly play."
    assert settings["registration"]["payment_info"] == "Pay at the field"
    assert settings["registration"]["player_reg_fee"] == 5
    assert client.get("/api/v1/leagues/metro/settings", headers=headers).get_json()["settings"] == settings

    week = client.post(
        "/api/v1/tournaments", headers=headers, json={"id": "metro-week-1", "name": "Week 1", "league": "metro"}
    )
    assert week.status_code == 201
    assert week.get_json()["tournament"]["league"]["id"] == "metro"
    league_registration = client.patch(
        "/api/v1/tournaments/metro-week-1", headers=headers, json={"registration": {"team_reg_fee": 1}}
    )
    assert league_registration.status_code == 400
    assert (
        client.get("/api/v1/tournaments/metro-week-1/settings", headers=headers).get_json()["settings"]["registration"]
        is None
    )

    outsider = _bearer(issue_access_token(people["bystander"], scope="manage"))
    attach = client.post(
        "/api/v1/tournaments", headers=outsider, json={"id": "sneaky", "name": "Sneaky", "league": "metro"}
    )
    assert attach.status_code == 403
    assert client.patch("/api/v1/leagues/metro", headers=outsider, json={"about": "x"}).status_code == 404

    added = client.post(
        "/api/v1/leagues/metro/organizers", headers=headers, json={"user_id": "helper", "user_type": "player"}
    )
    assert {o["id"] for o in added.get_json()["organizers"]} == {"organizer", "helper"}
    assert client.delete("/api/v1/leagues/metro/organizers/player/helper", headers=headers).status_code == 204

    still_has_events = client.delete("/api/v1/leagues/metro?confirm=metro", headers=headers)
    assert still_has_events.status_code == 400
    assert client.delete("/api/v1/tournaments/metro-week-1?confirm=metro-week-1", headers=headers).status_code == 204
    assert client.delete("/api/v1/leagues/metro?confirm=metro", headers=headers).status_code == 204
    assert League.query.get("metro") is None


def test_set_up_a_tournament_end_to_end(client, headers, people):
    """Create a tournament, configure it, import a schedule script, and read the schedule back."""
    for team_id in ("bears", "wolves", "otters"):
        db.session.add(Team(id=team_id, name=team_id.title(), pw_hash="x"))
    db.session.commit()

    assert (
        client.post("/api/v1/tournaments", headers=headers, json={"id": "summer-cup", "name": "Summer Cup"}).status_code
        == 201
    )
    configured = client.patch(
        "/api/v1/tournaments/summer-cup",
        headers=headers,
        json={"start_date": "2026-07-04", "end_date": "2026-07-04", "location": "Riverside"},
    )
    assert configured.status_code == 200
    for team_id in ("bears", "wolves", "otters"):
        db.session.add(
            TeamRegistration(event="summer-cup", team=team_id, pseudonym=team_id.title(), status="CONFIRMED")
        )
    db.session.commit()

    script = textwrap.dedent(
        """
        [[variables]]
        name = "final_length"
        expression = "40"

        [[tags]]
        name = "Champion"

        [[fields]]
        name = "North"

        [[fields]]
        name = "South"

        [[matches]]
        name = "Semi 1"
        field = "North"
        schedule_type = "STATIC"
        set_type = "SETS"
        nsets = 3
        nominal_length = 30
        team1_initial = "bears"
        team2_initial = "wolves"
        refs_initial = "otters"
        scheduled_start_time = 2026-07-04T16:00:00
        nominal_start_time = 2026-07-04T16:00:00

        [[matches]]
        name = "Semi 2"
        field = "South"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 30
        team1_initial = "otters"
        team2_initial = "Semi 1::loser"
        scheduled_start_time = 2026-07-04T16:30:00
        nominal_start_time = 2026-07-04T16:30:00

        [[matches]]
        name = "Final"
        field = "North"
        schedule_type = "FAST"
        set_type = "SETS"
        nominal_length = 40
        previous_match = "Semi 1"
        team1_initial = "Semi 1::winner"
        team2_initial = "Semi 2::winner"
        """
    )
    imported = client.put("/api/v1/tournaments/summer-cup/schedule", headers=headers, json={"script": script})
    assert imported.status_code == 200, imported.get_json()
    counts = imported.get_json()
    assert counts["matches_created"] == 3
    assert counts["fields_created"] == 2
    assert counts["warnings"] == []

    assert client.get("/api/v1/tournaments/summer-cup/schedule/warnings", headers=headers).get_json()["warnings"] == []
    matches = client.get("/api/v1/tournaments/summer-cup/matches", headers=headers).get_json()["matches"]
    by_name = {match["name"]: match for match in matches}
    assert [match["name"] for match in matches] == ["Semi 1", "Semi 2", "Final"]
    assert by_name["Semi 1"]["team1"] == {"id": "bears", "name": "Bears", "shortname": None}
    assert by_name["Semi 1"]["start_time"] == "2026-07-04T16:00:00Z"
    assert by_name["Semi 2"]["team2"]["id"] is None
    assert by_name["Final"]["schedule_type"] == "FAST"
    # Fast, but waits for Semi 2 to decide its second team.
    assert by_name["Final"]["start_time"] == "2026-07-04T17:00:00Z"
    semi = client.get(f"/api/v1/tournaments/summer-cup/matches/{by_name['Semi 1']['id']}", headers=headers)
    assert semi.get_json()["match"]["referees"] == [{"id": "otters", "name": "Otters", "shortname": None}]
    fields = client.get("/api/v1/tournaments/summer-cup/fields", headers=headers).get_json()["fields"]
    assert [field["name"] for field in fields] == ["North", "South"]

    exported = client.get("/api/v1/tournaments/summer-cup/schedule/script", headers=headers)
    assert exported.status_code == 200
    reimported = client.put(
        "/api/v1/tournaments/summer-cup/schedule", headers=headers, json={"script": exported.get_json()["script"]}
    )
    assert reimported.status_code == 200, reimported.get_json()
    assert reimported.get_json()["matches_updated"] == 3
    assert Match.query.filter_by(event="summer-cup").count() == 3
