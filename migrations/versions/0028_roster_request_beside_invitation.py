"""Let a historical roster request sit beside an invitation.

``uq_player_registrations_player_event`` used to allow one row per player
per event. A player who was invited by one team and asks to join another
needs both rows. Settled and invited rows stay unique. One open request
stays unique. Rejected and cancelled rows are not.

Databases that ran ``0002_phase1_additive`` have that unique index.
Databases created with ``create_all()`` and stamped at head do not,
because the model never declared it. The upgrade drops the index only
when it is present.

Revision ID: 0028_roster_request_beside_invitation
Revises: 0027_team_claim_requests
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028_roster_request_beside_invitation"
down_revision: Union[str, Sequence[str], None] = "0027_team_claim_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPEN_STATUSES = "status NOT IN ('REQUESTED', 'REJECTED', 'CANCELLED')"
_REQUESTED = "status = 'REQUESTED'"
_TABLE = "player_registrations"
_OPEN_INDEX = "uq_player_registrations_player_event"
_REQUESTED_INDEX = "uq_player_registrations_player_event_requested"


def _index_names() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def upgrade() -> None:
    if _OPEN_INDEX in _index_names():
        op.drop_index(_OPEN_INDEX, table_name=_TABLE)
    op.create_index(
        _OPEN_INDEX,
        _TABLE,
        ["player", "event"],
        unique=True,
        sqlite_where=sa.text(_OPEN_STATUSES),
        postgresql_where=sa.text(_OPEN_STATUSES),
    )
    if _REQUESTED_INDEX not in _index_names():
        op.create_index(
            _REQUESTED_INDEX,
            _TABLE,
            ["player", "event"],
            unique=True,
            sqlite_where=sa.text(_REQUESTED),
            postgresql_where=sa.text(_REQUESTED),
        )


def downgrade() -> None:
    names = _index_names()
    if _REQUESTED_INDEX in names:
        op.drop_index(_REQUESTED_INDEX, table_name=_TABLE)
    if _OPEN_INDEX in names:
        op.drop_index(_OPEN_INDEX, table_name=_TABLE)
    op.create_index(_OPEN_INDEX, _TABLE, ["player", "event"], unique=True)
