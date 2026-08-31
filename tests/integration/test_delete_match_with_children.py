"""Integration test: deleting a match cleans up its FK children (referees, players)."""

import pytest

from app.domain.enums import MatchStatus, ScheduleType, WinnerSide
from app.models.normalised import CameraTimepoint, MatchPlayer, MatchReferee
from models import Camera, Field, Match, Point, TO, db
from tests.utils import login_as


@pytest.mark.integration
def test_delete_match_with_all_children(app, client, test_db, tournament, player):
    """A match with every kind of child row (points, referees, players, cameras
    and their timepoints) can be deleted without tripping any FK constraint, and
    all child rows are removed."""
    with app.app_context():
        t = db.session.merge(tournament)
        p = db.session.merge(player)
        tournament_url = t.url

        db.session.add(TO(user_id=p.id, user_type="player", event=tournament_url))
        field = Field(event=tournament_url, name="Field 1")
        db.session.add(field)
        db.session.flush()

        m = Match(
            name="Doomed",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
        )
        db.session.add(m)
        db.session.flush()
        match_id = m.uuid

        db.session.add(Point(match=match_id, winner="TEAM1"))
        db.session.add(MatchReferee(match_uuid=match_id, slot=0, team_id=None, initial="Some Match::winner"))
        db.session.add(MatchPlayer(match_uuid=match_id, player_id=p.id, side=WinnerSide.TEAM1))
        cam = Camera(match_uuid=match_id, event=tournament_url, field=field.id, name="cam1")
        db.session.add(cam)
        db.session.flush()
        camera_id = cam.uuid
        db.session.add(CameraTimepoint(camera_uuid=camera_id, sequence=0, time_world="t0", time_video=0.0))
        db.session.commit()
        login_as(client, p)

    resp = client.delete(f"/_api/tournaments/{tournament_url}/matches/{match_id}")
    assert resp.status_code == 200

    with app.app_context():
        assert Match.query.get(match_id) is None
        assert Point.query.filter_by(match=match_id).count() == 0
        assert MatchReferee.query.filter_by(match_uuid=match_id).count() == 0
        assert MatchPlayer.query.filter_by(match_uuid=match_id).count() == 0
        assert Camera.query.filter_by(match_uuid=match_id).count() == 0
        assert CameraTimepoint.query.filter_by(camera_uuid=camera_id).count() == 0


@pytest.mark.integration
def test_delete_match_closes_chain_gap(app, client, test_db, tournament, player):
    """Deleting a middle match reconnects its chain neighbours."""
    with app.app_context():
        t = db.session.merge(tournament)
        p = db.session.merge(player)
        tournament_url = t.url

        db.session.add(TO(user_id=p.id, user_type="player", event=tournament_url))
        db.session.add(Field(event=tournament_url, name="Field 1"))

        first = Match(
            name="A",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.SAFE,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
        )
        middle = Match(
            name="B",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.SAFE,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
        )
        last = Match(
            name="C",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.SAFE,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
        )
        db.session.add_all([first, middle, last])
        db.session.flush()
        first.next_match = middle.uuid
        middle.previous_match = first.uuid
        middle.next_match = last.uuid
        last.previous_match = middle.uuid
        first_id, middle_id, last_id = first.uuid, middle.uuid, last.uuid
        db.session.commit()
        login_as(client, p)

    resp = client.delete(f"/_api/tournaments/{tournament_url}/matches/{middle_id}")
    assert resp.status_code == 200

    with app.app_context():
        assert Match.query.get(middle_id) is None
        # Gap closes: A <-> C now link directly.
        assert Match.query.get(first_id).next_match == last_id
        assert Match.query.get(last_id).previous_match == first_id


@pytest.mark.integration
def test_delete_tournament_removes_match_children(app, client, test_db, tournament, player):
    """Deleting a tournament hard-deletes its matches and all their child rows."""
    with app.app_context():
        t = db.session.merge(tournament)
        p = db.session.merge(player)
        tournament_url = t.url

        db.session.add(TO(user_id=p.id, user_type="player", event=tournament_url))
        field = Field(event=tournament_url, name="Field 1")
        db.session.add(field)
        db.session.flush()

        m = Match(
            name="Doomed",
            event=tournament_url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            nominal_length=60,
        )
        db.session.add(m)
        db.session.flush()
        match_id = m.uuid

        db.session.add(Point(match=match_id, winner="TEAM1"))
        db.session.add(MatchReferee(match_uuid=match_id, slot=0, team_id=None, initial="X::winner"))
        db.session.add(MatchPlayer(match_uuid=match_id, player_id=p.id, side=WinnerSide.TEAM1))
        cam = Camera(match_uuid=match_id, event=tournament_url, field=field.id, name="cam1")
        db.session.add(cam)
        db.session.flush()
        camera_id = cam.uuid
        db.session.add(CameraTimepoint(camera_uuid=camera_id, sequence=0, time_world="t0", time_video=0.0))
        db.session.commit()
        login_as(client, p)

    resp = client.post(f"/_api/{tournament_url}/delete", data={"confirm_url": tournament_url})
    assert resp.status_code == 200

    with app.app_context():
        assert Match.query.filter_by(event=tournament_url).count() == 0
        assert Point.query.filter_by(match=match_id).count() == 0
        assert MatchReferee.query.filter_by(match_uuid=match_id).count() == 0
        assert MatchPlayer.query.filter_by(match_uuid=match_id).count() == 0
        assert Camera.query.filter_by(match_uuid=match_id).count() == 0
        assert CameraTimepoint.query.filter_by(camera_uuid=camera_id).count() == 0
