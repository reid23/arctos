"""Integration tests for the bulk match-length endpoint.

POST /_api/tournaments/<url>/matches/bulk-length sets nominal_length on many
matches at once, skipping JOINs and started (locked) matches.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from models import TO, Match, Tournament, db
from tests.utils import login_as, make_registrable_config


@pytest.fixture
def to_player(test_db, tournament, player):
    """Register the default player as TO of the default tournament."""
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    return player


def _make_match(event, name, schedule_type=ScheduleType.STATIC, status=MatchStatus.NOT_STARTED, length=30):
    m = Match(
        name=name,
        event=event,
        field="Field 1",
        schedule_type=schedule_type,
        status=status,
        nominal_length=length,
        nominal_start_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(m)
    db.session.flush()
    return m


@pytest.mark.integration
def test_bulk_length_happy_path(app, client, tournament, to_player):
    """All editable matches get the shared length; response reports each as updated."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        m1 = _make_match(t.url, "Bulk A")
        m2 = _make_match(t.url, "Bulk B", schedule_type=ScheduleType.SAFE)
        m2.previous_match = m1.uuid
        m1.next_match = m2.uuid
        db.session.commit()
        ids = [m1.uuid, m2.uuid]
        login_as(client, db.session.merge(to_player))

    resp = client.post(
        f"/_api/tournaments/{t_url}/matches/bulk-length",
        json={"match_ids": ids, "length": 45},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["updated"] == 2
    assert all(r["status"] == "updated" for r in data["results"])

    with app.app_context():
        for mid in ids:
            m = Match.query.filter_by(uuid=mid).first()
            assert m.nominal_length == 45


@pytest.mark.integration
def test_bulk_length_skips_locked_but_updates_statbreak(app, client, tournament, to_player):
    """Started matches are skipped; a COMPLETED STATBREAK is still editable."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        locked = _make_match(t.url, "Locked", status=MatchStatus.COMPLETED)
        statbreak = _make_match(
            t.url,
            "Lunch",
            schedule_type=ScheduleType.STATBREAK,
            status=MatchStatus.COMPLETED,
        )
        db.session.commit()
        locked_id, statbreak_id = locked.uuid, statbreak.uuid
        login_as(client, db.session.merge(to_player))

    resp = client.post(
        f"/_api/tournaments/{t_url}/matches/bulk-length",
        json={"match_ids": [locked_id, statbreak_id], "length": 20},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] == 1
    statuses = {r["match_id"]: r["status"] for r in data["results"]}
    assert statuses[locked_id] == "skipped_locked"
    assert statuses[statbreak_id] == "updated"

    with app.app_context():
        assert Match.query.filter_by(uuid=locked_id).first().nominal_length == 30
        assert Match.query.filter_by(uuid=statbreak_id).first().nominal_length == 20


@pytest.mark.integration
def test_bulk_length_skips_join(app, client, tournament, to_player):
    """JOIN matches are structurally zero-length and reported as skipped."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        anchor = _make_match(t.url, "Anchor")
        join = _make_match(t.url, "JoinPoint", schedule_type=ScheduleType.JOIN, length=0)
        join.previous_match = anchor.uuid
        anchor.next_match = join.uuid
        db.session.commit()
        join_id = join.uuid
        login_as(client, db.session.merge(to_player))

    resp = client.post(
        f"/_api/tournaments/{t_url}/matches/bulk-length",
        json={"match_ids": [join_id], "length": 25},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] == 0
    assert data["results"][0]["status"] == "skipped_join"

    with app.app_context():
        assert Match.query.filter_by(uuid=join_id).first().nominal_length == 0


@pytest.mark.integration
def test_bulk_length_foreign_tournament_match_not_touched(app, client, tournament, to_player):
    """A match belonging to another tournament is reported not_found and left unchanged."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        cfg = make_registrable_config()
        other = Tournament(
            url="other-bulk-tournament",
            name="Other Tournament",
            start_date=datetime.now(timezone.utc),
            published=True,
            registrable_config_id=cfg.id,
        )
        db.session.add(other)
        db.session.flush()
        foreign = _make_match(other.url, "Foreign Match")
        db.session.commit()
        foreign_id = foreign.uuid
        login_as(client, db.session.merge(to_player))

    resp = client.post(
        f"/_api/tournaments/{t_url}/matches/bulk-length",
        json={"match_ids": [foreign_id], "length": 90},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] == 0
    assert data["results"][0]["status"] == "not_found"

    with app.app_context():
        assert Match.query.filter_by(uuid=foreign_id).first().nominal_length == 30


@pytest.mark.integration
def test_bulk_length_requires_to(app, client, tournament, player):
    """Non-TOs get a 403."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        m = _make_match(t.url, "NoAuth Match")
        db.session.commit()
        mid = m.uuid
        login_as(client, db.session.merge(player))

    resp = client.post(
        f"/_api/tournaments/{t_url}/matches/bulk-length",
        json={"match_ids": [mid], "length": 45},
    )
    assert resp.status_code == 403


@pytest.mark.integration
def test_bulk_length_validates_input(app, client, tournament, to_player):
    """Bad lengths and empty id lists are rejected with 400."""
    with app.app_context():
        t = db.session.merge(tournament)
        t_url = t.url
        m = _make_match(t.url, "Validate Match")
        db.session.commit()
        mid = m.uuid
        login_as(client, db.session.merge(to_player))

    url = f"/_api/tournaments/{t_url}/matches/bulk-length"
    assert client.post(url, json={"match_ids": [mid], "length": 0}).status_code == 400
    assert client.post(url, json={"match_ids": [mid], "length": "abc"}).status_code == 400
    assert client.post(url, json={"match_ids": [], "length": 30}).status_code == 400
    assert client.post(url, json={"match_ids": "not-a-list", "length": 30}).status_code == 400
