"""``/api/v1/tournaments``: organizer setup of tournaments (``manage`` scope).

Create, configure, and delete tournaments, and manage their organizers,
fields, tags, and script variables. Every route needs a token with the
``manage`` scope and a user; all but creation also need that user to
organize the tournament. Shares
:class:`~app.services.tournament_management_service.TournamentManagementService`
and :mod:`app.utils.settings_save` with the SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify, request

from app.serializers.api_v1_serializer import (
    field_json,
    organizers_json,
    script_variable_json,
    tag_json,
    tournament_json,
    tournament_settings_json,
)
from app.services.registration_resolver import to_entries_for_tournament
from app.services.tournament_management_service import TournamentManagementService
from app.utils.oauth_server import require_bearer
from app.utils.settings_save import SettingsSaveError, update_tournament_settings
from models import TO, ScriptVariable, Tag

from . import bp, error, json_object_body, result_response, token_actor, with_organized_tournament


@bp.post("/tournaments")
@require_bearer(scope="manage", user_required=True)
def create_tournament():
    """JSON ``{id, name, league}`` -> 201 ``{"tournament"}``. The token's user becomes its organizer."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    result = TournamentManagementService.create_tournament(
        **token_actor(),
        name=str(body.get("name") or ""),
        url=str(body.get("id") or ""),
        league_id=str(body.get("league") or ""),
    )
    return result_response(result, lambda tournament: {"tournament": tournament_json(tournament)}, 201)


@bp.get("/tournaments/<tournament_id>/settings")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def get_tournament_settings(tournament):
    return jsonify({"settings": tournament_settings_json(tournament)})


@bp.patch("/tournaments/<tournament_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_tournament(tournament):
    """JSON object of the settings to change, keyed as in ``GET .../settings`` -> ``{"settings"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    registration = body.pop("registration", None) or {}
    if not isinstance(registration, dict):
        return error("registration must be an object", 400)
    try:
        update_tournament_settings(tournament, body | registration)
    except SettingsSaveError as save_error:
        return error(str(save_error), save_error.status)
    return jsonify({"settings": tournament_settings_json(tournament)})


@bp.delete("/tournaments/<tournament_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_tournament(tournament):
    """Query ``confirm`` must repeat the tournament id."""
    if request.args.get("confirm") != tournament.url:
        return error("Repeat the tournament id in ?confirm= to delete it", 400)
    return result_response(TournamentManagementService.delete_tournament(tournament.url), lambda _name: {}, 204)


@bp.post("/tournaments/<tournament_id>/organizers")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def add_tournament_organizer(tournament):
    """JSON ``{user_id, user_type}`` (``player`` or ``team``) -> 201 ``{"organizers"}``, all of them."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.add_organizer(
            tournament.url, str(body.get("user_id") or ""), str(body.get("user_type") or "")
        ),
        lambda _name: {"organizers": organizers_json(to_entries_for_tournament(tournament))},
        201,
    )


@bp.delete("/tournaments/<tournament_id>/organizers/<user_type>/<user_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def remove_tournament_organizer(tournament, user_type: str, user_id: str):
    organizer = TO.query.filter_by(event=tournament.url, user_type=user_type, user_id=user_id).first()
    if organizer is None:
        return error("Organizer not found", 404)
    return result_response(
        TournamentManagementService.remove_organizer(tournament.url, organizer.id, **token_actor()),
        lambda _name: {},
        204,
    )


@bp.post("/tournaments/<tournament_id>/fields")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_field(tournament):
    """JSON ``{name}`` -> 201 ``{"field"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.create_field(tournament.url, str(body.get("name") or "")),
        lambda field: {"field": field_json(field)},
        201,
    )


@bp.patch("/tournaments/<tournament_id>/fields/<int:field_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def rename_field(tournament, field_id: int):
    """JSON ``{name}`` -> ``{"field"}``. Matches and schedule items on the field move with it."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.rename_field(tournament.url, field_id, str(body.get("name") or "")),
        lambda field: {"field": field_json(field)},
    )


@bp.delete("/tournaments/<tournament_id>/fields/<int:field_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_field(tournament, field_id: int):
    return result_response(TournamentManagementService.delete_field(tournament.url, field_id), lambda _: {}, 204)


@bp.get("/tournaments/<tournament_id>/tags")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def list_tags(tournament):
    tags = Tag.query.filter_by(event=tournament.url).order_by(Tag.name).all()
    return jsonify({"tags": [tag_json(tag) for tag in tags]})


@bp.post("/tournaments/<tournament_id>/tags")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_tag(tournament):
    """JSON ``{name, expression}`` -> 201 ``{"tag"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.create_tag(tournament.url, str(body.get("name") or ""), body.get("expression")),
        lambda tag: {"tag": tag_json(tag)},
        201,
    )


@bp.put("/tournaments/<tournament_id>/tags/<int:tag_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_tag(tournament, tag_id: int):
    """JSON ``{name, expression}`` (``expression`` optional) -> ``{"tag"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.update_tag(tournament.url, tag_id, body), lambda tag: {"tag": tag_json(tag)}
    )


@bp.put("/tournaments/<tournament_id>/tags/<int:tag_id>/team")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def set_tag_team(tournament, tag_id: int):
    """JSON ``{team_id}`` (``null`` clears the override) -> ``{"tag"}``."""
    body = json_object_body()
    if body is None or "team_id" not in body:
        return error("Expected a JSON object body with team_id", 400)
    return result_response(
        TournamentManagementService.assign_tag(tournament.url, tag_id, {"team_id": body["team_id"]}),
        lambda tag: {"tag": tag_json(tag)},
    )


@bp.delete("/tournaments/<tournament_id>/tags/<int:tag_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_tag(tournament, tag_id: int):
    return result_response(TournamentManagementService.delete_tag(tournament.url, tag_id), lambda _: {}, 204)


@bp.get("/tournaments/<tournament_id>/script-variables")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def list_script_variables(tournament):
    variables = ScriptVariable.query.filter_by(event=tournament.url).order_by(ScriptVariable.name).all()
    return jsonify({"script_variables": [script_variable_json(variable) for variable in variables]})


@bp.post("/tournaments/<tournament_id>/script-variables")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_script_variable(tournament):
    """JSON ``{name, expression}`` -> 201 ``{"script_variable"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.create_script_variable(
            tournament.url, str(body.get("name") or ""), str(body.get("expression") or "")
        ),
        lambda variable: {"script_variable": script_variable_json(variable)},
        201,
    )


@bp.patch("/tournaments/<tournament_id>/script-variables/<int:variable_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_script_variable(tournament, variable_id: int):
    """JSON ``{name, expression}``, either optional -> ``{"script_variable"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        TournamentManagementService.update_script_variable(
            tournament.url, variable_id, body.get("name"), body.get("expression")
        ),
        lambda variable: {"script_variable": script_variable_json(variable)},
    )


@bp.delete("/tournaments/<tournament_id>/script-variables/<int:variable_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_script_variable(tournament, variable_id: int):
    return result_response(
        TournamentManagementService.delete_script_variable(tournament.url, variable_id), lambda _: {}, 204
    )
