"""Tests for graceful handling of cyclic match dependencies.

A cycle can come from manual editing (e.g. ``A`` references ``B::winner`` and
``B`` references ``A::winner``). Before, this raised from
``MatchGraph.topological_sort`` and surfaced as a 500 from any endpoint that
ran scheduling. Now ``topological_sort`` is lenient and ``run_scheduling``
falls back to a best-effort placement for cycle nodes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.utils.MatchGraph import build_match_graph
from app.utils.scheduling import (
    recompute_all_match_times,
    validate_match_warnings,
)
from models import Match, db


def _mk(tournament_url: str, name: str, *, schedule_type=ScheduleType.SAFE, **kwargs) -> Match:
    m = Match(
        name=name,
        event=tournament_url,
        schedule_type=schedule_type,
        status=MatchStatus.NOT_STARTED,
        nominal_length=60,
        field="F1",
        **kwargs,
    )
    db.session.add(m)
    db.session.flush()
    return m


@pytest.mark.unit
def test_topological_sort_returns_cycle_keys_without_raising(app, test_db, tournament):
    """Mutual `Match::winner` references between A and B form a cycle."""
    with app.app_context():
        a = _mk(tournament.url, "A", team1_initial="B::winner")
        b = _mk(tournament.url, "B", team1_initial="A::winner")
        db.session.flush()

        graph = build_match_graph(tournament.url)
        order, cycle_keys = graph.topological_sort()
        # Both A and B should be in the cycle remnant.
        cycle_names = {name for name, _field in cycle_keys}
        assert {"A", "B"} <= cycle_names
        # Order shouldn't include any cycle node.
        order_names = {name for name, _field in order}
        assert order_names.isdisjoint(cycle_names)


@pytest.mark.unit
def test_recompute_all_match_times_does_not_raise_on_cycle(app, test_db, tournament):
    """Scheduling completes without an unhandled exception when a cycle exists."""
    with app.app_context():
        a = _mk(tournament.url, "A", team1_initial="B::winner")
        b = _mk(tournament.url, "B", team1_initial="A::winner")
        db.session.commit()

        # Should not raise. We don't assert on the resulting times here — just
        # that the call returns cleanly.
        recompute_all_match_times(tournament.url)


@pytest.mark.unit
def test_dynamic_cycle_node_falls_back_to_previous_match_end_time(app, test_db, tournament):
    """A dynamic match in a cycle gets nominal_start_time = previous_match.end_time."""
    with app.app_context():
        anchor_start = datetime(2026, 6, 8, 10, 0, 0)
        # Anchor: a STATIC match with a known nominal start + length so its end is
        # well-defined and outside any cycle.
        anchor = _mk(
            tournament.url,
            "Anchor",
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=anchor_start,
            scheduled_start_time=anchor_start,
        )
        # Two SAFE matches that reference each other's winner — a cycle. B is linked
        # to Anchor via the previous_match FK so the fallback has somewhere to look.
        a = _mk(tournament.url, "A", team1_initial="B::winner")
        b = _mk(tournament.url, "B", team1_initial="A::winner", previous_match=anchor.uuid)
        anchor.next_match = b.uuid
        db.session.commit()

        recompute_all_match_times(tournament.url)
        db.session.refresh(b)
        # Anchor ends at start + 60min = 11:00.
        assert b.nominal_start_time == anchor_start + timedelta(minutes=60)


@pytest.mark.unit
def test_cycle_path_clears_ready_to_start(app, test_db, tournament):
    """A previously READY match that becomes cyclic must not stay startable."""
    with app.app_context():
        a = _mk(tournament.url, "A", team1_initial="B::winner")
        b = _mk(tournament.url, "B", team1_initial="A::winner")
        a.status = MatchStatus.READY_TO_START
        db.session.commit()

        recompute_all_match_times(tournament.url)
        db.session.refresh(a)
        assert a.status == MatchStatus.NOT_STARTED


@pytest.mark.unit
def test_static_cycle_node_keeps_user_supplied_start_time(app, test_db, tournament):
    """A STATIC match in a cycle keeps its user-supplied nominal_start_time."""
    with app.app_context():
        user_picked = datetime(2026, 6, 8, 14, 30, 0)
        # `previous_match`-based cycle isn't possible without dynamic refs. Use
        # mutual `Match::winner` skip-condition refs instead and place a STATIC
        # match into that cycle by referencing it from a SAFE match's deps.
        s = _mk(
            tournament.url,
            "S",
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=user_picked,
            scheduled_start_time=user_picked,
            team1_initial="X::winner",
        )
        x = _mk(tournament.url, "X", team1_initial="S::winner")
        db.session.commit()

        recompute_all_match_times(tournament.url)
        db.session.refresh(s)
        # STATIC time stayed put.
        assert s.nominal_start_time == user_picked


@pytest.mark.unit
def test_validate_match_warnings_lists_cycle_member_names(app, test_db, tournament):
    """The warnings endpoint surfaces the names of matches involved in the cycle."""
    with app.app_context():
        _mk(tournament.url, "Loop1", team1_initial="Loop2::winner")
        _mk(tournament.url, "Loop2", team1_initial="Loop1::winner")
        db.session.commit()

        warnings = validate_match_warnings(tournament.url)
        cycles = [w for w in warnings if w["kind"] == "cycle"]
        assert len(cycles) == 1
        assert "Loop1" in cycles[0]["matches"]
        assert "Loop2" in cycles[0]["matches"]


@pytest.mark.unit
def test_no_cycle_no_cycle_warning(app, test_db, tournament):
    """Sanity: a non-cyclic schedule reports no cycle warnings."""
    with app.app_context():
        anchor_start = datetime(2026, 6, 8, 10, 0, 0)
        a = _mk(
            tournament.url,
            "A",
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=anchor_start,
            scheduled_start_time=anchor_start,
        )
        _mk(tournament.url, "B", team1_initial="A::winner", previous_match=a.uuid)
        db.session.commit()

        warnings = validate_match_warnings(tournament.url)
        cycles = [w for w in warnings if w["kind"] == "cycle"]
        assert cycles == []
