#!/usr/bin/env python3
"""scripts/cleanup_data_quality.py — find and (optionally) fix data-quality issues.

The additive schema migration adds UNIQUE constraints, mutual-exclusivity
CHECK constraints, and FK-strict ``batch_alter_table`` rebuilds. Pre-existing
data that violates any of these will block the migration. This script
detects the three known classes of issue and offers idempotent fixes:

* **Empty-string emails** on ``players`` and ``teams``. SQLite's ``UNIQUE``
  treats ``""`` as a real distinct value, so 15 teams with ``email = ""``
  collide on the would-be ``UNIQUE(email)`` constraint. Converting them to
  ``NULL`` (which they semantically already are) makes them coexist legally.
  Lossless and safe.

* **Orphan foreign-key references**: rows whose FK column points at a
  parent that no longer exists. ``PRAGMA foreign_keys = ON`` (which the
  application now enforces) means those rows survive but cannot be
  ``UPDATE``d. They also break ``batch_alter_table`` rebuilds. Deleting
  them is a policy decision — defaults to dry-run.

* **Duplicate rows** on logically-unique column groups (the same set
  ``scripts/check_duplicates.py`` reports). The default dedupe policy is
  "keep the row with the lowest ``id`` (or ``uuid``) per group, delete the
  rest". Choosing which row to keep is also a policy decision — defaults
  to dry-run.

Usage::

    # Read-only audit covering all three issue classes.
    uv run python scripts/cleanup_data_quality.py report

    # Convert empty-string emails to NULL. Lossless — no operator review
    # needed. Add --apply to actually write.
    uv run python scripts/cleanup_data_quality.py normalize-emails
    uv run python scripts/cleanup_data_quality.py normalize-emails --apply

    # Delete rows whose FK target no longer exists. Reviews in dry-run by
    # default; add --apply to actually delete.
    uv run python scripts/cleanup_data_quality.py delete-orphans
    uv run python scripts/cleanup_data_quality.py delete-orphans --apply

    # Deduplicate rows that violate a (would-be-)unique column group.
    # Default policy: keep the row with the lowest ``id``/``uuid`` per
    # group, delete the others. Reviews in dry-run by default.
    uv run python scripts/cleanup_data_quality.py dedupe
    uv run python scripts/cleanup_data_quality.py dedupe --apply

    # Point at a different database (default: instance/tournament.db).
    uv run python scripts/cleanup_data_quality.py report \\
        --db /path/to/snapshot.db
    SQLALCHEMY_DATABASE_URI=sqlite:////tmp/x.db \\
        uv run python scripts/cleanup_data_quality.py report

Exit codes:

* ``0`` — completed successfully (in dry-run mode this just means the
  report ran; it does *not* mean the database is clean).
* ``1`` — invoked with an unrecognised subcommand or option.

Idempotency:

Every operation is safe to re-run. ``normalize-emails`` only updates rows
whose ``email`` is currently ``""``; ``delete-orphans`` only deletes rows
whose FK target is currently missing; ``dedupe`` only deletes rows other
than the lowest-id member of an existing duplicate group. Running any
command twice on a clean database is a no-op.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Configuration: what tables and column groups to operate on. Mirrors the
# CHECKS list in check_duplicates.py and the FK-bearing columns in the
# Arctos schema. Adding a new entry to either of these lists extends the
# corresponding subcommand automatically.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmailColumn:
    """An ``email``-shaped column on which ``""`` should be normalised to NULL."""

    table: str
    column: str = "email"


EMAIL_COLUMNS: Sequence[EmailColumn] = (
    EmailColumn(table="players"),
    EmailColumn(table="teams"),
)


@dataclass(frozen=True)
class ForeignKey:
    """A foreign-key column whose orphans should be reported / deleted.

    Attributes:
        table: Child table.
        column: Child column holding the FK reference.
        parent_table: Parent table being referenced.
        parent_column: Parent column being referenced (usually a PK).
        why_safe_to_delete: Free-text rationale shown in the report so the
            operator can decide whether to apply.
    """

    table: str
    column: str
    parent_table: str
    parent_column: str
    why_safe_to_delete: str


# Only the FK pairs we actually expect to find orphans on, based on the
# verification run against the live snapshot. Add more here when new ones
# surface.
ORPHAN_FK_CHECKS: Sequence[ForeignKey] = (
    ForeignKey(
        table="team_registrations",
        column="team",
        parent_table="teams",
        parent_column="id",
        why_safe_to_delete=(
            "Registration rows pointing at deleted teams cannot be UPDATEd "
            "under the runtime FK pragma. Surviving rows are typically CANCELLED."
        ),
    ),
    ForeignKey(
        table="player_registrations",
        column="player",
        parent_table="players",
        parent_column="id",
        why_safe_to_delete=(
            "Registration rows pointing at deleted players cannot be UPDATEd under the runtime FK pragma."
        ),
    ),
    ForeignKey(
        table="player_registrations",
        column="team",
        parent_table="teams",
        parent_column="id",
        why_safe_to_delete=(
            "Player registration rows pointing at a deleted team. The team "
            "field is nullable, so 'unattaching' the player by setting team "
            "to NULL would be a less destructive alternative — but this "
            "script defaults to delete to mirror the other policies."
        ),
    ),
    ForeignKey(
        table="headrefs",
        column="player",
        parent_table="players",
        parent_column="id",
        why_safe_to_delete="Head-ref grant for a deleted player has no effect.",
    ),
)


@dataclass(frozen=True)
class DedupeRule:
    """A (table, columns) group that should be unique together.

    Attributes:
        table: SQL table the rule applies to.
        columns: Columns that should be unique together.
        id_column: PK column used to pick which row to keep — the row with
            the lowest value wins. Defaults to ``"id"``; override for
            tables like ``matches`` whose PK is ``uuid``.
        child_handling: How to handle other tables that have FK references
            to this table when one of the rows being deleted has children.

            * ``"leaf"`` — no FK children expected. Direct DELETE works.
              If the script discovers child rows at runtime, it bails and
              warns rather than silently orphaning them.
            * ``"reassign"`` — reassign every child row's FK to the keeper
              row before deleting the duplicate. Safe for join-table-like
              parents (matches, headrefs) where rows are interchangeable
              from the child's perspective.
            * ``"refuse"`` — refuse to delete; print a warning telling the
              operator to manually merge. Used for user-account-like
              tables (``players``, ``teams``) where reassigning all child
              rows would conflate two distinct user identities.
    """

    table: str
    columns: tuple[str, ...]
    id_column: str = "id"
    child_handling: str = "leaf"


# Mirrors the §2 unique-constraint targets, plus emails. Edits to this list
# must stay in sync with scripts/check_duplicates.py — both reflect the
# same set of (would-be-)unique column groups.
DEDUPE_RULES: Sequence[DedupeRule] = (
    DedupeRule(table="team_registrations", columns=("team", "event")),
    DedupeRule(table="team_registrations", columns=("team", "league_id")),
    DedupeRule(table="player_registrations", columns=("player", "event")),
    DedupeRule(table="player_registrations", columns=("player", "league_id")),
    DedupeRule(table="headrefs", columns=("player", "event"), child_handling="reassign"),
    DedupeRule(table="matches", columns=("name", "event"), id_column="uuid", child_handling="reassign"),
    DedupeRule(table="tags", columns=("name", "event")),
    DedupeRule(table="fields", columns=("name", "event")),
    DedupeRule(table="players", columns=("email",), child_handling="refuse"),
    DedupeRule(table="teams", columns=("email",), child_handling="refuse"),
)


# ---------------------------------------------------------------------------
# Database resolution + helpers (shared across all subcommands).
# ---------------------------------------------------------------------------


def _resolve_database_url(cli_db: str | None) -> str:
    """Same precedence rule as ``check_duplicates.py``."""
    if cli_db:
        if cli_db.startswith("sqlite:") or "://" in cli_db:
            return cli_db
        return f"sqlite:///{Path(cli_db).expanduser().resolve()}"
    env = os.environ.get("SQLALCHEMY_DATABASE_URI")
    if env:
        return env
    return f"sqlite:///{PROJECT_ROOT / 'instance' / 'tournament.db'}"


def _table_exists(engine: Engine, name: str) -> bool:
    """True iff ``name`` is in the connected database's table list."""
    return name in sa.inspect(engine).get_table_names()


