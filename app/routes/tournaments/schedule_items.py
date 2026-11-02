"""Organizer editing and public details for non-match schedule items."""

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.services.permission_service import PermissionService
from app.services.schedule_item_service import schedule_item_payload, validate_schedule_item, schedule_item_matches
from app.utils.decorators import require_tournament_organizer
from models import ScheduleItem, db
from . import bp
from .read import _require_tournament


def _creation_replay(item, values):
    if not schedule_item_matches(item, values):
        return jsonify(
            {"error": "This activity was already saved. Close this form and edit it from the schedule."}
        ), 409
    return jsonify(schedule_item_payload(item))


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
    try:
        values = validate_schedule_item(tournament_url, request.get_json(silent=True))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    request_id = values["request_id"]
    existing = ScheduleItem.query.filter_by(event=tournament_url, request_id=request_id).first() if request_id else None
    if existing:
        return _creation_replay(existing, values)
    item = ScheduleItem(event=tournament_url, **values)
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = (
            ScheduleItem.query.filter_by(event=tournament_url, request_id=request_id).first() if request_id else None
        )
        if existing:
            return _creation_replay(existing, values)
        raise
    return jsonify(schedule_item_payload(item)), 201


@bp.route("/tournaments/<tournament_url>/schedule-items/<int:item_id>", methods=["PUT"])
@require_tournament_organizer()
def update_schedule_item(tournament_url, item_id):
    item = ScheduleItem.query.filter_by(id=item_id, event=tournament_url).first_or_404()
    try:
        values = validate_schedule_item(tournament_url, request.get_json(silent=True))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    values.pop("request_id", None)
    for key, value in values.items():
        setattr(item, key, value)
    db.session.commit()
    db.session.expire(item)
    return jsonify(schedule_item_payload(item))


@bp.route("/tournaments/<tournament_url>/schedule-items/<int:item_id>", methods=["DELETE"])
@require_tournament_organizer()
def delete_schedule_item(tournament_url, item_id):
    item = ScheduleItem.query.filter_by(id=item_id, event=tournament_url).first_or_404()
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})
