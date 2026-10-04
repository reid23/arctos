"""Add weapon selection to side competitions.

Adds ``allowed_weapons`` (JSON list of Pompfen names) to ``sidecomps`` and
``weapon`` (integer Pompfen value) to ``sidecomp_registrations``.

Revision ID: 0016_sidecomp_weapons
Revises: 0015_script_vars_cascade
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_sidecomp_weapons"
down_revision: Union[str, Sequence[str], None] = "0015_script_vars_cascade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_ALLOWED = '["CHAIN","LONG","QTIP","STAFF","BOARD","FLOURENTINE","SKULL","UNARMED"]'


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("sidecomps") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "allowed_weapons",
                    sa.Text(),
                    nullable=False,
                    server_default=_DEFAULT_ALLOWED,
                )
            )

        with op.batch_alter_table("sidecomp_registrations") as batch_op:
            batch_op.add_column(sa.Column("weapon", sa.Integer(), nullable=True))

        # Backfill existing registrations as UNARMED (7).
        bind.exec_driver_sql("UPDATE sidecomp_registrations SET weapon = 7 WHERE weapon IS NULL")

        with op.batch_alter_table("sidecomp_registrations") as batch_op:
            batch_op.alter_column("weapon", existing_type=sa.Integer(), nullable=False)
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("sidecomp_registrations") as batch_op:
            batch_op.drop_column("weapon")
        with op.batch_alter_table("sidecomps") as batch_op:
            batch_op.drop_column("allowed_weapons")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
