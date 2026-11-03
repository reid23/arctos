"""Adding event banners preserves league links and published/draft state."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect


def test_event_banner_migration_preserves_event_data_and_relationships(tmp_path):
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0024_event_banners.py"
    spec = importlib.util.spec_from_file_location("migration_0024", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0023_profile_banners"
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE leagues (url TEXT PRIMARY KEY, name TEXT, published BOOLEAN)")
        connection.exec_driver_sql(
            "CREATE TABLE tournaments (url TEXT PRIMARY KEY, name TEXT, published BOOLEAN, "
            "league_id TEXT REFERENCES leagues(url))"
        )
        connection.exec_driver_sql("INSERT INTO leagues VALUES ('season', 'Existing season', 1)")
        connection.exec_driver_sql("INSERT INTO tournaments VALUES ('round', 'Existing round', 0, 'season')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        for table in ("tournaments", "leagues"):
            assert next(
                column for column in inspect(connection).get_columns(table) if column["name"] == "banner_photo"
            )["nullable"]
            assert connection.exec_driver_sql(f"SELECT banner_photo FROM {table}").scalar() is None
        assert list(connection.exec_driver_sql("SELECT * FROM leagues")) == [("season", "Existing season", 1, None)]
        assert list(connection.exec_driver_sql("SELECT * FROM tournaments")) == [
            ("round", "Existing round", 0, "season", None)
        ]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert list(connection.exec_driver_sql("SELECT * FROM leagues")) == [("season", "Existing season", 1)]
        assert list(connection.exec_driver_sql("SELECT * FROM tournaments")) == [
            ("round", "Existing round", 0, "season")
        ]
        assert list(connection.exec_driver_sql("PRAGMA foreign_key_check")) == []
    engine.dispose()
