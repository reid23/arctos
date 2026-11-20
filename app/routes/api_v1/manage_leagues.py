"""``/api/v1/leagues``: organizer setup of leagues (``manage`` scope).

Create, configure, and delete leagues and manage their organizers. Every
route needs a token with the ``manage`` scope and a user; all but creation
also need that user to organize the league. Shares
:class:`~app.services.league_management_service.LeagueManagementService`
and :mod:`app.utils.settings_save` with the SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify, request

from app.serializers.api_v1_serializer import league_json, league_organizers_json, league_settings_json
from app.services.league_management_service import LeagueManagementService
from app.utils.oauth_server import require_bearer
from app.utils.settings_save import SettingsSaveError, apply_league_settings
from models import TO

from . import bp, error, json_object_body, result_response, token_actor, with_organized_league


@bp.post("/leagues")
@require_bearer(scope="manage", user_required=True)
def create_league():
    """JSON ``{id, name}`` -> 201 ``{"league"}``. The token's user becomes its organizer."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    result = LeagueManagementService.create_league(
        **token_actor(), name=str(body.get("name") or ""), url=str(body.get("id") or "")
    )
    return result_response(result, lambda league: {"league": league_json(league)}, 201)


@bp.get("/leagues/<league_id>/settings")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def get_league_settings(league):
    return jsonify({"settings": league_settings_json(league)})


@bp.patch("/leagues/<league_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def update_league(league):
    """JSON object of the settings to change, keyed as in ``GET .../settings`` -> ``{"settings"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    registration = body.pop("registration", None) or {}
    if not isinstance(registration, dict):
        return error("registration must be an object", 400)
    try:
        apply_league_settings(league, body | registration)
    except SettingsSaveError as save_error:
        return error(str(save_error), save_error.status)
    return jsonify({"settings": league_settings_json(league)})


@bp.delete("/leagues/<league_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def delete_league(league):
    """Query ``confirm`` must repeat the league id. The league must have no tournaments left."""
    if request.args.get("confirm") != league.url:
        return error("Repeat the league id in ?confirm= to delete it", 400)
    return result_response(LeagueManagementService.delete_league(league.url), lambda _name: {}, 204)


@bp.post("/leagues/<league_id>/organizers")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def add_league_organizer(league):
    """JSON ``{user_id, user_type}`` (``player`` or ``team``) -> 201 ``{"organizers"}``, all of them."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        LeagueManagementService.add_organizer(
            league.url, str(body.get("user_id") or ""), str(body.get("user_type") or "")
        ),
        lambda _name: {"organizers": league_organizers_json(league)},
        201,
    )


@bp.delete("/leagues/<league_id>/organizers/<user_type>/<user_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def remove_league_organizer(league, user_type: str, user_id: str):
    organizer = TO.query.filter_by(league_id=league.url, user_type=user_type, user_id=user_id).first()
    if organizer is None:
        return error("Organizer not found", 404)
    return result_response(
        LeagueManagementService.remove_organizer(league.url, organizer.id, **token_actor()), lambda _name: {}, 204
    )
