"""Video descope: drop unused livestream capture columns.

Removes the field-level livestream camera column and the per-point stream
offset columns that only the removed livestream/recording capture path
wrote. Existing footage playback does not depend on these: match-scoped
cameras and their ``camera_timepoints`` anchors, ``matches.camera_stream_starts``,
and ``points.footage`` are all retained.

**Data loss (intentional):** upgrade drops any values stored in
``fields.camera``, ``points.camera_index``, and ``points.stream_timestamp``.
Downgrade recreates those columns as empty nullables; the dropped values
cannot be restored.

Revision ID: 0012_video_descope
Revises: 0011_bracket_placements
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_video_descope"
down_revision: Union[str, Sequence[str], None] = "0011_bracket_placements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite batch_alter_table rebuilds via DROP TABLE; match_notes.point_id
    # references points.uuid, so FK enforcement must be off for the swap
    # (same pattern as 0003/0005/0006/0007).
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("fields") as batch:
            batch.drop_column("camera")
        with op.batch_alter_table("points") as batch:
            batch.drop_column("camera_index")
            batch.drop_column("stream_timestamp")
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        with op.batch_alter_table("points") as batch:
            batch.add_column(sa.Column("stream_timestamp", sa.Float(), nullable=True))
            batch.add_column(sa.Column("camera_index", sa.Integer(), nullable=True))
        with op.batch_alter_table("fields") as batch:
            batch.add_column(sa.Column("camera", sa.Text(), nullable=True))
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys = ON")
