"""Add finalized flag to sidecomps.

Adds ``finalized`` boolean. Defaults to ``False``. Once set, the side
competition cannot be activated again and the public results page is marked
Final rather than Unofficial.

Revision ID: 0019_sidecomp_finalized
Revises: 0018_sidecomp_active
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_sidecomp_finalized"
down_revision: Union[str, Sequence[str], None] = "0018_sidecomp_active"
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
                    "finalized",
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
            batch.drop_column("finalized")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
