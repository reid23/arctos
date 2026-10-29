"""Add optional player pronouns and selected pompfen without changing profiles.

Revision ID: 0025_player_profile_details
Revises: 0024_event_banners
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_player_profile_details"
down_revision = "0024_event_banners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("players", sa.Column("pronouns", sa.String(length=64), nullable=True))
    op.add_column("players", sa.Column("pompfen", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("players", "pompfen")
    op.drop_column("players", "pronouns")
