"""Voluntary Arctos license keys.

A key is created before it belongs to any event. The value an organizer
types is ``{key_id}.{secret}``. ``key_id`` is only a lookup handle. The
secret is stored with the same password hash used for account passwords,
so a copy of the database is not a usable key. Responses never include
the key. Entering it attaches this row to one standalone event or one
league, and that attachment is what stops the key from being reused.
"""

from __future__ import annotations

from app.models.base import db
from app.models.constants import AUTH_STRING_LEN, LICENSE_KEY_ID_LEN, SHORT_CODE_LEN, URL_SLUG_LEN
from app.utils.datetime_helpers import now_utc_naive

LICENSE_KIND_EVENT = "event"
LICENSE_KIND_LEAGUE = "league"


class License(db.Model):
    """One purchased license and, once entered, the event or league using it.

    Attributes:
        key_id: Public id from the left side of the key. Not sufficient to
            redeem the license.
        key_hash: Werkzeug password hash of the secret half of the key.
        kind: ``event`` ($5 per team) or ``league`` ($10 per team). The key
            can be entered on any one holder of that kind.
        amount_paid_cents: Money recorded when the key was issued.
        team_allowance: How many teams that payment covers. This is a record
            of the purchase, not a feature gate.
        created_at: When the key was issued.
        redeemed_at: When an organizer entered the key, or ``None`` if it is
            still unused.
        tournament_url: Standalone event currently using the key, or ``None``.
        league_url: League currently using the key, or ``None``.
    """

    __tablename__ = "licenses"

    id = db.Column(db.Integer, primary_key=True)
    key_id = db.Column(db.String(LICENSE_KEY_ID_LEN), unique=True, nullable=False)
    key_hash = db.Column(db.String(AUTH_STRING_LEN), nullable=False)
    kind = db.Column(db.String(SHORT_CODE_LEN), nullable=False)
    amount_paid_cents = db.Column(db.Integer, nullable=False)
    team_allowance = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_utc_naive)
    redeemed_at = db.Column(db.DateTime, nullable=True)
    tournament_url = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    league_url = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("leagues.url", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )

    __table_args__ = (
        db.CheckConstraint(
            "kind IN ('event', 'league') AND ("
            "(tournament_url IS NULL AND league_url IS NULL) OR "
            "(kind = 'event' AND tournament_url IS NOT NULL AND league_url IS NULL) OR "
            "(kind = 'league' AND league_url IS NOT NULL AND tournament_url IS NULL)"
            ")",
            name="ck_license_holder",
        ),
    )
