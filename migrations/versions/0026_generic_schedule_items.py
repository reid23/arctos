"""Add fixed-time activities and linked side competitions to schedules.

Revision ID: generic_schedule_items
Revises: 0025_player_profile_details
"""

from alembic import op
import sqlalchemy as sa

revision = "generic_schedule_items"
down_revision = "0025_player_profile_details"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedule_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(100), sa.ForeignKey("tournaments.url", ondelete="CASCADE"), nullable=False),
        sa.Column("sidecomp_id", sa.Integer(), sa.ForeignKey("sidecomps.id", ondelete="CASCADE"), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("created_sidecomp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("event", "request_id", name="uq_schedule_item_request"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("field", sa.String(100), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.CheckConstraint("end_time > start_time", name="ck_schedule_item_positive_duration"),
        sa.CheckConstraint(
            "(sidecomp_id IS NULL AND title IS NOT NULL) OR (sidecomp_id IS NOT NULL AND title IS NULL AND description IS NULL)",
            name="ck_schedule_item_content_source",
        ),
    )
    op.create_index("ix_schedule_items_event", "schedule_items", ["event"])


def downgrade():
    op.drop_table("schedule_items")
