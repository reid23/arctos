"""Application-managed, idempotent schema migrations.

This module provides a lightweight migration runner for deployments where we do
not use Alembic. Migrations are tracked in ``schema_migrations`` and are safe
to run repeatedly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.domain.enums import MatchStatus, ScheduleType, SetType


@dataclass(frozen=True)
class Migration:
    """Single idempotent migration step."""

    key: str
    apply: Callable[[Connection], None]


def _exec(conn: Connection, sql: str, **params) -> None:
    conn.execute(text(sql), params)


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    insp = inspect(conn)
    return any(col["name"] == column for col in insp.get_columns(table))


def _index_exists(conn: Connection, table: str, index_name: str) -> bool:
    insp = inspect(conn)
    return any(idx.get("name") == index_name for idx in insp.get_indexes(table))


def _register_migration_table(conn: Connection) -> None:
    _exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )


def _has_migration(conn: Connection, key: str) -> bool:
    row = conn.execute(
        text("SELECT id FROM schema_migrations WHERE id = :id"),
        {"id": key},
    ).first()
    return row is not None


def _mark_migration(conn: Connection, key: str) -> None:
    conn.execute(
        text("INSERT INTO schema_migrations (id) VALUES (:id)"),
        {"id": key},
    )


def _migration_backfill_defaults_and_sanitize(conn: Connection) -> None:
    # Fill nullable columns that the application treats as always present.
    _exec(
        conn,
        """
        UPDATE matches
        SET status = :status
        WHERE status IS NULL
        """,
        status=MatchStatus.NOT_STARTED.value,
    )
    _exec(
        conn,
        """
        UPDATE matches
        SET schedule_type = :schedule_type
        WHERE schedule_type IS NULL
        """,
        schedule_type=ScheduleType.STATIC.value,
    )
    _exec(
        conn,
        """
        UPDATE matches
        SET set_type = :set_type
        WHERE set_type IS NULL
        """,
        set_type=SetType.SETS.value,
    )
    _exec(
        conn,
        """
        UPDATE matches
        SET ribbon = FALSE
        WHERE ribbon IS NULL
        """,
    )
    _exec(
        conn,
        """
        UPDATE points
        SET rerolled = FALSE
        WHERE rerolled IS NULL
        """,
    )
    _exec(
        conn,
        """
        UPDATE points
        SET set_number = 1
        WHERE set_number IS NULL
        """,
    )
    _exec(
        conn,
        """
        UPDATE points
        SET stamp = CURRENT_TIMESTAMP
        WHERE stamp IS NULL
        """,
    )
    # Winner should only be TEAM1/TEAM2/NULL.
    _exec(
        conn,
        """
        UPDATE points
        SET winner = NULL
        WHERE winner IS NOT NULL
          AND winner NOT IN ('TEAM1', 'TEAM2')
        """,
    )
    # Migrate deprecated nstonesperset content into stones_per_set where missing.
    _exec(
        conn,
        """
        UPDATE matches
        SET stones_per_set = nstonesperset
        WHERE stones_per_set IS NULL
          AND nstonesperset IS NOT NULL
        """,
    )


def _migration_cleanup_duplicates(conn: Connection) -> None:
    # Keep earliest id for duplicate registrations / tags / fields / head refs.
    for sql in (
        """
        DELETE FROM team_registrations
        WHERE id NOT IN (
            SELECT MIN(id) FROM team_registrations
            WHERE event IS NOT NULL
            GROUP BY event, team
        )
          AND event IS NOT NULL
        """,
        """
        DELETE FROM team_registrations
        WHERE id NOT IN (
            SELECT MIN(id) FROM team_registrations
            WHERE league_id IS NOT NULL
            GROUP BY league_id, team
        )
          AND league_id IS NOT NULL
        """,
        """
        DELETE FROM player_registrations
        WHERE id NOT IN (
            SELECT MIN(id) FROM player_registrations
            WHERE event IS NOT NULL
            GROUP BY event, player
        )
          AND event IS NOT NULL
        """,
        """
        DELETE FROM player_registrations
        WHERE id NOT IN (
            SELECT MIN(id) FROM player_registrations
            WHERE league_id IS NOT NULL
            GROUP BY league_id, player
        )
          AND league_id IS NOT NULL
        """,
        """
        DELETE FROM fields
        WHERE id NOT IN (
            SELECT MIN(id) FROM fields
            GROUP BY event, name
        )
        """,
        """
        DELETE FROM tags
        WHERE id NOT IN (
            SELECT MIN(id) FROM tags
            GROUP BY event, name
        )
        """,
        """
        DELETE FROM headrefs
        WHERE id NOT IN (
            SELECT MIN(id) FROM headrefs
            GROUP BY player, event
        )
        """,
    ):
        _exec(conn, sql)


def _migration_registration_mutual_exclusivity(conn: Connection) -> None:
    # Normalize existing bad rows first: prefer event when both are set.
    _exec(
        conn,
        """
        UPDATE team_registrations
        SET league_id = NULL
        WHERE event IS NOT NULL AND league_id IS NOT NULL
        """,
    )
    _exec(
        conn,
        """
        UPDATE player_registrations
        SET league_id = NULL
        WHERE event IS NOT NULL AND league_id IS NOT NULL
        """,
    )
    # Remove rows that cannot be scoped to either event or league.
    _exec(
        conn,
        """
        DELETE FROM team_registrations
        WHERE event IS NULL AND league_id IS NULL
        """,
    )
    _exec(
        conn,
        """
        DELETE FROM player_registrations
        WHERE event IS NULL AND league_id IS NULL
        """,
    )

    # Cross-database enforcement via triggers (SQLite cannot add check constraints in-place).
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_team_reg_event_xor_insert
        BEFORE INSERT ON team_registrations
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'team_registrations requires exactly one of event or league_id');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_team_reg_event_xor_update
        BEFORE UPDATE ON team_registrations
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'team_registrations requires exactly one of event or league_id');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_player_reg_event_xor_insert
        BEFORE INSERT ON player_registrations
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'player_registrations requires exactly one of event or league_id');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_player_reg_event_xor_update
        BEFORE UPDATE ON player_registrations
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'player_registrations requires exactly one of event or league_id');
        END
        """,
    )


