"""Add ON DELETE CASCADE for script_variables.event → tournaments.url.

Revision ID: 0015_script_vars_cascade
Revises: 0014_tag_expr_script_vars
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_script_vars_cascade"
down_revision: Union[str, Sequence[str], None] = "0014_tag_expr_script_vars"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recreate(ondelete: str | None) -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        fk_kwargs = {"ondelete": ondelete} if ondelete else {}
        op.create_table(
            "_script_variables_new",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("expression", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["event"], ["tournaments.url"], **fk_kwargs),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event", "name", name="uq_script_variables_event_name"),
        )
        op.execute(
            sa.text(
                "INSERT INTO _script_variables_new (id, event, name, expression) "
                "SELECT id, event, name, expression FROM script_variables"
            )
        )
        op.drop_table("script_variables")
        op.rename_table("_script_variables_new", "script_variables")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _recreate(ondelete="CASCADE")


def downgrade() -> None:
    _recreate(ondelete=None)
