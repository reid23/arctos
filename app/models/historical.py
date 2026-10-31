"""SQLAlchemy models specific to historical tournaments."""

from __future__ import annotations

from app.models.base import db
from app.models.constants import USER_ID_LEN
from app.utils.datetime_helpers import now_utc_naive


class TeamClaimInvitation(db.Model):
    """An organiser's invitation for a real team to claim a placeholder team.

    Accepting merges the placeholder into the invited team: every record the
    placeholder holds in its historical tournament (registration, matches,
    referee slots, roster) is re-pointed at the invited team and the
    placeholder row is deleted. A placeholder has at most one open invitation.

    Attributes:
        id: Auto-increment primary key.
        placeholder_team: The placeholder :class:`~app.models.user.Team` being
            offered.
        invited_team: The real team invited to claim it.
        created_at: When the invitation was sent.
    """

    __tablename__ = "team_claim_invitations"

    id = db.Column(db.Integer, primary_key=True)
    placeholder_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False, unique=True)
    invited_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)


class TeamClaimRequest(db.Model):
    """A real team's request to claim a placeholder, waiting for an organiser.

    Unlike :class:`TeamClaimInvitation`, the team starts this and several
    teams may request the same placeholder. Approving one merges that
    placeholder into the requesting team and drops the rest.

    Attributes:
        id: Auto-increment primary key.
        placeholder_team: The placeholder :class:`~app.models.user.Team`.
        requesting_team: The real team asking to claim it.
        created_at: When the request was made.
    """

    __tablename__ = "team_claim_requests"
    __table_args__ = (db.UniqueConstraint("placeholder_team", "requesting_team"),)

    id = db.Column(db.Integer, primary_key=True)
    placeholder_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False, index=True)
    requesting_team = db.Column(db.String(USER_ID_LEN), db.ForeignKey("teams.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
