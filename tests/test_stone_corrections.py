"""Atomic manual stone corrections; every write uses the isolated test database."""

from datetime import datetime, timedelta, timezone

import pytest

from app.error_values import Err, Ok
from app.services.match_actions_service import MatchActionsService
from models import Match, Point, db
from tests.utils import login_as

NOW = datetime(2026, 10, 24, 12, 0, 30)


def millis(value):
    return value.replace(tzinfo=timezone.utc).timestamp() * 1000


@pytest.fixture
def stone_match(test_db, tournament, head_ref_player, monkeypatch):
    monkeypatch.setattr("app.utils.datetime_helpers.now_utc_naive", lambda: NOW)
    match = Match(
        name="Correction",
        event=tournament.url,
        schedule_type="SAFE",
        set_type="STONES",
        status="IN_PROGRESS",
        nominal_length=60,
        field="Field 1",
        stones_remaining=100,
    )
    db.session.add(match)
    db.session.flush()
    point = Point(match=match.uuid, stamp=NOW - timedelta(seconds=30), stones_at_start=100, set_number=1)
    db.session.add(point)
    db.session.commit()
    return match, point, head_ref_player


def payload(match, point, **changes):
    data = {
        "match_id": match.uuid,
        "stones_remaining": 50,
        "correction": {
            "point_id": point.uuid,
            "latest_point_id": point.uuid,
            "observed_at_ms": millis(NOW),
            "expected_anchor": 100,
            "expected_remaining": 100,
        },
    }
    data.update(changes)
    return data


def call(match, ref, data):
    return MatchActionsService.update_stones(match.event, ref.id, **data)


def test_running_correction_keeps_duration_and_global_beat(stone_match):
    match, point, ref = stone_match
    stamp = point.stamp
    result = call(match, ref, payload(match, point))
    assert isinstance(result, Ok)
    assert result.unwrap()["stones_remaining"] == 50
    assert point.stones_at_start == 70
    assert point.stamp == stamp and point.end_stamp is None
    assert match.stones_remaining == 50
    db.session.expire_all()
    assert db.session.get(Point, point.uuid).stones_at_start == 70


def test_delayed_retry_reuses_anchor_not_new_observation(stone_match, monkeypatch):
    match, point, ref = stone_match
    body = payload(match, point)
    assert isinstance(call(match, ref, body), Ok)
    monkeypatch.setattr("app.utils.datetime_helpers.now_utc_naive", lambda: NOW + timedelta(seconds=6))
    result = call(match, ref, body)
    assert isinstance(result, Ok)
    assert result.unwrap()["stones_remaining"] == 46
    assert point.stones_at_start == 70


def test_between_points_correction_and_retry(stone_match):
    match, point, ref = stone_match
    point.end_stamp = NOW
    db.session.commit()
    body = payload(match, point)
    body["correction"]["point_id"] = None
    assert isinstance(call(match, ref, body), Ok)
    assert isinstance(call(match, ref, body), Ok)
    assert match.stones_remaining == 50 and point.stones_at_start == 100


@pytest.mark.parametrize("change", ["ended", "new", "anchor"])
def test_intervening_state_rejects_stale_correction(stone_match, change):
    match, point, ref = stone_match
    body = payload(match, point)
    if change == "ended":
        point.end_stamp = NOW
    elif change == "new":
        point.end_stamp = NOW - timedelta(seconds=2)
        db.session.add(Point(match=match.uuid, stamp=NOW - timedelta(seconds=1), stones_at_start=80))
    else:
        point.stones_at_start = 90
    db.session.commit()
    result = call(match, ref, body)
    assert isinstance(result, Err) and result.unwrap_err().status_code == 409
    assert db.session.get(Match, match.uuid).stones_remaining == 100


@pytest.mark.parametrize("count", [-1, 1.5, True, "no", 2**40])
def test_invalid_count_never_writes(stone_match, count):
    match, point, ref = stone_match
    assert isinstance(call(match, ref, payload(match, point, stones_remaining=count)), Err)
    assert match.stones_remaining == 100 and point.stones_at_start == 100


@pytest.mark.parametrize(
    "observed",
    [
        float("nan"),
        float("inf"),
        10**1000,
        "time",
        True,
        millis(NOW + timedelta(seconds=6)),
        millis(NOW - timedelta(minutes=6)),
        millis(NOW - timedelta(seconds=31)),
    ],
)
def test_invalid_or_stale_observation_never_writes(stone_match, observed):
    match, point, ref = stone_match
    body = payload(match, point)
    body["correction"]["observed_at_ms"] = observed
    assert isinstance(call(match, ref, body), Err)
    assert db.session.get(Match, match.uuid).stones_remaining == 100
    assert db.session.get(Point, point.uuid).stones_at_start == 100


def test_transaction_rollback_keeps_count_and_anchor(stone_match, monkeypatch):
    match, point, ref = stone_match
    session = db.session()
    original = session.commit

    def fail():
        session.flush()
        raise RuntimeError("Simulated commit failure")

    monkeypatch.setattr(session, "commit", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        call(match, ref, payload(match, point))
    session.rollback()
    monkeypatch.setattr(session, "commit", original)
    assert db.session.get(Match, match.uuid).stones_remaining == 100
    assert db.session.get(Point, point.uuid).stones_at_start == 100


def test_route_returns_authoritative_anchor(client, stone_match):
    match, point, ref = stone_match
    path = f"/_api/{match.event}/match-actions/update-stones"
    body = payload(match, point)
    login_as(client, ref)
    response = client.post(path, json=body)
    assert response.status_code == 200
    assert response.json["corrected_point"] == {"uuid": point.uuid, "stones_at_start": 70}
    assert response.json["stones_remaining"] == 50


def test_route_preserves_head_ref_permission(client, stone_match, player):
    match, point, _ = stone_match
    login_as(client, player)
    response = client.post(f"/_api/{match.event}/match-actions/update-stones", json=payload(match, point))
    assert response.status_code == 403
    assert match.stones_remaining == 100 and point.stones_at_start == 100


def test_between_point_check_refreshes_identity_map_after_write_lock(stone_match):
    from sqlalchemy import update

    match, point, ref = stone_match
    point.end_stamp = NOW
    db.session.commit()
    body = payload(match, point)
    body["correction"]["point_id"] = None
    assert match.stones_remaining == 100
    with db.engine.begin() as connection:
        connection.execute(update(Match).where(Match.uuid == match.uuid).values(stones_remaining=60))
    assert match.stones_remaining == 100  # Deliberately stale request-local identity map.
    result = call(match, ref, body)
    assert isinstance(result, Err) and result.unwrap_err().status_code == 409
    assert db.session.get(Match, match.uuid).stones_remaining == 60
