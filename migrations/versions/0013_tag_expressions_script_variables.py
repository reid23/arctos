"""Tag expressions and tournament script variables.

Two additions for ASS (Arctos Schedule Script) scripting support:

* ``tags.expression`` — nullable TEXT column holding an ASS expression whose
  result type includes TEAM. The manually assigned ``tags.team`` column acts
  as an override; when unset, the expression is evaluated to resolve the tag.
* ``script_variables`` — new table of per-tournament ASS variables
  (identifier name + expression), unique per (event, name). Variables are
  bound into the interpreter environment for every expression evaluated in
  the tournament.

SQLite supports plain ADD COLUMN for a nullable column with no default, so no
batch_alter is needed for the upgrade; the downgrade drops the column with
batch_alter (table rebuild) for SQLite compatibility.

Revision ID: 0013_tag_expr_script_vars
Revises: 0012_statbreak
Create Date: 2026-08-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_tag_expr_script_vars"
down_revision: Union[str, Sequence[str], None] = "0012_statbreak"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tags", sa.Column("expression", sa.Text(), nullable=True))
    op.create_table(
        "script_variables",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["event"], ["tournaments.url"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event", "name", name="uq_script_variables_event_name"),
    )


def downgrade() -> None:
    op.drop_table("script_variables")
    with op.batch_alter_table("tags") as batch_op:
        batch_op.drop_column("expression")
