"""Prepaid registration entries replace the payment columns on registrations.

Revision ID: 0032_paid_entries
Revises: 0031_oidc

Backfill, per team or player registration outside historical tournaments:

- A paid registration becomes a paid entry for its holder in that scope:
  ``APPLIED`` to the registration when the registration is active,
  otherwise ``AVAILABLE`` (the holder had deregistered and could
  re-register as paid). A holder gets at most one entry per scope; the
  active row wins, then the newest.
- The entry's source is ``MANUAL`` when the organizer recorded an amount or
  details, ``FREE`` when the fee is zero, and ``ORGANIZER`` otherwise.
- Any recorded amount, method, reference, or notes becomes a manual
  payment, linked to the entry when the row produced one.

Historical tournaments never take payments and get no entries.

``downgrade`` restores the columns from the entries and payments.
"""

from collections import defaultdict
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

revision = "0032_paid_entries"
down_revision = "0031_oidc"
branch_labels = None
depends_on = None

ENTRY_STATUSES = ("APPLIED", "AVAILABLE", "VOIDED")
ENTRY_SOURCES = ("MANUAL", "ORGANIZER", "FREE")
ENTRY_ACTIONS = (
    "CREATED",
    "APPLIED",
    "RELEASED",
    "TRANSFERRED",
    "TRANSFER_REVOKED",
    "TRANSFER_DECLINED",
    "VOIDED",
)
REGISTRATION_TABLES = (
    # table, kind, holder column, entry registration column, fee column, active statuses
    ("team_registrations", "team", "team", "team_registration_id", "team_reg_fee", ("CONFIRMED",)),
    (
        "player_registrations",
        "player",
        "player",
        "player_registration_id",
        "player_reg_fee",
        ("CONFIRMED", "PENDING_TEAM_APPROVAL"),
    ),
)
PAYMENT_COLUMNS = ("paid", "amount_paid", "paid_at", "payment_method", "payment_reference", "payment_notes")


