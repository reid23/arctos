"""The local demo must be useful, repeatable, and isolated from normal data."""

from datetime import datetime, timezone
import sqlite3

from alembic import command
from alembic.script import ScriptDirectory
from flask import Flask
import pytest

from app import create_app
from app.domain.enums import MatchStatus, RegistrationStatus
from app.services.permission_service import PermissionService
from app.services.registration_resolver import team_registrations_for_tournament
from models import (
    League,
    Match,
    Player,
    PlayerRegistration,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from scripts import dev_demo


@pytest.fixture
def demo_path(tmp_path):
    path = tmp_path / "instance" / "demo.db"
    assert dev_demo.prepare_demo_database(path, now=datetime(2026, 10, 22, 18, tzinfo=timezone.utc))
    return path


@pytest.fixture
def demo_app(demo_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{demo_path}"})
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


def test_reseeding_preserves_edits_and_does_not_touch_other_database(demo_path):
    protected = demo_path.with_name("tournament.db")
    protected.write_bytes(b"existing tournament data must not change")
    with sqlite3.connect(demo_path) as connection:
        connection.execute("UPDATE tournaments SET name = 'My local changes' WHERE url = ?", (dev_demo.LIVE_EVENT,))
        before = connection.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    snapshot = demo_path.read_bytes()
    assert dev_demo.prepare_demo_database(demo_path) is False
    assert demo_path.read_bytes() == snapshot
    assert protected.read_bytes() == b"existing tournament data must not change"
    with sqlite3.connect(demo_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM players").fetchone()[0] == before
        assert (
            connection.execute("SELECT name FROM tournaments WHERE url = ?", (dev_demo.LIVE_EVENT,)).fetchone()[0]
            == "My local changes"
        )


def test_demo_records_have_valid_references_and_expected_organizer_access(demo_app):
    assert {player.id for player in Player.query.all()} == dev_demo.RESERVED_PLAYER_IDS
    assert {team.id for team in Team.query.all()} == dev_demo.RESERVED_TEAM_IDS
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
    organizer = db.session.get(Player, dev_demo.ORGANIZER_ID)
    assert organizer.check_password(dev_demo.DEMO_PASSWORD)
    assert db.session.get(Player, dev_demo.PLAYER_ID).check_password(dev_demo.DEMO_PASSWORD)
    assert db.session.get(Team, dev_demo.TEAM_ID).check_password(dev_demo.DEMO_PASSWORD)
    for tournament in Tournament.query.all():
        assert tournament.published and tournament.schedule_published
        assert PermissionService.is_tournament_organizer(tournament.url, organizer)
        assert PermissionService.can_head_ref_match(tournament.url, organizer)
        assert (tournament.league_id is None) != (tournament.registrable_config_id is None)
    league_event = db.session.get(Tournament, dev_demo.LEAGUE_EVENT)
    assert team_registrations_for_tournament(league_event)
    assert TeamRegistration.query.filter_by(event=dev_demo.LEAGUE_EVENT).count() == 0
    pending = PlayerRegistration.query.filter_by(event=dev_demo.LIVE_EVENT, player=dev_demo.PLAYER_ID).one()
    assert pending.status == RegistrationStatus.PENDING_TEAM_APPROVAL and not pending.paid
    assert {match.status for match in Match.query.all()} >= {MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED}


def test_demo_api_authenticates_both_account_types(demo_app):
    client = demo_app.test_client()
    for username, expected_type in [(dev_demo.ORGANIZER_ID, "player"), (dev_demo.TEAM_ID, "team")]:
        response = client.post("/_api/login", json={"username": username, "password": dev_demo.DEMO_PASSWORD})
        assert response.status_code == 200, response.get_json()
        user = client.get("/_api/me").get_json()
        assert user["id"] == username and user["type"] == expected_type
        client.post("/_api/logout")


def test_demo_info_is_only_registered_by_demo_launcher(demo_path, demo_app):
    assert demo_app.test_client().get("/_api/demo-info").status_code == 404
    isolated_app = dev_demo.create_demo_app(demo_path)
    try:
        response = isolated_app.test_client().get("/_api/demo-info")
        assert response.status_code == 200
        assert response.get_json() == {"demo": True, "organizer": dev_demo.ORGANIZER_ID}
        assert demo_app.test_client().get("/_api/demo-info").status_code == 404
    finally:
        with isolated_app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_demo_rejects_production_names_and_symlinks(tmp_path):
    production = tmp_path / "tournament.db"
    production.write_bytes(b"do not modify")
    with pytest.raises(ValueError):
        dev_demo.prepare_demo_database(production)
    symlink = tmp_path / "demo.db"
    symlink.symlink_to(production)
    with pytest.raises(ValueError):
        dev_demo.prepare_demo_database(symlink)
    assert production.read_bytes() == b"do not modify"


def test_seed_only_never_runs_the_server(tmp_path, monkeypatch, capsys):
    path = tmp_path / "instance" / "demo.db"
    monkeypatch.setattr(dev_demo, "DEFAULT_DATABASE", path)
    monkeypatch.setattr(dev_demo, "prepare_demo_database", lambda: True)
    monkeypatch.setattr(dev_demo.Flask, "run", lambda *args, **kwargs: pytest.fail("Server started"))
    dev_demo.main(["--seed-only"])
    assert "Created sample data" in capsys.readouterr().out


@pytest.fixture
def versioned_snapshot(tmp_path):
    """Reconstruct the known pre-banner schema without opening a real snapshot."""
    path = tmp_path / "source.db"
    bootstrap = Flask("snapshot_test")
    bootstrap.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(bootstrap)
    with bootstrap.app_context():
        try:
            db.create_all()
            db.session.add_all(
                [
                    Player(id="snapshot-player", name="Existing player", pw_hash="existing-player-password"),
                    Team(id="snapshot-team", name="Existing team", pw_hash="existing-team-password"),
                    Tournament(
                        url="snapshot-event",
                        name="Existing event",
                        start_date=datetime(2026, 1, 1),
                        end_date=datetime(2026, 1, 2),
                        registrable_config=dev_demo._config(),
                    ),
                    League(url="snapshot-league", name="Existing league", registrable_config=dev_demo._config()),
                ]
            )
            db.session.commit()
        finally:
            db.session.remove()
            db.engine.dispose()
    config = dev_demo._migration_config(path)
    command.stamp(config, "head")
    command.downgrade(config, "0022_oauth_provider")
    return path


def test_snapshot_copy_migrates_additively_without_touching_source_or_other_databases(
    versioned_snapshot, tmp_path, monkeypatch
):
    source = versioned_snapshot
    before = source.read_bytes()
    original_records = {}
    with sqlite3.connect(source) as connection:
        for table, key in [("players", "id"), ("teams", "id"), ("tournaments", "url"), ("leagues", "url")]:
            cursor = connection.execute(f"SELECT * FROM {table} ORDER BY {key}")
            original_records[table] = ([column[0] for column in cursor.description], cursor.fetchall())
    ordinary = tmp_path / "instance" / "demo.db"
    ordinary.parent.mkdir()
    ordinary.write_bytes(b"ordinary demo edits")
    protected = ordinary.with_name("tournament.db")
    protected.write_bytes(b"ordinary tournament data")
    monkeypatch.setattr(dev_demo, "DEFAULT_DATABASE", ordinary)
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", f"sqlite:///{protected}")
    monkeypatch.setattr(dev_demo, "create_app", lambda *_: pytest.fail("Preparation ran application startup"))
    destination = ordinary.parent / "snapshot-demo" / "demo.db"
    assert dev_demo.prepare_snapshot_demo_database(source, destination)
    assert source.read_bytes() == before
    assert ordinary.read_bytes() == b"ordinary demo edits"
    assert protected.read_bytes() == b"ordinary tournament data"
    with sqlite3.connect(destination) as connection:
        head = ScriptDirectory.from_config(dev_demo._migration_config(destination)).get_current_head()
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (head,)
        for table, (columns, rows) in original_records.items():
            key = "id" if table in {"players", "teams"} else "url"
            for row in rows:
                saved = connection.execute(
                    f"SELECT {','.join(columns)} FROM {table} WHERE {key} = ?", (row[columns.index(key)],)
                ).fetchone()
                assert saved == row
            assert "banner_photo" in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert connection.execute("SELECT id FROM players WHERE id = ?", (dev_demo.ORGANIZER_ID,)).fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo.db"]


def test_snapshot_restarts_preserve_edits_passwords_and_new_records(versioned_snapshot, tmp_path):
    destination = tmp_path / "snapshot-demo" / "demo.db"
    assert dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "UPDATE players SET name = ?, pw_hash = ? WHERE id = ?",
            ("Local edit", "changed-password", dev_demo.ORGANIZER_ID),
        )
        connection.execute("INSERT INTO players (id, name) VALUES ('new-local-player', 'New local player')")
        connection.execute("UPDATE tournaments SET name = 'Local event edit' WHERE url = ?", (dev_demo.LIVE_EVENT,))
    before = destination.read_bytes()
    assert dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination) is False
    assert destination.read_bytes() == before
    # Once copied, the original need not remain available to reopen the demo.
    assert dev_demo.prepare_snapshot_demo_database(tmp_path / "unavailable.db", destination) is False
    assert destination.read_bytes() == before


@pytest.mark.parametrize(
    ("table", "key", "reserved_id"),
    [
        ("players", "id", dev_demo.ORGANIZER_ID),
        ("players", "id", "demo-duelist-10"),
        ("teams", "id", dev_demo.TEAM_ID),
        ("teams", "id", dev_demo.ORGANIZER_ID),
        ("players", "id", dev_demo.TEAM_ID),
        ("tournaments", "url", dev_demo.LEAGUE_EVENT),
        ("leagues", "url", dev_demo.LEAGUE_ID),
    ],
)
def test_snapshot_rejects_reserved_ids_before_seeding(
    versioned_snapshot, tmp_path, monkeypatch, table, key, reserved_id
):
    with sqlite3.connect(versioned_snapshot) as connection:
        connection.execute(f"UPDATE {table} SET {key} = ?", (reserved_id,))
    before = versioned_snapshot.read_bytes()
    destination = tmp_path / "copy" / "demo.db"
    monkeypatch.setattr(
        dev_demo, "_seed_database", lambda *_args, **_kwargs: pytest.fail("Seeded a colliding snapshot")
    )
    with pytest.raises(ValueError, match="reserved demo IDs"):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
    assert versioned_snapshot.read_bytes() == before
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("revision", [None, "not-a-known-revision"])
def test_snapshot_never_stamps_unknown_or_unversioned_databases(versioned_snapshot, tmp_path, revision):
    with sqlite3.connect(versioned_snapshot) as connection:
        if revision is None:
            connection.execute("DROP TABLE alembic_version")
        else:
            connection.execute("UPDATE alembic_version SET version_num = ?", (revision,))
    before = versioned_snapshot.read_bytes()
    destination = tmp_path / "copy" / "demo.db"
    with pytest.raises(ValueError, match="[Rr]evision"):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
    assert versioned_snapshot.read_bytes() == before
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("failure", ["migration", "seed"])
def test_snapshot_failure_does_not_publish_a_partial_copy(versioned_snapshot, tmp_path, monkeypatch, failure):
    before = versioned_snapshot.read_bytes()
    destination = tmp_path / "copy" / "demo.db"

    def fail(*_args, **_kwargs):
        raise RuntimeError("deliberate failure")

    if failure == "migration":
        monkeypatch.setattr(dev_demo.command, "upgrade", fail)
    else:
        monkeypatch.setattr(dev_demo, "_seed_database", fail)
    with pytest.raises(RuntimeError, match="deliberate failure"):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
    assert versioned_snapshot.read_bytes() == before
    assert list(destination.parent.iterdir()) == []


def test_snapshot_backup_includes_committed_wal_data(versioned_snapshot, tmp_path, monkeypatch):
    destination = tmp_path / "copy" / "demo.db"
    # Keep the writer open so its committed row stays in the WAL during backup.
    with sqlite3.connect(versioned_snapshot) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO players (id, name) VALUES ('wal-player', 'Committed WAL player')")
        writer.commit()
        assert versioned_snapshot.with_name(versioned_snapshot.name + "-wal").stat().st_size > 0
        before = versioned_snapshot.read_bytes()
        monkeypatch.setattr(dev_demo, "_seed_database", lambda *_args, **_kwargs: None)
        assert dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
        assert versioned_snapshot.read_bytes() == before
        with sqlite3.connect(destination) as connection:
            assert connection.execute("SELECT name FROM players WHERE id = 'wal-player'").fetchone() == (
                "Committed WAL player",
            )
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo.db"]


def test_snapshot_publication_cannot_overwrite_a_concurrent_destination(versioned_snapshot, tmp_path, monkeypatch):
    destination = tmp_path / "copy" / "demo.db"

    def concurrent_writer(*_args, **_kwargs):
        destination.write_bytes(b"another process created this file")

    monkeypatch.setattr(dev_demo, "_seed_database", concurrent_writer)
    with pytest.raises(FileExistsError):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)
    assert destination.read_bytes() == b"another process created this file"
    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo.db"]


