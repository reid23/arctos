"""Resolve an explicitly selected team without changing the signed-in account."""

from collections.abc import Mapping

from flask import abort, jsonify, make_response, request
from flask_login import current_user

from app.services.permission_service import PermissionService
from app.utils.user_helpers import is_team


def requested_team_id() -> str:
    payload = request.get_json(silent=True) if request.is_json else request.form
    selected = request.args.get("team_id") or (payload.get("team_id") if isinstance(payload, Mapping) else None)
    if selected is None and is_team(current_user):
        selected = current_user.id
    if not isinstance(selected, str) or not PermissionService.can_manage_team(selected, current_user):
        abort(make_response(jsonify({"error": "You do not have permission to manage this team."}), 403))
    return selected
