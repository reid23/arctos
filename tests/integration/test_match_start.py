"""Integration tests for match-start flow (HEAD ref starts a match via POST)."""

import pytest

from app.domain.enums import MatchStatus, WinnerSide
from app.services.dual_write import get_match_player_ids
from models import Field, Match, Player, db
from tests.utils import login_as


@pytest.mark.integration
def test_start_match_post_starts_match(app, client, tournament, head_ref_player, seeded_teams):
    """A head ref can successfully start a READY_TO_START match via the API."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        tournament_url = t.url
        ref_id = ref.id
        for pid in ("p1", "p2", "p3"):
            db.session.add(Player(id=pid, name=pid, pw_hash="h"))
        login_as(client, ref)

        m = Match(
            name="Start Me",
            event=tournament_url,
            schedule_type="SAFE",
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            nominal_length=60,
            field="Field 1",
            team1="team1",
            team2="team2",
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.uuid

    resp = client.post(
        f"/_api/{tournament_url}/start-match",
        data={
            "match_id": match_id,
            "team1_players": "p1,p2",
            "team2_players": "p3",
            "match_notes": "hello",
        },
    )
    assert resp.status_code == 200

    with app.app_context():
        m2 = Match.query.get(match_id)
        assert m2.status == MatchStatus.IN_PROGRESS
        assert m2.started_by == ref_id
        assert get_match_player_ids(m2, WinnerSide.TEAM1) == ["p1", "p2"]
        assert get_match_player_ids(m2, WinnerSide.TEAM2) == ["p3"]


@pytest.mark.integration
def test_start_match_post_rejects_overlap(app, client, tournament, head_ref_player):
    """start-match rejects a request when a player appears on both rosters."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        tournament_url = t.url
        login_as(client, ref)

        m = Match(
            name="Overlap",
            event=tournament_url,
            schedule_type="SAFE",
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
            field="Field 1",
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.uuid

    resp = client.post(
        f"/_api/{tournament_url}/start-match",
        data={
            "match_id": match_id,
            "team1_players": "p1,p2",
            "team2_players": "p2,p3",
        },
    )
    assert resp.status_code == 400

    with app.app_context():
        m2 = Match.query.get(match_id)
        assert m2.status == MatchStatus.NOT_STARTED


@pytest.mark.integration
def test_start_match_post_rejects_when_another_in_progress_on_same_field(
    app, client, tournament, head_ref_player, seeded_teams
):
    """Starting a match on a field that has another match IN_PROGRESS returns 400 with field-busy reason."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        tournament_url = t.url
        login_as(client, ref)

        # Ensure field exists
        field = Field(event=tournament_url, name="Field 1")
        db.session.add(field)
        db.session.flush()

        # Match already in progress on Field 1
        other = Match(
            name="Ongoing",
            event=tournament_url,
            field="Field 1",
            schedule_type="STATIC",
            set_type="SETS",
            status=MatchStatus.IN_PROGRESS,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(other)
        db.session.flush()

        # Match we want to start (also on Field 1)
        m = Match(
            name="Want Start",
            event=tournament_url,
            field="Field 1",
            schedule_type="STATIC",
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.uuid

    # POST to API start-match (JSON)
    resp = client.post(
        f"/_api/tournaments/{tournament_url}/start-match",
        json={
            "match_id": match_id,
            "team1_players": ["p1", "p2"],
            "team2_players": ["p3"],
            "match_notes": "",
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data is not None
    assert "error" in data or "reasons" in data
    if "error" in data:
        assert "in progress" in data["error"].lower() or "field" in data["error"].lower()

    with app.app_context():
        m2 = Match.query.get(match_id)
        assert m2.status == MatchStatus.READY_TO_START