def _connect(engine: Engine):
    """Open a connection with FK enforcement on, mirroring runtime."""
    conn = engine.connect()
    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# normalize-emails
# ---------------------------------------------------------------------------


def find_blank_emails(engine: Engine) -> dict[str, int]:
    """Count rows whose ``email`` is exactly the empty string, per table."""
    counts: dict[str, int] = {}
    with _connect(engine) as conn:
        for ec in EMAIL_COLUMNS:
            if not _table_exists(engine, ec.table):
                continue
            n = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {ec.table} WHERE {ec.column} = ''")  # noqa: S608
            ).scalar_one()
            counts[ec.table] = int(n)
    return counts


def normalize_blank_emails(engine: Engine, apply: bool) -> dict[str, int]:
    """Convert ``email = ''`` to ``email = NULL`` on every email column.

    Args:
        engine: Live database engine.
        apply: When True, perform the UPDATE. When False (default), the
            function still reports counts but does not write.

    Returns:
        Per-table count of rows that were (or would be) updated.
    """
    counts = find_blank_emails(engine)
    if not apply:
        return counts
    with _connect(engine) as conn:
        for ec in EMAIL_COLUMNS:
            if counts.get(ec.table, 0) == 0:
                continue
            conn.execute(
                sa.text(f"UPDATE {ec.table} SET {ec.column} = NULL WHERE {ec.column} = ''")  # noqa: S608
            )
        conn.commit()
    return counts


