"""Adding optional banners preserves account data and referencing records."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect


def test_profile_banner_migration_preserves_accounts_and_avatar_paths(tmp_path):
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0023_profile_banners.py"
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection:
        for table in ("players", "teams"):
            connection.exec_driver_sql(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, name TEXT, profile_photo TEXT)")
            connection.exec_driver_sql(
                f"INSERT INTO {table} VALUES ('existing', 'Existing name', 'uploads/avatar.jpg')"
            )
        connection.exec_driver_sql(
            "CREATE TABLE registrations (player TEXT REFERENCES players(id), team TEXT REFERENCES teams(id))"
        )
        connection.exec_driver_sql("INSERT INTO registrations VALUES ('existing', 'existing')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        for table in ("players", "teams"):
            assert next(
                column for column in inspect(connection).get_columns(table) if column["name"] == "banner_photo"
            )["nullable"]
            assert list(connection.exec_driver_sql(f"SELECT * FROM {table}")) == [
                ("existing", "Existing name", "uploads/avatar.jpg", None)
            ]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        for table in ("players", "teams"):
            assert list(connection.exec_driver_sql(f"SELECT * FROM {table}")) == [
                ("existing", "Existing name", "uploads/avatar.jpg")
            ]
        assert list(connection.exec_driver_sql("SELECT * FROM registrations")) == [("existing", "existing")]
        assert list(connection.exec_driver_sql("PRAGMA foreign_key_check")) == []
    engine.dispose()
