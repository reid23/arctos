"""Player detail columns preserve existing accounts, images, and references."""

import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect


def test_player_details_migration_preserves_profiles_and_relationships(tmp_path):
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0025_player_profile_details.py"
    spec = importlib.util.spec_from_file_location("migration_0025", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0024_event_banners"
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE players (id TEXT PRIMARY KEY, name TEXT, bio TEXT, profile_photo TEXT, banner_photo TEXT)"
        )
        connection.exec_driver_sql("CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT)")
        connection.exec_driver_sql("CREATE TABLE registrations (player TEXT REFERENCES players(id))")
        old_player = ("existing", "Existing player", "Biography", "avatar.jpg", "cover.jpg")
        connection.exec_driver_sql("INSERT INTO players VALUES (?, ?, ?, ?, ?)", old_player)
        connection.exec_driver_sql("INSERT INTO teams VALUES ('existing', 'Existing team')")
        connection.exec_driver_sql("INSERT INTO registrations VALUES ('existing')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert list(connection.exec_driver_sql("SELECT * FROM players")) == [(*old_player, None, "[]")]
        connection.exec_driver_sql("INSERT INTO players (id, name) VALUES ('new', 'New player')")
        assert json.loads(connection.exec_driver_sql("SELECT pompfen FROM players WHERE id='new'").scalar()) == []
        assert "pompfen" not in {column["name"] for column in inspect(connection).get_columns("teams")}
        connection.exec_driver_sql("UPDATE players SET pronouns='they / them', pompfen='[\"CHAIN\"]'")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT * FROM players WHERE id='existing'").one() == old_player
        assert list(connection.exec_driver_sql("SELECT * FROM teams")) == [("existing", "Existing team")]
        assert list(connection.exec_driver_sql("SELECT * FROM registrations")) == [("existing",)]
        assert list(connection.exec_driver_sql("PRAGMA foreign_key_check")) == []
    engine.dispose()
