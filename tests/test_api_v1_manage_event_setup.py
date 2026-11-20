"""Integration tests for event setup in the public API: penalty types, side competitions,
schedule items, and brackets (writes need the ``manage`` scope)."""

from datetime import datetime, timezone

import pytest

from app.domain.enums import ScheduleType
from models import TO, Field, League, Match, Player, Tournament, db
from tests.utils import issue_access_token, make_registrable_config

pytestmark = pytest.mark.integration


@pytest.fixture
def world(test_db):
    """A published standalone tournament with a field and two matches, and a league; one organizer of both."""
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
    db.session.add_all([tournament, league, organizer, bystander])
    db.session.flush()
    db.session.add(TO(user_id=organizer.id, user_type="player", event=tournament.url))
    db.session.add(TO(user_id=organizer.id, user_type="player", league_id=league.url))
    db.session.add(Field(event=tournament.url, name="Field 1"))
    semifinal = Match(name="Semi", event=tournament.url, field="Field 1", schedule_type=ScheduleType.STATIC)
    final = Match(
        name="Final",
        event=tournament.url,
        field="Field 1",
        schedule_type=ScheduleType.STATIC,
        team1_initial="Semi::winner",
    )
    db.session.add_all([semifinal, final])
    db.session.commit()
    return {
        "organizer": organizer,
        "bystander": bystander,
        "organizer_headers": _bearer(issue_access_token(organizer, scope="manage")),
        "final": final.uuid,
    }


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("scope_path", ["/api/v1/tournaments/spring-open", "/api/v1/leagues/metro"])
def test_penalty_type_lifecycle(client, world, scope_path):
    headers = world["organizer_headers"]
    created = client.post(
        f"{scope_path}/penalty-types", headers=headers, json={"name": "Shield foul", "description": "Rule 4"}
    )
    assert created.status_code == 201, created.get_json()
    penalty_type = created.get_json()["penalty_type"]
    assert penalty_type["description"] == "Rule 4"
    assert len(penalty_type["color"]) == 6

    updated = client.patch(
        f"{scope_path}/penalty-types/{penalty_type['id']}", headers=headers, json={"name": "Shield", "color": "#00ff00"}
    )
    assert updated.get_json()["penalty_type"] == penalty_type | {"name": "Shield", "color": "00ff00"}

    listed = client.get(f"{scope_path}/penalty-types", headers=_bearer(issue_access_token())).get_json()
    assert [row["name"] for row in listed["penalty_types"]] == ["Shield"]

    assert client.post(f"{scope_path}/penalty-types", headers=headers, json={"name": "x" * 51}).status_code == 400
    assert client.delete(f"{scope_path}/penalty-types/{penalty_type['id']}", headers=headers).status_code == 204
    assert client.delete(f"{scope_path}/penalty-types/{penalty_type['id']}", headers=headers).status_code == 404


def test_league_tournament_uses_league_penalty_types(client, world):
    db.session.add(Tournament(url="metro-week-1", name="Week 1", start_date=datetime(2026, 5, 1), league_id="metro"))
    db.session.commit()
    headers = world["organizer_headers"]
    client.post("/api/v1/tournaments/metro-week-1/penalty-types", headers=headers, json={"name": "Late"})
    listed = client.get("/api/v1/leagues/metro/penalty-types", headers=headers).get_json()
    assert [row["name"] for row in listed["penalty_types"]] == ["Late"]


