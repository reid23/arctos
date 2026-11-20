"""Side competition setup: writes under ``/api/v1/tournaments/<id>/side-competitions``.

Needs a token with the ``manage`` scope whose user organizes the
tournament. Shares :class:`~app.services.sidecomp_service.SideCompService`
with the SPA's session routes; the service checks the organizer again.
"""

from __future__ import annotations

from app.serializers.api_v1_serializer import side_competition_detail_json
from app.services.sidecomp_service import SideCompService
from app.utils.oauth_server import require_bearer
from models import SideComp

from . import bp, error, json_object_body, result_response, token_actor, with_organized_tournament

SIDE_COMPETITION_PATH = "/tournaments/<tournament_id>/side-competitions/<int:side_competition_id>"
UPDATABLE_KEYS = (
    "name",
    "type",
    "description",
    "registration_open",
    "active",
    "track_losses",
    "track_chain_losses",
    "track_break_losses",
    "track_matchups",
    "allowed_weapons",
    "only_show_top_n_results",
)


def _detail_response(result, ok_status_code: int = 200):
    """Respond with the written side competition, reloaded with its registrants."""
    return result_response(
        result.and_then(lambda side_competition: SideCompService.get_with_registrants(side_competition.id)),
        lambda found: {"side_competition": side_competition_detail_json(*found)},
        ok_status_code,
    )


def _in_tournament(tournament, side_competition_id: int) -> bool:
    side_competition = SideComp.query.get(side_competition_id)
    return side_competition is not None and side_competition.event == tournament.url


@bp.post("/tournaments/<tournament_id>/side-competitions")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_side_competition(tournament):
    """JSON ``{name, type, description, allowed_weapons, only_show_top_n_results, schedule}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    return _detail_response(
        SideCompService.create(
            tournament.url,
            **token_actor(),
            name=body.get("name", ""),
            type=body.get("type", ""),
            description=body.get("description"),
            allowed_weapons=body.get("allowed_weapons"),
            only_show_top_n_results=body.get("only_show_top_n_results"),
            schedule=body.get("schedule"),
        ),
        ok_status_code=201,
    )


@bp.patch(SIDE_COMPETITION_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_side_competition(tournament, side_competition_id: int):
    """Changes the keys present in the JSON body; ``only_show_top_n_results: null`` shows every result."""
    if not _in_tournament(tournament, side_competition_id):
        return error("Side competition not found", 404)
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    changes = {key: body[key] for key in UPDATABLE_KEYS if key in body}
    return _detail_response(SideCompService.update(side_competition_id, **token_actor(), **changes))


@bp.delete(SIDE_COMPETITION_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_side_competition(tournament, side_competition_id: int):
    """Deletes the side competition with its registrations and results."""
    if not _in_tournament(tournament, side_competition_id):
        return error("Side competition not found", 404)
    return result_response(
        SideCompService.delete(side_competition_id, **token_actor()), lambda _: {}, ok_status_code=204
    )


@bp.post(f"{SIDE_COMPETITION_PATH}/finalize")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def finalize_side_competition(tournament, side_competition_id: int):
    """Permanently finalizes the results."""
    if not _in_tournament(tournament, side_competition_id):
        return error("Side competition not found", 404)
    return _detail_response(SideCompService.finalize(side_competition_id, **token_actor()))
