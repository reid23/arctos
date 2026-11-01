"""Add historical tournaments: records of past events entered after the fact.

Organisers can record tournaments that were never run live in Arctos. Teams
at such an event usually have no Arctos account yet, so they are entered as
placeholder team rows; a real team later claims a placeholder and inherits
its results.

* ``tournaments.historical`` marks the event as a historical record.
* ``teams.placeholder_event`` marks a team row as a placeholder belonging to
  that historical tournament.
* ``team_claim_invitations`` holds an organiser's invitation for a real team
  to claim a placeholder.

Historical rosters also use a new ``INVITED`` player registration status.
``player_registrations.status`` is a plain string column with no CHECK
constraint, so the new value needs no schema change.

Revision ID: 0026_historical_tournaments
Revises: generic_schedule_items
Create Date: 2026-10-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_historical_tournaments"
down_revision: Union[str, Sequence[str], None] = "generic_schedule_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("tournaments") as batch:
            batch.add_column(sa.Column("historical", sa.Boolean(), nullable=False, server_default=sa.false()))
        with op.batch_alter_table("teams") as batch:
            batch.add_column(sa.Column("placeholder_event", sa.String(length=100), nullable=True))
            batch.create_foreign_key("fk_teams_placeholder_event", "tournaments", ["placeholder_event"], ["url"])
            batch.create_index("ix_teams_placeholder_event", ["placeholder_event"], unique=False)
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")

    op.create_table(
        "team_claim_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("placeholder_team", sa.String(length=50), nullable=False),
        sa.Column("invited_team", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["placeholder_team"], ["teams.id"]),
        sa.ForeignKeyConstraint(["invited_team"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("placeholder_team"),
    )
    op.create_index(
        "ix_team_claim_invitations_invited_team",
        "team_claim_invitations",
        ["invited_team"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_team_claim_invitations_invited_team", table_name="team_claim_invitations")
    op.drop_table("team_claim_invitations")

    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("teams") as batch:
            batch.drop_index("ix_teams_placeholder_event")
            # SQLite batch reflection does not keep the name given to this
            # foreign key, so dropping the column removes the key with it.
            batch.drop_column("placeholder_event")
        with op.batch_alter_table("tournaments") as batch:
            batch.drop_column("historical")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
