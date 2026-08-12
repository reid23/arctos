"""
Arctos SQLAlchemy models package.

The project historically used a top-level `models.py`. This package is the
new home for model definitions. The top-level `models.py` remains as a
compatibility layer that re-exports from here.
"""

from app.models.base import db, init_db
from app.models import validators  # noqa: F401 - registers length validators before any mapper is configured
from app.models import (
    constants,
)  # noqa: F401 — re-export for ``from app.models import constants``
from app.models.user import Player, Team
from app.models.registrable_config import RegistrableConfig
from app.models.league import League
from app.models.tournament import Tournament, TO, Field, Tag
from app.models.registration import TeamRegistration, PlayerRegistration
from app.models.match import Match, Point, MatchNote
from app.models.bracket import BracketImage, BracketLabeledTeam, BracketPlacement, BracketText
from app.models.penalty_type import PenaltyType
from app.models.records import Injury, HeadRef
from app.models.sidecomp import SideComp, SideCompRegistration, SideCompResult
from app.models.camera import Camera
from app.models.normalised import (
    CameraTimepoint,
    HeadRefAllowList,
    MatchPlayer,
    MatchReferee,
)

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
    "BracketPlacement",
    "BracketText",
    "BracketLabeledTeam",
    "BracketImage",
    "Camera",
    "PenaltyType",
    "Injury",
    "HeadRef",
    "SideComp",
    "SideCompRegistration",
    "SideCompResult",
    # Normalised join tables (added by the additive schema migration).
    "HeadRefAllowList",
    "MatchReferee",
    "MatchPlayer",
    "CameraTimepoint",
]