def _create_tables() -> None:
    op.create_table(
        "paid_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(100), sa.ForeignKey("tournaments.url"), nullable=True),
        sa.Column("league_id", sa.String(100), sa.ForeignKey("leagues.url"), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("holder_player", sa.String(50), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("holder_team", sa.String(50), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_registration_id", sa.Integer(), sa.ForeignKey("team_registrations.id"), nullable=True),
        sa.Column("player_registration_id", sa.Integer(), sa.ForeignKey("player_registrations.id"), nullable=True),
        sa.Column("status", sa.Enum(*ENTRY_STATUSES, name="paidentrystatus"), nullable=False),
        sa.Column("source", sa.Enum(*ENTRY_SOURCES, name="paidentrysource"), nullable=False),
        sa.Column("transferred_from", sa.String(50), nullable=True),
        sa.Column("reserves_team_slot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(event IS NOT NULL AND league_id IS NULL) OR (event IS NULL AND league_id IS NOT NULL)",
            name="ck_paid_entries_event_league_mutual_exclusive",
        ),
        sa.CheckConstraint(
            "(kind = 'player' AND holder_player IS NOT NULL AND holder_team IS NULL"
            " AND team_registration_id IS NULL AND NOT reserves_team_slot)"
            " OR (kind = 'team' AND holder_team IS NOT NULL AND holder_player IS NULL"
            " AND player_registration_id IS NULL)",
            name="ck_paid_entries_kind_holder",
        ),
        sa.CheckConstraint(
            "(status = 'APPLIED') = (COALESCE(team_registration_id, player_registration_id) IS NOT NULL)",
            name="ck_paid_entries_applied_has_registration",
        ),
    )
    op.create_index("ix_paid_entries_holder_player", "paid_entries", ["holder_player"])
    op.create_index("ix_paid_entries_holder_team", "paid_entries", ["holder_team"])
    op.create_index("ix_paid_entries_team_registration_id", "paid_entries", ["team_registration_id"])
    op.create_index("ix_paid_entries_player_registration_id", "paid_entries", ["player_registration_id"])
    op.create_index(
        "uq_paid_entries_holder_scope",
        "paid_entries",
        ["kind", sa.text("coalesce(holder_player, holder_team)"), sa.text("coalesce(event, league_id)")],
        unique=True,
        sqlite_where=sa.text("status != 'VOIDED'"),
    )

    op.create_table(
        "paid_entry_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("paid_entries.id"), nullable=False),
        sa.Column("action", sa.Enum(*ENTRY_ACTIONS, name="paidentryaction"), nullable=False),
        sa.Column("from_holder", sa.String(50), nullable=True),
        sa.Column("to_holder", sa.String(50), nullable=True),
        sa.Column("actor_user_id", sa.String(50), nullable=True),
        sa.Column("actor_user_type", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_paid_entry_events_entry_id", "paid_entry_events", ["entry_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_registration_id", sa.Integer(), sa.ForeignKey("team_registrations.id"), nullable=True),
        sa.Column("player_registration_id", sa.Integer(), sa.ForeignKey("player_registrations.id"), nullable=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("paid_entries.id"), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("method", sa.String(50), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(team_registration_id IS NULL) != (player_registration_id IS NULL)",
            name="ck_payments_one_registration",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payments_amount_nonneg"),
    )
    op.create_index("ix_payments_team_registration_id", "payments", ["team_registration_id"])
    op.create_index("ix_payments_player_registration_id", "payments", ["player_registration_id"])
    op.create_index("ix_payments_entry_id", "payments", ["entry_id"])


def _backfill(bind) -> None:
    now = bind.execute(sa.text("SELECT CURRENT_TIMESTAMP")).scalar()
    for table, kind, holder_column, entry_column, fee_column, active_statuses in REGISTRATION_TABLES:
        rows = (
            bind.execute(
                sa.text(
                    f"""
                    SELECT r.id, r.event, r.league_id, r.{holder_column} AS holder, r.status, r.paid,
                           r.amount_paid, r.paid_at, r.registered_at,
                           r.payment_method, r.payment_reference, r.payment_notes,
                           COALESCE(t.historical, 0) AS historical,
                           (NOT EXISTS (SELECT 1 FROM {kind}s holder WHERE holder.id = r.{holder_column})
                            OR COALESCE(t.url, l.url) IS NULL) AS orphaned,
                           COALESCE(event_config.{fee_column}, event_league_config.{fee_column},
                                    league_config.{fee_column}, 0) AS fee
                    FROM {table} r
                    LEFT JOIN tournaments t ON t.url = r.event
                    LEFT JOIN registrable_configs event_config ON event_config.id = t.registrable_config_id
                    LEFT JOIN leagues event_league ON event_league.url = t.league_id
                    LEFT JOIN registrable_configs event_league_config
                        ON event_league_config.id = event_league.registrable_config_id
                    LEFT JOIN leagues l ON l.url = r.league_id
                    LEFT JOIN registrable_configs league_config ON league_config.id = l.registrable_config_id
                    """
                )
            )
            .mappings()
            .all()
        )
        rows_by_holder_scope = defaultdict(list)
        for row in rows:
            if not row["historical"]:
                rows_by_holder_scope[(row["holder"], row["event"], row["league_id"])].append(row)

        for (holder, event, league_id), holder_rows in rows_by_holder_scope.items():
            holder_rows.sort(key=lambda row: (row["status"] not in active_statuses, -row["id"]))
            entry_created = False
            for row in holder_rows:
                amount_cents = int((Decimal(str(row["amount_paid"] or 0)) * 100).quantize(Decimal(1)))
                details = [(row[column] or "").strip() for column in ("payment_method", "payment_reference")]
                notes = (row["payment_notes"] or "").strip()
                has_payment = amount_cents > 0 or any(details) or notes != ""
                entry_id = None
                if row["paid"] and not row["orphaned"] and not entry_created:
                    entry_created = True
                    active = row["status"] in active_statuses
                    if has_payment:
                        source = "MANUAL"
                    elif Decimal(str(row["fee"] or 0)) == 0:
                        source = "FREE"
                    else:
                        source = "ORGANIZER"
                    paid_at = row["paid_at"] or row["registered_at"] or now
                    entry_id = bind.execute(
                        sa.text(
                            f"""
                            INSERT INTO paid_entries
                                (event, league_id, kind, holder_{kind}, {entry_column}, status, source,
                                 reserves_team_slot, created_at, applied_at)
                            VALUES (:event, :league_id, :kind, :holder, :registration_id, :status, :source,
                                    0, :created_at, :applied_at)
                            """
                        ),
                        {
                            "event": event,
                            "league_id": league_id,
                            "kind": kind,
                            "holder": holder,
                            "registration_id": row["id"] if active else None,
                            "status": "APPLIED" if active else "AVAILABLE",
                            "source": source,
                            "created_at": paid_at,
                            "applied_at": paid_at if active else None,
                        },
                    ).lastrowid
                    for action in ("CREATED", "APPLIED") if active else ("CREATED",):
                        bind.execute(
                            sa.text(
                                "INSERT INTO paid_entry_events (entry_id, action, to_holder, created_at)"
                                " VALUES (:entry_id, :action, :holder, :created_at)"
                            ),
                            {"entry_id": entry_id, "action": action, "holder": holder, "created_at": paid_at},
                        )
                if has_payment:
                    recorded_at = row["paid_at"] or row["registered_at"] or now
                    bind.execute(
                        sa.text(
                            f"""
                            INSERT INTO payments
                                ({entry_column}, entry_id, amount_cents, method, reference, notes,
                                 created_at, updated_at)
                            VALUES (:registration_id, :entry_id, :amount_cents, :method, :reference, :notes,
                                    :recorded_at, :recorded_at)
                            """
                        ),
                        {
                            "registration_id": row["id"],
                            "entry_id": entry_id,
                            "amount_cents": amount_cents,
                            "method": row["payment_method"],
                            "reference": row["payment_reference"],
                            "notes": row["payment_notes"],
                            "recorded_at": recorded_at,
                        },
                    )


def upgrade():
    bind = op.get_bind()
    _create_tables()
    _backfill(bind)
    # Native DROP COLUMN (SQLite 3.35+) keeps the partial unique indexes that a
    # batch table rebuild would have to recreate.
    for table, *_ in REGISTRATION_TABLES:
        for column in PAYMENT_COLUMNS:
            bind.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")


def downgrade():
    bind = op.get_bind()
    for table, *_ in REGISTRATION_TABLES:
        op.add_column(table, sa.Column("paid", sa.Boolean(), nullable=True))
        op.add_column(table, sa.Column("amount_paid", sa.Numeric(10, 2), nullable=True))
        op.add_column(table, sa.Column("paid_at", sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column("payment_method", sa.String(50), nullable=True))
        op.add_column(table, sa.Column("payment_reference", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("payment_notes", sa.Text(), nullable=True))

    for table, _kind, _holder_column, entry_column, _fee_column, _active_statuses in REGISTRATION_TABLES:
        applied = f"SELECT 1 FROM paid_entries e WHERE e.{entry_column} = {table}.id AND e.status = 'APPLIED'"
        first_payment = f"SELECT {{column}} FROM payments p WHERE p.{entry_column} = {table}.id ORDER BY p.id LIMIT 1"
        bind.execute(
            sa.text(
                f"""
                UPDATE {table} SET
                    paid = EXISTS ({applied})
                        OR event IN (SELECT url FROM tournaments WHERE historical),
                    paid_at = (SELECT e.applied_at FROM paid_entries e
                               WHERE e.{entry_column} = {table}.id AND e.status = 'APPLIED'),
                    amount_paid = COALESCE((SELECT SUM(p.amount_cents) FROM payments p
                                            WHERE p.{entry_column} = {table}.id), 0) / 100.0,
                    payment_method = ({first_payment.format(column="p.method")}),
                    payment_reference = ({first_payment.format(column="p.reference")}),
                    payment_notes = ({first_payment.format(column="p.notes")})
                """
            )
        )
    op.drop_table("payments")
    op.drop_table("paid_entry_events")
    op.drop_table("paid_entries")
