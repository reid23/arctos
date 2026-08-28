"""Integration tests for the footage API (link + chunked upload + list/delete)."""

import io
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from models import db, Tournament, Field, Match, Point, Player, TO, Camera
from app.domain.enums import MatchStatus, ScheduleType
from app.services.dual_write import get_camera_timepoint_arrays
from tests.utils import make_registrable_config, login_as

YT_ID = "dQw4w9WgXcQ"
YT_URL = f"https://www.youtube.com/watch?v={YT_ID}"
YT_SHORT = f"https://youtu.be/{YT_ID}"


@pytest.fixture
def footage_setup(test_db):
    """A tournament with a field, a TO player, and a match with two points."""
    cfg = make_registrable_config()
    tourn = Tournament(
        url="footage-cup",
        name="Footage Cup",
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=1),
        published=True,
        schedule_published=True,
        registrable_config_id=cfg.id,
    )
    db.session.add(tourn)
    db.session.flush()
    db.session.add(Field(event=tourn.url, name="Field 1"))

    to_player = Player(id="footage-to", name="Footage TO", pw_hash="x")
    to_player.set_password("pw")
    db.session.add(to_player)
    db.session.add(TO(user_id=to_player.id, user_type="player", event=tourn.url))

    match = Match(
        name="Game 1",
        event=tourn.url,
        field="Field 1",
        schedule_type=ScheduleType.STATIC,
        status=MatchStatus.COMPLETED,
    )
    db.session.add(match)
    db.session.flush()

    base = datetime(2026, 1, 1, 0, 0, 0)
    db.session.add(Point(match=match.uuid, winner="TEAM1", stamp=base))
    db.session.add(Point(match=match.uuid, winner="TEAM2", stamp=base + timedelta(seconds=30)))
    db.session.add(Point(match=match.uuid, winner="TEAM1", stamp=base + timedelta(seconds=90)))
    db.session.commit()
    db.session.refresh(match)
    return {"url": tourn.url, "match_id": match.uuid, "to": to_player}


def _link_url(setup):
    return f"/_api/tournaments/{setup['url']}/matches/{setup['match_id']}/footage/link"


def test_footage_link_creates_success_camera(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={"youtube_link": YT_SHORT, "camera_name": "Main"},
    )
    assert resp.status_code == 200
    uuid = resp.get_json()["camera_uuid"]
    cam = Camera.query.filter_by(uuid=uuid).first()
    assert cam.status == "SUCCESS"
    assert cam.link == YT_URL
    assert cam.source_type == "user_upload"
    assert get_camera_timepoint_arrays(cam) == ([], [])


def test_footage_link_rejects_non_youtube(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={"youtube_link": "https://example.com/watch?v=abcdefghijk", "camera_name": "Main"},
    )
    assert resp.status_code == 400


def test_footage_link_world_time_anchors_sorted(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={
            "youtube_link": YT_SHORT,
            "camera_name": "Cam",
            "anchors": [
                {"world_time": "2026-01-01T00:00:10Z", "video_offset": 10.0},
                {"world_time": "2026-01-01T00:00:00Z", "video_offset": 0.0},
            ],
        },
    )
    assert resp.status_code == 200
    uuid = resp.get_json()["camera_uuid"]
    worlds, videos = get_camera_timepoint_arrays(Camera.query.filter_by(uuid=uuid).first())
    assert videos == [0.0, 10.0]
    assert worlds == ["2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z"]


def test_footage_link_rejects_nonfinite_offset(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={
            "youtube_link": YT_SHORT,
            "camera_name": "Cam",
            "anchors": [{"world_time": "2026-01-01T00:00:00Z", "video_offset": "nan"}],
        },
    )
    assert resp.status_code == 400


def test_footage_link_point_index_anchor_resolved_to_stamp(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={
            "youtube_link": YT_SHORT,
            "camera_name": "Cam",
            "anchors": [{"point_index": 1, "video_offset": 5.0}],
        },
    )
    assert resp.status_code == 200
    uuid = resp.get_json()["camera_uuid"]
    worlds, videos = get_camera_timepoint_arrays(Camera.query.filter_by(uuid=uuid).first())
    assert videos == [5.0]
    # point_index 1 is the second point, stamp 2026-01-01T00:00:30.
    assert worlds[0].startswith("2026-01-01T00:00:30")


def test_footage_link_point_index_out_of_range_is_400(client, footage_setup):
    login_as(client, footage_setup["to"])
    resp = client.post(
        _link_url(footage_setup),
        json={
            "youtube_link": YT_SHORT,
            "camera_name": "Cam",
            "anchors": [{"point_index": 99, "video_offset": 1.0}],
        },
    )
    assert resp.status_code == 400


