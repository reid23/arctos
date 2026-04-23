"""Unit tests for match start eligibility (can_start and block_reasons)."""

import pytest

from app.domain.enums import MatchStatus, ScheduleType
from app.services.match_start_eligibility import get_can_start_and_reasons
from models import Match, MatchRefSlot, db


@pytest.mark.unit
def test_can_start_true_when_ref_ready_no_conflict(
    app, test_db, tournament, head_ref_player
):
    """When user is ref, match is READY_TO_START, and no other match on field, can_start is True."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        m = Match(
            name="Ready",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()

        can_start, reasons, _ = get_can_start_and_reasons(t.url, m, ref)
        assert can_start is True
        assert reasons == []


@pytest.mark.unit
def test_can_start_false_field_busy(app, test_db, tournament, head_ref_player):
    """When another match is IN_PROGRESS on same field, can_start is False with field-busy reason."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        other = Match(
            name="Other",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.IN_PROGRESS,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(other)
        db.session.flush()
        m = Match(
            name="Want Start",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()

        can_start, reasons, _ = get_can_start_and_reasons(t.url, m, ref)
        assert can_start is False
        assert any("in progress" in r.lower() and "field" in r.lower() for r in reasons)


@pytest.mark.unit
def test_can_start_false_user_not_ref(app, test_db, tournament, player):
    """When user is not in allowed refs list, can_start is False with perms reason."""
    with app.app_context():
        t = db.session.merge(tournament)
        p = db.session.merge(player)
        m = Match(
            name="Ready",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()

        can_start, reasons, _ = get_can_start_and_reasons(t.url, m, p)
        assert can_start is False
        assert any(
            "not allowed" in r.lower()
            or "not registered" in r.lower()
            or "logged in" in r.lower()
            for r in reasons
        )


@pytest.mark.unit
def test_can_start_false_status_not_ready(app, test_db, tournament, head_ref_player):
    """When match status is NOT_STARTED, can_start is False."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        m = Match(
            name="Not Ready",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.NOT_STARTED,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()

        can_start, reasons, _ = get_can_start_and_reasons(t.url, m, ref)
        assert can_start is False
        assert len(reasons) >= 1


@pytest.mark.unit
def test_can_start_completed_returns_false_no_reasons(
    app, test_db, tournament, head_ref_player
):
    """When match is COMPLETED, can_start is False and reasons are empty (match is over)."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        m = Match(
            name="Done",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.COMPLETED,
            team1="team1",
            team2="team2",
            nominal_length=60,
        )
        db.session.add(m)
        db.session.commit()

        can_start, reasons, why_sections = get_can_start_and_reasons(t.url, m, ref)
        assert can_start is False
        assert reasons == []
        assert why_sections.match_ready["status"] == str(MatchStatus.COMPLETED)


@pytest.mark.unit
def test_refs_read_from_normalized_slots_for_block_reasons(
    app, test_db, tournament, head_ref_player
):
    """Eligibility reason builder should use normalized refs when legacy columns are empty."""
    with app.app_context():
        t = db.session.merge(tournament)
        ref = db.session.merge(head_ref_player)
        m = Match(
            name="Needs Ref Resolution",
            event=t.url,
            field="Field 1",
            schedule_type=ScheduleType.STATIC,
            set_type="SETS",
            status=MatchStatus.READY_TO_START,
            team1="team1",
            team2="team2",
            nominal_length=60,
            refs=None,
            refs_initial=None,
        )
        db.session.add(m)
        db.session.flush()
        db.session.add(
            MatchRefSlot(
                match_uuid=m.uuid,
                slot_index=0,
                resolved_team_id=None,
                initial_token="tag::Pool A",
            )
        )
        db.session.commit()

        can_start, reasons, _ = get_can_start_and_reasons(t.url, m, ref)
        assert can_start is False
        assert any("ref slot 1" in r.lower() for r in reasons)
