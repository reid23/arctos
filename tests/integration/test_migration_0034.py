"""Legacy tables are dropped where they exist, and the migration is harmless where they don't."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def _migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0034_drop_legacy_tables.py"
    spec = importlib.util.spec_from_file_location("migration_0034", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_drops_legacy_tables_and_keeps_everything_else(tmp_path):
    migration = _migration()
    assert migration.down_revision == "0033_account_merges"
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE players (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE team_invitations (id INTEGER PRIMARY KEY, player TEXT REFERENCES players (id))"
        )
        connection.exec_driver_sql("CREATE TABLE playerrecords (id INTEGER PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert inspect(connection).get_table_names() == ["players"]