def _migration_indexes_uniques(conn: Connection) -> None:
    stmts = (
        # Uniqueness constraints.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_reg_event_team ON team_registrations(event, team)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_reg_league_team ON team_registrations(league_id, team)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_player_reg_event_player ON player_registrations(event, player)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_player_reg_league_player ON player_registrations(league_id, player)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_field_event_name ON fields(event, name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tag_event_name ON tags(event, name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_headref_event_player ON headrefs(event, player)",
        # Query-path indexes.
        "CREATE INDEX IF NOT EXISTS ix_matches_event ON matches(event)",
        "CREATE INDEX IF NOT EXISTS ix_matches_status ON matches(status)",
        "CREATE INDEX IF NOT EXISTS ix_matches_field ON matches(field)",
        "CREATE INDEX IF NOT EXISTS ix_points_match ON points(match)",
        "CREATE INDEX IF NOT EXISTS ix_match_notes_match ON match_notes(match)",
        "CREATE INDEX IF NOT EXISTS ix_match_notes_point_id ON match_notes(point_id)",
        "CREATE INDEX IF NOT EXISTS ix_team_registrations_event ON team_registrations(event)",
        "CREATE INDEX IF NOT EXISTS ix_team_registrations_league_id ON team_registrations(league_id)",
        "CREATE INDEX IF NOT EXISTS ix_team_registrations_team ON team_registrations(team)",
        "CREATE INDEX IF NOT EXISTS ix_player_registrations_event ON player_registrations(event)",
        "CREATE INDEX IF NOT EXISTS ix_player_registrations_league_id ON player_registrations(league_id)",
        "CREATE INDEX IF NOT EXISTS ix_player_registrations_player ON player_registrations(player)",
        "CREATE INDEX IF NOT EXISTS ix_player_registrations_team ON player_registrations(team)",
        "CREATE INDEX IF NOT EXISTS ix_injuries_player ON injuries(player)",
        "CREATE INDEX IF NOT EXISTS ix_headrefs_event ON headrefs(event)",
        "CREATE INDEX IF NOT EXISTS ix_headrefs_player ON headrefs(player)",
    )
    for stmt in stmts:
        _exec(conn, stmt)


def _migration_match_camera_field_fk_transition(conn: Connection) -> None:
    if not _column_exists(conn, "matches", "field_id"):
        _exec(conn, "ALTER TABLE matches ADD COLUMN field_id INTEGER")
    if not _column_exists(conn, "cameras", "field_id"):
        _exec(conn, "ALTER TABLE cameras ADD COLUMN field_id INTEGER")

    if not _index_exists(conn, "matches", "ix_matches_field_id"):
        _exec(
            conn, "CREATE INDEX IF NOT EXISTS ix_matches_field_id ON matches(field_id)"
        )
    if not _index_exists(conn, "cameras", "ix_cameras_field_id"):
        _exec(
            conn, "CREATE INDEX IF NOT EXISTS ix_cameras_field_id ON cameras(field_id)"
        )

    # Backfill from legacy field name.
    _exec(
        conn,
        """
        UPDATE matches
        SET field_id = (
            SELECT f.id
            FROM fields f
            WHERE f.event = matches.event
              AND f.name = matches.field
            LIMIT 1
        )
        WHERE field_id IS NULL
          AND field IS NOT NULL
          AND TRIM(field) <> ''
        """,
    )
    _exec(
        conn,
        """
        UPDATE cameras
        SET field_id = (
            SELECT m.field_id
            FROM matches m
            WHERE m.uuid = cameras.match_uuid
            LIMIT 1
        )
        WHERE field_id IS NULL
        """,
    )


