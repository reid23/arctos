"""Schema integrity regression tests for bootstrap migrations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db_migrations import run_bootstrap_migrations
from app.domain.enums import WinnerSide
from models import (
    Field,
    Match,
    MatchCameraStreamStart,
    MatchRefSlot,
    MatchRosterEntry,
    Point,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from tests.utils import make_registrable_config


def _seed_tournament(url: str = "schema-int") -> Tournament:
    cfg = make_registrable_config(
        team_registration_open=True,
        player_registration_open=True,
        registration_open=True,
    )
    t = Tournament(
        url=url,
        name="Schema Integrity Tournament",
        start_date=datetime.now(timezone.utc),
        registrable_config_id=cfg.id,
    )
    db.session.add(t)
    return t


def test_team_registration_rejects_duplicate_event_team(test_db):
    """Composite unique index prevents duplicate team registration rows."""
    t = _seed_tournament("schema-dup-team")
    team = Team(id="dup-team", name="Dup Team")
    db.session.add_all([t, team])
    db.session.commit()

    db.session.add(
        TeamRegistration(event=t.url, team=team.id, pseudonym="Dup Team", paid=False)
    )
    db.session.commit()

    db.session.add(
        TeamRegistration(event=t.url, team=team.id, pseudonym="Dup Team 2", paid=False)
    )
    try:
        db.session.commit()
        raise AssertionError(
            "Expected unique index violation for duplicate registration"
        )
    except IntegrityError:
        db.session.rollback()


def test_team_registration_requires_exactly_one_scope(test_db):
    """Trigger/check invariant rejects rows with both event and league scope."""
    team = Team(id="xor-team", name="Xor Team")
    db.session.add(team)
    db.session.commit()

    db.session.add(
        TeamRegistration(
            event="some-event",
            league_id="some-league",
            team=team.id,
            pseudonym="Bad Scope",
            paid=False,
        )
    )
    try:
        db.session.commit()
        raise AssertionError("Expected scope mutual exclusivity violation")
    except IntegrityError:
        db.session.rollback()


def test_point_winner_enforced_to_known_enum_values(test_db):
    """Point.winner accepts TEAM1/TEAM2 and rejects arbitrary strings."""
    t = _seed_tournament("schema-point-winner")
    team1 = Team(id="winner-team-1", name="Winner Team 1")
    team2 = Team(id="winner-team-2", name="Winner Team 2")
    db.session.add_all([t, team1, team2])
    db.session.commit()

    match = Match(
        event=t.url,
        name="Winner Match",
        team1=team1.id,
        team2=team2.id,
        field="Field A",
    )
    db.session.add(match)
    db.session.commit()

    good_point = Point(match=match.uuid, winner=WinnerSide.TEAM1)
    db.session.add(good_point)
    db.session.commit()

    bad_point = Point(match=match.uuid)
    bad_point.winner = "INVALID_SIDE"  # legacy-path assignment should fail at DB level
    db.session.add(bad_point)
    try:
        db.session.commit()
        raise AssertionError("Expected winner validation failure")
    except IntegrityError:
        db.session.rollback()


def test_field_id_backfill_migration_maps_legacy_match_field(test_db):
    """Re-running field-id migration backfills legacy name-only matches."""
    t = _seed_tournament("schema-field-backfill")
    db.session.add(t)
    db.session.commit()

    field = Field(event=t.url, name="Field A")
    db.session.add(field)
    db.session.commit()

    match = Match(
        event=t.url, name="Legacy Field Match", field=field.name, field_id=None
    )
    db.session.add(match)
    db.session.commit()
    assert match.field_id is None

    db.session.execute(
        text(
            "DELETE FROM schema_migrations WHERE id = :id",
        ),
        {"id": "20260423_005_match_camera_field_fk_transition"},
    )
    db.session.commit()

    run_bootstrap_migrations(db)
    db.session.refresh(match)
    assert match.field_id == field.id


def test_match_core_1nf_backfill_migrates_refs_rosters_and_stream_starts(test_db):
    """1NF migration backfills normalized match child tables from legacy columns."""
    t = _seed_tournament("schema-match-core-1nf")
    team1 = Team(id="core-team-1", name="Core Team 1")
    team2 = Team(id="core-team-2", name="Core Team 2")
    team3 = Team(id="core-team-3", name="Core Team 3")
    db.session.add_all([t, team1, team2, team3])
    db.session.commit()

    match = Match(
        event=t.url,
        name="Legacy 1NF Match",
        field="Field A",
        refs=f"{team1.id},,{team2.id}",
        refs_initial="tag::Pool A,,tag::Pool B",
        team1_players='["p1","p2","p1"]',
        team2_players='["p3"]',
        camera_stream_starts='{"0":"2026-04-23T10:00:00","x":"bad","1":"2026-04-23T10:05:00"}',
    )
    db.session.add(match)
    db.session.commit()

    db.session.execute(
        text("DELETE FROM schema_migrations WHERE id = :id"),
        {"id": "20260423_008_match_core_1nf_backfill"},
    )
    db.session.commit()

    run_bootstrap_migrations(db)

    ref_rows = (
        MatchRefSlot.query.filter_by(match_uuid=match.uuid)
        .order_by(MatchRefSlot.slot_index.asc())
        .all()
    )
    assert len(ref_rows) == 2
    assert [r.slot_index for r in ref_rows] == [0, 2]
    assert [r.resolved_team_id for r in ref_rows] == [team1.id, team2.id]
    assert [r.initial_token for r in ref_rows] == ["tag::Pool A", "tag::Pool B"]

    roster_rows = (
        MatchRosterEntry.query.filter_by(match_uuid=match.uuid)
        .order_by(MatchRosterEntry.side.asc(), MatchRosterEntry.slot_index.asc())
        .all()
    )
    assert {(r.side, r.player_id) for r in roster_rows} == {
        ("team1", "p1"),
        ("team1", "p2"),
        ("team2", "p3"),
    }

    stream_rows = (
        MatchCameraStreamStart.query.filter_by(match_uuid=match.uuid)
        .order_by(MatchCameraStreamStart.camera_index.asc())
        .all()
    )
    assert [(r.camera_index, r.stream_start_iso) for r in stream_rows] == [
        (0, "2026-04-23T10:00:00"),
        (1, "2026-04-23T10:05:00"),
    ]
