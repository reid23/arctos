"""Add STATBREAK uniqueness grouping and structural ``group_id``.

STATBREAK is a statically-scheduled break: user-supplied start time, never
moved by the solver, auto-completed once its window elapses. Like BREAK and
JOIN it is unique per (name, event, field) rather than per (name, event), so
the two partial unique indexes on ``matches`` are recreated with STATBREAK
included in the with-field predicate.

Also adds ``matches.group_id``: a stable UUID shared by every row in a
multi-field BREAK/STATBREAK/JOIN group. The break-groups API addresses groups
by this id (not display name), so renaming and ``/`` in names cannot break
routes, and unrelated same-name rows cannot merge.

No CHECK constraint rebuild is needed: SQLAlchemy's ``Enum`` did not create a
constraint for ``matches.schedule_type`` on SQLite (the column is plain TEXT;
``Enum(create_constraint=...)`` defaults to False), so only the indexes encode
the type grouping.

Revision ID: 0013_statbreak_and_group_id
Revises: 0012_video_descope
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_statbreak_and_group_id"
down_revision: Union[str, Sequence[str], None] = "0012_video_descope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("group_id", sa.String(length=36), nullable=True))
    op.create_index("ix_matches_group_id", "matches", ["group_id"])

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
    # Demote any STATBREAK rows to plain BREAK first: the pre-0013 predicates
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

    op.drop_index("ix_matches_group_id", table_name="matches")
    op.drop_column("matches", "group_id")