def test_side_competition_lifecycle(client, world):
    headers = world["organizer_headers"]
    base = "/api/v1/tournaments/spring-open/side-competitions"
    created = client.post(base, headers=headers, json={"name": "Dueling", "type": "DUELING"})
    assert created.status_code == 201, created.get_json()
    side_competition = created.get_json()["side_competition"]
    assert side_competition["registrants"] == []

    updated = client.patch(
        f"{base}/{side_competition['id']}",
        headers=headers,
        json={"name": "Duels", "registration_open": True, "only_show_top_n_results": 3},
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["side_competition"]["only_show_top_n_results"] == 3
    public = client.get(f"{base}/{side_competition['id']}", headers=_bearer(issue_access_token())).get_json()
    assert public["name"] == "Duels"
    assert public["registration_open"] is True

    finalized = client.post(f"{base}/{side_competition['id']}/finalize", headers=headers)
    assert finalized.get_json()["side_competition"]["finalized"] is True
    assert client.delete(f"{base}/{side_competition['id']}", headers=headers).status_code == 204
    assert client.get(base, headers=headers).get_json()["side_competitions"] == []


def test_side_competition_of_another_tournament_is_not_found(client, world):
    headers = world["organizer_headers"]
    db.session.add(
        Tournament(
            url="other",
            name="Other",
            start_date=datetime(2026, 5, 1),
            published=True,
            registrable_config_id=make_registrable_config().id,
        )
    )
    db.session.flush()
    db.session.add(TO(user_id="organizer", user_type="player", event="other"))
    db.session.commit()
    created = client.post(
        "/api/v1/tournaments/other/side-competitions", headers=headers, json={"name": "Duels", "type": "DUELING"}
    ).get_json()["side_competition"]
    response = client.delete(f"/api/v1/tournaments/spring-open/side-competitions/{created['id']}", headers=headers)
    assert response.status_code == 404


def test_schedule_item_lifecycle(client, world):
    headers = world["organizer_headers"]
    base = "/api/v1/tournaments/spring-open/schedule-items"
    draft = {
        "title": "Opening ceremony",
        "start_time": "2026-05-01T09:00:00Z",
        "end_time": "2026-05-01T09:30:00Z",
        "field": "Field 1",
        "request_id": "6f1c3e1a-8a8c-4a52-9a43-2b1f0b0c7c11",
    }
    created = client.post(base, headers=headers, json=draft)
    assert created.status_code == 201, created.get_json()
    item = created.get_json()["schedule_item"]
    assert client.post(base, headers=headers, json=draft).status_code == 200
    assert client.post(base, headers=headers, json=draft | {"title": "Changed"}).status_code == 409
    assert client.post(base, headers=headers, json=draft | {"request_id": None, "field": "Nowhere"}).status_code == 400

    updated = client.put(f"{base}/{item['id']}", headers=headers, json=draft | {"title": "Welcome"})
    assert updated.get_json()["schedule_item"]["title"] == "Welcome"

    unpublished = client.get(base, headers=_bearer(issue_access_token()))
    assert unpublished.status_code == 403
    assert [row["title"] for row in client.get(base, headers=headers).get_json()["schedule_items"]] == ["Welcome"]

    assert client.delete(f"{base}/{item['id']}", headers=headers).status_code == 204
    assert client.get(base, headers=headers).get_json()["schedule_items"] == []


def test_bracket_layout_and_publishing(client, world):
    headers = world["organizer_headers"]
    path = "/api/v1/tournaments/spring-open/bracket"
    public_headers = _bearer(issue_access_token())
    assert client.get(path, headers=public_headers).status_code == 403

    saved = client.put(
        f"{path}/layout",
        headers=headers,
        json={
            "placements": [{"match": world["final"], "x_pos": 300, "y_pos": 40, "team1": "NET", "team2": "NET"}],
            "texts": [{"id": "title", "text": "Finals", "x_pos": 10, "y_pos": 10}],
        },
    )
    assert saved.status_code == 200, saved.get_json()
    [placement] = saved.get_json()["bracket"]["placements"]
    assert placement["match"] == world["final"]
    assert placement["placed"] is True
    assert (placement["team1"], placement["team2"]) == ("NET", "LABEL")
    assert client.put(f"{path}/layout", headers=headers, json={"texts": []}).status_code == 400

    published = client.patch(path, headers=headers, json={"published": True})
    assert published.get_json()["bracket"]["published"] is True
    bracket = client.get(path, headers=public_headers).get_json()["bracket"]
    assert [(text["id"], text["text"]) for text in bracket["texts"]] == [("title", "Finals")]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/tournaments/spring-open/penalty-types"),
        ("post", "/api/v1/leagues/metro/penalty-types"),
        ("post", "/api/v1/tournaments/spring-open/side-competitions"),
        ("post", "/api/v1/tournaments/spring-open/schedule-items"),
        ("patch", "/api/v1/tournaments/spring-open/bracket"),
        ("put", "/api/v1/tournaments/spring-open/bracket/layout"),
    ],
)
def test_writes_need_manage_scope_and_organizer(client, world, method, path):
    send = getattr(client, method)
    without_scope = send(path, headers=_bearer(issue_access_token(world["organizer"], scope="footage")), json={})
    assert without_scope.status_code == 403
    assert without_scope.get_json()["error"] == "insufficient_scope"
    assert send(path, headers=_bearer(issue_access_token(scope="manage")), json={}).status_code == 403
    bystander_headers = _bearer(issue_access_token(world["bystander"], scope="manage"))
    assert send(path, headers=bystander_headers, json={}).status_code == 403

    for model, key in ((Tournament, "spring-open"), (League, "metro")):
        model.query.get(key).published = False
    db.session.commit()
    assert send(path, headers=bystander_headers, json={}).status_code == 404
