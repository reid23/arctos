"""Schedule items (activities other than matches): ``/api/v1/tournaments/<id>/schedule-items``.

Reading follows the schedule's visibility; writes need a token with the
``manage`` scope whose user organizes the tournament. Shares
:mod:`app.services.schedule_item_service` with the SPA's session routes.
"""

from __future__ import annotations

from flask import jsonify

from app.error_values import Err, Ok
from app.services import schedule_item_service
from app.services.schedule_item_service import schedule_item_payload, schedule_items_payload
from app.utils.oauth_server import require_bearer
from app.utils.result_helpers import public_error_message

from . import (
    bp,
    can_view_schedule,
    error,
    json_object_body,
    result_response,
    with_organized_tournament,
    with_tournament,
)

SCHEDULE_ITEMS_PATH = "/tournaments/<tournament_id>/schedule-items"


@bp.get(SCHEDULE_ITEMS_PATH)
@require_bearer()
@with_tournament
def list_schedule_items(tournament):
    """By start time. 403 while the schedule is unpublished, as for matches."""
    if not can_view_schedule(tournament):
        return error("Schedule not published", 403)
    return jsonify({"schedule_items": schedule_items_payload(tournament.url)})


@bp.post(SCHEDULE_ITEMS_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def create_schedule_item(tournament):
    """201 with the new item; 200 with the saved one when a ``request_id`` retry matches it."""
    match schedule_item_service.create_schedule_item(tournament.url, json_object_body()):
        case Ok((item, created)):
            return jsonify({"schedule_item": schedule_item_payload(item)}), 201 if created else 200
        case Err(failure):
            return error(public_error_message(failure), failure.status_code)


@bp.put(f"{SCHEDULE_ITEMS_PATH}/<int:item_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_schedule_item(tournament, item_id: int):
    return result_response(
        schedule_item_service.update_schedule_item(tournament.url, item_id, json_object_body()),
        lambda item: {"schedule_item": schedule_item_payload(item)},
    )


@bp.delete(f"{SCHEDULE_ITEMS_PATH}/<int:item_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def delete_schedule_item(tournament, item_id: int):
    return result_response(
        schedule_item_service.delete_schedule_item(tournament.url, item_id), lambda _: {}, ok_status_code=204
    )
