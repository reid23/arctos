"""The bracket diagram: ``/api/v1/tournaments/<id>/bracket``.

Reading needs the bracket to be published (or an organizer token, as for
unpublished schedules); writes need a token with the ``manage`` scope whose
user organizes the tournament. Shares
:class:`~app.services.bracket_service.BracketService` with the SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify

from app.serializers.api_v1_serializer import bracket_layout_json
from app.services.bracket_service import BracketService
from app.utils.oauth_server import require_bearer

from . import bp, can_view_bracket, error, json_object_body, result_response, with_organized_tournament, with_tournament

BRACKET_PATH = "/tournaments/<tournament_id>/bracket"


@bp.get(BRACKET_PATH)
@require_bearer()
@with_tournament
def get_bracket(tournament):
    if not can_view_bracket(tournament):
        return error("Bracket not published", 403)
    return jsonify({"bracket": bracket_layout_json(tournament)})


@bp.patch(BRACKET_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_bracket(tournament):
    """JSON ``{"published": bool}``."""
    body = json_object_body()
    if body is None or not isinstance(body.get("published"), bool):
        return error("published must be true or false", 400)
    BracketService.set_published(tournament, body["published"])
    return jsonify({"bracket": bracket_layout_json(tournament)})


@bp.put(f"{BRACKET_PATH}/layout")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def save_bracket_layout(tournament):
    """JSON ``{placements, texts, labeled_teams, images, clear_missing}``, as returned by ``GET``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return result_response(
        BracketService.save_layout(tournament.url, body),
        lambda _: {"bracket": bracket_layout_json(tournament)},
    )
