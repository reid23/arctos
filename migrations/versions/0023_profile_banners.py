"""Add optional player and team profile banners without changing avatars.

Revision ID: 0023_profile_banners
Revises: 0022_oauth_provider
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_profile_banners"
down_revision = "0022_oauth_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable additive columns preserve existing accounts and profile photos.
    op.add_column("players", sa.Column("banner_photo", sa.String(length=255), nullable=True))
    op.add_column("teams", sa.Column("banner_photo", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("teams", "banner_photo")
    op.drop_column("players", "banner_photo")
