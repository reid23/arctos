"""Solver statuses must be re-earned on every solve.

Regression tests for stale statuses after schedule edits: reordering (or any
dependency change) used to leave TIME_FINALIZED / READY_TO_START / structural
COMPLETED in place because the live PROCEDURE only ever upgraded statuses.
"""

from datetime import datetime, timedelta

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.routes.tournaments import update_match_previous_link
from app.utils.scheduling import recompute_all_match_times, recompute_scheduled_and_nominal_times
from models import Match, db

pytestmark = pytest.mark.unit

BASE = datetime(2026, 8, 20, 9, 0)


def _mk(name, event, sched, status=MatchStatus.NOT_STARTED, start=None, length=30, prev=None):
    m = Match(
        name=name,
        event=event,
        field="Field 1",
        schedule_type=sched,
        nominal_length=length,
        status=status,
        skip_condition="false",
    )
    if start is not None:
        m.nominal_start_time = start
        m.scheduled_start_time = start
    db.session.add(m)
    db.session.flush()
    if prev is not None:
        m.previous_match = prev.uuid
        prev.next_match = m.uuid
    return m


def _get(name, event):
    return Match.query.filter_by(name=name, event=event).first()


class TestStatusDowngradeOnReorder:
    def test_reorder_downgrades_ready_to_start(self, app, test_db, tournament):
        """A(completed) -> B -> C; moving C in front of B strips B's earned status."""
        url = tournament.url
        with app.app_context():
            a = _mk("A", url, ScheduleType.STATIC, status=MatchStatus.COMPLETED, start=BASE)
            a.completed_time = BASE + timedelta(minutes=30)
            b = _mk("B", url, ScheduleType.SAFE, prev=a)
            c = _mk("C", url, ScheduleType.SAFE, prev=b)
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            # B earned READY_TO_START (dep A complete, no team slots to resolve).
            assert _get("B", url).status == MatchStatus.READY_TO_START
            assert _get("C", url).status == MatchStatus.NOT_STARTED

            # TO moves C in front of B.
            c = _get("C", url)
            update_match_previous_link(c, a.uuid, url)
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            # C is now the ready one; B's status was re-earned from its new
            # dependency (C, unfinished) and downgraded.
            assert _get("C", url).status == MatchStatus.READY_TO_START
            assert _get("B", url).status == MatchStatus.NOT_STARTED
            # And B's nominal moved after C's end.
            assert _get("B", url).nominal_start_time == _get("C", url).nominal_start_time + timedelta(minutes=30)

    def test_reorder_downgrades_safe_time_finalized(self, app, test_db, tournament):
        """SAFE TIME_FINALIZED (deps started) resets when a NOT_STARTED match is inserted before it."""
        url = tournament.url
        with app.app_context():
            a = _mk("A", url, ScheduleType.STATIC, status=MatchStatus.IN_PROGRESS, start=BASE)
            a.confirmed_start_time = BASE
            b = _mk("B", url, ScheduleType.SAFE, prev=a)
            c = _mk("C", url, ScheduleType.SAFE, prev=b)
            db.session.commit()
            recompute_all_match_times(url)

            # Dep A is in progress -> B's start time freezes.
            assert _get("B", url).status == MatchStatus.TIME_FINALIZED

            c = _get("C", url)
            update_match_previous_link(c, a.uuid, url)
            db.session.commit()
            recompute_all_match_times(url)

            # B now depends on C (not started): the freeze is no longer justified.
            assert _get("B", url).status == MatchStatus.NOT_STARTED
            # C inherits the freeze instead (its dep A is in progress).
            assert _get("C", url).status == MatchStatus.TIME_FINALIZED

    def test_reorder_uncompletes_break(self, app, test_db, tournament):
        """A solver-COMPLETED break re-opens when an unfinished match is inserted before it."""
        url = tournament.url
        with app.app_context():
            a = _mk("A", url, ScheduleType.STATIC, status=MatchStatus.COMPLETED, start=BASE)
            a.completed_time = BASE + timedelta(minutes=30)
            brk = _mk("Lunch", url, ScheduleType.BREAK, prev=a, length=60)
            db.session.commit()
            recompute_all_match_times(url)

            # Break's dep is complete -> solver marks it COMPLETED.
            assert _get("Lunch", url).status == MatchStatus.COMPLETED

            # Insert a new SAFE match between A and the break.
            d = _mk("D", url, ScheduleType.SAFE)
            db.session.commit()
            update_match_previous_link(d, a.uuid, url)
            db.session.commit()
            recompute_all_match_times(url)

            # The break is no longer "done": its dependency changed under it.
            assert _get("Lunch", url).status == MatchStatus.NOT_STARTED
            # And it moved after D.
            assert _get("Lunch", url).nominal_start_time == _get("D", url).nominal_start_time + timedelta(minutes=30)

    def test_tag_unassignment_downgrades_ready(self, app, test_db, tournament, team):
        """READY_TO_START falls back to TIME_FINALIZED when a team slot unresolves."""
        from models import Tag

        url = tournament.url
        with app.app_context():
            tag = Tag(event=url, name="Seed 1", team=team.id)
            db.session.add(tag)
            a = _mk("A", url, ScheduleType.STATIC, start=BASE)
            a.team1_initial = "tag::Seed 1"
            a.team1 = team.id
            db.session.commit()
            recompute_all_match_times(url)
            assert _get("A", url).status == MatchStatus.READY_TO_START

            # TO unassigns the tag (and the slot's resolved team).
            tag = Tag.query.filter_by(event=url, name="Seed 1").first()
            tag.team = None
            a = _get("A", url)
            a.team1 = None
            db.session.commit()
            recompute_all_match_times(url)

            # STATIC's floor is TIME_FINALIZED, not NOT_STARTED.
            assert _get("A", url).status == MatchStatus.TIME_FINALIZED

    def test_real_world_statuses_never_downgrade(self, app, test_db, tournament):
        """IN_PROGRESS / COMPLETED / SKIPPED matches are untouched by reordering."""
        url = tournament.url
        with app.app_context():
            a = _mk("A", url, ScheduleType.STATIC, status=MatchStatus.COMPLETED, start=BASE)
            a.completed_time = BASE + timedelta(minutes=30)
            b = _mk("B", url, ScheduleType.SAFE, status=MatchStatus.IN_PROGRESS, prev=a)
            b.confirmed_start_time = BASE + timedelta(minutes=30)
            c = _mk("C", url, ScheduleType.SAFE, prev=b)
            db.session.commit()
            recompute_all_match_times(url)

            c = _get("C", url)
            update_match_previous_link(c, a.uuid, url)
            db.session.commit()
            recompute_all_match_times(url)

            assert _get("A", url).status == MatchStatus.COMPLETED
            assert _get("B", url).status == MatchStatus.IN_PROGRESS
