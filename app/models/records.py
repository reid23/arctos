"""SQLAlchemy models for player injury records and head-ref assignments."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.base import db
from app.models.constants import URL_SLUG_LEN, USER_ID_LEN


class Injury(db.Model):
    """A reported injury or medical note for a player.

    Used by TOs and refs to track player safety concerns during an event.

    Attributes:
        id: Auto-increment primary key.
        player: ID of the affected player.
        message: Description of the injury or concern.
        stamp: Timestamp when the record was created.
        show: Whether the record is visible to other officials.
        active: Whether the injury is currently active / unresolved.
    """

    __tablename__ = "injuries"
    __table_args__ = (db.Index("ix_injuries_player", "player"),)

    id = db.Column(db.Integer, primary_key=True)
    player = db.Column(
        db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False
    )
    message = db.Column(db.Text, nullable=False)
    stamp = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    show = db.Column(db.Boolean, default=True)
    active = db.Column(db.Boolean, default=True)


class HeadRef(db.Model):
    """A head-referee assignment for a player at a specific tournament.

    Grants the player head-ref privileges for the referenced event,
    optionally limited by an expiry date.

    Attributes:
        id: Auto-increment primary key.
        player: ID of the player granted head-ref status.
        event: Tournament URL slug the privileges apply to.
        expdate: Optional expiry :class:`~datetime.datetime`; ``None`` means
            no expiry.
    """

    __tablename__ = "headrefs"
    __table_args__ = (
        db.UniqueConstraint("player", "event", name="uq_headref_event_player"),
        db.Index("ix_headrefs_event", "event"),
        db.Index("ix_headrefs_player", "player"),
    )

    id = db.Column(db.Integer, primary_key=True)
    player = db.Column(
        db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False
    )
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=False
    )
    expdate = db.Column(db.DateTime)
