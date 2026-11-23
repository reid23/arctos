"""Penalty types: the named, colored categories organizers attach to match notes.

A standalone tournament owns its penalty types; a league owns them for all
of its tournaments. Callers pass the owning :class:`~app.services._common.Scope`
and check organizer permission themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.services._common import Scope

if TYPE_CHECKING:  # pragma: no cover
    from models import PenaltyType

NAME_LENGTH_LIMIT = 50


def penalty_type_payload(penalty_type) -> dict:
    return {
        "id": penalty_type.id,
        "name": penalty_type.name,
        "color": penalty_type.color,
        "desc": (penalty_type.desc or ""),
    }


@dataclass(frozen=True)
class PenaltyTypeService:
    """Create, update, and delete penalty types within one scope."""

    @staticmethod
    def _scope_filter(scope: Scope) -> dict:
        return {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}

    @staticmethod
    def list_for_scope(scope: Scope) -> list["PenaltyType"]:
        from models import PenaltyType

        return PenaltyType.query.filter_by(**PenaltyTypeService._scope_filter(scope)).all()

    @staticmethod
    def _find(scope: Scope, penalty_type_id: int) -> Result["PenaltyType", ArctosError]:
        from models import PenaltyType

        penalty_type = PenaltyType.query.filter_by(
            id=penalty_type_id, **PenaltyTypeService._scope_filter(scope)
        ).first()
        if penalty_type is None:
            return Err(NotFoundError("Penalty type not found"))
        return Ok(penalty_type)

    @staticmethod
    def create(scope: Scope, data: dict) -> Result["PenaltyType", ArctosError]:
        """Create from JSON ``{name, desc?, color?}``; without a color, picks the next unused one."""
        from app.utils.helpers import get_next_penalty_color
        from models import PenaltyType, db

        name = data.get("name")
        if not name:
            return Err(ValidationError("Name required"))
        if len(name) > NAME_LENGTH_LIMIT:
            return Err(ValidationError("Name too long"))

        color = data.get("color")
        if not color:
            color = get_next_penalty_color({existing.color for existing in PenaltyTypeService.list_for_scope(scope)})
        else:
            color = color.strip().lstrip("#")
            if len(color) != 6:
                return Err(ValidationError("Invalid color format"))

        penalty_type = PenaltyType(
            name=name, color=color, desc=data.get("desc", ""), **PenaltyTypeService._scope_filter(scope)
        )
        db.session.add(penalty_type)
        db.session.commit()
        return Ok(penalty_type)

    @staticmethod
    def update(scope: Scope, penalty_type_id: int, data: dict) -> Result["PenaltyType", ArctosError]:
        """Apply the ``name``, ``desc``, and ``color`` keys present in *data*.

        A color that isn't six hex digits (after an optional ``#``) is ignored.
        """
        from models import db

        match PenaltyTypeService._find(scope, penalty_type_id):
            case Ok(penalty_type):
                pass
            case Err(_) as error:
                return error
        if "name" in data:
            if len(data["name"]) > NAME_LENGTH_LIMIT:
                return Err(ValidationError("Name too long"))
            penalty_type.name = data["name"]
        if "desc" in data:
            penalty_type.desc = data["desc"]
        if "color" in data:
            color = data["color"].strip().lstrip("#")
            if len(color) == 6:
                penalty_type.color = color
        db.session.commit()
        return Ok(penalty_type)

    @staticmethod
    def delete(scope: Scope, penalty_type_id: int) -> Result[None, ArctosError]:
        """Delete a penalty type that no match note uses (409 otherwise)."""
        from models import MatchNote, db

        match PenaltyTypeService._find(scope, penalty_type_id):
            case Ok(penalty_type):
                pass
            case Err(_) as error:
                return error
        if MatchNote.query.filter_by(penalty_type_id=penalty_type.id).first():
            return Err(ArctosError("Cannot delete penalty type that is in use.", status_code=409))
        db.session.delete(penalty_type)
        db.session.commit()
        return Ok(None)
