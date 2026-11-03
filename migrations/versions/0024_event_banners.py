"""Add optional tournament and league banners without changing event data.

Revision ID: 0024_event_banners
Revises: 0023_profile_banners
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_event_banners"
down_revision = "0023_profile_banners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tournaments", sa.Column("banner_photo", sa.String(length=255), nullable=True))
    op.add_column("leagues", sa.Column("banner_photo", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("leagues", "banner_photo")
    op.drop_column("tournaments", "banner_photo")
