"""Players' Stripe payout accounts, and online payments on registration configs.

Revision ID: 0033_payout_accounts
Revises: 0032_paid_entries
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_payout_accounts"
down_revision = "0032_paid_entries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payout_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_player", sa.String(50), sa.ForeignKey("players.id"), nullable=False, unique=True),
        sa.Column("stripe_account_id", sa.String(255), nullable=False, unique=True),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_currency", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.add_column(
        "registrable_configs",
        sa.Column("online_payments_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Native ADD COLUMN keeps the foreign key without a table rebuild.
    op.get_bind().exec_driver_sql(
        "ALTER TABLE registrable_configs ADD COLUMN payout_account_id INTEGER REFERENCES payout_accounts (id)"
    )
    op.create_index("ix_registrable_configs_payout_account_id", "registrable_configs", ["payout_account_id"])


def downgrade():
    bind = op.get_bind()
    op.drop_index("ix_registrable_configs_payout_account_id", "registrable_configs")
    # SQLite can't drop a foreign key column in place.
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("registrable_configs") as batch:
            batch.drop_column("payout_account_id")
            batch.drop_column("online_payments_enabled")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")
    op.drop_table("payout_accounts")
