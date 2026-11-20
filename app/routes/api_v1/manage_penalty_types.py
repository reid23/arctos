"""Penalty types: ``/api/v1/{tournaments,leagues}/<id>/penalty-types``.

Anyone can list them; writes need a token with the ``manage`` scope whose
user organizes the tournament or league. A league tournament uses its
league's penalty types. Shares
:class:`~app.services.penalty_type_service.PenaltyTypeService` with the
SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify

from app.serializers.api_v1_serializer import penalty_type_json, scope_of
from app.services._common import Scope
from app.services.penalty_type_service import PenaltyTypeService
from app.utils.oauth_server import require_bearer

from . import (
    bp,
    error,
    json_object_body,
    result_response,
    with_league,
    with_organized_league,
    with_organized_tournament,
    with_tournament,
)


def _penalty_types_response(scope: Scope):
    return jsonify({"penalty_types": [penalty_type_json(t) for t in PenaltyTypeService.list_for_scope(scope)]})


def _service_body() -> dict | None:
    """The JSON body with ``description`` renamed to the service's ``desc``; ``None`` if not an object."""
    body = json_object_body()
    if body is None:
        return None
    for key in ("name", "description", "color"):
        if key in body and not isinstance(body[key], str):
            return None
    if "description" in body:
        body["desc"] = body.pop("description")
    return body


def _create(scope: Scope):
    body = _service_body()
    if body is None:
        return error("Expected a JSON object with text name, description, and color", 400)
    return result_response(
        PenaltyTypeService.create(scope, body),
        lambda penalty_type: {"penalty_type": penalty_type_json(penalty_type)},
        ok_status_code=201,
    )


def _update(scope: Scope, penalty_type_id: int):
    body = _service_body()
    if body is None:
        return error("Expected a JSON object with text name, description, and color", 400)
    return result_response(
        PenaltyTypeService.update(scope, penalty_type_id, body),
        lambda penalty_type: {"penalty_type": penalty_type_json(penalty_type)},
    )


@bp.get("/tournaments/<tournament_id>/penalty-types")
@require_bearer()
@with_tournament
def list_tournament_penalty_types(tournament):
    return _penalty_types_response(scope_of(tournament))


@bp.post("/tournaments/<tournament_id>/penalty-types")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_tournament_penalty_type(tournament):
    return _create(scope_of(tournament))


@bp.patch("/tournaments/<tournament_id>/penalty-types/<int:penalty_type_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_tournament_penalty_type(tournament, penalty_type_id: int):
    return _update(scope_of(tournament), penalty_type_id)


@bp.delete("/tournaments/<tournament_id>/penalty-types/<int:penalty_type_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_tournament_penalty_type(tournament, penalty_type_id: int):
    return result_response(
        PenaltyTypeService.delete(scope_of(tournament), penalty_type_id), lambda _: {}, ok_status_code=204
    )


@bp.get("/leagues/<league_id>/penalty-types")
@require_bearer()
@with_league
def list_league_penalty_types(league):
    return _penalty_types_response(Scope.league(league.url))


@bp.post("/leagues/<league_id>/penalty-types")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def create_league_penalty_type(league):
    return _create(Scope.league(league.url))


@bp.patch("/leagues/<league_id>/penalty-types/<int:penalty_type_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def update_league_penalty_type(league, penalty_type_id: int):
    return _update(Scope.league(league.url), penalty_type_id)


@bp.delete("/leagues/<league_id>/penalty-types/<int:penalty_type_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def delete_league_penalty_type(league, penalty_type_id: int):
    return result_response(
        PenaltyTypeService.delete(Scope.league(league.url), penalty_type_id), lambda _: {}, ok_status_code=204
    )
