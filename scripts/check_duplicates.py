#!/usr/bin/env python3
"""scripts/check_duplicates.py — find rows that should be unique together.

Several pairs of columns in the Arctos schema *should* be unique together
but currently have no database-level constraint enforcing it. Examples:

* ``team_registrations(team, event)`` — a team registered twice for the same
  tournament double-counts toward ``n_max_teams``.
* playable ``matches(name, event)`` rows — the skip-condition DSL looks
  matches up by name; duplicates make the lookup ambiguous.
* ``BREAK`` / ``JOIN`` matches ``(name, event, field)`` — operators need to
  reuse names across fields, but duplicates on the same field are still
  ambiguous.
* ``fields(name, event)`` — ``Match.field`` references fields by name; two
  same-named fields in one event are indistinguishable.

The full list of (table, columns) pairs lives in the ``CHECKS`` constant
below. The plan is to add a real ``UNIQUE`` constraint for each pair, but
adding a unique constraint to a table that already contains duplicates
fails at migration time. This script reports the offenders so they can be
merged or deleted before the constraint is added.

Usage::

    uv run python scripts/check_duplicates.py            # check default DB
    uv run python scripts/check_duplicates.py --db /path/to/snapshot.db
    SQLALCHEMY_DATABASE_URI=sqlite:////tmp/test.db \\
        uv run python scripts/check_duplicates.py        # via env var

Pre-conditions:
    * The database file exists and is readable by the current user.
    * The tables listed in ``CHECKS`` exist (tables that don't are skipped
      with a note rather than failing).

Exit codes:
    * ``0`` — every check returned zero duplicate groups.
    * ``1`` — at least one duplicate group was found. The offending column
      values are printed so an operator can merge or delete the conflicting
      rows before any unique-constraint migration is applied.

CI integration:
    Wire this into the same job that runs the test suite so duplicates
    introduced by application code are caught early, before they block a
    schema change.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DuplicateCheck:
    """One duplicate-detection query.

    Attributes:
        table: SQL table the check is run against.
        columns: Tuple of column names that should be unique together.
        why: Human-readable note explaining the impact of duplicates, so
            operators reading the report do not have to dig into the schema
            to understand why a duplicate matters.
        where: Optional SQL predicate limiting which rows participate in the
            uniqueness rule. Used for partial unique indexes such as the
            schedule-type-specific match constraints.
    """

    table: str
    columns: tuple[str, ...]
    why: str
    where: str | None = None


# Each entry is a (table, columns) pair that should logically be unique together
# but currently has no database-level UNIQUE constraint. Add new entries here
# whenever you identify another such pair; the rest of the script picks them up
# automatically.
CHECKS: Sequence[DuplicateCheck] = (
    DuplicateCheck(
        table="team_registrations",
        columns=("team", "event"),
        why="A team can register for the same tournament twice, double-counting toward n_max_teams.",
    ),
    DuplicateCheck(
        table="team_registrations",
        columns=("team", "league_id"),
        why="Same as above, for league registrations.",
    ),
    DuplicateCheck(
        table="player_registrations",
        columns=("player", "event"),
        why="A player can register multiple times per event.",
    ),
    DuplicateCheck(
        table="player_registrations",
        columns=("player", "league_id"),
        why="Same as above, for league registrations.",
    ),
    DuplicateCheck(
        table="headrefs",
        columns=("player", "event"),
        why="Duplicate head-ref rows break head-ref counting logic.",
    ),
    DuplicateCheck(
        table="matches",
        columns=("name", "event"),
        why="Playable matches are looked up by name in scheduling logic; duplicates make the lookup ambiguous.",
        where="schedule_type NOT IN ('BREAK', 'JOIN')",
    ),
    DuplicateCheck(
        table="matches",
        columns=("name", "event", "field"),
        why="BREAK/JOIN schedule rows may reuse a name across fields, but duplicates on the same field remain ambiguous.",
        where="schedule_type IN ('BREAK', 'JOIN')",
    ),
    DuplicateCheck(
        table="tags",
        columns=("name", "event"),
        why="ASS expressions look up teams by tag name; duplicates cause silent wrong results.",
    ),
    DuplicateCheck(
        table="fields",
        columns=("name", "event"),
        why="Match.field references fields by name string; duplicates are indistinguishable.",
    ),
)


def _resolve_database_url(cli_db: str | None) -> str:
    """Pick a database URL using the same precedence as ``migrations/env.py``.

    Args:
        cli_db: The value passed via ``--db`` on the command line, or ``None``.

    Returns:
        A SQLAlchemy URL string. Order of precedence: ``--db`` flag, then
        ``SQLALCHEMY_DATABASE_URI`` from the environment, then the default
        ``sqlite:///<repo>/instance/tournament.db``.
    """
    if cli_db:
        if cli_db.startswith("sqlite:") or "://" in cli_db:
            return cli_db
        return f"sqlite:///{Path(cli_db).expanduser().resolve()}"
    env = os.environ.get("SQLALCHEMY_DATABASE_URI")
    if env:
        return env
    return f"sqlite:///{PROJECT_ROOT / 'instance' / 'tournament.db'}"


def _table_exists(engine: Engine, table_name: str) -> bool:
    """Return True iff ``table_name`` exists in the connected database.

    Used so that running this script against a freshly created database
    (where some tables may not yet exist) skips the check with a warning
    instead of raising an opaque ``OperationalError``.
    """
    inspector = sa.inspect(engine)
    return table_name in inspector.get_table_names()


def _run_check(engine: Engine, check: DuplicateCheck) -> list[tuple]:
    """Execute one duplicate-detection query and return offending rows.

    Rows where any of the grouping columns is ``NULL`` are excluded because
    SQLite's ``UNIQUE`` constraint treats ``NULL`` values as distinct (SQL-
    standard ``UNIQUE`` semantics), so such rows would not violate a future
    unique constraint and reporting them would be a false positive. This
    matters most for the polymorphic registration tables where every row has
    exactly one of ``event`` / ``league_id`` set.

    Args:
        engine: A live SQLAlchemy :class:`~sqlalchemy.engine.Engine`.
        check: The :class:`DuplicateCheck` to run.

    Returns:
        A list of tuples, one per duplicate group, of the form
        ``(*column_values, count)``. Empty if the table is clean.
    """
    cols = ", ".join(check.columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in check.columns)
    where_parts = [not_null]
    if check.where:
        where_parts.append(f"({check.where})")
    where_clause = " AND ".join(where_parts)
    sql = sa.text(
        f"SELECT {cols}, COUNT(*) AS n "  # noqa: S608 — column names are static, not user input
        f"FROM {check.table} "
        f"WHERE {where_clause} "
        f"GROUP BY {cols} "
        f"HAVING COUNT(*) > 1 "
        f"ORDER BY n DESC"
    )
    with engine.connect() as conn:
        result = conn.execute(sql)
        return [tuple(row) for row in result]


def _format_report(check: DuplicateCheck, rows: Iterable[tuple]) -> str:
    """Render a human-readable report for one failing check.

    The output format is intentionally pasteable into a chat or commit
    message: one line of context, then a markdown table of duplicates.
    """
    lines = [
        f"  table:   {check.table}",
        f"  columns: {', '.join(check.columns)}",
        f"  impact:  {check.why}",
        "  duplicates:",
    ]
    if check.where:
        lines.insert(2, f"  filter:  {check.where}")
    header = "    " + " | ".join(list(check.columns) + ["count"])
    lines.append(header)
    lines.append("    " + "-" * (len(header) - 4))
    for row in rows:
        lines.append("    " + " | ".join(repr(v) for v in row))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run every check; return 0 if clean, 1 if any duplicates were found.

    Args:
        argv: Optional argv override (for tests). Defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` when every table is clean (or all relevant tables are missing),
        ``1`` when at least one duplicate group was reported.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        help="Path or SQLAlchemy URL to check. Defaults to instance/tournament.db.",
    )
    args = parser.parse_args(argv)

    url = _resolve_database_url(args.db)
    print(f"Checking duplicates against: {url}")
    engine = sa.create_engine(url)

    try:
        any_failures = False
        skipped: list[str] = []
        for check in CHECKS:
            if not _table_exists(engine, check.table):
                skipped.append(check.table)
                continue
            duplicates = _run_check(engine, check)
            if duplicates:
                any_failures = True
                print(f"\n[FAIL] {check.table} ({', '.join(check.columns)}): {len(duplicates)} duplicate group(s)")
                print(_format_report(check, duplicates))
            else:
                print(f"[ ok ] {check.table} ({', '.join(check.columns)})")

        if skipped:
            unique_skipped = sorted(set(skipped))
            print(
                f"\nNote: skipped {len(unique_skipped)} table(s) that do not exist in this DB: "
                f"{', '.join(unique_skipped)}"
            )
    finally:
        engine.dispose()

    if any_failures:
        print(
            "\nResult: duplicates present. Resolve them (merge or delete the "
            "conflicting rows) before adding a UNIQUE constraint to the affected table."
        )
        return 1
    print("\nResult: all duplicate checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