# ---------------------------------------------------------------------------
# delete-orphans
# ---------------------------------------------------------------------------


def find_orphan_rows(engine: Engine, fk: ForeignKey) -> list[tuple]:
    """Return rows from ``fk.table`` whose FK target does not exist.

    The result is a list of ``(id, fk_column_value)`` tuples — minimal
    enough to print in a report, sufficient to identify the rows for
    deletion. Returns an empty list if either table is missing.
    """
    if not (_table_exists(engine, fk.table) and _table_exists(engine, fk.parent_table)):
        return []
    sql = sa.text(
        f"SELECT id, {fk.column} FROM {fk.table} "  # noqa: S608
        f"WHERE {fk.column} IS NOT NULL "
        f"AND {fk.column} NOT IN (SELECT {fk.parent_column} FROM {fk.parent_table})"
    )
    with _connect(engine) as conn:
        return [tuple(row) for row in conn.execute(sql)]


def delete_orphan_rows(engine: Engine, apply: bool) -> dict[str, int]:
    """Find (and optionally delete) every orphan-FK row across all checks.

    Returns:
        Per-check count of rows that were (or would be) deleted, keyed by
        ``"<table>.<column>"`` so the report is unambiguous when one table
        has multiple FK columns.
    """
    deleted: dict[str, int] = {}
    for fk in ORPHAN_FK_CHECKS:
        rows = find_orphan_rows(engine, fk)
        key = f"{fk.table}.{fk.column}"
        deleted[key] = len(rows)
        if not apply or not rows:
            continue
        ids = [row[0] for row in rows]
        with _connect(engine) as conn:
            # Chunk to keep the IN clause sane on very large lists.
            for chunk_start in range(0, len(ids), 500):
                chunk = ids[chunk_start : chunk_start + 500]
                placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
                conn.execute(
                    sa.text(f"DELETE FROM {fk.table} WHERE id IN ({placeholders})"),  # noqa: S608
                    {f"id{i}": v for i, v in enumerate(chunk)},
                )
            conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


def find_duplicate_groups(engine: Engine, rule: DedupeRule) -> list[tuple]:
    """Return one row per duplicate group: ``(*column_values, count)``."""
    if not _table_exists(engine, rule.table):
        return []
    cols = ", ".join(rule.columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in rule.columns)
    sql = sa.text(
        f"SELECT {cols}, COUNT(*) AS n "  # noqa: S608
        f"FROM {rule.table} "
        f"WHERE {not_null} "
        f"GROUP BY {cols} "
        f"HAVING COUNT(*) > 1 "
        f"ORDER BY n DESC"
    )
    with _connect(engine) as conn:
        return [tuple(row) for row in conn.execute(sql)]


