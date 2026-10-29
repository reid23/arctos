"""Allow player accounts to manage teams.

Revision ID: 0026_team_managers
Revises: 0025_player_profile_details
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_team_managers"
down_revision = "0025_player_profile_details"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_managers",
        sa.Column("team_id", sa.String(50), sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("player_id", sa.String(50), sa.ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_team_managers_player_id", "team_managers", ["player_id"])


def downgrade():
    op.drop_table("team_managers")
