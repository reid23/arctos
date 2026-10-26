"""Optional form configuration preserves registration data and shared scopes."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event


def load_migration(name):
    path = Path(__file__).resolve().parents[2] / "migrations/versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_registration_form_migration_preserves_existing_scopes_and_managers(tmp_path):
    manager_migration = load_migration("0026_team_managers")
    migration = load_migration("0027_registration_forms")
    assert migration.down_revision == manager_migration.revision
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE teams (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE players (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO teams VALUES ('team')")
        connection.exec_driver_sql("INSERT INTO players VALUES ('manager')")
        with Operations.context(MigrationContext.configure(connection)):
            manager_migration.upgrade()
        connection.exec_driver_sql("INSERT INTO team_managers VALUES ('team', 'manager')")
        connection.exec_driver_sql("CREATE TABLE registrable_configs (id INTEGER PRIMARY KEY, player_reg_fee NUMERIC)")
        connection.exec_driver_sql(
            "CREATE TABLE leagues (id TEXT PRIMARY KEY, config INTEGER REFERENCES registrable_configs(id))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE tournaments (id TEXT PRIMARY KEY, config INTEGER REFERENCES registrable_configs(id), league TEXT REFERENCES leagues(id))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE registrations (player TEXT, tournament TEXT REFERENCES tournaments(id))"
        )
        connection.exec_driver_sql("INSERT INTO registrable_configs VALUES (1, 12.50), (2, 0)")
        connection.exec_driver_sql("INSERT INTO leagues VALUES ('league', 2)")
        connection.exec_driver_sql(
            "INSERT INTO tournaments VALUES ('standalone', 1, NULL), ('league-event', NULL, 'league')"
        )
        connection.exec_driver_sql("INSERT INTO registrations VALUES ('player', 'standalone')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert list(connection.exec_driver_sql("SELECT * FROM registrable_configs")) == [
            (1, 12.5, None, None, None, None, None, None),
            (2, 0, None, None, None, None, None, None),
        ]
        connection.exec_driver_sql(
            "UPDATE registrable_configs SET registration_form_url='https://docs.google.com/forms/d/e/form/viewform', registration_form_entry_id='123', registration_form_sheet_id='sheet', registration_form_sheet_gid=0, registration_form_code_column='Arctos registration code', registration_form_signing_key='private-test-key' WHERE id=1"
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert list(connection.exec_driver_sql("SELECT * FROM registrable_configs")) == [(1, 12.5), (2, 0)]
        assert list(connection.exec_driver_sql("SELECT * FROM registrations")) == [("player", "standalone")]
        assert list(connection.exec_driver_sql("SELECT * FROM tournaments")) == [
            ("standalone", 1, None),
            ("league-event", None, "league"),
        ]
        assert list(connection.exec_driver_sql("SELECT * FROM team_managers")) == [("team", "manager")]
        assert list(connection.exec_driver_sql("PRAGMA foreign_key_check")) == []
        with Operations.context(MigrationContext.configure(connection)):
            manager_migration.downgrade()
        assert connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='team_managers'").first() is None
    engine.dispose()
