"""
Tests for multi-field break groups and STATBREAK.

Covers:
- same-name BREAK rows across fields start together in both solver passes
  (their dependency edges are unioned in build_match_graph);
- SAFE matches wait for breaks on other fields that require one of their teams
  (cross-field resource-conflict edges now see referee team requirements);
- STATBREAK: never moved by either pass, auto-completes once its window
  elapses, chained matches respect its end, editable while COMPLETED;
- break-group JSON endpoints (create/update/delete).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.services.dual_write import (
    get_match_ref_initials,
    get_match_ref_team_ids,
    set_match_referees_from_csv,
)
from app.utils.scheduling import recompute_scheduled_and_nominal_times
from models import TO, Match, Player, db
from tests.utils import login_as


def _aware_utc(d: datetime) -> datetime:
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _close(a: datetime, b: datetime, seconds: float = 2.0) -> bool:
    return abs((_aware_utc(a) - _aware_utc(b)).total_seconds()) < seconds


@pytest.fixture
def to_player(test_db, tournament):
    """A player who is a Tournament Organiser for the test tournament."""
    p = Player(id="test_to", name="Test TO", pw_hash="dummy_hash")
    p.set_password("testpass")
    db.session.add(p)
    db.session.flush()
    db.session.add(TO(user_id=p.id, user_type="player", event=tournament.url))
    db.session.commit()
    db.session.refresh(p)
    return p


def _mk(
    tournament_url,
    name,
    field,
    schedule_type,
    *,
    start=None,
    scheduled=None,
    length=60,
    status=MatchStatus.NOT_STARTED,
):
    m = Match(
        name=name,
        event=tournament_url,
        field=field,
        nominal_start_time=start,
        scheduled_start_time=scheduled,
        schedule_type=schedule_type,
        nominal_length=length,
        status=status,
    )
    db.session.add(m)
    db.session.flush()
    return m


class TestSameNameBreakSync:
    @pytest.mark.unit
    def test_same_name_breaks_start_together_both_passes(self, app, test_db, tournament):
        """Same-name breaks on two fields share the latest predecessor end in both
        timelines; each field's downstream match waits for shared start + its own
        break's length."""
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            # Field 1 anchor finishes on plan (base + 60); Field 2 anchor is
            # planned later (ends base + 90) and actually finishes at base + 120.
            a1 = _mk(url, "A1", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            a1.status = MatchStatus.COMPLETED
            a1.finalized_at = base + timedelta(minutes=60)
            b1 = _mk(
                url,
                "B1",
                "Field 2",
                ScheduleType.STATIC,
                start=base + timedelta(minutes=30),
                scheduled=base + timedelta(minutes=30),
                length=60,
            )
            b1.status = MatchStatus.COMPLETED
            b1.finalized_at = base + timedelta(minutes=120)

            lunch1 = _mk(url, "Lunch", "Field 1", ScheduleType.BREAK, length=30)
            lunch1.previous_match = a1.uuid
            a1.next_match = lunch1.uuid
            lunch2 = _mk(url, "Lunch", "Field 2", ScheduleType.BREAK, length=45)
            lunch2.previous_match = b1.uuid
            b1.next_match = lunch2.uuid

            after1 = _mk(url, "After1", "Field 1", ScheduleType.SAFE, length=60)
            after1.previous_match = lunch1.uuid
            lunch1.next_match = after1.uuid
            after2 = _mk(url, "After2", "Field 2", ScheduleType.SAFE, length=60)
            after2.previous_match = lunch2.uuid
            lunch2.next_match = after2.uuid
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)

            for m in (lunch1, lunch2, after1, after2):
                db.session.refresh(m)

            # Planned pass: shared start = latest planned predecessor end (base+90).
            assert _close(lunch1.scheduled_start_time, base + timedelta(minutes=90))
            assert _close(lunch2.scheduled_start_time, base + timedelta(minutes=90))
            assert _close(after1.scheduled_start_time, base + timedelta(minutes=120))
            assert _close(after2.scheduled_start_time, base + timedelta(minutes=135))

            # Live pass: shared start = latest actual predecessor end (base+120).
            assert _close(lunch1.nominal_start_time, base + timedelta(minutes=120))
            assert _close(lunch2.nominal_start_time, base + timedelta(minutes=120))
            assert _close(after1.nominal_start_time, base + timedelta(minutes=150))
            assert _close(after2.nominal_start_time, base + timedelta(minutes=165))

    @pytest.mark.unit
    def test_single_field_break_unchanged(self, app, test_db, tournament):
        """A lone break keeps today's behaviour: start = its own predecessor's end."""
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            a1 = _mk(url, "A1", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            a1.status = MatchStatus.COMPLETED
            a1.finalized_at = base + timedelta(minutes=75)
            brk = _mk(url, "Solo Break", "Field 1", ScheduleType.BREAK, length=30)
            brk.previous_match = a1.uuid
            a1.next_match = brk.uuid
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)
            db.session.refresh(brk)
            assert _close(brk.scheduled_start_time, base + timedelta(minutes=60))
            assert _close(brk.nominal_start_time, base + timedelta(minutes=75))


