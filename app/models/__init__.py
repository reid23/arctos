"""
Arctos SQLAlchemy models package.

The project historically used a top-level `models.py`. This package is the
new home for model definitions. The top-level `models.py` remains as a
compatibility layer that re-exports from here.
"""

from app.models.base import db, init_db
from app.models import (
    constants,
)  # noqa: F401 — re-export for ``from app.models import constants``
from app.models.user import Player, Team
from app.models.registrable_config import RegistrableConfig
from app.models.league import League
from app.models.tournament import Tournament, TO, Field, Tag
from app.models.registration import TeamRegistration, PlayerRegistration
from app.models.match import (
    Match,
    Point,
    MatchNote,
    MatchRefSlot,
    MatchRosterEntry,
    MatchCameraStreamStart,
)
from app.models.penalty_type import PenaltyType
from app.models.records import Injury, HeadRef
from app.models.sidecomp import SideComp, SideCompResult
from app.models.camera import Camera

__all__ = [
    "db",
    "init_db",
    "Player",
    "Team",
    "RegistrableConfig",
    "League",
    "Tournament",
    "TO",
    "Field",
    "Tag",
    "TeamRegistration",
    "PlayerRegistration",
    "Match",
    "Point",
    "MatchNote",
    "MatchRefSlot",
    "MatchRosterEntry",
    "MatchCameraStreamStart",
    "Camera",
    "PenaltyType",
    "Injury",
    "HeadRef",
    "SideComp",
    "SideCompResult",
]