def _incoming_fk_columns(engine: Engine, table: str) -> list[tuple[str, str]]:
    """Return ``(child_table, child_column)`` for every FK that references ``table``.

    Implemented via SQLite's ``pragma_foreign_key_list`` so the result
    automatically reflects whatever FKs currently exist — no need to
    hard-code the relationship map.
    """
    with _connect(engine) as conn:
        rows = conn.execute(
            sa.text(
                'SELECT m.name AS child_table, fk."from" AS child_col '
                "FROM sqlite_master m, pragma_foreign_key_list(m.name) fk "
                "WHERE m.type = 'table' AND fk.\"table\" = :parent"
            ),
            {"parent": table},
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _per_group_keepers_and_deletes(engine: Engine, rule: DedupeRule) -> list[tuple[Any, list[Any]]]:
    """Return ``[(keeper_id, [delete_ids, ...]), ...]`` per duplicate group.

    The ``keeper_id`` is the lowest ``id_column`` value in the group; every
    other row in that group is a deletion candidate.
    """
    cols = ", ".join(rule.columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in rule.columns)
    sql = sa.text(
        f"SELECT {rule.id_column}, {cols} FROM {rule.table} "  # noqa: S608
        f"WHERE {not_null} ORDER BY {rule.id_column}"
    )
    groups: dict[tuple, list] = {}
    with _connect(engine) as conn:
        for row in conn.execute(sql):
            key = tuple(row[1:])
            groups.setdefault(key, []).append(row[0])

    result: list[tuple[Any, list[Any]]] = []
    for ids in groups.values():
        if len(ids) > 1:
            result.append((ids[0], ids[1:]))  # min(id) is first because ORDER BY
    return result


def delete_duplicate_rows(engine: Engine, apply: bool) -> dict[str, int]:
    """For every dedupe rule, count or delete the surplus rows per group.

    Default policy: keep the row with the lowest ``id`` (or ``uuid``) per
    group, delete the rest. For tables whose ``child_handling`` is
    ``"reassign"`` the script first updates every child row that
    references a deletion candidate so it points at the keeper instead;
    for ``"refuse"`` tables it warns and skips.

    Returns per-rule count of surplus rows that were (or would be)
    deleted. ``"refuse"`` rules report counts but never delete.
    """
    deleted: dict[str, int] = {}
    for rule in DEDUPE_RULES:
        if not _table_exists(engine, rule.table):
            continue
        cols = ", ".join(rule.columns)
        key = f"{rule.table}({cols})"
        groups = _per_group_keepers_and_deletes(engine, rule)
        ids_to_delete = [d for (_, dels) in groups for d in dels]
        deleted[key] = len(ids_to_delete)
        if not ids_to_delete:
            continue

        if rule.child_handling == "refuse":
            print(
                f"  REFUSED {key}: {len(ids_to_delete)} duplicate row(s) found, "
                "but auto-deleting would orphan or conflate user-owned data. "
                "Merge the accounts manually before re-running."
            )
            continue

        if not apply:
            continue

        with _connect(engine) as conn:
            if rule.child_handling == "reassign":
                children = _incoming_fk_columns(engine, rule.table)
                for keeper, dels in groups:
                    if not dels:
                        continue
                    placeholders = ", ".join(f":id{i}" for i in range(len(dels)))
                    params = {f"id{i}": v for i, v in enumerate(dels)} | {"keeper": keeper}
                    for child_table, child_col in children:
                        conn.execute(
                            sa.text(
                                f"UPDATE {child_table} SET {child_col} = :keeper "  # noqa: S608
                                f"WHERE {child_col} IN ({placeholders})"
                            ),
                            params,
                        )

            elif rule.child_handling == "leaf":
                children = _incoming_fk_columns(engine, rule.table)
                if children:
                    print(
                        f"  WARN {key}: declared as 'leaf' but discovered {len(children)} "
                        f"incoming FK reference(s) ({', '.join(f'{t}.{c}' for t, c in children)}). "
                        "Skipping to avoid orphaning child rows. Update the rule's "
                        "child_handling to 'reassign' if appropriate."
                    )
                    deleted[key] = 0
                    continue

            for chunk_start in range(0, len(ids_to_delete), 500):
                chunk = ids_to_delete[chunk_start : chunk_start + 500]
                placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
                conn.execute(
                    sa.text(
                        f"DELETE FROM {rule.table} "  # noqa: S608
                        f"WHERE {rule.id_column} IN ({placeholders})"
                    ),
                    {f"id{i}": v for i, v in enumerate(chunk)},
                )
            conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(engine: Engine) -> None:
    """Print a comprehensive read-only audit covering all three issue classes."""
    print("=== empty-string emails (would block UNIQUE(email)) ===")
    counts = find_blank_emails(engine)
    if not any(counts.values()):
        print("  none")
    else:
        for table, n in counts.items():
            print(f"  {table:<24}  {n} row(s) with email = ''")
        print("  fix: cleanup_data_quality.py normalize-emails --apply")

    print("\n=== orphan FK references (rows pointing at deleted parents) ===")
    any_orphans = False
    for fk in ORPHAN_FK_CHECKS:
        rows = find_orphan_rows(engine, fk)
        if not rows:
            continue
        any_orphans = True
        print(f"  {fk.table}.{fk.column} → {fk.parent_table}.{fk.parent_column}: {len(rows)} orphan(s)")
        sample = [f"id={r[0]}, {fk.column}={r[1]!r}" for r in rows[:5]]
        for s in sample:
            print(f"      {s}")
        if len(rows) > 5:
            print(f"      ... +{len(rows) - 5} more")
        print(f"      why safe: {fk.why_safe_to_delete}")
    if not any_orphans:
        print("  none")
    else:
        print("  fix: cleanup_data_quality.py delete-orphans --apply")

    print("\n=== duplicate rows on (would-be-)unique column groups ===")
    any_dups = False
    for rule in DEDUPE_RULES:
        groups = find_duplicate_groups(engine, rule)
        if not groups:
            continue
        any_dups = True
        cols = ", ".join(rule.columns)
        print(f"  {rule.table}({cols}): {len(groups)} duplicate group(s)")
        for row in groups[:5]:
            *values, n = row
            print(f"      {dict(zip(rule.columns, values))} × {n}")
        if len(groups) > 5:
            print(f"      ... +{len(groups) - 5} more groups")
    if not any_dups:
        print("  none")
    else:
        print("  fix: cleanup_data_quality.py dedupe --apply")
        print("       (default policy: keep MIN(id|uuid) per group, delete the rest)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_action_summary(title: str, counts: dict[str, int], apply: bool, fix_hint: str | None = None) -> None:
    """Render the per-table summary that each apply / dry-run command prints."""
    total = sum(counts.values())
    mode = "APPLIED" if apply else "DRY RUN"
    print(f"=== {title} ({mode}) ===")
    if total == 0:
        print("  no rows affected.")
        return
    for label, n in counts.items():
        if n:
            verb = "deleted" if apply else "would delete"
            if "email" in title.lower():
                verb = "updated" if apply else "would update"
            print(f"  {label:<40}  {verb} {n}")
    if not apply and fix_hint:
        print(f"\n  Re-run with --apply to actually write. {fix_hint}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        help="Path or SQLAlchemy URL to operate on. Defaults to instance/tournament.db.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("report", help="Read-only audit of all data-quality issues.")

    p_emails = sub.add_parser(
        "normalize-emails",
        help='Convert email = "" to email = NULL on players and teams.',
    )
    p_emails.add_argument("--apply", action="store_true", help="Actually update rows.")

    p_orphans = sub.add_parser(
        "delete-orphans",
        help="Delete rows whose FK target no longer exists.",
    )
    p_orphans.add_argument("--apply", action="store_true", help="Actually delete rows.")

    p_dedupe = sub.add_parser(
        "dedupe",
        help="Delete duplicate rows; keeps the lowest-id row per group.",
    )
    p_dedupe.add_argument("--apply", action="store_true", help="Actually delete rows.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. See module docstring for usage and exit codes."""
    args = _build_parser().parse_args(argv)
    url = _resolve_database_url(args.db)
    print(f"Database: {url}")
    engine = sa.create_engine(url)

    try:
        if args.command == "report":
            report(engine)
            return 0

        if args.command == "normalize-emails":
            counts = normalize_blank_emails(engine, apply=args.apply)
            _print_action_summary(
                "normalize empty-string emails to NULL",
                counts,
                apply=args.apply,
                fix_hint="(lossless: '' and NULL are semantically equivalent here.)",
            )
            return 0

        if args.command == "delete-orphans":
            counts = delete_orphan_rows(engine, apply=args.apply)
            _print_action_summary(
                "delete orphan FK rows",
                counts,
                apply=args.apply,
                fix_hint="(review the per-row report from `report` first.)",
            )
            return 0

        if args.command == "dedupe":
            counts = delete_duplicate_rows(engine, apply=args.apply)
            _print_action_summary(
                "deduplicate (keep min-id per group)",
                counts,
                apply=args.apply,
                fix_hint="(review the duplicate groups from `report` first.)",
            )
            return 0

        # argparse should have rejected this already.
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