class TestBreakTeamRequirements:
    @pytest.mark.unit
    def test_safe_match_waits_for_break_requiring_its_team(self, app, test_db, tournament, seeded_teams):
        """A SAFE match with team X waits for a break on another field that
        requires X to attend (cross-field resource-conflict edge, live pass)."""
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)

            # Field 1: anchor runs 30 min late, then a break requiring team1.
            a1 = _mk(url, "F1 Anchor", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            a1.status = MatchStatus.COMPLETED
            a1.finalized_at = base + timedelta(minutes=90)
            brk = _mk(url, "Team Break", "Field 1", ScheduleType.BREAK, length=30)
            brk.previous_match = a1.uuid
            a1.next_match = brk.uuid
            set_match_referees_from_csv(brk, "team1", "team1")

            # Field 2: anchor ends at base+80; team1's match is planned after the
            # break's planned start (base+60), so the conflict edge applies.
            a2 = _mk(
                url,
                "F2 Anchor",
                "Field 2",
                ScheduleType.STATIC,
                start=base + timedelta(minutes=70),
                scheduled=base + timedelta(minutes=70),
                length=10,
            )
            a2.status = MatchStatus.COMPLETED
            a2.finalized_at = base + timedelta(minutes=80)
            m2 = _mk(url, "Team1 Game", "Field 2", ScheduleType.SAFE, length=60)
            m2.previous_match = a2.uuid
            a2.next_match = m2.uuid
            m2.team1 = "team1"
            m2.team1_initial = "team1"
            m2.team2 = "team2"
            m2.team2_initial = "team2"
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)
            db.session.refresh(m2)
            db.session.refresh(brk)

            # Break runs base+90 .. base+120 (anchor finished late). Without the
            # conflict edge m2 would start at base+80; it must wait for the break.
            assert _close(brk.nominal_start_time, base + timedelta(minutes=90))
            assert _aware_utc(m2.nominal_start_time) >= _aware_utc(base + timedelta(minutes=120))

    @pytest.mark.unit
    def test_unrelated_team_not_delayed_by_break(self, app, test_db, tournament, seeded_teams):
        """A SAFE match with no shared team ignores the other field's break."""
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            a1 = _mk(url, "F1 Anchor", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            a1.status = MatchStatus.COMPLETED
            a1.finalized_at = base + timedelta(minutes=90)
            brk = _mk(url, "Team Break", "Field 1", ScheduleType.BREAK, length=30)
            brk.previous_match = a1.uuid
            a1.next_match = brk.uuid
            set_match_referees_from_csv(brk, "team1", "team1")

            a2 = _mk(
                url,
                "F2 Anchor",
                "Field 2",
                ScheduleType.STATIC,
                start=base + timedelta(minutes=70),
                scheduled=base + timedelta(minutes=70),
                length=10,
            )
            a2.status = MatchStatus.COMPLETED
            a2.finalized_at = base + timedelta(minutes=80)
            m2 = _mk(url, "Other Game", "Field 2", ScheduleType.SAFE, length=60)
            m2.previous_match = a2.uuid
            a2.next_match = m2.uuid
            m2.team1 = "team2"
            m2.team1_initial = "team2"
            m2.team2 = "team3"
            m2.team2_initial = "team3"
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)
            db.session.refresh(m2)
            assert _close(m2.nominal_start_time, base + timedelta(minutes=80))


