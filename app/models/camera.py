"""SQLAlchemy model for camera/video upload records."""

from __future__ import annotations

import uuid

from app.models.base import db
from app.models.constants import (
    LONG_NAME_LEN,
    LONG_URL_LEN,
    SHORT_CODE_LEN,
    SHORT_LABEL_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
    UUID_LEN,
)


class Camera(db.Model):
    """A camera recording or video upload record associated with a match.

    Tracks the full lifecycle of a video clip from initial upload through
    S3 transfer and optional YouTube publication.

    Attributes:
        uuid: UUID primary key, auto-generated.
        match_uuid: UUID FK of the :class:`~app.models.match.Match` this
            recording belongs to.
        event: Tournament URL slug (denormalised for efficient filtering
            without joins).
        field: Field index within the tournament (0-based integer).
        field_id: FK to the tournament field row used by this recording.
        name: Human-readable display name for the recording.
        source_type: Where the recording originated (e.g. ``"recording"``).
        uploaded_by_user_id: ID of the user who uploaded the recording.
        uploaded_by_user_type: ``"player"`` or ``"team"``.
        status: Upload/processing status: ``"UPLOADING"``, ``"SUCCESS"``,
            or ``"FAILED"``.
        link: YouTube URL or video ID once the upload succeeds.
        file: Local file path or S3 key, used for failed or in-progress
            uploads.
        time_world: JSON array of wall-clock timestamps (one per
            session/clip boundary) aligning footage to real time.
        time_video: JSON array of float second offsets (one per
            session/clip boundary) within the video file.
    """

    __tablename__ = "cameras"
    __table_args__ = (
        db.Index("ix_cameras_field_id", "field_id"),
        db.CheckConstraint(
            "(uploaded_by_user_type IS NULL OR uploaded_by_user_type IN ('player', 'team'))",
            name="ck_cameras_uploaded_by_user_type_allowed_values",
        ),
    )

    uuid = db.Column(
        db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Camera belongs to a single match.
    match_uuid = db.Column(
        db.String(UUID_LEN), db.ForeignKey("matches.uuid"), nullable=False, index=True
    )

    # Convenience fields to avoid joins for UI / filtering.
    event = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url"),
        nullable=False,
        index=True,
    )
    field = db.Column(db.Integer, nullable=False, index=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=True)

    # Display / identity.
    name = db.Column(db.String(LONG_NAME_LEN), nullable=False)

    # Upload source tracking.
    source_type = db.Column(
        db.String(SHORT_LABEL_LEN), nullable=False, default="recording"
    )

    uploaded_by_user_id = db.Column(db.String(USER_ID_LEN), nullable=True)
    uploaded_by_user_type = db.Column(
        db.String(SHORT_CODE_LEN), nullable=True
    )  # e.g. "player" or "team"

    # Output identity.
    status = db.Column(
        db.String(SHORT_LABEL_LEN), nullable=False, default="UPLOADING"
    )  # UPLOADING|SUCCESS|FAILED
    link = db.Column(db.String(LONG_URL_LEN))  # YouTube URL/id when SUCCESS

    # Local/static/S3 key for FAILED downloads (and possibly for in-progress uploads).
    file = db.Column(db.String(LONG_URL_LEN))

    # JSON arrays stored as strings (SQLite-safe). Keep format consistent with frontend expectations.
    time_world = db.Column(
        db.Text
    )  # JSON array of world timestamps; one per session/clip boundary
    time_video = db.Column(
        db.Text
    )  # JSON array of float seconds; one per session/clip boundary