def test_footage_link_requires_youtube_link_and_name(client, footage_setup):
    login_as(client, footage_setup["to"])
    assert client.post(_link_url(footage_setup), json={"camera_name": "Cam"}).status_code == 400
    assert client.post(_link_url(footage_setup), json={"youtube_link": YT_SHORT}).status_code == 400


def test_footage_requires_to(client, footage_setup):
    resp = client.post(
        _link_url(footage_setup),
        json={"youtube_link": YT_SHORT, "camera_name": "Cam"},
    )
    assert resp.status_code in (401, 403)


def test_footage_chunked_upload_creates_uploading_camera_and_spawns_youtube(client, footage_setup):
    login_as(client, footage_setup["to"])
    base = f"/_api/tournaments/{footage_setup['url']}/matches/{footage_setup['match_id']}/footage/upload"
    with (
        patch("app.utils.user_uploads.upload_camera_to_youtube") as mocked,
        patch("app.utils.user_uploads.threading.Thread") as thread,
    ):
        # Run the "thread" target synchronously so status transitions are observable.
        thread.side_effect = lambda target, daemon=False: type("T", (), {"start": lambda self: target()})()
        init = client.post(
            base + "/init",
            json={
                "camera_name": "Cam",
                "filename": "v.mp4",
                "content_type": "video/mp4",
                "total_chunks": 1,
                "anchors": [{"point_index": 0, "video_offset": 2.0}],
            },
        )
        assert init.status_code == 200
        upload_id = init.get_json()["upload_id"]
        chunk = client.post(
            base + "/chunk",
            data={"upload_id": upload_id, "chunk_index": "0", "chunk": (io.BytesIO(b"fakevideo"), "chunk")},
            content_type="multipart/form-data",
        )
        assert chunk.status_code == 200
        done = client.post(base + "/complete", json={"upload_id": upload_id})
    assert done.status_code == 200
    cam = Camera.query.filter_by(uuid=done.get_json()["camera_uuid"]).first()
    assert cam.status == "UPLOADING"
    assert cam.source_type == "user_upload"
    mocked.assert_called_once()
    worlds, videos = get_camera_timepoint_arrays(cam)
    assert videos == [2.0]


def test_footage_upload_rejects_bad_extension_and_chunk_cap(client, footage_setup):
    login_as(client, footage_setup["to"])
    base = f"/_api/tournaments/{footage_setup['url']}/matches/{footage_setup['match_id']}/footage/upload"
    bad_ext = client.post(
        base + "/init",
        json={
            "camera_name": "Cam",
            "filename": "clip.mov",
            "content_type": "video/quicktime",
            "total_chunks": 1,
        },
    )
    assert bad_ext.status_code == 400
    too_many = client.post(
        base + "/init",
        json={
            "camera_name": "Cam",
            "filename": "clip.mp4",
            "content_type": "video/mp4",
            "total_chunks": 10_000,
        },
    )
    assert too_many.status_code == 400


def test_footage_list_and_delete(client, footage_setup):
    login_as(client, footage_setup["to"])
    created = client.post(
        _link_url(footage_setup),
        json={"youtube_link": YT_SHORT, "camera_name": "Cam"},
    ).get_json()["camera_uuid"]

    event_list = client.get(f"/_api/tournaments/{footage_setup['url']}/footage").get_json()
    assert any(c["uuid"] == created for c in event_list["cameras"])

    match_list = client.get(
        f"/_api/tournaments/{footage_setup['url']}/matches/{footage_setup['match_id']}/footage"
    ).get_json()
    assert any(c["uuid"] == created for c in match_list["cameras"])

    d = client.delete(f"/_api/tournaments/{footage_setup['url']}/matches/{footage_setup['match_id']}/footage/{created}")
    assert d.status_code == 200
    assert Camera.query.filter_by(uuid=created).first() is None


def test_match_detail_serializes_camera_timepoints(client, footage_setup):
    """Regression: the preserved display path still emits time_world/time_video."""
    login_as(client, footage_setup["to"])
    client.post(
        _link_url(footage_setup),
        json={
            "youtube_link": YT_SHORT,
            "camera_name": "Cam",
            "anchors": [{"world_time": "2026-01-01T00:00:00Z", "video_offset": 0.0}],
        },
    )
    resp = client.get(f"/_api/tournaments/{footage_setup['url']}/match?id={footage_setup['match_id']}")
    assert resp.status_code == 200
    cams = resp.get_json()["available_cameras"]
    cam = next(c for c in cams if c.get("source_type") == "user_upload")
    assert cam["time_world"] == ["2026-01-01T00:00:00Z"]
    assert cam["time_video"] == [0.0]
    assert cam["url"] == YT_URL