class TestStatBreak:
    @pytest.mark.unit
    def test_statbreak_never_moves_and_autocompletes(self, app, test_db, tournament):
        """A STATBREAK keeps its user-set times through both passes; once its
        window has elapsed it auto-completes; a chained match respects its end."""
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            past_start = base - timedelta(minutes=60)

            sb = _mk(
                url,
                "Dinner",
                "Field 1",
                ScheduleType.STATBREAK,
                start=past_start,
                scheduled=past_start,
                length=30,
            )
            after = _mk(url, "After Dinner", "Field 1", ScheduleType.SAFE, length=60)
            after.previous_match = sb.uuid
            sb.next_match = after.uuid
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)
            db.session.refresh(sb)
            db.session.refresh(after)

            assert _close(sb.nominal_start_time, past_start)
            assert _close(sb.scheduled_start_time, past_start)
            assert sb.status == MatchStatus.COMPLETED  # window elapsed
            # Chained match starts no earlier than the STATBREAK's end.
            assert _close(after.nominal_start_time, past_start + timedelta(minutes=30))
            assert _close(after.scheduled_start_time, past_start + timedelta(minutes=30))

    @pytest.mark.unit
    def test_future_statbreak_not_completed(self, app, test_db, tournament):
        url = tournament.url
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            future_start = base + timedelta(hours=2)
            sb = _mk(
                url,
                "Dinner",
                "Field 1",
                ScheduleType.STATBREAK,
                start=future_start,
                scheduled=future_start,
                length=30,
            )
            db.session.commit()

            recompute_scheduled_and_nominal_times(url)
            db.session.refresh(sb)
            assert sb.status == MatchStatus.NOT_STARTED
            assert _close(sb.nominal_start_time, future_start)
            assert _close(sb.scheduled_start_time, future_start)


