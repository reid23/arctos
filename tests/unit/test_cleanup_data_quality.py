"""Tests for ``scripts/cleanup_data_quality``.

Each test seeds a specific kind of dirty data into the in-memory test DB,
then exercises the cleanup module's detection function (always),
``--apply=False`` dry-run (counts but no write), and ``--apply=True`` (counts
plus write). Idempotency is verified by running the apply path twice and
asserting the second run is a no-op.

The cleanup module is loaded directly from ``scripts/`` so tests catch
regressions in the script itself rather than re-implementing its logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import cleanup_data_quality as cleanup  # noqa: E402

from app.domain.enums import TeamRegistrationStatus  # noqa: E402
from models import (  # noqa: E402
    Player,
    Team,
    TeamRegistration,
    db,
)


# ---------------------------------------------------------------------------
# normalize-emails
# ---------------------------------------------------------------------------


def _drop_unique_indexes_for_dirty_seeding():
    """Drop the model-declared UNIQUE indexes so dirty data can be inserted.

    The ``test_db`` fixture creates a fresh DB from the current models,
    which already include the dedupe indexes. Tests that need to
    seed pre-existing dirty data drop those indexes first.
    """
    for ix in (
        "uq_team_registrations_team_event",
        "uq_team_registrations_team_league",
        "uq_player_registrations_player_event",
        "uq_player_registrations_player_league",
        "unique_with_field",
        "unique_without_field",
        "uq_tags_name_event",
        "uq_fields_name_event",
        "uq_headrefs_player_event",
    ):
        db.session.execute(sa.text(f"DROP INDEX IF EXISTS {ix}"))
    db.session.commit()


@pytest.mark.unit
def test_find_blank_emails_counts_empty_strings(test_db):
    """Empty-string emails are reported per table; NULL emails are not."""
    _drop_unique_indexes_for_dirty_seeding()
    db.session.add_all(
        [
            Player(id="p1", name="P1", pw_hash="h", email=""),
            Player(id="p2", name="P2", pw_hash="h", email="real@x.com"),
            Player(id="p3", name="P3", pw_hash="h", email=None),
            Team(id="t1", name="T1", pw_hash="h", email=""),
            Team(id="t2", name="T2", pw_hash="h", email=""),
        ]
    )
    db.session.commit()

    counts = cleanup.find_blank_emails(db.engine)
    assert counts == {"players": 1, "teams": 2}


@pytest.mark.unit
def test_normalize_emails_dry_run_does_not_write(test_db):
    """Dry run reports counts but leaves the database unchanged."""
    db.session.add(Player(id="p1", name="P1", pw_hash="h", email=""))
    db.session.commit()

    counts = cleanup.normalize_blank_emails(db.engine, apply=False)
    assert counts == {"players": 1, "teams": 0}
    # Verify nothing actually changed.
    assert Player.query.get("p1").email == ""


@pytest.mark.unit
def test_normalize_emails_apply_converts_to_null(test_db):
    """Apply mode actually performs the UPDATE."""
    db.session.add_all(
        [
            Player(id="p1", name="P1", pw_hash="h", email=""),
            Team(id="t1", name="T1", pw_hash="h", email=""),
        ]
    )
    db.session.commit()

    cleanup.normalize_blank_emails(db.engine, apply=True)
    db.session.expire_all()

    assert Player.query.get("p1").email is None
    assert Team.query.get("t1").email is None


@pytest.mark.unit
def test_normalize_emails_is_idempotent(test_db):
    """Re-running on a clean DB is a no-op."""
    db.session.add(Player(id="p1", name="P1", pw_hash="h", email=""))
    db.session.commit()

    cleanup.normalize_blank_emails(db.engine, apply=True)
    db.session.expire_all()
    second = cleanup.normalize_blank_emails(db.engine, apply=True)
    assert second == {"players": 0, "teams": 0}


@pytest.mark.unit
def test_normalize_emails_does_not_touch_real_addresses(test_db):
    """Real emails are not modified — only the empty string is."""
    db.session.add(Player(id="p1", name="P1", pw_hash="h", email="real@x.com"))
    db.session.commit()

    cleanup.normalize_blank_emails(db.engine, apply=True)
    db.session.expire_all()

    assert Player.query.get("p1").email == "real@x.com"


# ---------------------------------------------------------------------------
# delete-orphans
# ---------------------------------------------------------------------------


def _seed_orphan_team_registration(test_db, tournament) -> int:
    """Insert a TeamRegistration whose `team` references no real team.

    Returns the row's primary-key id. Inserted with FK enforcement off so
    that the row exists but is invalid — exactly the state the cleanup
    script is designed to surface and fix.
    """
    db.session.commit()  # flush anything pending so the raw SQL sees it
    db.session.execute(sa.text("PRAGMA foreign_keys = OFF"))
    db.session.execute(
        sa.text("INSERT INTO team_registrations (event, team, pseudonym, status) VALUES (:event, :team, :ps, :status)"),
        {"event": tournament.url, "team": "ghost_team", "ps": "Ghost", "status": "CANCELLED"},
    )
    row_id = db.session.execute(sa.text("SELECT MAX(id) FROM team_registrations WHERE team = 'ghost_team'")).scalar()
    db.session.execute(sa.text("PRAGMA foreign_keys = ON"))
    db.session.commit()
    return int(row_id)


@pytest.mark.unit
def test_find_orphan_rows_detects_missing_parent(test_db, tournament):
    """An orphan row (FK target deleted) is reported."""
    row_id = _seed_orphan_team_registration(test_db, tournament)

    fk = next(f for f in cleanup.ORPHAN_FK_CHECKS if (f.table, f.column) == ("team_registrations", "team"))
    rows = cleanup.find_orphan_rows(db.engine, fk)
    assert (row_id, "ghost_team") in rows


@pytest.mark.unit
def test_delete_orphans_dry_run_reports_but_does_not_delete(test_db, tournament):
    """Dry run reports the count but the row is still present afterwards."""
    row_id = _seed_orphan_team_registration(test_db, tournament)

    counts = cleanup.delete_orphan_rows(db.engine, apply=False)
    assert counts.get("team_registrations.team", 0) >= 1
    assert (
        db.session.execute(
            sa.text("SELECT COUNT(*) FROM team_registrations WHERE id = :id"),
            {"id": row_id},
        ).scalar()
        == 1
    )


@pytest.mark.unit
def test_delete_orphans_apply_removes_the_row(test_db, tournament):
    """Apply mode actually deletes the orphan rows."""
    row_id = _seed_orphan_team_registration(test_db, tournament)

    cleanup.delete_orphan_rows(db.engine, apply=True)
    assert (
        db.session.execute(
            sa.text("SELECT COUNT(*) FROM team_registrations WHERE id = :id"),
            {"id": row_id},
        ).scalar()
        == 0
    )


@pytest.mark.unit
def test_delete_orphans_is_idempotent(test_db, tournament):
    """Second apply on a now-clean DB is a no-op."""
    _seed_orphan_team_registration(test_db, tournament)
    cleanup.delete_orphan_rows(db.engine, apply=True)
    second = cleanup.delete_orphan_rows(db.engine, apply=True)
    assert all(n == 0 for n in second.values())


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_duplicate_groups_reports_team_event_pairs(test_db, tournament, team):
    """A repeated (team, event) pair is reported as one group with count >= 2."""
    db.session.add_all(
        [
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="A",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="B",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
        ]
    )
    db.session.commit()

    rule = next(r for r in cleanup.DEDUPE_RULES if r.table == "team_registrations" and r.columns == ("team", "event"))
    groups = cleanup.find_duplicate_groups(db.engine, rule)
    assert len(groups) == 1
    *vals, count = groups[0]
    assert vals == [team.id, tournament.url]
    assert count == 2


@pytest.mark.unit
def test_dedupe_dry_run_reports_but_keeps_all_rows(test_db, tournament, team):
    """Dry run reports surplus rows but leaves them in place."""
    db.session.add_all(
        [
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="A",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="B",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
        ]
    )
    db.session.commit()
    before = TeamRegistration.query.count()

    counts = cleanup.delete_duplicate_rows(db.engine, apply=False)
    surplus = counts.get("team_registrations(team, event)", 0)
    assert surplus >= 1
    assert TeamRegistration.query.count() == before


@pytest.mark.unit
def test_dedupe_apply_keeps_lowest_id_per_group(test_db, tournament, team):
    """Apply mode keeps the row with the lowest id and deletes the rest."""
    rows = [
        TeamRegistration(
            event=tournament.url,
            team=team.id,
            pseudonym=label,
            status=TeamRegistrationStatus.CONFIRMED,
        )
        for label in ("first_to_keep", "B", "C")
    ]
    db.session.add_all(rows)
    db.session.commit()
    kept_id = min(r.id for r in rows)

    cleanup.delete_duplicate_rows(db.engine, apply=True)

    remaining = TeamRegistration.query.filter_by(event=tournament.url, team=team.id).all()
    assert len(remaining) == 1
    assert remaining[0].id == kept_id
    assert remaining[0].pseudonym == "first_to_keep"


@pytest.mark.unit
def test_dedupe_uses_uuid_for_matches(test_db, tournament, seeded_teams):
    """``matches`` uses ``uuid`` not ``id`` as the keep-key. Verify the rule applies."""
    from models import Match

    _drop_unique_indexes_for_dirty_seeding()
    a = Match(
        name="Same",
        event=tournament.url,
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    b = Match(
        name="Same",
        event=tournament.url,
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    db.session.add_all([a, b])
    db.session.commit()
    # Capture UUIDs before deletion — after delete_duplicate_rows runs, one
    # of the ORM instances will be detached and reading .uuid raises.
    a_uuid, b_uuid = a.uuid, b.uuid
    expected_kept_uuid = min(a_uuid, b_uuid)

    cleanup.delete_duplicate_rows(db.engine, apply=True)
    db.session.expire_all()

    remaining = Match.query.filter_by(event=tournament.url, name="Same").all()
    assert len(remaining) == 1
    assert remaining[0].uuid == expected_kept_uuid


@pytest.mark.unit
def test_dedupe_is_idempotent(test_db, tournament, team):
    """Second apply on a now-clean DB is a no-op."""
    db.session.add_all(
        [
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym=label,
                status=TeamRegistrationStatus.CONFIRMED,
            )
            for label in ("A", "B")
        ]
    )
    db.session.commit()
    cleanup.delete_duplicate_rows(db.engine, apply=True)
    second = cleanup.delete_duplicate_rows(db.engine, apply=True)
    assert all(n == 0 for n in second.values())


# ---------------------------------------------------------------------------
# CLI smoke tests — verify the subcommand wiring works end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_report_runs_without_error(test_db, tournament, capsys):
    """`report` exits 0 against a clean DB."""
    db_url = str(db.engine.url)
    rc = cleanup.main(["--db", db_url, "report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "empty-string emails" in out
    assert "orphan FK references" in out
    assert "duplicate rows" in out


@pytest.mark.unit
def test_cli_normalize_emails_apply_writes(test_db, capsys):
    """`normalize-emails --apply` actually updates."""
    db.session.add(Team(id="ta", name="A", pw_hash="h", email=""))
    db.session.commit()
    rc = cleanup.main(["--db", str(db.engine.url), "normalize-emails", "--apply"])
    assert rc == 0
    db.session.expire_all()
    assert Team.query.get("ta").email is None


# ---------------------------------------------------------------------------
# End-to-end: the full cleanup chain unblocks adding the would-be UNIQUE.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_full_cleanup_then_unique_index_succeeds(test_db, tournament, team):
    """After running all three cleanup operations, an ad-hoc UNIQUE(email) becomes possible.

    This test still proves the cleanup
    script brings emails to a state where uniqueness *could* be enforced
    if anyone ever wanted to: it seeds a dirty state, runs the cleanup,
    and confirms an ad-hoc unique index can then be created without a
    constraint violation.
    """
    # Drop the model-declared UNIQUE indexes so we can seed the dirty
    # pre-Phase-1-shaped state.
    _drop_unique_indexes_for_dirty_seeding()
    db.session.add_all(
        [
            Team(id="ghost_a", name="A", pw_hash="h", email=""),
            Team(id="ghost_b", name="B", pw_hash="h", email=""),
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="dup1",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
            TeamRegistration(
                event=tournament.url,
                team=team.id,
                pseudonym="dup2",
                status=TeamRegistrationStatus.CONFIRMED,
            ),
        ]
    )
    db.session.commit()

    # Verify the dirty state actually blocks UNIQUE(email):
    with pytest.raises(sa.exc.IntegrityError):
        db.session.execute(sa.text("CREATE UNIQUE INDEX uq_teams_email_test ON teams (email)"))
        db.session.commit()
    db.session.rollback()

    # Run the full cleanup chain.
    cleanup.normalize_blank_emails(db.engine, apply=True)
    cleanup.delete_orphan_rows(db.engine, apply=True)
    cleanup.delete_duplicate_rows(db.engine, apply=True)
    db.session.expire_all()

    # Now the UNIQUE constraint can be added without error.
    db.session.execute(sa.text("CREATE UNIQUE INDEX uq_teams_email_test ON teams (email)"))
    db.session.commit()

    # And the duplicate registration is gone.
    assert TeamRegistration.query.filter_by(event=tournament.url, team=team.id).count() == 1
