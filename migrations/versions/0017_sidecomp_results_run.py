"""Refactor sidecomp results and add top-N results setting.

Drops the legacy ``sidecompresults`` shape (player id string + scanner_id)
and recreates it with uuid PK, registration FKs, points, ref, flagged, and
valid. Adds ``only_show_top_n_results`` to ``sidecomps``.

Revision ID: 0017_sidecomp_results_run
Revises: 0016_sidecomp_weapons
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_sidecomp_results_run"
down_revision: Union[str, Sequence[str], None] = "0016_sidecomp_weapons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("sidecomps") as batch_op:
            batch_op.add_column(sa.Column("only_show_top_n_results", sa.Integer(), nullable=True))

        # Legacy result rows (if any) cannot be mapped to the new schema —
        # drop and recreate.
        op.drop_table("sidecompresults")
        op.create_table(
            "sidecompresults",
            sa.Column("uuid", sa.String(length=36), nullable=False),
            sa.Column("comp", sa.Integer(), nullable=False),
            sa.Column("player", sa.Integer(), nullable=False),
            sa.Column("opponent", sa.Integer(), nullable=True),
            sa.Column("stamp", sa.DateTime(), nullable=False),
            sa.Column("points", sa.Integer(), nullable=False),
            sa.Column("ref", sa.String(length=50), nullable=False),
            sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.ForeignKeyConstraint(["comp"], ["sidecomps.id"]),
            sa.ForeignKeyConstraint(["player"], ["sidecomp_registrations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["opponent"], ["sidecomp_registrations.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("uuid"),
        )
        op.create_index("ix_sidecompresults_comp", "sidecompresults", ["comp"])
        op.create_index("ix_sidecompresults_player", "sidecompresults", ["player"])
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.drop_index("ix_sidecompresults_player", table_name="sidecompresults")
        op.drop_index("ix_sidecompresults_comp", table_name="sidecompresults")
        op.drop_table("sidecompresults")
        op.create_table(
            "sidecompresults",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("comp", sa.Integer(), nullable=False),
            sa.Column("player", sa.String(length=50), nullable=False),
            sa.Column("scanner_id", sa.Integer(), nullable=True),
            sa.Column("stamp", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["comp"], ["sidecomps.id"]),
            sa.ForeignKeyConstraint(["player"], ["players.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("sidecomps") as batch_op:
            batch_op.drop_column("only_show_top_n_results")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
