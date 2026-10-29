"""Let a historical roster request sit beside an invitation.

``uq_player_registrations_player_event`` used to allow one row per player
per event. A player who was invited by one team and asks to join another
needs both rows. Settled and invited rows stay unique. One open request
stays unique. Rejected and cancelled rows are not.

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


def upgrade() -> None:
    op.drop_index("uq_player_registrations_player_event", table_name="player_registrations")
    op.create_index(
        "uq_player_registrations_player_event",
        "player_registrations",
        ["player", "event"],
        unique=True,
        sqlite_where=sa.text(_OPEN_STATUSES),
        postgresql_where=sa.text(_OPEN_STATUSES),
    )
    op.create_index(
        "uq_player_registrations_player_event_requested",
        "player_registrations",
        ["player", "event"],
        unique=True,
        sqlite_where=sa.text(_REQUESTED),
        postgresql_where=sa.text(_REQUESTED),
    )


def downgrade() -> None:
    op.drop_index("uq_player_registrations_player_event_requested", table_name="player_registrations")
    op.drop_index("uq_player_registrations_player_event", table_name="player_registrations")
    op.create_index(
        "uq_player_registrations_player_event",
        "player_registrations",
        ["player", "event"],
        unique=True,
    )
