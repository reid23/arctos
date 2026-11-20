"""Record merged-away accounts so their ids stay reserved.

Revision ID: 0033_account_merges
Revises: 0032_licenses
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_account_merges"
down_revision = "0032_licenses"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_merges",
        sa.Column("source_id", sa.String(length=50), primary_key=True),
        sa.Column("source_type", sa.String(length=10), nullable=False),
        sa.Column("target_id", sa.String(length=50), nullable=False),
        sa.Column("merged_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_account_merges_target_id", "account_merges", ["target_id"])


def downgrade():
    op.drop_index("ix_account_merges_target_id", "account_merges")
    op.drop_table("account_merges")
