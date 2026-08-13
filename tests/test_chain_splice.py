"""Regression tests for per-field chain splicing (previous_match/next_match).

Covers the "half-linked chain" bug: a successor points at its predecessor via
``previous_match`` while the predecessor's ``next_match`` is unset. This state
arises around STATIC matches (deliberately detached on edit) and TOML imports
(which only populate ``previous_match``). Inserting a match after the
predecessor used to leave the old successor still pointing at it, so both
matches shared a dependency and solved to the same start time.
"""

from datetime import datetime

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.routes.tournaments import update_match_previous_link
from app.utils.scheduling import recompute_scheduled_and_nominal_times
from models import Match, db

pytestmark = pytest.mark.unit


def _mk(name, event, sched, start=None, length=30):
    m = Match(
        name=name,
        event=event,
        field="Field 1",
        schedule_type=sched,
        nominal_length=length,
        status=MatchStatus.NOT_STARTED,
        skip_condition="false",
    )
    if start is not None:
        m.nominal_start_time = start
        m.scheduled_start_time = start
    db.session.add(m)
    db.session.flush()
    return m


class TestHalfLinkedChainSplice:
    def test_insert_after_half_linked_static_repoints_successor(self, app, test_db, tournament):
        """Moving A in front of B (both after a detached STATIC) must re-point B at A."""
        url = tournament.url
        with app.app_context():
            s = _mk("S", url, ScheduleType.STATIC, start=datetime(2026, 8, 20, 9, 0))
            b = _mk("B", url, ScheduleType.SAFE)
            a = _mk("A", url, ScheduleType.SAFE)
            # Half-linked: B points back at S, but S.next_match is unset
            # (exactly what a STATIC edit's detach leaves behind).
            b.previous_match = s.uuid
            a.previous_match = b.uuid
            b.next_match = a.uuid
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            # Drag-move: A in front of B == insert A after S.
            a = Match.query.filter_by(name="A", event=url).first()
            update_match_previous_link(a, s.uuid, url)
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            s = Match.query.filter_by(name="S", event=url).first()
            a = Match.query.filter_by(name="A", event=url).first()
            b = Match.query.filter_by(name="B", event=url).first()

            # Chain is S -> A -> B.
            assert s.next_match == a.uuid
            assert a.previous_match == s.uuid
            assert a.next_match == b.uuid
            assert b.previous_match == a.uuid
            assert b.next_match is None

            # And the solved times are strictly ordered, not identical.
            assert a.nominal_start_time == datetime(2026, 8, 20, 9, 30)
            assert b.nominal_start_time == datetime(2026, 8, 20, 10, 0)
            assert a.scheduled_start_time < b.scheduled_start_time

    def test_fully_linked_reorder_still_works(self, app, test_db, tournament):
        """The normal fully-linked reorder path is unchanged by the fallback."""
        url = tournament.url
        with app.app_context():
            s = _mk("S", url, ScheduleType.STATIC, start=datetime(2026, 8, 20, 9, 0))
            b = _mk("B", url, ScheduleType.SAFE)
            c = _mk("C", url, ScheduleType.SAFE)
            a = _mk("A", url, ScheduleType.SAFE)
            for prev, nxt in ((s, b), (b, c), (c, a)):
                nxt.previous_match = prev.uuid
                prev.next_match = nxt.uuid
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            a = Match.query.filter_by(name="A", event=url).first()
            update_match_previous_link(a, s.uuid, url)
            db.session.commit()
            recompute_scheduled_and_nominal_times(url)

            names_in_chain = []
            cur = Match.query.filter_by(name="S", event=url).first()
            while cur is not None:
                names_in_chain.append(cur.name)
                cur = Match.query.filter_by(uuid=cur.next_match, event=url).first() if cur.next_match else None
            assert names_in_chain == ["S", "A", "B", "C"]

            starts = {m.name: m.nominal_start_time for m in Match.query.filter_by(event=url).all()}
            assert len(set(starts.values())) == 4, f"duplicate start times: {starts}"

    def test_ambiguous_back_pointers_do_not_guess(self, app, test_db, tournament):
        """Two matches pointing back at the same predecessor: no arbitrary re-pointing."""
        url = tournament.url
        with app.app_context():
            s = _mk("S", url, ScheduleType.STATIC, start=datetime(2026, 8, 20, 9, 0))
            b = _mk("B", url, ScheduleType.SAFE)
            c = _mk("C", url, ScheduleType.SAFE)
            a = _mk("A", url, ScheduleType.SAFE)
            # Already-degenerate: B and C both claim S as previous.
            b.previous_match = s.uuid
            c.previous_match = s.uuid
            a.previous_match = c.uuid
            c.next_match = a.uuid
            db.session.commit()

            a = Match.query.filter_by(name="A", event=url).first()
            update_match_previous_link(a, s.uuid, url)
            db.session.commit()

            a = Match.query.filter_by(name="A", event=url).first()
            b = Match.query.filter_by(name="B", event=url).first()
            c = Match.query.filter_by(name="C", event=url).first()
            # A is spliced in after S with no successor adopted (ambiguous),
            # and the pre-existing degenerate pointers are left alone.
            assert a.previous_match == s.uuid
            assert a.next_match is None
            assert b.previous_match == s.uuid
            assert c.previous_match == s.uuid
