"""Opt-in practice fixtures must be usable, additive, and safe to repeat."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from app.domain.enums import MatchStatus
from app.services.match_start_eligibility import get_can_start_and_reasons
from app.services.permission_service import PermissionService
from app.services.sidecomp_service import SideCompService
from models import (
    League,
    Match,
    Player,
    Team,
    Tournament,
    db,
)
from scripts import dev_demo, dev_practice

NOW = datetime(2026, 10, 22, 18, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def base_demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("practice-base") / "demo.db"
    dev_demo.prepare_demo_database(path, now=NOW)
    return path


@pytest.fixture
def demo_path(base_demo, tmp_path):
    path = tmp_path / "snapshot-demo" / "demo.db"
    path.parent.mkdir()
    with sqlite3.connect(base_demo) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    return path


def read_app(path: Path) -> Flask:
    """Inspect fixture rows without the regular app's startup recomputation."""
    app = Flask("practice_inspection")
    app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    return app


def rows(path: Path) -> dict[str, set[tuple]]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        names = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return {name: set(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')) for (name,) in names}


def test_practice_preserves_existing_rows_and_adds_usable_event(demo_path, tmp_path, monkeypatch):
    source_snapshot = tmp_path / "original-snapshot.db"
    source_snapshot.write_bytes(demo_path.read_bytes())
    source_bytes = source_snapshot.read_bytes()
    other_demo = tmp_path / "demo.db"
    other_demo.write_bytes(b"another demo must not be changed")
    production = tmp_path / "tournament.db"
    production.write_bytes(b"production must not be changed")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", f"sqlite:///{production}")
    monkeypatch.setattr(dev_demo, "create_app", lambda *_: pytest.fail("Practice preparation ran application startup"))
    with sqlite3.connect(demo_path) as connection:
        connection.execute("UPDATE tournaments SET name = 'Keep my edited event' WHERE url = 'demo-open'")
        connection.execute("UPDATE players SET pw_hash = 'Keep my edited password' WHERE id = 'demo-organizer'")
    before = rows(demo_path)
    manifest = dev_practice.prepare_practice_database(demo_path, now=NOW)
    assert manifest["created"] is True
    assert source_snapshot.read_bytes() == source_bytes
    assert other_demo.read_bytes() == b"another demo must not be changed"
    assert production.read_bytes() == b"production must not be changed"
    after = rows(demo_path)
    for table, old_rows in before.items():
        assert old_rows <= after[table], table
    app = read_app(demo_path)
    with app.app_context():
        try:
            event = db.session.get(Tournament, manifest["slug"])
            organizer = db.session.get(Player, dev_demo.ORGANIZER_ID)
            assert event.published and event.schedule_published
            assert PermissionService.is_tournament_organizer(event.url, organizer)
            assert PermissionService.can_head_ref_match(event.url, organizer)
            ready = db.session.get(Match, manifest["matches"]["ready"])
            assert ready.status == MatchStatus.READY_TO_START and ready.ready_to_start
            allowed, reasons, _ = get_can_start_and_reasons(event.url, ready, organizer)
            assert allowed, reasons
            running = db.session.get(Match, manifest["matches"]["running"])
            assert running.status == MatchStatus.IN_PROGRESS and running.field != ready.field
            assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
        finally:
            db.session.remove()
            db.engine.dispose()


def test_repeated_runs_preserve_all_edits_and_do_not_recreate_deleted_data(demo_path):
    first = dev_practice.prepare_practice_database(demo_path, now=NOW)
    with sqlite3.connect(demo_path) as connection:
        connection.execute("UPDATE tournaments SET name='Edited practice', published=0 WHERE url=?", (first["slug"],))
        connection.execute("UPDATE sidecomps SET active=0 WHERE id=?", (first["sidecomp_id"],))
        connection.execute("UPDATE players SET pw_hash='New password hash' WHERE id=?", (first["players"][0],))
        connection.execute("DELETE FROM sidecompresults WHERE comp=?", (first["sidecomp_id"],))
    before = demo_path.read_bytes()
    repeated = dev_practice.prepare_practice_database(demo_path, now=NOW + timedelta(days=3))
    assert repeated == {**first, "created": False}
    assert demo_path.read_bytes() == before


def test_failure_rolls_back_service_commits_and_fixture_bookkeeping(demo_path, monkeypatch):
    before = rows(demo_path)
    original = SideCompService.log_result
    calls = 0

    def fail_after_commits(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("Injected practice failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(SideCompService, "log_result", fail_after_commits)
    with pytest.raises(RuntimeError, match="Injected practice failure"):
        dev_practice.prepare_practice_database(demo_path, now=NOW)
    assert rows(demo_path) == before
    monkeypatch.setattr(SideCompService, "log_result", original)
    assert dev_practice.prepare_practice_database(demo_path, now=NOW)["created"]


@pytest.mark.parametrize("collision", ["opposite-player", "opposite-team", "tournament", "league", "match"])
def test_reserved_collision_aborts_without_changes(demo_path, collision):
    teams, players, matches = dev_practice._identifiers(dev_practice.DEFAULT_SLUG)
    app = read_app(demo_path)
    with app.app_context():
        try:
            if collision == "opposite-player":
                record = Player(id=teams[-1], name="Keep existing player", pw_hash="keep")
            elif collision == "opposite-team":
                record = Team(id=players[-1], name="Keep existing team", pw_hash="keep")
            elif collision == "tournament":
                record = Tournament(
                    url=dev_practice.DEFAULT_SLUG,
                    name="Keep existing event",
                    start_date=NOW.replace(tzinfo=None),
                    registrable_config=dev_demo._config(),
                )
            elif collision == "league":
                record = League(
                    url=dev_practice.DEFAULT_SLUG, name="Keep existing league", registrable_config=dev_demo._config()
                )
            else:
                record = Match(uuid=matches["ready"], event=dev_demo.LIVE_EVENT, name="Keep existing match")
            db.session.add(record)
            db.session.commit()
        finally:
            db.session.remove()
            db.engine.dispose()
    before = demo_path.read_bytes()
    with pytest.raises(ValueError, match="Practice IDs already exist"):
        dev_practice.prepare_practice_database(demo_path, now=NOW)
    assert demo_path.read_bytes() == before


@pytest.mark.parametrize("slug", ["demo-open", "../demo-practice", "demo-practice;delete", "demo-practice-" + "a" * 40])
def test_unrelated_or_invalid_slugs_are_rejected(demo_path, slug):
    before = demo_path.read_bytes()
    with pytest.raises(ValueError, match="demo-practice"):
        dev_practice.prepare_practice_database(demo_path, slug=slug)
    assert demo_path.read_bytes() == before


def test_missing_wrong_named_and_symlink_databases_are_rejected(demo_path, tmp_path):
    protected = tmp_path / "source.db"
    protected.write_bytes(demo_path.read_bytes())
    before = protected.read_bytes()
    with pytest.raises(ValueError, match="named demo.db"):
        dev_practice.prepare_practice_database(protected)
    linked = tmp_path / "demo.db"
    linked.symlink_to(protected)
    with pytest.raises(ValueError, match="symlink"):
        dev_practice.prepare_practice_database(linked)
    with pytest.raises(ValueError, match="already exist"):
        dev_practice.prepare_practice_database(tmp_path / "absent" / "demo.db")
    assert protected.read_bytes() == before


def test_outdated_schema_requires_explicit_migration_without_writes(demo_path):
    with sqlite3.connect(demo_path) as connection:
        connection.execute("ALTER TABLE players DROP COLUMN pronouns")
    before = demo_path.read_bytes()
    with pytest.raises(ValueError, match="schema is out of date.*players.pronouns"):
        dev_practice.prepare_practice_database(demo_path)
    assert demo_path.read_bytes() == before


def test_existing_non_demo_database_is_not_extended(demo_path):
    with sqlite3.connect(demo_path) as connection:
        connection.execute("DELETE FROM tos WHERE user_id='demo-organizer' AND event='demo-open'")
    before = demo_path.read_bytes()
    with pytest.raises(ValueError, match="Prepare a local demo"):
        dev_practice.prepare_practice_database(demo_path)
    assert demo_path.read_bytes() == before


def test_new_slug_preserves_the_existing_practice_event(demo_path):
    first = dev_practice.prepare_practice_database(demo_path, now=NOW)
    before = rows(demo_path)
    second = dev_practice.prepare_practice_database(demo_path, slug="demo-practice-2", now=NOW)
    assert second["created"] and second["slug"] != first["slug"]
    after = rows(demo_path)
    for table, old_rows in before.items():
        assert old_rows <= after[table], table
    assert dev_practice.prepare_practice_database(demo_path, now=NOW) == {**first, "created": False}
