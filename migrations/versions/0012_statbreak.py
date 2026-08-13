"""Add STATBREAK to the schedule-type uniqueness groups.

STATBREAK is a statically-scheduled break: user-supplied start time, never
moved by the solver, auto-completed once its window elapses. Like BREAK and
JOIN it is unique per (name, event, field) rather than per (name, event), so
the two partial unique indexes on ``matches`` are recreated with STATBREAK
included in the with-field predicate.

No CHECK constraint rebuild is needed: SQLAlchemy's ``Enum`` did not create a
constraint for ``matches.schedule_type`` on SQLite (the column is plain TEXT;
``Enum(create_constraint=...)`` defaults to False), so only the indexes encode
the type grouping.

Revision ID: 0012_statbreak
Revises: 0011_bracket_placements
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_statbreak"
down_revision: Union[str, Sequence[str], None] = "0011_bracket_placements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("unique_with_field", table_name="matches")
    op.drop_index("unique_without_field", table_name="matches")
    op.create_index(
        "unique_with_field",
        "matches",
        ["name", "event", "field"],
        unique=True,
        sqlite_where=sa.text("schedule_type IN ('BREAK', 'JOIN', 'STATBREAK')"),
    )
    op.create_index(
        "unique_without_field",
        "matches",
        ["name", "event"],
        unique=True,
        sqlite_where=sa.text("schedule_type NOT IN ('BREAK', 'JOIN', 'STATBREAK')"),
    )


def downgrade() -> None:
    # Demote any STATBREAK rows to plain BREAK first: the pre-0012 predicates
    # would otherwise put STATBREAK rows into the without-field uniqueness
    # group, where same-name per-field break rows collide.
    op.execute("UPDATE matches SET schedule_type = 'BREAK' WHERE schedule_type = 'STATBREAK'")
    op.drop_index("unique_with_field", table_name="matches")
    op.drop_index("unique_without_field", table_name="matches")
    op.create_index(
        "unique_with_field",
        "matches",
        ["name", "event", "field"],
        unique=True,
        sqlite_where=sa.text("schedule_type IN ('BREAK', 'JOIN')"),
    )
    op.create_index(
        "unique_without_field",
        "matches",
        ["name", "event"],
        unique=True,
        sqlite_where=sa.text("schedule_type NOT IN ('BREAK', 'JOIN')"),
    )