def test_snapshot_rejects_source_destination_alias_and_ordinary_demo(versioned_snapshot, tmp_path, monkeypatch):
    ordinary = tmp_path / "instance" / "demo.db"
    monkeypatch.setattr(dev_demo, "DEFAULT_DATABASE", ordinary)
    with pytest.raises(ValueError, match="separate directory"):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, ordinary)
    destination = tmp_path / "demo.db"
    destination.hardlink_to(versioned_snapshot)
    with pytest.raises(ValueError, match="different files"):
        dev_demo.prepare_snapshot_demo_database(versioned_snapshot, destination)


def test_snapshot_seed_only_uses_separate_destination_without_starting_server(
    versioned_snapshot, tmp_path, monkeypatch, capsys
):
    destination = tmp_path / "snapshot-demo" / "demo.db"
    monkeypatch.setattr(dev_demo, "DEFAULT_SNAPSHOT_DATABASE", destination)
    calls = []
    monkeypatch.setattr(
        dev_demo, "prepare_snapshot_demo_database", lambda source, target: calls.append((source, target)) or True
    )
    monkeypatch.setattr(dev_demo, "create_demo_app", lambda *_: pytest.fail("Server application created"))
    dev_demo.main(["--seed-only", "--snapshot", str(versioned_snapshot)])
    assert calls == [(versioned_snapshot, destination)]
    assert str(destination) in capsys.readouterr().out
