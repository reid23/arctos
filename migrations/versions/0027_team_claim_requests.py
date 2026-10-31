"""Add team claim requests for historical tournaments.

A real team can ask to claim a placeholder. The organiser approves or
declines. Several teams may request the same placeholder.

Player roster requests reuse ``player_registrations.status``, which is a
plain string column, so the ``REQUESTED`` value needs no schema change.

Revision ID: 0027_team_claim_requests
Revises: 0026_historical_tournaments
Create Date: 2026-10-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027_team_claim_requests"
down_revision: Union[str, Sequence[str], None] = "0026_historical_tournaments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_claim_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("placeholder_team", sa.String(length=50), nullable=False),
        sa.Column("requesting_team", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["placeholder_team"], ["teams.id"]),
        sa.ForeignKeyConstraint(["requesting_team"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("placeholder_team", "requesting_team"),
    )
    op.create_index(
        "ix_team_claim_requests_placeholder_team",
        "team_claim_requests",
        ["placeholder_team"],
        unique=False,
    )
    op.create_index(
        "ix_team_claim_requests_requesting_team",
        "team_claim_requests",
        ["requesting_team"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_team_claim_requests_requesting_team", table_name="team_claim_requests")
    op.drop_index("ix_team_claim_requests_placeholder_team", table_name="team_claim_requests")
    op.drop_table("team_claim_requests")
