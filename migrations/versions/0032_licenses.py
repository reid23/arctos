"""Voluntary per-event and per-league license keys.

Revision ID: 0032_licenses
Revises: 0031_oidc
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_licenses"
down_revision = "0031_oidc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "licenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("amount_paid_cents", sa.Integer(), nullable=False),
        sa.Column("team_allowance", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("tournament_url", sa.String(length=100), nullable=True),
        sa.Column("league_url", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["tournament_url"], ["tournaments.url"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["league_url"], ["leagues.url"], ondelete="SET NULL"),
        sa.UniqueConstraint("key_id"),
        sa.UniqueConstraint("tournament_url"),
        sa.UniqueConstraint("league_url"),
        sa.CheckConstraint(
            "kind IN ('event', 'league') AND ("
            "(tournament_url IS NULL AND league_url IS NULL) OR "
            "(kind = 'event' AND tournament_url IS NOT NULL AND league_url IS NULL) OR "
            "(kind = 'league' AND league_url IS NOT NULL AND tournament_url IS NULL)"
            ")",
            name="ck_license_holder",
        ),
    )


def downgrade():
    op.drop_table("licenses")