@pytest.mark.integration
class TestBreakGroupEndpoints:
    def _login(self, app, client, tournament, to_player):
        with app.app_context():
            t = db.session.merge(tournament)
            p = db.session.merge(to_player)
            login_as(client, p)
        return t

    def test_create_break_group_rows_per_field_with_refs(self, app, client, tournament, to_player, seeded_teams):
        t = self._login(app, client, tournament, to_player)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={
                "name": "Lunch",
                "schedule_type": "BREAK",
                "length": 30,
                "fields": ["Field 1", "Field 2"],
                "teams": ["team1", "team2"],
            },
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["success"] is True

        rows = Match.query.filter_by(event=t.url, name="Lunch").all()
        assert {m.field for m in rows} == {"Field 1", "Field 2"}
        for m in rows:
            assert m.schedule_type == ScheduleType.BREAK
            assert m.nominal_length == 30
            assert m.team1 is None and m.team2 is None
            assert get_match_ref_team_ids(m) == ["team1", "team2"]

        # Creating the same group again collides per (name, event, field).
        resp2 = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Lunch", "schedule_type": "BREAK", "length": 30, "fields": ["Field 1"]},
        )
        assert resp2.status_code == 400

    def test_create_break_group_appends_at_chain_tail(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            a1 = _mk(t.url, "G1", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            db.session.commit()
            a1_uuid = a1.uuid

        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Pause", "schedule_type": "BREAK", "length": 15, "fields": ["Field 1"]},
        )
        assert resp.status_code == 200, resp.get_json()
        brk = Match.query.filter_by(event=t.url, name="Pause").one()
        assert brk.previous_match == a1_uuid
        # Appended after the tail, so it inherits the chain's planned end.
        assert brk.scheduled_start_time is not None

    def test_update_break_group_length_teams_and_fields(self, app, client, tournament, to_player, seeded_teams):
        t = self._login(app, client, tournament, to_player)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={
                "name": "Lunch",
                "schedule_type": "BREAK",
                "length": 30,
                "fields": ["Field 1"],
                "teams": ["team1"],
            },
        )
        assert resp.status_code == 200, resp.get_json()

        # Add Field 2, change length and teams in one PUT.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Lunch",
            json={"length": 45, "teams": ["team2"], "fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()
        rows = Match.query.filter_by(event=t.url, name="Lunch").all()
        assert {m.field for m in rows} == {"Field 1", "Field 2"}
        for m in rows:
            assert m.nominal_length == 45
            assert get_match_ref_team_ids(m) == ["team2"]
            assert get_match_ref_initials(m) == ["team2"]

        # Remove Field 1 again.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Lunch",
            json={"fields": ["Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()
        rows = Match.query.filter_by(event=t.url, name="Lunch").all()
        assert [m.field for m in rows] == ["Field 2"]

        # Removing every field is rejected (use DELETE instead).
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Lunch",
            json={"fields": []},
        )
        assert resp.status_code == 400

    def test_delete_break_group(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Lunch", "schedule_type": "BREAK", "length": 30, "fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()
        resp = client.delete(f"/_api/tournaments/{t.url}/break-groups/Lunch")
        assert resp.status_code == 200
        assert Match.query.filter_by(event=t.url, name="Lunch").count() == 0
        resp = client.delete(f"/_api/tournaments/{t.url}/break-groups/Lunch")
        assert resp.status_code == 404

    def test_statbreak_group_requires_start_time_and_sets_both_timelines(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Dinner", "schedule_type": "STATBREAK", "length": 45, "fields": ["Field 1"]},
        )
        assert resp.status_code == 400  # start_time required

        start = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={
                "name": "Dinner",
                "schedule_type": "STATBREAK",
                "length": 45,
                "fields": ["Field 1", "Field 2"],
                "start_time": start,
            },
        )
        assert resp.status_code == 200, resp.get_json()
        rows = Match.query.filter_by(event=t.url, name="Dinner").all()
        assert len(rows) == 2
        expected = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        for m in rows:
            assert m.schedule_type == ScheduleType.STATBREAK
            assert _close(m.nominal_start_time, expected)
            assert _close(m.scheduled_start_time, expected)

    def test_completed_statbreak_still_editable(self, app, client, tournament, to_player):
        """STATBREAKs auto-complete on a timer, so COMPLETED must not lock edits
        (group PUT and single-match PUT both work)."""
        t = self._login(app, client, tournament, to_player)
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={
                "name": "Dinner",
                "schedule_type": "STATBREAK",
                "length": 30,
                "fields": ["Field 1"],
                "start_time": start,
            },
        )
        assert resp.status_code == 200, resp.get_json()
        row = Match.query.filter_by(event=t.url, name="Dinner").one()
        assert row.status == MatchStatus.COMPLETED  # window already elapsed

        new_start = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Dinner",
            json={"length": 60, "start_time": new_start},
        )
        assert resp.status_code == 200, resp.get_json()
        row = Match.query.filter_by(event=t.url, name="Dinner").one()
        expected = datetime.fromisoformat(new_start.replace("Z", "+00:00")).replace(tzinfo=None)
        assert row.nominal_length == 60
        assert _close(row.nominal_start_time, expected)
        assert row.status == MatchStatus.NOT_STARTED  # window no longer elapsed

        # Single-match update endpoint is also exempt from the started-lock.
        resp = client.put(
            f"/_api/tournaments/{t.url}/matches/{row.uuid}",
            json={"length": 90},
        )
        assert resp.status_code == 200, resp.get_json()
        row = Match.query.filter_by(event=t.url, name="Dinner").one()
        assert row.nominal_length == 90

    def test_update_match_api_accepts_refs_on_break(self, app, client, tournament, to_player, seeded_teams):
        """The single-match PUT stores refs on BREAK rows instead of clearing them."""
        t = self._login(app, client, tournament, to_player)
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            a1 = _mk(t.url, "G1", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            brk = _mk(t.url, "Break A", "Field 1", ScheduleType.BREAK, length=20)
            brk.previous_match = a1.uuid
            a1.next_match = brk.uuid
            db.session.commit()
            brk_uuid = brk.uuid
            a1_uuid = a1.uuid

        resp = client.put(
            f"/_api/tournaments/{t.url}/matches/{brk_uuid}",
            json={
                "schedule_type": "BREAK",
                "length": 20,
                "previous_match_id": a1_uuid,
                "field": "Field 1",
                "refs": ["team1"],
            },
        )
        assert resp.status_code == 200, resp.get_json()
        brk = Match.query.filter_by(uuid=brk_uuid).one()
        assert get_match_ref_team_ids(brk) == ["team1"]
        assert brk.team1 is None and brk.team2 is None

    def test_break_to_statbreak_conversion(self, app, client, tournament, to_player):
        """BREAK→STATBREAK conversion via the single-match PUT is allowed."""
        t = self._login(app, client, tournament, to_player)
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            a1 = _mk(t.url, "G1", "Field 1", ScheduleType.STATIC, start=base, scheduled=base, length=60)
            brk = _mk(t.url, "Break A", "Field 1", ScheduleType.BREAK, length=20)
            brk.previous_match = a1.uuid
            a1.next_match = brk.uuid
            db.session.commit()
            brk_uuid = brk.uuid

        start = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.put(
            f"/_api/tournaments/{t.url}/matches/{brk_uuid}",
            json={"schedule_type": "STATBREAK", "length": 20, "start_time": start},
        )
        assert resp.status_code == 200, resp.get_json()
        brk = Match.query.filter_by(uuid=brk_uuid).one()
        assert brk.schedule_type == ScheduleType.STATBREAK
        expected = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        assert _close(brk.nominal_start_time, expected)
        assert _close(brk.scheduled_start_time, expected)


@pytest.mark.integration
class TestJoinGroupEndpoints:
    """JOIN groups through the structural-group ("break-groups") endpoints."""

    def _login(self, app, client, tournament, to_player):
        with app.app_context():
            t = db.session.merge(tournament)
            p = db.session.merge(to_player)
            login_as(client, p)
        return t

    def _mk_anchors(self, app, t, fields=("Field 1", "Field 2")):
        """One STATIC anchor per field; returns {field: uuid}."""
        with app.app_context():
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            uuids = {}
            for i, f in enumerate(fields):
                a = _mk(
                    t.url,
                    f"Anchor {f}",
                    f,
                    ScheduleType.STATIC,
                    start=base + timedelta(minutes=10 * i),
                    scheduled=base + timedelta(minutes=10 * i),
                    length=60,
                )
                uuids[f] = a.uuid
            db.session.commit()
        return uuids

    def test_create_join_group_rows_per_field_zero_length(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        anchors = self._mk_anchors(app, t)

        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()

        rows = Match.query.filter_by(event=t.url, name="Sync").all()
        assert {m.field for m in rows} == {"Field 1", "Field 2"}
        for m in rows:
            assert m.schedule_type == ScheduleType.JOIN
            assert m.nominal_length == 0
            assert m.team1 is None and m.team2 is None
            assert get_match_ref_team_ids(m) == []
            # Chain-tail default: appended after each field's anchor.
            assert m.previous_match == anchors[m.field]

        # Same-name-per-field uniqueness within the structural group.
        resp2 = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1"]},
        )
        assert resp2.status_code == 400

    def test_join_group_rejects_teams(self, app, client, tournament, to_player, seeded_teams):
        t = self._login(app, client, tournament, to_player)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={
                "name": "Sync",
                "schedule_type": "JOIN",
                "fields": ["Field 1"],
                "teams": ["team1"],
            },
        )
        assert resp.status_code == 400
        assert "team requirements" in resp.get_json()["error"]

        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1"]},
        )
        assert resp.status_code == 200, resp.get_json()

        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Sync",
            json={"teams": ["team1"]},
        )
        assert resp.status_code == 400
        assert "team requirements" in resp.get_json()["error"]

    def test_update_join_group_fields_with_chain_splicing(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        anchors = self._mk_anchors(app, t)

        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1"]},
        )
        assert resp.status_code == 200, resp.get_json()
        join1 = Match.query.filter_by(event=t.url, name="Sync", field="Field 1").one()
        assert Match.query.filter_by(uuid=anchors["Field 1"]).one().next_match == join1.uuid

        # Add Field 2: new zero-length row appended at that field's chain tail.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Sync",
            json={"fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()
        rows = Match.query.filter_by(event=t.url, name="Sync").all()
        assert {m.field for m in rows} == {"Field 1", "Field 2"}
        join2 = next(m for m in rows if m.field == "Field 2")
        assert join2.nominal_length == 0
        assert join2.previous_match == anchors["Field 2"]

        # A group PUT length is ignored for JOIN: rows stay zero-length.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Sync",
            json={"length": 30},
        )
        assert resp.status_code == 200, resp.get_json()
        assert {m.nominal_length for m in Match.query.filter_by(event=t.url, name="Sync").all()} == {0}

        # Remove Field 1: the row is deleted and its chain is spliced.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Sync",
            json={"fields": ["Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()
        rows = Match.query.filter_by(event=t.url, name="Sync").all()
        assert [m.field for m in rows] == ["Field 2"]
        assert Match.query.filter_by(uuid=anchors["Field 1"]).one().next_match is None

    def test_delete_join_group(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        anchors = self._mk_anchors(app, t)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()

        resp = client.delete(f"/_api/tournaments/{t.url}/break-groups/Sync")
        assert resp.status_code == 200
        assert Match.query.filter_by(event=t.url, name="Sync").count() == 0
        # Chains are spliced: anchors are tails again.
        for uuid in anchors.values():
            assert Match.query.filter_by(uuid=uuid).one().next_match is None
        resp = client.delete(f"/_api/tournaments/{t.url}/break-groups/Sync")
        assert resp.status_code == 404

    def test_join_break_conversion_rejected(self, app, client, tournament, to_player):
        t = self._login(app, client, tournament, to_player)
        self._mk_anchors(app, t)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1"]},
        )
        assert resp.status_code == 200, resp.get_json()
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Lunch", "schedule_type": "BREAK", "length": 30, "fields": ["Field 1"]},
        )
        assert resp.status_code == 200, resp.get_json()
        join_uuid = Match.query.filter_by(event=t.url, name="Sync").one().uuid
        break_uuid = Match.query.filter_by(event=t.url, name="Lunch").one().uuid

        # Single-match PUT: JOIN converts to/from nothing.
        resp = client.put(
            f"/_api/tournaments/{t.url}/matches/{join_uuid}",
            json={"schedule_type": "BREAK", "length": 30},
        )
        assert resp.status_code == 400
        resp = client.put(
            f"/_api/tournaments/{t.url}/matches/{break_uuid}",
            json={"schedule_type": "JOIN"},
        )
        assert resp.status_code == 400

        # Group PUT: any type change involving JOIN is rejected.
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Sync",
            json={"schedule_type": "BREAK"},
        )
        assert resp.status_code == 400
        resp = client.put(
            f"/_api/tournaments/{t.url}/break-groups/Lunch",
            json={"schedule_type": "JOIN"},
        )
        assert resp.status_code == 400

        assert Match.query.filter_by(uuid=join_uuid).one().schedule_type == ScheduleType.JOIN
        assert Match.query.filter_by(uuid=break_uuid).one().schedule_type == ScheduleType.BREAK

    def test_same_name_join_rows_merge_in_solver(self, app, client, tournament, to_player):
        """Rows created via the group endpoint still collapse into one JOIN node
        (component_uuids fan-out) whose dependencies span both fields."""
        from app.utils.MatchGraph import build_match_graph

        t = self._login(app, client, tournament, to_player)
        anchors = self._mk_anchors(app, t)
        resp = client.post(
            f"/_api/tournaments/{t.url}/break-groups",
            json={"name": "Sync", "schedule_type": "JOIN", "fields": ["Field 1", "Field 2"]},
        )
        assert resp.status_code == 200, resp.get_json()

        with app.app_context():
            rows = Match.query.filter_by(event=t.url, name="Sync").all()
            graph = build_match_graph(t.url)
            node = graph.nodes_by_key.get(("Sync", ""))
            assert node is not None
            assert node.component_uuids == {m.uuid for m in rows}
            dep_names = {dep.node.name for dep in node.dependencies}
            assert {"Anchor Field 1", "Anchor Field 2"} <= dep_names
