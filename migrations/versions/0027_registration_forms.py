"""Store optional response-sheet requirements on the shared registration config.

Existing events and leagues retain their registration behavior because all
six fields default to null. A private per-configuration signing key supports
personalized form links without storing player responses.

Revision ID: 0027_registration_forms
Revises: 0026_team_managers
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_registration_forms"
down_revision = "0026_team_managers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("registrable_configs", sa.Column("registration_form_url", sa.String(500), nullable=True))
    op.add_column("registrable_configs", sa.Column("registration_form_entry_id", sa.String(20), nullable=True))
    op.add_column("registrable_configs", sa.Column("registration_form_sheet_id", sa.String(255), nullable=True))
    op.add_column("registrable_configs", sa.Column("registration_form_sheet_gid", sa.Integer(), nullable=True))
    op.add_column("registrable_configs", sa.Column("registration_form_code_column", sa.String(200), nullable=True))
    op.add_column("registrable_configs", sa.Column("registration_form_signing_key", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("registrable_configs", "registration_form_signing_key")
    op.drop_column("registrable_configs", "registration_form_code_column")
    op.drop_column("registrable_configs", "registration_form_sheet_gid")
    op.drop_column("registrable_configs", "registration_form_sheet_id")
    op.drop_column("registrable_configs", "registration_form_entry_id")
    op.drop_column("registrable_configs", "registration_form_url")
