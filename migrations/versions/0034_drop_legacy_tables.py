"""Drop three tables left behind by early versions of Arctos.

``team_invitations`` tracked a player's request to join a team; player
registration statuses replaced it. ``playerrecords`` and ``teamrecords`` held
head-referee notes and discipline; match notes with penalty types replaced
them. Their models were removed long ago, but databases created before
migrations still have the tables, and their foreign keys to ``players`` and
``teams`` block deleting a merged account.

Revision ID: 0034_drop_legacy_tables
Revises: 0033_account_merges
"""

from alembic import op

revision = "0034_drop_legacy_tables"
down_revision = "0033_account_merges"
branch_labels = None
depends_on = None

LEGACY_TABLES = ("team_invitations", "playerrecords", "teamrecords")


def upgrade():
    for table in LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade():
    # No migration ever created these tables and no code reads them, so
    # there is nothing to restore.
    pass
