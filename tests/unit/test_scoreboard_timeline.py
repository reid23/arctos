"""Unit tests for ScoreboardService.timeline (pure; no DB)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.enums import SetType
from app.exceptions import ValidationError
from app.services.scoreboard_service import ScoreboardMatch, ScoreboardService, ScoreboardSide

ALPHA = ScoreboardSide(name="Alpha", shortname=None, photo=None)
BETA = ScoreboardSide(name="Beta", shortname=None, photo=None)
# A multiple of the 1.5 s stones beat, so beat boundaries fall on whole offsets from here.
BEAT_ALIGNED = datetime(2026, 1, 1, 12, 0, 0)


def make_match(set_type=SetType.SETS, started_at=BEAT_ALIGNED, stones_per_set=None):
    match = SimpleNamespace(uuid="m1", set_type=set_type, started_at=started_at, stones_per_set=stones_per_set)
    return ScoreboardMatch(match=match, team1=ALPHA, team2=BETA)


def make_point(start_seconds, end_seconds, winner, set_number=1, rerolled=False, stones_at_start=None):
    return SimpleNamespace(
        stamp=BEAT_ALIGNED + timedelta(seconds=start_seconds),
        end_stamp=None if end_seconds is None else BEAT_ALIGNED + timedelta(seconds=end_seconds),
        winner=winner,
        set_number=set_number,
        rerolled=rerolled,
        stones_at_start=stones_at_start,
    )


def at(seconds):
    return BEAT_ALIGNED + timedelta(seconds=seconds)


def scores(entry, set_number=1):
    by_set = entry.state["scores_by_set"][set_number]
    return by_set["team1_score"], by_set["team2_score"]


@pytest.mark.unit
def test_score_progression_for_plain_match():
    points = [make_point(10, 20, "TEAM1"), make_point(30, 45, "TEAM2"), make_point(50, 60, "TEAM1")]

    timeline = ScoreboardService.timeline(make_match(), points).unwrap()

    assert [entry.effective_at for entry in timeline] == [at(0), at(10), at(20), at(45), at(60)]
    assert timeline[0].state["sets"] == []
    assert [scores(entry) for entry in timeline[1:]] == [(0, 0), (1, 0), (1, 1), (2, 1)]
    final = timeline[-1].state
    assert final["has_active_match"] is True
    assert final["match_id"] == "m1"
    assert final["team1_name"] == "Alpha"
    assert final["stones_info"] is None
    assert final["timestamp"] == "2026-01-01T12:01:00Z"


@pytest.mark.unit
def test_rerolled_point_never_counts():
    points = [make_point(10, 20, "TEAM1", rerolled=True), make_point(30, 40, "TEAM2")]

    timeline = ScoreboardService.timeline(make_match(), points).unwrap()

    assert [scores(entry) for entry in timeline[1:]] == [(0, 0), (0, 1)]
    assert timeline[-1].effective_at == at(40)


@pytest.mark.unit
def test_set_column_appears_at_point_start():
    points = [make_point(10, 20, "TEAM1", set_number=1), make_point(100, 110, "TEAM2", set_number=2)]

    timeline = ScoreboardService.timeline(make_match(), points).unwrap()

    second_set_starts = next(entry for entry in timeline if entry.effective_at == at(100))
    assert second_set_starts.state["sets"] == [1, 2]
    assert scores(second_set_starts, 2) == (0, 0)
    assert scores(timeline[-1], 2) == (0, 1)


@pytest.mark.unit
def test_stones_tick_on_beats_during_point_and_hold_between_points():
    match = make_match(set_type=SetType.STONES, stones_per_set=100)
    first = make_point(0.5, 6.5, "TEAM1", stones_at_start=100)
    second = make_point(15.25, 16.25, "TEAM2", stones_at_start=96)

    timeline = ScoreboardService.timeline(match, [first, second]).unwrap()

    stones_by_time = [(entry.effective_at, entry.state["stones_info"]["stones_remaining"]) for entry in timeline]
    assert stones_by_time == [
        (at(0), 100),
        (at(0.5), 100),
        (at(1.5), 99),
        (at(3.0), 98),
        (at(4.5), 97),
        (at(6.0), 96),
        (at(6.5), 96),
        (at(16.25), 96),
    ]
    assert timeline[0].state["stones_info"]["stones_per_set"] == 100
    assert scores(timeline[-1]) == (1, 1)


@pytest.mark.unit
def test_consecutive_identical_states_are_collapsed():
    # Second point starts in the same set and has no winner, so neither its start nor end changes anything visible.
    points = [make_point(10, 20, "TEAM1"), make_point(30, 40, None)]

    timeline = ScoreboardService.timeline(make_match(), points).unwrap()

    assert [entry.effective_at for entry in timeline] == [at(0), at(10), at(20)]


@pytest.mark.unit
def test_starts_at_first_point_when_match_has_no_start_time():
    timeline = ScoreboardService.timeline(make_match(started_at=None), [make_point(10, 20, "TEAM1")]).unwrap()

    assert timeline[0].effective_at == at(10)


@pytest.mark.unit
def test_unstarted_match_without_points_is_an_error():
    result = ScoreboardService.timeline(make_match(started_at=None), [])

    assert isinstance(result.unwrap_err(), ValidationError)
