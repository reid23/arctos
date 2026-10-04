"""SQLAlchemy models for side competitions, their registrations, and results."""

from __future__ import annotations

import json
import uuid
from typing import List, Optional

from app.domain.enums import Pompfen, SideCompType
from app.models.base import db
from app.utils.datetime_helpers import now_utc_naive
from app.models.constants import (
    SHORT_NAME_LEN,
    URL_SLUG_LEN,
    USER_ID_LEN,
    UUID_LEN,
)


_ALL_POMPFEN_NAMES = [p.name for p in Pompfen]
_DEFAULT_ALLOWED_WEAPONS_JSON = json.dumps(_ALL_POMPFEN_NAMES)


class SideComp(db.Model):
    """A side competition (e.g. dueling, chain/breaking) at a tournament.

    Attributes:
        id: Auto-increment primary key.
        event: Tournament URL slug this side competition belongs to.
        name: Display name of the competition.
        type: One of :class:`SideCompType`.
        description: Optional free-form description of the side competition.
        registration_open: When ``True``, players can self-register; when
            ``False`` (default), only TO can add registrants.
        active: When ``True``, signed-in users may log results; when
            ``False`` (default), result logging is rejected.
        allowed_weapons: JSON-encoded list of :class:`Pompfen` member names
            that players may select when registering. Defaults to all.
        only_show_top_n_results: When ``None``, the results page shows all
            ranked players. When ``0``, the results page is hidden. When a
            positive integer ``N``, each results table shows only the top
            ``N`` players (filters then apply within that cut).
        created_at: Timestamp when the side competition was created.
    """

    __tablename__ = "sidecomps"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(URL_SLUG_LEN), db.ForeignKey("tournaments.url"), nullable=False)
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    type = db.Column(db.Enum(SideCompType), nullable=False)
    description = db.Column(db.Text, nullable=True)
    registration_open = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    allowed_weapons = db.Column(
        db.Text,
        nullable=False,
        default=_DEFAULT_ALLOWED_WEAPONS_JSON,
        server_default=_DEFAULT_ALLOWED_WEAPONS_JSON,
    )
    only_show_top_n_results = db.Column(db.Integer, nullable=True, default=None)
    created_at = db.Column(
        db.DateTime,
        default=now_utc_naive,
        nullable=False,
    )

    def get_allowed_weapons(self) -> List[Pompfen]:
        """Return the enabled :class:`Pompfen` options for this side competition."""
        raw = self.allowed_weapons or _DEFAULT_ALLOWED_WEAPONS_JSON
        try:
            names = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return list(Pompfen)
        if not isinstance(names, list):
            return list(Pompfen)
        out: List[Pompfen] = []
        for name in names:
            parsed = Pompfen.from_name(name)
            if parsed is not None and parsed not in out:
                out.append(parsed)
        return out

    def set_allowed_weapons(self, weapons: List[Pompfen]) -> None:
        """Persist *weapons* as the enabled pompfen options (order preserved)."""
        names = [w.name for w in weapons]
        self.allowed_weapons = json.dumps(names)

    def allowed_weapon_names(self) -> List[str]:
        """Return enabled pompfen names for JSON payloads."""
        return [w.name for w in self.get_allowed_weapons()]

    def results_page_enabled(self) -> bool:
        """Return whether the public results page should be shown."""
        return self.only_show_top_n_results is None or self.only_show_top_n_results != 0


class SideCompRegistration(db.Model):
    """A player's registration in a side competition.

    Attributes:
        id: Auto-increment primary key.
        comp: FK to the parent :class:`SideComp`.
        player: FK to the registering player.
        entry_number: 1-indexed sequential entry number assigned at
            registration time, unique within a comp. Numbers are not reused
            after a deregistration.
        weapon: Integer value of the selected :class:`Pompfen`.
        registered_at: Timestamp when the registration was created.
        registered_by_to: ``True`` when the row was created via TO registration,
            ``False`` for player self-registration.
    """

    __tablename__ = "sidecomp_registrations"

    id = db.Column(db.Integer, primary_key=True)
    comp = db.Column(db.Integer, db.ForeignKey("sidecomps.id"), nullable=False)
    player = db.Column(db.String(USER_ID_LEN), db.ForeignKey("players.id"), nullable=False)
    entry_number = db.Column(db.Integer, nullable=False)
    weapon = db.Column(db.Integer, nullable=False)
    registered_at = db.Column(
        db.DateTime,
        default=now_utc_naive,
        nullable=False,
    )
    registered_by_to = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("comp", "player", name="uq_sidecomp_registrations_comp_player"),
        db.UniqueConstraint("comp", "entry_number", name="uq_sidecomp_registrations_comp_entry_number"),
    )

    def get_weapon(self) -> Pompfen | None:
        """Return the selected :class:`Pompfen`, or ``None`` if invalid."""
        return Pompfen.from_value(self.weapon)

    def weapon_name(self) -> str | None:
        """Return the selected pompfen name for JSON payloads."""
        w = self.get_weapon()
        return w.name if w is not None else None


class SideCompResult(db.Model):
    """A single scored point logged against a side-comp registration.

    Attributes:
        uuid: UUID primary key.
        comp: FK to the parent :class:`SideComp`.
        player: FK to the scoring :class:`SideCompRegistration`.
        opponent: Optional FK to an opposing registration (reserved for
            future features).
        stamp: Timestamp when the point was recorded.
        points: Point delta (currently ``+1`` or ``-1``).
        ref: User ID of the account that logged the point.
        flagged: When ``True``, the entry is marked for later review.
        valid: Soft-validity flag; defaults to ``True``. Invalid rows are
            excluded from standings.
    """

    __tablename__ = "sidecompresults"

    uuid = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    comp = db.Column(db.Integer, db.ForeignKey("sidecomps.id"), nullable=False, index=True)
    player = db.Column(
        db.Integer,
        db.ForeignKey("sidecomp_registrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opponent = db.Column(
        db.Integer,
        db.ForeignKey("sidecomp_registrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    stamp = db.Column(db.DateTime, default=now_utc_naive, nullable=False)
    points = db.Column(db.Integer, nullable=False)
    ref = db.Column(db.String(USER_ID_LEN), nullable=False)
    flagged = db.Column(db.Boolean, nullable=False, default=False)
    valid = db.Column(db.Boolean, nullable=False, default=True)

    def to_payload(self) -> dict:
        """Serialize this result for API responses."""
        return {
            "uuid": self.uuid,
            "comp": self.comp,
            "player": self.player,
            "opponent": self.opponent,
            "stamp": self.stamp.isoformat() if self.stamp else None,
            "points": self.points,
            "ref": self.ref,
            "flagged": bool(self.flagged),
            "valid": bool(self.valid),
        }
