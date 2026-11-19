"""Cross-field same-team serialization edges and the planned/nominal pass split.

Design: the planned (scheduled_start_time) pass is purely structural — it omits
the cross-field same-team serialization edge so the planned timeline can't depend
on itself through a shifting edge. The nominal pass includes the edge, anchored on
the now-stable scheduled times. Planned-timeline double-bookings become warnings.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.utils.MatchGraph import build_match_graph
from app.utils.scheduling import (
    recompute_scheduled_and_nominal_times,
    validate_match_warnings,
)
from models import Match, db


def _mk(url, name, field, sched, **kw):
    m = Match(
        name=name,
        event=url,
        field=field,
        nominal_length=30,
        status=MatchStatus.NOT_STARTED,
        scheduled_start_time=sched,
        **kw,
    )
    db.session.add(m)
    db.session.flush()
    return m


def _deps_of(graph, name, field):
    node = graph.get_node(name, field)
    return {dep.node.name for dep in node.dependencies}


@pytest.mark.unit
def test_cross_field_edge_present_in_nominal_pass(app, test_db, tournament, seeded_teams):
    """B (F2, later) depends on A (F1, earlier) when they share a team — nominal solve."""
    with app.app_context():
        url = tournament.url
        _mk(
            url,
            "A",
            "F1",
            datetime(2026, 6, 9, 10, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        _mk(
            url,
            "B",
            "F2",
            datetime(2026, 6, 9, 11, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        db.session.commit()

        graph = build_match_graph(url, include_resource_conflict_edges=True)
        assert "A" in _deps_of(graph, "B", "F2")


@pytest.mark.unit
def test_cross_field_edge_absent_in_scheduled_pass(app, test_db, tournament, seeded_teams):
    """The planned pass omits the cross-field edge entirely."""
    with app.app_context():
        url = tournament.url
        _mk(
            url,
            "A",
            "F1",
            datetime(2026, 6, 9, 10, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        _mk(
            url,
            "B",
            "F2",
            datetime(2026, 6, 9, 11, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        db.session.commit()

        graph = build_match_graph(url, include_resource_conflict_edges=False)
        assert "A" not in _deps_of(graph, "B", "F2")


@pytest.mark.unit
def test_same_field_shared_team_gets_no_resource_edge(app, test_db, tournament, seeded_teams):
    """Two SAFE matches sharing a team on the *same* field rely on the chain, not the
    cross-field edge — so no resource-conflict edge is added between them."""
    with app.app_context():
        url = tournament.url
        # A and B on F1, no previous_match chain between them, A scheduled earlier.
        _mk(
            url,
            "A",
            "F1",
            datetime(2026, 6, 9, 10, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        _mk(
            url,
            "B",
            "F1",
            datetime(2026, 6, 9, 11, 0),
            schedule_type=ScheduleType.SAFE,
            team1="team_1",
            team1_initial="team_1",
        )
        db.session.commit()

        graph = build_match_graph(url, include_resource_conflict_edges=True)
        # No previous_match link, same field → B must not gain a resource edge to A.
        assert "A" not in _deps_of(graph, "B", "F1")


@pytest.mark.unit
def test_planned_overlap_emits_double_booked_warning(app, test_db, tournament, seeded_teams):
    """Two matches sharing a team that overlap on the planned timeline are flagged."""
    with app.app_context():
        url = tournament.url
        t = datetime(2026, 6, 9, 10, 0)
        _mk(
            url,
            "A",
            "F1",
            t,
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=t,
            team1="team_1",
            team1_initial="team_1",
        )
        _mk(
            url,
            "B",
            "F2",
            t,
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=t,
            team1="team_1",
            team1_initial="team_1",
        )
        db.session.commit()

        warnings = validate_match_warnings(url)
        dbk = [w for w in warnings if w["kind"] == "double_booked"]
        assert len(dbk) == 1
        assert set(dbk[0]["matches"]) == {"A", "B"}


@pytest.mark.unit
def test_non_overlapping_planned_no_warning(app, test_db, tournament, seeded_teams):
    """Same team across fields but non-overlapping planned times → no warning."""
    with app.app_context():
        url = tournament.url
        _mk(
            url,
            "A",
            "F1",
            datetime(2026, 6, 9, 10, 0),
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=datetime(2026, 6, 9, 10, 0),
            team1="team_1",
            team1_initial="team_1",
        )
        _mk(
            url,
            "B",
            "F2",
            datetime(2026, 6, 9, 11, 0),
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=datetime(2026, 6, 9, 11, 0),
            team1="team_1",
            team1_initial="team_1",
        )
        db.session.commit()

        warnings = validate_match_warnings(url)
        assert not any(w["kind"] == "double_booked" for w in warnings)


@pytest.mark.unit
def test_recompute_is_idempotent_with_cross_field_sharing(app, test_db, tournament, seeded_teams):
    """Two cross-field shared-team chains recompute to stable scheduled times (no flip-flop)."""
    with app.app_context():
        url = tournament.url
        t0 = datetime(2026, 6, 9, 10, 0)
        # F1: S1 static @10:00 → A safe. F2: S2 static @10:30 → B safe. A and B share team_1.
        from app.utils.match_chain import update_match_previous_link

        s1 = _mk(
            url,
            "S1",
            "F1",
            t0,
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=t0,
            team1="team_1",
            team1_initial="team_1",
        )
        a = _mk(url, "A", "F1", None, schedule_type=ScheduleType.SAFE, team1="team_1", team1_initial="team_1")
        update_match_previous_link(a, s1.uuid, url, is_new=True)
        s2t = datetime(2026, 6, 9, 10, 30)
        s2 = _mk(
            url,
            "S2",
            "F2",
            s2t,
            schedule_type=ScheduleType.STATIC,
            nominal_start_time=s2t,
            team1="team_2",
            team1_initial="team_2",
        )
        b = _mk(url, "B", "F2", None, schedule_type=ScheduleType.SAFE, team1="team_1", team1_initial="team_1")
        update_match_previous_link(b, s2.uuid, url, is_new=True)
        db.session.commit()

        recompute_scheduled_and_nominal_times(url)
        for m in (a, b):
            db.session.refresh(m)
        first = (a.scheduled_start_time, b.scheduled_start_time)

        recompute_scheduled_and_nominal_times(url)
        for m in (a, b):
            db.session.refresh(m)
        second = (a.scheduled_start_time, b.scheduled_start_time)

        assert first == second  # stable across re-runs
