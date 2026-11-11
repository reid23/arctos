"""Stripe checkout holds, and Stripe details on payments.

Revision ID: 0034_stripe_checkout
Revises: 0033_payout_accounts
"""

from alembic import op
import sqlalchemy as sa

revision = "0034_stripe_checkout"
down_revision = "0033_payout_accounts"
branch_labels = None
depends_on = None

PAYMENT_COLUMNS = (
    "id, team_registration_id, player_registration_id, entry_id, amount_cents, method, reference, notes,"
    " created_at, updated_at"
)
PAYMENT_INDEXES = ("team_registration_id", "player_registration_id", "entry_id")
MANUAL_PAYMENT_CHECK = "(team_registration_id IS NULL) != (player_registration_id IS NULL)"
PAYMENT_CHECK = (
    "(stripe_checkout_session_id IS NULL"
    " AND (team_registration_id IS NULL) != (player_registration_id IS NULL))"
    " OR (stripe_checkout_session_id IS NOT NULL"
    " AND team_registration_id IS NULL AND player_registration_id IS NULL)"
)


def _payments_table_sql(*, with_stripe: bool) -> str:
    stripe_columns = (
        "stripe_checkout_session_id VARCHAR(255) UNIQUE, "
        "stripe_payment_intent_id VARCHAR(255), "
        "stripe_account_id VARCHAR(255), "
        "currency VARCHAR(10), "
        "application_fee_cents INTEGER DEFAULT '0' NOT NULL, "
        "payer_user_id VARCHAR(50), "
        "payer_user_type VARCHAR(10), "
        if with_stripe
        else ""
    )
    check = PAYMENT_CHECK if with_stripe else MANUAL_PAYMENT_CHECK
    return (
        "CREATE TABLE payments_rebuilt ("
        "id INTEGER NOT NULL, "
        "team_registration_id INTEGER, "
        "player_registration_id INTEGER, "
        "entry_id INTEGER, "
        "amount_cents INTEGER DEFAULT '0' NOT NULL, "
        "method VARCHAR(50), "
        "reference VARCHAR(100), "
        "notes TEXT, "
        f"{stripe_columns}"
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), "
        f"CONSTRAINT ck_payments_one_registration CHECK ({check}), "
        "CONSTRAINT ck_payments_amount_nonneg CHECK (amount_cents >= 0), "
        "FOREIGN KEY(team_registration_id) REFERENCES team_registrations (id), "
        "FOREIGN KEY(player_registration_id) REFERENCES player_registrations (id), "
        "FOREIGN KEY(entry_id) REFERENCES paid_entries (id))"
    )


def _rebuild_payments(*, with_stripe: bool) -> None:
    # SQLite can't change a CHECK constraint in place.
    bind = op.get_bind()
    bind.exec_driver_sql(_payments_table_sql(with_stripe=with_stripe))
    bind.exec_driver_sql(f"INSERT INTO payments_rebuilt ({PAYMENT_COLUMNS}) SELECT {PAYMENT_COLUMNS} FROM payments")
    for column in PAYMENT_INDEXES:
        bind.exec_driver_sql(f"DROP INDEX ix_payments_{column}")
    bind.exec_driver_sql("DROP TABLE payments")
    bind.exec_driver_sql("ALTER TABLE payments_rebuilt RENAME TO payments")
    for column in PAYMENT_INDEXES:
        bind.exec_driver_sql(f"CREATE INDEX ix_payments_{column} ON payments ({column})")


def upgrade():
    _rebuild_payments(with_stripe=True)
    op.create_table(
        "checkout_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stripe_session_id", sa.String(255), nullable=False, unique=True),
        sa.Column("stripe_account_id", sa.String(255), nullable=False),
        sa.Column("event", sa.String(100), sa.ForeignKey("tournaments.url"), nullable=True),
        sa.Column("league_id", sa.String(100), sa.ForeignKey("leagues.url"), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("holder_player", sa.String(50), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("holder_team", sa.String(50), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("payer_user_id", sa.String(50), nullable=False),
        sa.Column("payer_user_type", sa.String(10), nullable=False),
        sa.Column("form_payload", sa.JSON(), nullable=True),
        sa.Column("team_registration_id", sa.Integer(), sa.ForeignKey("team_registrations.id"), nullable=True),
        sa.Column("player_registration_id", sa.Integer(), sa.ForeignKey("player_registrations.id"), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("application_fee_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL AND league_id IS NOT NULL)",
            name="ck_checkout_holds_event_league_mutual_exclusive",
        ),
        sa.CheckConstraint(
            "(kind = 'player' AND holder_player IS NOT NULL AND holder_team IS NULL"
            " AND team_registration_id IS NULL)"
            " OR (kind = 'team' AND holder_team IS NOT NULL AND holder_player IS NULL"
            " AND player_registration_id IS NULL)",
            name="ck_checkout_holds_kind_holder",
        ),
        sa.CheckConstraint("amount_cents > 0", name="ck_checkout_holds_amount_positive"),
    )
    op.create_index("ix_checkout_holds_holder_player", "checkout_holds", ["holder_player"])
    op.create_index("ix_checkout_holds_holder_team", "checkout_holds", ["holder_team"])
    op.get_bind().exec_driver_sql(
        "CREATE UNIQUE INDEX uq_checkout_holds_open_holder_scope ON checkout_holds"
        " (kind, coalesce(holder_player, holder_team), coalesce(event, league_id)) WHERE status = 'OPEN'"
    )


def downgrade():
    op.drop_table("checkout_holds")
    op.get_bind().exec_driver_sql("DELETE FROM payments WHERE stripe_checkout_session_id IS NOT NULL")
    _rebuild_payments(with_stripe=False)
