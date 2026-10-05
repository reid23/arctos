"""Add active flag to sidecomps.

Adds ``active`` boolean gate for logging results. Defaults to ``False`` so
existing comps remain closed to result entry until a TO explicitly activates
them.

Revision ID: 0018_sidecomp_active
Revises: 0017_sidecomp_results_run
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_sidecomp_active"
down_revision: Union[str, Sequence[str], None] = "0017_sidecomp_results_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("sidecomps") as batch:
            batch.add_column(
                sa.Column(
                    "active",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("sidecomps") as batch:
            batch.drop_column("active")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
