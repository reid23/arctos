"""
Tests for dynamic match scheduling (MatchGraph-based scheduler).

These tests validate recompute_all_match_times() / run_scheduling()
with the current Match/Tournament schema:
- Match.schedule_type (STATIC/SAFE/FAST/BREAK/JOIN)
- Match.previous_match for dependency chains
- Match.finalized_at for completion time used by the graph

The scheduler:
- Builds a dependency graph from previous_match and team1_initial/team2_initial refs
- Sets nominal_start_time from get_deps_latest_end_time() (uses finalized_at for end time)
- Respects STATIC matches as boundaries (not pulled forward)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.enums import MatchStatus
from app.utils.scheduling import (
    recompute_all_match_times,
    recompute_scheduled_and_nominal_times,
)
from models import Match, db


def _aware_utc(d: datetime) -> datetime:
    """Normalize possibly-naive datetimes to aware UTC for comparisons in tests."""
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _link_chain(matches: list) -> None:
    """Set previous_match so matches form a chain in order."""
    for i in range(1, len(matches)):
        matches[i].previous_match = matches[i - 1].uuid
        matches[i - 1].next_match = matches[i].uuid


class TestDynamicScheduling:
    @pytest.mark.unit
    def test_basic_dynamic_scheduling(self, app, test_db, tournament):
        """Completing a match updates subsequent dynamic matches' nominal times from dependency end."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)
            field = "Field 1"

            match1 = Match(
                name="Match 1",
                event=tournament_url,
                field=field,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            match2 = Match(
                name="Match 2",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=1)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            match3 = Match(
                name="Match 3",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=2)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            db.session.add_all([match1, match2, match3])
            db.session.flush()
            _link_chain([match1, match2, match3])

            finalize_time = base_time + timedelta(minutes=55)
            match1.finalized_at = finalize_time.replace(tzinfo=None)
            db.session.commit()

            recompute_all_match_times(tournament_url)

            db.session.refresh(match2)
            db.session.refresh(match3)

            assert abs((_aware_utc(match2.nominal_start_time) - finalize_time).total_seconds()) < 2
            expected_match3 = finalize_time + timedelta(minutes=match2.nominal_length or 60)
            assert abs((_aware_utc(match3.nominal_start_time) - expected_match3).total_seconds()) < 2

    @pytest.mark.unit
    def test_static_match_boundary(self, app, test_db, tournament):
        """Dynamic scheduling does not change STATIC match times (boundary)."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)
            field = "Field 1"

            match1 = Match(
                name="Match 1",
                event=tournament_url,
                field=field,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            match2 = Match(
                name="Match 2",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=2)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            boundary_static = Match(
                name="Match 3 Static",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=4)).replace(tzinfo=None),
                schedule_type="STATIC",
                nominal_length=60,
                status=MatchStatus.TIME_FINALIZED,
            )
            after_boundary = Match(
                name="Match 4",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=6)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            db.session.add_all([match1, match2, boundary_static, after_boundary])
            db.session.flush()
            _link_chain([match1, match2, boundary_static, after_boundary])

            finalize_time = base_time + timedelta(minutes=50)
            match1.finalized_at = finalize_time.replace(tzinfo=None)
            db.session.commit()

            recompute_all_match_times(tournament_url)

            db.session.refresh(match2)
            db.session.refresh(boundary_static)
            db.session.refresh(after_boundary)

            assert abs((_aware_utc(match2.nominal_start_time) - finalize_time).total_seconds()) < 2
            assert (
                abs((_aware_utc(boundary_static.nominal_start_time) - (base_time + timedelta(hours=4))).total_seconds())
                < 2
            )
            assert (
                abs((_aware_utc(after_boundary.nominal_start_time) - (base_time + timedelta(hours=5))).total_seconds())
                < 2
            )

    @pytest.mark.unit
    def test_dependency_constraint_same_field(self, app, test_db, tournament):
        """A match cannot be scheduled earlier than its dependency's completion time."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)
            field = "Field 1"

            completed = Match(
                name="Trigger",
                event=tournament_url,
                field=field,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            dep = Match(
                name="Dep",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=1, minutes=30)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            next_match = Match(
                name="Next",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=1)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            constrained = Match(
                name="Constrained",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=2)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                team1_initial="Dep::winner",
                status="NOT_STARTED",
            )
            db.session.add_all([completed, dep, next_match, constrained])
            db.session.flush()
            next_match.previous_match = completed.uuid
            constrained.previous_match = next_match.uuid
            # Constrained also depends on Dep via team1_initial

            dep_late = base_time + timedelta(hours=3)
            completed.finalized_at = base_time + timedelta(minutes=50)
            dep.finalized_at = dep_late.replace(tzinfo=None)
            db.session.commit()

            recompute_all_match_times(tournament_url)

            db.session.refresh(next_match)
            db.session.refresh(constrained)

            assert (
                abs((_aware_utc(next_match.nominal_start_time) - (base_time + timedelta(minutes=50))).total_seconds())
                < 2
            )
            assert _aware_utc(constrained.nominal_start_time) >= _aware_utc(dep_late)

    @pytest.mark.unit
    def test_dependency_on_different_field_does_not_constrain(self, app, test_db, tournament):
        """Completing a match on one field does not change times on another field."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)

            completed = Match(
                name="Field1 Trigger",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            other_field_dep = Match(
                name="Dep",
                event=tournament_url,
                field="Field 2",
                nominal_start_time=(base_time + timedelta(hours=1)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            other_field_match = Match(
                name="Field2 Match",
                event=tournament_url,
                field="Field 2",
                nominal_start_time=(base_time + timedelta(hours=2)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                team1_initial="Dep::winner",
                status="NOT_STARTED",
            )
            db.session.add_all([completed, other_field_dep, other_field_match])
            db.session.flush()
            other_field_match.previous_match = other_field_dep.uuid

            completed.finalized_at = base_time + timedelta(minutes=50)
            db.session.commit()

            original = _aware_utc(other_field_match.nominal_start_time)
            recompute_all_match_times(tournament_url)

            db.session.refresh(other_field_match)
            assert _aware_utc(other_field_match.nominal_start_time) == original

    @pytest.mark.unit
    def test_multiple_dependencies_latest_wins(self, app, test_db, tournament):
        """When a match has multiple completed dependencies, latest completion time wins."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)
            field = "Field 1"

            trigger = Match(
                name="Trigger",
                event=tournament_url,
                field=field,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            dep1 = Match(
                name="Dep 1",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=1)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            dep2 = Match(
                name="Dep 2",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=1, minutes=10)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            next_match = Match(
                name="Next",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=2)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            target = Match(
                name="Target",
                event=tournament_url,
                field=field,
                nominal_start_time=(base_time + timedelta(hours=3)).replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                team1_initial="Dep 1::winner",
                team2_initial="Dep 2::winner",
                status="NOT_STARTED",
            )
            db.session.add_all([trigger, dep1, dep2, next_match, target])
            db.session.flush()
            next_match.previous_match = trigger.uuid
            target.previous_match = next_match.uuid

            trigger.finalized_at = base_time + timedelta(minutes=50)
            dep1.finalized_at = (base_time + timedelta(hours=2)).replace(tzinfo=None)
            dep2.finalized_at = (base_time + timedelta(hours=4)).replace(tzinfo=None)
            db.session.commit()

            recompute_all_match_times(tournament_url)

            db.session.refresh(target)
            assert _aware_utc(target.nominal_start_time) >= _aware_utc(dep2.finalized_at)

    @pytest.mark.unit
    def test_no_subsequent_matches(self, app, test_db, tournament):
        """Completing the last match on a field should not error."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)
            field = "Field 1"

            match1 = Match(
                name="Only Match",
                event=tournament_url,
                field=field,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            db.session.add(match1)
            db.session.commit()

            recompute_all_match_times(tournament_url)

    @pytest.mark.unit
    def test_match_without_field(self, app, test_db, tournament):
        """Matches without a field can be processed without errors."""
        tournament_url = tournament.url
        with app.app_context():
            base_time = datetime.now(timezone.utc)

            match1 = Match(
                name="No Field",
                event=tournament_url,
                field=None,
                nominal_start_time=base_time.replace(tzinfo=None),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            db.session.add(match1)
            db.session.commit()

            recompute_all_match_times(tournament_url)


class TestScheduledVsNominalTimeline:
    """scheduled_start_time is the planned timeline; nominal_start_time tracks reality."""

    @pytest.mark.unit
    def test_scheduled_tracks_plan_nominal_tracks_reality(self, app, test_db, tournament):
        """scheduled_start_time follows the on-plan timeline (anchor + cumulative
        nominal_length); nominal_start_time follows actual finish times."""
        tournament_url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            anchor = Match(
                name="Anchor",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base,
                scheduled_start_time=base,
                schedule_type="STATIC",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            # Anchor ran 30 min long (finished at base + 90, not the planned base + 60).
            anchor.finalized_at = base + timedelta(minutes=90)
            m2 = Match(
                name="M2",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=60),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            m3 = Match(
                name="M3",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=120),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            db.session.add_all([anchor, m2, m3])
            db.session.flush()
            _link_chain([anchor, m2, m3])
            db.session.commit()

            recompute_scheduled_and_nominal_times(tournament_url)

            db.session.refresh(anchor)
            db.session.refresh(m2)
            db.session.refresh(m3)

            # Planned timeline: anchor unchanged, then +60 per match, ignoring reality.
            assert _aware_utc(anchor.scheduled_start_time) == _aware_utc(base)
            assert _aware_utc(m2.scheduled_start_time) == _aware_utc(base + timedelta(minutes=60))
            assert _aware_utc(m3.scheduled_start_time) == _aware_utc(base + timedelta(minutes=120))

            # Dynamic timeline: m2 starts when anchor actually finished (base + 90),
            # m3 follows m2's nominal end (base + 90 + 60).
            assert (
                abs((_aware_utc(m2.nominal_start_time) - _aware_utc(base + timedelta(minutes=90))).total_seconds()) < 2
            )
            assert (
                abs((_aware_utc(m3.nominal_start_time) - _aware_utc(base + timedelta(minutes=150))).total_seconds()) < 2
            )

    @pytest.mark.unit
    def test_normal_pass_leaves_scheduled_untouched(self, app, test_db, tournament):
        """recompute_all_match_times (normal pass) updates nominal but never scheduled."""
        tournament_url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            anchor = Match(
                name="Anchor",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base,
                scheduled_start_time=base,
                schedule_type="STATIC",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            anchor.finalized_at = base + timedelta(minutes=90)
            m2 = Match(
                name="M2",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=60),
                scheduled_start_time=base + timedelta(minutes=60),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            db.session.add_all([anchor, m2])
            db.session.flush()
            _link_chain([anchor, m2])
            db.session.commit()

            recompute_all_match_times(tournament_url)

            db.session.refresh(m2)
            # scheduled untouched by the normal pass...
            assert _aware_utc(m2.scheduled_start_time) == _aware_utc(base + timedelta(minutes=60))
            # ...while nominal tracks the late finish.
            assert (
                abs((_aware_utc(m2.nominal_start_time) - _aware_utc(base + timedelta(minutes=90))).total_seconds()) < 2
            )

    @pytest.mark.unit
    def test_scheduled_pass_ignores_skip(self, app, test_db, tournament):
        """A SKIPPED match still contributes its full nominal_length to the planned timeline."""
        tournament_url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            anchor = Match(
                name="Anchor",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base,
                scheduled_start_time=base,
                schedule_type="STATIC",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            anchor.finalized_at = base + timedelta(minutes=60)
            skipped = Match(
                name="Skipped",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=60),
                schedule_type="SAFE",
                nominal_length=45,
                status=MatchStatus.SKIPPED,
            )
            after = Match(
                name="After",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=120),
                schedule_type="SAFE",
                nominal_length=60,
                status="NOT_STARTED",
            )
            db.session.add_all([anchor, skipped, after])
            db.session.flush()
            _link_chain([anchor, skipped, after])
            db.session.commit()

            recompute_scheduled_and_nominal_times(tournament_url)

            db.session.refresh(skipped)
            db.session.refresh(after)

            # Planned timeline treats the skipped match as if it ran for its full 45 min.
            assert _aware_utc(skipped.scheduled_start_time) == _aware_utc(base + timedelta(minutes=60))
            assert _aware_utc(after.scheduled_start_time) == _aware_utc(base + timedelta(minutes=105))


class TestPlanAnchorWritePaths:
    """Write paths that intentionally shift the *plan* must touch scheduled_start_time."""

    @pytest.mark.unit
    def test_push_back_moves_scheduled_and_downstream_plan(self, app, test_db, tournament):
        """STATIC push-back must move the plan anchor, then re-solve dynamic planned times."""
        from app.domain.enums import ScheduleType

        tournament_url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            anchor = Match(
                name="Anchor",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base,
                scheduled_start_time=base,
                schedule_type=ScheduleType.STATIC,
                nominal_length=60,
                status=MatchStatus.NOT_STARTED,
            )
            m2 = Match(
                name="M2",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=60),
                scheduled_start_time=base + timedelta(minutes=60),
                schedule_type=ScheduleType.SAFE,
                nominal_length=60,
                status=MatchStatus.NOT_STARTED,
            )
            db.session.add_all([anchor, m2])
            db.session.flush()
            _link_chain([anchor, m2])
            db.session.commit()

            # Mirror push_back_matches_api: shift STATIC plan anchors, then both passes.
            delta = timedelta(minutes=30)
            anchor.scheduled_start_time = anchor.scheduled_start_time + delta
            anchor.nominal_start_time = anchor.nominal_start_time + delta
            db.session.commit()
            recompute_scheduled_and_nominal_times(tournament_url)

            db.session.refresh(anchor)
            db.session.refresh(m2)

            assert _aware_utc(anchor.scheduled_start_time) == _aware_utc(base + delta)
            assert _aware_utc(anchor.nominal_start_time) == _aware_utc(base + delta)
            # Downstream plan follows the shifted STATIC anchor.
            assert _aware_utc(m2.scheduled_start_time) == _aware_utc(base + delta + timedelta(minutes=60))
            assert _aware_utc(m2.nominal_start_time) == _aware_utc(base + delta + timedelta(minutes=60))

    @pytest.mark.unit
    def test_live_pass_after_late_finish_does_not_move_plan(self, app, test_db, tournament):
        """Regression: recompute_all_match_times after a late finish must leave scheduled alone."""
        tournament_url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            anchor = Match(
                name="Anchor",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base,
                scheduled_start_time=base,
                schedule_type="STATIC",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
            )
            # Finished 45 min late relative to plan end (plan end = base+60).
            anchor.confirmed_start_time = base
            anchor.completed_time = base + timedelta(minutes=105)
            anchor.finalized_at = base + timedelta(minutes=105)
            m2 = Match(
                name="M2",
                event=tournament_url,
                field="Field 1",
                nominal_start_time=base + timedelta(minutes=60),
                scheduled_start_time=base + timedelta(minutes=60),
                schedule_type="SAFE",
                nominal_length=60,
                status=MatchStatus.NOT_STARTED,
            )
            db.session.add_all([anchor, m2])
            db.session.flush()
            _link_chain([anchor, m2])
            db.session.commit()

            planned_m2 = m2.scheduled_start_time
            recompute_all_match_times(tournament_url)
            db.session.refresh(m2)

            assert _aware_utc(m2.scheduled_start_time) == _aware_utc(planned_m2)
            assert (
                abs((_aware_utc(m2.nominal_start_time) - _aware_utc(base + timedelta(minutes=105))).total_seconds()) < 2
            )
