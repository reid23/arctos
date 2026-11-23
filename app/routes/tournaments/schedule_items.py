"""Organizer editing and public details for non-match schedule items."""

from flask import jsonify, request
from flask_login import current_user

from app.error_values import Err, Ok
from app.services import schedule_item_service
from app.services.permission_service import PermissionService
from app.services.schedule_item_service import schedule_item_payload
from app.utils.decorators import require_tournament_organizer
from app.utils.result_helpers import result_response
from models import ScheduleItem
from . import bp
from .read import _require_tournament


@bp.route("/tournaments/<tournament_url>/schedule-items/<int:item_id>", methods=["GET"])
def schedule_item_detail(tournament_url, item_id):
    tournament, error = _require_tournament(tournament_url)
    if error:
        return jsonify({"error": "Not found"}), error
    if not PermissionService.can_view_schedule(tournament, current_user):
        return jsonify({"error": "Schedule not published"}), 403
    item = ScheduleItem.query.filter_by(id=item_id, event=tournament_url).first_or_404()
    return jsonify(schedule_item_payload(item))


@bp.route("/tournaments/<tournament_url>/schedule-items", methods=["POST"])
@require_tournament_organizer()
def create_schedule_item(tournament_url):
    match schedule_item_service.create_schedule_item(tournament_url, request.get_json(silent=True)):
        case Ok((item, created)):
            return jsonify(schedule_item_payload(item)), 201 if created else 200
        case Err(error):
            return jsonify({"error": error.message}), error.status_code


@bp.route("/tournaments/<tournament_url>/schedule-items/<int:item_id>", methods=["PUT"])
@require_tournament_organizer()
def update_schedule_item(tournament_url, item_id):
    result = schedule_item_service.update_schedule_item(tournament_url, item_id, request.get_json(silent=True))
    return result_response(result, schedule_item_payload)


@bp.route("/tournaments/<tournament_url>/schedule-items/<int:item_id>", methods=["DELETE"])
@require_tournament_organizer()
def delete_schedule_item(tournament_url, item_id):
    result = schedule_item_service.delete_schedule_item(tournament_url, item_id)
    return result_response(result, lambda _: {"success": True})
