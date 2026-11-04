"""OpenID Connect signing key and authorization-code nonce.

Revision ID: 0031_oidc
Revises: 0030_sidecomp_track_matchups
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_oidc"
down_revision = "0030_sidecomp_track_matchups"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("oauth_authorization_codes", sa.Column("nonce", sa.Text(), nullable=True))
    op.create_table(
        "oidc_signing_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kid", sa.String(length=255), nullable=False),
        sa.Column("private_pem", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("oidc_signing_keys")
    op.drop_column("oauth_authorization_codes", "nonce")
