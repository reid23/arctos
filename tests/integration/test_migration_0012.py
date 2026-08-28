"""Round-trip test for migration 0012 (video descope column drops).

The repo's Alembic chain assumes tables already exist (baseline 0001 layers
on top of ``db.create_all()``), so running the whole chain from an empty DB
is not how migrations execute here. Instead we build a minimal schema that
matches the pre-0012 shape and exercise the migration's ``upgrade`` /
``downgrade`` directly with a batch-aware Operations context.
"""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0012_video_descope.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0012", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(conn, table):
    return [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")]


def test_0012_upgrade_downgrade_round_trip(tmp_path):
    module = _load_migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE fields (id INTEGER PRIMARY KEY, event TEXT, name TEXT, camera TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE points (uuid TEXT PRIMARY KEY, match TEXT, footage TEXT, "
                "camera_index INTEGER, stream_timestamp FLOAT, length TEXT)"
            )
        )
        conn.execute(text("INSERT INTO fields (id, event, name, camera) VALUES (1, 'ev', 'Field A', '[]')"))
        conn.execute(
            text(
                "INSERT INTO points (uuid, match, footage, camera_index, stream_timestamp) VALUES ('p1','m','f',0,1.5)"
            )
        )

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            module.upgrade()

    with engine.begin() as conn:
        assert "camera" not in _columns(conn, "fields")
        assert "camera_index" not in _columns(conn, "points")
        assert "stream_timestamp" not in _columns(conn, "points")
        # Retained columns + row data survive the rebuild.
        assert "footage" in _columns(conn, "points")
        assert list(conn.exec_driver_sql("SELECT uuid, footage FROM points")) == [("p1", "f")]

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            module.downgrade()

    with engine.begin() as conn:
        assert "camera" in _columns(conn, "fields")
        assert "camera_index" in _columns(conn, "points")
        assert "stream_timestamp" in _columns(conn, "points")
