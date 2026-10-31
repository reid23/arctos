"""Roster-request indexes apply whether or not the old unique index exists."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect


def _migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0028_roster_request_beside_invitation.py"
    spec = importlib.util.spec_from_file_location("migration_0028", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _engine(path):
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    return engine


def _create_registrations(connection, *, with_old_unique_index: bool):
    connection.exec_driver_sql(
        "CREATE TABLE player_registrations (id INTEGER PRIMARY KEY, player TEXT, event TEXT, status TEXT)"
    )
    connection.exec_driver_sql(
        "INSERT INTO player_registrations (player, event, status) VALUES ('runner', 'old-cup', 'CONFIRMED')"
    )
    if with_old_unique_index:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_player_registrations_player_event ON player_registrations (player, event)"
        )


def _index_sql(connection):
    rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'player_registrations' ORDER BY name"
    ).all()
    return {name: sql for name, sql in rows}


def test_roster_request_migration_replaces_an_existing_unique_index(tmp_path):
    migration = _migration()
    assert migration.down_revision == "0027_team_claim_requests"
    engine = _engine(tmp_path / "migrated.db")
    with engine.begin() as connection:
        _create_registrations(connection, with_old_unique_index=True)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        indexes = _index_sql(connection)
        assert "WHERE status NOT IN" in indexes["uq_player_registrations_player_event"]
        assert "WHERE status = 'REQUESTED'" in indexes["uq_player_registrations_player_event_requested"]
        connection.exec_driver_sql(
            "INSERT INTO player_registrations (player, event, status) VALUES ('runner', 'old-cup', 'REQUESTED')"
        )
        connection.exec_driver_sql("DELETE FROM player_registrations WHERE status = 'REQUESTED'")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        indexes = _index_sql(connection)
        assert list(indexes) == ["uq_player_registrations_player_event"]
        assert "WHERE" not in indexes["uq_player_registrations_player_event"]
    engine.dispose()


def test_roster_request_migration_adds_indexes_when_the_old_one_is_absent(tmp_path):
    migration = _migration()
    engine = _engine(tmp_path / "stamped.db")
    with engine.begin() as connection:
        _create_registrations(connection, with_old_unique_index=False)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        indexes = _index_sql(connection)
        assert "WHERE status NOT IN" in indexes["uq_player_registrations_player_event"]
        assert "WHERE status = 'REQUESTED'" in indexes["uq_player_registrations_player_event_requested"]
        listed = {index["name"] for index in inspect(connection).get_indexes("player_registrations")}
        assert listed == {
            "uq_player_registrations_player_event",
            "uq_player_registrations_player_event_requested",
        }
    engine.dispose()