def _migration_point_winner_and_not_null_guards(conn: Connection) -> None:
    # Cross-database invariant enforcement for winner and non-null lifecycle fields.
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_points_winner_guard_insert
        BEFORE INSERT ON points
        FOR EACH ROW
        WHEN (
            NEW.winner IS NOT NULL
            AND NEW.winner NOT IN ('TEAM1', 'TEAM2')
        )
        BEGIN
            SELECT RAISE(ABORT, 'points.winner must be TEAM1, TEAM2, or NULL');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_points_winner_guard_update
        BEFORE UPDATE ON points
        FOR EACH ROW
        WHEN (
            NEW.winner IS NOT NULL
            AND NEW.winner NOT IN ('TEAM1', 'TEAM2')
        )
        BEGIN
            SELECT RAISE(ABORT, 'points.winner must be TEAM1, TEAM2, or NULL');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_matches_required_lifecycle_insert
        BEFORE INSERT ON matches
        FOR EACH ROW
        WHEN (
            NEW.status IS NULL OR NEW.schedule_type IS NULL OR NEW.set_type IS NULL OR NEW.ribbon IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'matches lifecycle columns cannot be NULL');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_matches_required_lifecycle_update
        BEFORE UPDATE ON matches
        FOR EACH ROW
        WHEN (
            NEW.status IS NULL OR NEW.schedule_type IS NULL OR NEW.set_type IS NULL OR NEW.ribbon IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'matches lifecycle columns cannot be NULL');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_points_required_lifecycle_insert
        BEFORE INSERT ON points
        FOR EACH ROW
        WHEN (
            NEW.rerolled IS NULL OR NEW.set_number IS NULL OR NEW.stamp IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'points lifecycle columns cannot be NULL');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_points_required_lifecycle_update
        BEFORE UPDATE ON points
        FOR EACH ROW
        WHEN (
            NEW.rerolled IS NULL OR NEW.set_number IS NULL OR NEW.stamp IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'points lifecycle columns cannot be NULL');
        END
        """,
    )


def _migration_to_camera_polymorphic_guards(conn: Connection) -> None:
    # Constrain known user_type values and mutual exclusivity for TO scope.
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_to_user_type_insert
        BEFORE INSERT ON tos
        FOR EACH ROW
        WHEN NEW.user_type NOT IN ('player', 'team')
        BEGIN
            SELECT RAISE(ABORT, 'tos.user_type must be player or team');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_to_user_type_update
        BEFORE UPDATE ON tos
        FOR EACH ROW
        WHEN NEW.user_type NOT IN ('player', 'team')
        BEGIN
            SELECT RAISE(ABORT, 'tos.user_type must be player or team');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_to_scope_insert
        BEFORE INSERT ON tos
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'tos requires exactly one of event or league_id');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_to_scope_update
        BEFORE UPDATE ON tos
        FOR EACH ROW
        WHEN (
            (NEW.event IS NULL AND NEW.league_id IS NULL)
            OR
            (NEW.event IS NOT NULL AND NEW.league_id IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'tos requires exactly one of event or league_id');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_camera_uploader_type_insert
        BEFORE INSERT ON cameras
        FOR EACH ROW
        WHEN (
            NEW.uploaded_by_user_type IS NOT NULL
            AND NEW.uploaded_by_user_type NOT IN ('player', 'team')
        )
        BEGIN
            SELECT RAISE(ABORT, 'cameras.uploaded_by_user_type must be player or team');
        END
        """,
    )
    _exec(
        conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_camera_uploader_type_update
        BEFORE UPDATE ON cameras
        FOR EACH ROW
        WHEN (
            NEW.uploaded_by_user_type IS NOT NULL
            AND NEW.uploaded_by_user_type NOT IN ('player', 'team')
        )
        BEGIN
            SELECT RAISE(ABORT, 'cameras.uploaded_by_user_type must be player or team');
        END
        """,
    )


def _migration_match_core_1nf_backfill(conn: Connection) -> None:
    """Backfill normalized Match child tables from legacy text/blob columns."""

    rows = conn.execute(
        text(
            """
            SELECT uuid, refs, refs_initial, team1_players, team2_players, camera_stream_starts
            FROM matches
            """
        )
    ).mappings()

    for row in rows:
        match_uuid = row["uuid"]

        # Ref slots backfill (only if target rows do not exist).
        ref_count = conn.execute(
            text("SELECT COUNT(*) FROM match_ref_slots WHERE match_uuid = :uuid"),
            {"uuid": match_uuid},
        ).scalar_one()
        if ref_count == 0:
            refs_raw = (
                [s.strip() for s in str(row["refs"] or "").split(",")]
                if row["refs"]
                else []
            )
            refs_initial_raw = (
                [s.strip() for s in str(row["refs_initial"] or "").split(",")]
                if row["refs_initial"]
                else []
            )
            n = max(len(refs_raw), len(refs_initial_raw))
            refs_raw += [""] * (n - len(refs_raw))
            refs_initial_raw += [""] * (n - len(refs_initial_raw))
            for idx in range(n):
                resolved = refs_raw[idx] or None
                initial = refs_initial_raw[idx] or None
                if resolved is None and initial is None:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO match_ref_slots (match_uuid, slot_index, resolved_team_id, initial_token)
                        VALUES (:match_uuid, :slot_index, :resolved_team_id, :initial_token)
                        """
                    ),
                    {
                        "match_uuid": match_uuid,
                        "slot_index": idx,
                        "resolved_team_id": resolved,
                        "initial_token": initial,
                    },
                )

        # Roster backfill.
        roster_count = conn.execute(
            text("SELECT COUNT(*) FROM match_roster_entries WHERE match_uuid = :uuid"),
            {"uuid": match_uuid},
        ).scalar_one()
        if roster_count == 0:
            for side, raw in (
                ("team1", row["team1_players"]),
                ("team2", row["team2_players"]),
            ):
                values: list[str] = []
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            values = [str(v).strip() for v in parsed if str(v).strip()]
                    except Exception:
                        values = []
                seen: set[str] = set()
                deduped: list[str] = []
                for player_id in values:
                    if player_id in seen:
                        continue
                    seen.add(player_id)
                    deduped.append(player_id)
                for idx, player_id in enumerate(deduped):
                    conn.execute(
                        text(
                            """
                            INSERT INTO match_roster_entries (match_uuid, side, player_id, slot_index)
                            VALUES (:match_uuid, :side, :player_id, :slot_index)
                            """
                        ),
                        {
                            "match_uuid": match_uuid,
                            "side": side,
                            "player_id": player_id,
                            "slot_index": idx,
                        },
                    )

        # Stream starts backfill.
        stream_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM match_camera_stream_starts WHERE match_uuid = :uuid"
            ),
            {"uuid": match_uuid},
        ).scalar_one()
        if stream_count == 0 and row["camera_stream_starts"]:
            parsed_obj: dict[str, str] = {}
            try:
                loaded = json.loads(row["camera_stream_starts"])
                if isinstance(loaded, dict):
                    parsed_obj = {str(k): str(v) for k, v in loaded.items()}
            except Exception:
                parsed_obj = {}
            for k, stamp in parsed_obj.items():
                if not stamp.strip():
                    continue
                try:
                    idx = int(k)
                except Exception:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO match_camera_stream_starts (match_uuid, camera_index, stream_start_iso)
                        VALUES (:match_uuid, :camera_index, :stream_start_iso)
                        """
                    ),
                    {
                        "match_uuid": match_uuid,
                        "camera_index": idx,
                        "stream_start_iso": stamp.strip(),
                    },
                )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "20260423_001_backfill_defaults_and_sanitize",
        _migration_backfill_defaults_and_sanitize,
    ),
    Migration("20260423_002_cleanup_duplicates", _migration_cleanup_duplicates),
    Migration(
        "20260423_003_registration_mutual_exclusivity",
        _migration_registration_mutual_exclusivity,
    ),
    Migration("20260423_004_indexes_and_uniques", _migration_indexes_uniques),
    Migration(
        "20260423_005_match_camera_field_fk_transition",
        _migration_match_camera_field_fk_transition,
    ),
    Migration(
        "20260423_006_point_winner_and_not_null_guards",
        _migration_point_winner_and_not_null_guards,
    ),
    Migration(
        "20260423_007_to_camera_polymorphic_guards",
        _migration_to_camera_polymorphic_guards,
    ),
    Migration(
        "20260423_008_match_core_1nf_backfill", _migration_match_core_1nf_backfill
    ),
)


def run_bootstrap_migrations(db) -> None:
    """Apply all known app-managed migrations once."""
    with db.engine.begin() as conn:
        _register_migration_table(conn)
        for migration in MIGRATIONS:
            if _has_migration(conn, migration.key):
                continue
            migration.apply(conn)
            _mark_migration(conn, migration.key)
