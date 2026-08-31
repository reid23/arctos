"""Integration test: update_tags triggers schedule recompute."""

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from models import Field, Match, Tag, TO, db
from tests.utils import login_as


@pytest.mark.integration
def test_update_tags_recomputes_schedule(app, client, test_db, tournament, player, seeded_teams):
    """After update_tags, recompute_all_match_times runs so match status can transition to READY_TO_START."""
    with app.app_context():
        t = db.session.merge(tournament)
        p = db.session.merge(player)
        tournament_url = t.url

        # Make player a TO so they can call update-tags
        db.session.add(TO(user_id=p.id, user_type="player", event=tournament_url))
        db.session.flush()

        # Create field and tag
        field = Field(event=tournament_url, name="Field 1")
        db.session.add(field)
        tag = Tag(event=tournament_url, name="PoolA", team=None)
        db.session.add(tag)
        db.session.flush()

        # Match with team1 from tag (unresolved) and team2 explicit
        m = Match(
            name="Tag Match",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            team1_initial="tag::PoolA",
            team2_initial="team2",
            team1=None,
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.uuid
        tag_id = tag.id
        login_as(client, p)

    # Assign tag to team1
    resp = client.post(
        f"/_api/tournaments/{tournament_url}/update-tags",
        json={"tag_id": tag_id, "team_id": "team1"},
        content_type="application/json",
    )
    assert resp.status_code == 200

    with app.app_context():
        m2 = Match.query.get(match_id)
        # update_tags sets team1 when tag is assigned
        assert m2.team1 == "team1"
        assert m2.team2 == "team2"
        # Recompute should have run: STATIC match with all teams resolved -> READY_TO_START
        assert m2.status == MatchStatus.READY_TO_START
