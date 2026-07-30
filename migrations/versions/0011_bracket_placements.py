"""Add bracket canvas tables and tournaments.bracket_published.

Interactive open-canvas bracket builder:

* ``bracket_placements`` — match block layout and port modes (LABEL/NET),
  including ``inputs_flipped`` for swapping team1/team2 vertical order.
* ``bracket_texts`` — free-text annotations.
* ``bracket_labeled_teams`` — standalone team chips with a short ``label``
  caption and LABEL/NET input mode.
* ``bracket_images`` — uploaded images under ``static/uploads/brackets/``.
* ``tournaments.bracket_published`` — whether non-TOs can view the bracket.

Revision ID: 0011_bracket_placements
Revises: 0010_normalize_match_names
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_bracket_placements"
down_revision: Union[str, Sequence[str], None] = "0010_normalize_match_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bracket_placements",
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("match", sa.String(length=36), nullable=False),
        sa.Column("x_pos", sa.Float(), nullable=True),
        sa.Column("y_pos", sa.Float(), nullable=True),
        sa.Column("width", sa.Float(), nullable=False, server_default="280"),
        sa.Column("height", sa.Float(), nullable=False, server_default="100"),
        sa.Column(
            "team1",
            sa.Enum("LABEL", "NET", name="bracketportmode"),
            nullable=False,
            server_default="LABEL",
        ),
        sa.Column(
            "team2",
            sa.Enum("LABEL", "NET", name="bracketportmode"),
            nullable=False,
            server_default="LABEL",
        ),
        sa.Column(
            "inputs_flipped",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.ForeignKeyConstraint(["event"], ["tournaments.url"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match"], ["matches.uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event", "match"),
    )
    op.create_index(
        "ix_bracket_placements_event",
        "bracket_placements",
        ["event"],
        unique=False,
    )

    op.create_table(
        "bracket_texts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("x_pos", sa.Float(), nullable=False, server_default="40"),
        sa.Column("y_pos", sa.Float(), nullable=False, server_default="40"),
        sa.Column("size", sa.Float(), nullable=False, server_default="18"),
        sa.ForeignKeyConstraint(["event"], ["tournaments.url"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bracket_texts_event", "bracket_texts", ["event"], unique=False)

    op.create_table(
        "bracket_labeled_teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("team", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "kind",
            sa.Enum("LABEL", "NET", name="bracketportmode", create_type=False),
            nullable=False,
            server_default="LABEL",
        ),
        sa.Column("x_pos", sa.Float(), nullable=False, server_default="40"),
        sa.Column("y_pos", sa.Float(), nullable=False, server_default="40"),
        sa.ForeignKeyConstraint(["event"], ["tournaments.url"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bracket_labeled_teams_event",
        "bracket_labeled_teams",
        ["event"],
        unique=False,
    )

    op.create_table(
        "bracket_images",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("image", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("x_pos", sa.Float(), nullable=False, server_default="40"),
        sa.Column("y_pos", sa.Float(), nullable=False, server_default="40"),
        sa.Column("width", sa.Float(), nullable=False, server_default="240"),
        sa.Column("height", sa.Float(), nullable=False, server_default="160"),
        sa.ForeignKeyConstraint(["event"], ["tournaments.url"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bracket_images_event", "bracket_images", ["event"], unique=False)

    op.add_column(
        "tournaments",
        sa.Column(
            "bracket_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tournaments", "bracket_published")
    op.drop_index("ix_bracket_images_event", table_name="bracket_images")
    op.drop_table("bracket_images")
    op.drop_index("ix_bracket_labeled_teams_event", table_name="bracket_labeled_teams")
    op.drop_table("bracket_labeled_teams")
    op.drop_index("ix_bracket_texts_event", table_name="bracket_texts")
    op.drop_table("bracket_texts")
    op.drop_index("ix_bracket_placements_event", table_name="bracket_placements")
    op.drop_table("bracket_placements")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="bracketportmode").drop(bind, checkfirst=True)
