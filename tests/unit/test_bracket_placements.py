"""Tests for the interactive bracket canvas placement API."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.enums import BracketPortMode, MatchStatus, ScheduleType
from models import BracketPlacement, Match, TO, Tournament, db
from tests.utils import login_as, make_registrable_config


@pytest.fixture
def bracket_tournament(test_db, player):
    """Published tournament with the default player as TO and two linked matches."""
    cfg = make_registrable_config()
    t = Tournament(
        url="bracket-test",
        name="Bracket Test",
        start_date=datetime.now(timezone.utc),
        published=True,
        schedule_published=True,
        registrable_config_id=cfg.id,
    )
    db.session.add(t)
    db.session.flush()
    db.session.add(TO(event=t.url, user_id=player.id, user_type="player"))

    m1 = Match(
        uuid="11111111-1111-1111-1111-111111111111",
        name="Semi 1",
        event=t.url,
        schedule_type=ScheduleType.STATIC,
        status=MatchStatus.NOT_STARTED,
        team1_initial="TeamA",
        team2_initial="TeamB",
        nominal_length=30,
        nsets=2,
    )
    m2 = Match(
        uuid="22222222-2222-2222-2222-222222222222",
        name="Final",
        event=t.url,
        schedule_type=ScheduleType.STATIC,
        status=MatchStatus.NOT_STARTED,
        team1_initial="Semi 1::winner",
        team2_initial="TeamC",
        nominal_length=30,
        nsets=2,
    )
    db.session.add_all([m1, m2])
    db.session.commit()
    return t


def test_bracket_get_empty(client, bracket_tournament, player):
    login_as(client, player)
    r = client.get(f"/_api/tournaments/{bracket_tournament.url}/bracket")
    assert r.status_code == 200
    data = r.get_json()
    assert data["is_to"] is True
    assert len(data["matches"]) == 2
    assert all(m["placement"] is None for m in data["matches"])


def test_add_placement_auto_nets_when_source_placed(client, bracket_tournament, player):
    login_as(client, player)
    url = bracket_tournament.url

    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "11111111-1111-1111-1111-111111111111", "x_pos": 10, "y_pos": 20},
    )
    assert r.status_code == 200, r.get_json()

    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "22222222-2222-2222-2222-222222222222", "x_pos": 400, "y_pos": 20},
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    by_uuid = {m["uuid"]: m for m in data["matches"]}
    final = by_uuid["22222222-2222-2222-2222-222222222222"]
    assert final["placement"]["team1"] == "NET"
    assert final["placement"]["team2"] == "LABEL"
    assert final["placement"]["placed"] is True
    assert final["placement"]["inputs_flipped"] is False


def test_save_placements_persists_inputs_flipped(client, bracket_tournament, player):
    login_as(client, player)
    url = bracket_tournament.url
    mid = "11111111-1111-1111-1111-111111111111"

    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": mid, "x_pos": 10, "y_pos": 20},
    )
    assert r.status_code == 200, r.get_json()

    r = client.put(
        f"/_api/tournaments/{url}/bracket-placements",
        json={
            "placements": [
                {
                    "match": mid,
                    "x_pos": 10,
                    "y_pos": 20,
                    "width": 280,
                    "height": 100,
                    "team1": "LABEL",
                    "team2": "LABEL",
                    "inputs_flipped": True,
                }
            ]
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    m = next(mm for mm in data["matches"] if mm["uuid"] == mid)
    assert m["placement"]["inputs_flipped"] is True

    row = BracketPlacement.query.filter_by(event=url, match=mid).first()
    assert row is not None and row.inputs_flipped is True


def test_convert_port_label_to_net_places_source(client, bracket_tournament, player):
    login_as(client, player)
    url = bracket_tournament.url

    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "22222222-2222-2222-2222-222222222222", "x_pos": 400, "y_pos": 40},
    )
    assert r.status_code == 200
    final = next(m for m in r.get_json()["matches"] if m["name"] == "Final")
    assert final["placement"]["team1"] == "LABEL"

    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/convert-port",
        json={
            "match": "22222222-2222-2222-2222-222222222222",
            "side": "team1",
            "mode": "NET",
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    by_name = {m["name"]: m for m in data["matches"]}
    assert by_name["Final"]["placement"]["team1"] == "NET"
    assert by_name["Semi 1"]["placement"] is not None
    assert by_name["Semi 1"]["placement"]["placed"] is True

    src = BracketPlacement.query.filter_by(event=url, match="11111111-1111-1111-1111-111111111111").first()
    assert src is not None and src.is_placed


def test_convert_port_net_to_label_leaves_matches(client, bracket_tournament, player):
    login_as(client, player)
    url = bracket_tournament.url

    client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "11111111-1111-1111-1111-111111111111", "x_pos": 10, "y_pos": 20},
    )
    client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "22222222-2222-2222-2222-222222222222", "x_pos": 400, "y_pos": 20},
    )
    r = client.post(
        f"/_api/tournaments/{url}/bracket-placements/convert-port",
        json={
            "match": "22222222-2222-2222-2222-222222222222",
            "side": "team1",
            "mode": "LABEL",
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    by_name = {m["name"]: m for m in data["matches"]}
    assert by_name["Final"]["placement"]["team1"] == "LABEL"
    assert by_name["Semi 1"]["placement"]["placed"] is True


def test_put_placements_unplace(client, bracket_tournament, player):
    login_as(client, player)
    url = bracket_tournament.url

    client.post(
        f"/_api/tournaments/{url}/bracket-placements/add",
        json={"match": "11111111-1111-1111-1111-111111111111", "x_pos": 10, "y_pos": 20},
    )
    r = client.put(
        f"/_api/tournaments/{url}/bracket-placements",
        json={
            "placements": [
                {
                    "match": "11111111-1111-1111-1111-111111111111",
                    "x_pos": None,
                    "y_pos": None,
                    "width": 280,
                    "height": 100,
                    "team1": "LABEL",
                    "team2": "LABEL",
                }
            ]
        },
    )
    assert r.status_code == 200
    m = next(m for m in r.get_json()["matches"] if m["uuid"].startswith("1111"))
    assert m["placement"]["placed"] is False
    assert m["placement"]["x_pos"] is None

    row = BracketPlacement.query.filter_by(event=url, match="11111111-1111-1111-1111-111111111111").first()
    assert row is not None
    assert row.x_pos is None


def test_non_to_cannot_edit(client, tournament, player):
    # player is not a TO of the default tournament fixture
    login_as(client, player)
    r = client.put(
        f"/_api/tournaments/{tournament.url}/bracket-placements",
        json={"placements": []},
    )
    assert r.status_code == 403


def test_default_port_mode_helper():
    from app.routes.tournaments.brackets import _default_port_modes, _parse_match_ref

    assert _parse_match_ref("Semi 1::winner") == ("Semi 1", "winner")
    assert _parse_match_ref("tag::Seed") is None
    assert _parse_match_ref(None) is None

    class Fake:
        def __init__(self, t1, t2):
            self.team1_initial = t1
            self.team2_initial = t2

    m = Fake("Semi 1::winner", "TeamX")
    placed = {"semi 1": object()}
    t1, t2 = _default_port_modes(m, placed)
    assert t1 == BracketPortMode.NET
    assert t2 == BracketPortMode.LABEL


def test_add_text_labeled_team_and_image(client, bracket_tournament, player, app):
    login_as(client, player)
    url = bracket_tournament.url

    r = client.post(
        f"/_api/tournaments/{url}/bracket-elements/text",
        json={"text": "Finals", "x_pos": 10, "y_pos": 20, "size": 24},
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert len(data["texts"]) == 1
    assert data["texts"][0]["text"] == "Finals"
    assert data["texts"][0]["size"] == 24
    text_id = data["texts"][0]["id"]

    r = client.post(
        f"/_api/tournaments/{url}/bracket-elements/labeled-team",
        json={
            "label": "SF1 W",
            "team": "Semi 1::winner",
            "kind": "LABEL",
            "x_pos": 30,
            "y_pos": 40,
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert len(data["labeled_teams"]) == 1
    assert data["labeled_teams"][0]["team"] == "Semi 1::winner"
    assert data["labeled_teams"][0]["label"] == "SF1 W"
    lt_id = data["labeled_teams"][0]["id"]

    # Fake an uploaded image path (skip binary upload)
    r = client.post(
        f"/_api/tournaments/{url}/bracket-elements/image",
        json={
            "image": "uploads/brackets/test.png",
            "x_pos": 50,
            "y_pos": 60,
            "width": 120,
            "height": 80,
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert len(data["images"]) == 1
    img_id = data["images"][0]["id"]

    # GET includes all three
    r = client.get(f"/_api/tournaments/{url}/bracket")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["texts"]) == 1
    assert len(data["labeled_teams"]) == 1
    assert len(data["images"]) == 1

    # Bulk save updates + clear_missing deletes
    r = client.put(
        f"/_api/tournaments/{url}/bracket-placements",
        json={
            "placements": [],
            "texts": [{"id": text_id, "text": "Updated", "x_pos": 1, "y_pos": 2, "size": 18}],
            "labeled_teams": [
                {
                    "id": lt_id,
                    "label": "Finalist",
                    "team": "Semi 1::winner",
                    "kind": "NET",
                    "x_pos": 5,
                    "y_pos": 6,
                }
            ],
            "images": [],
            "clear_missing": True,
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["texts"][0]["text"] == "Updated"
    assert data["labeled_teams"][0]["kind"] == "NET"
    assert data["labeled_teams"][0]["label"] == "Finalist"
    assert data["images"] == []
    # image row deleted
    assert all(i["id"] != img_id for i in data["images"])
