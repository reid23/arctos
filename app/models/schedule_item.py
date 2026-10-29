"""Timed activities shown alongside matches in a tournament schedule."""

from app.models.base import db
from app.models.constants import LONG_NAME_LEN, SHORT_NAME_LEN, URL_SLUG_LEN, UUID_LEN


class ScheduleItem(db.Model):
    """An activity with a fixed time, independent of the match dependency graph.

    Linked side competitions supply their current name and description. Generic
    activities keep their own title and description. A field is optional; the
    location can describe another meeting place such as the registration desk.
    """

    __tablename__ = "schedule_items"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(
        db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url", ondelete="CASCADE"), nullable=False, index=True
    )
    sidecomp_id = db.Column(db.Integer, db.ForeignKey("sidecomps.id", ondelete="CASCADE"), nullable=True)
    request_id = db.Column(db.String(UUID_LEN), nullable=True)
    # Keep creation retries distinct from linking an existing competition.
    created_sidecomp = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    title = db.Column(db.String(LONG_NAME_LEN), nullable=True)
    description = db.Column(db.Text, nullable=True)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    field = db.Column(db.String(SHORT_NAME_LEN), nullable=True)
    location = db.Column(db.String(LONG_NAME_LEN), nullable=True)
    sidecomp = db.relationship("SideComp", lazy="joined")

    __table_args__ = (
        db.UniqueConstraint("event", "request_id", name="uq_schedule_item_request"),
        db.CheckConstraint("end_time > start_time", name="ck_schedule_item_positive_duration"),
        db.CheckConstraint(
            "(sidecomp_id IS NULL AND title IS NOT NULL) OR (sidecomp_id IS NOT NULL AND title IS NULL AND description IS NULL)",
            name="ck_schedule_item_content_source",
        ),
    )
