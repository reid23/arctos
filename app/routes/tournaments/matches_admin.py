"""Tournament match / field / tag administrative CRUD endpoints.

Part of the ``tournaments`` blueprint. Uses the same Blueprint object
defined in :mod:`app.routes.tournaments.__init__`. The work happens in
:class:`~app.services.schedule_edit_service.ScheduleEditService` and
:class:`~app.services.tournament_management_service.TournamentManagementService`,
which the public API shares.
"""

from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from app.services.permission_service import PermissionService
from app.services.schedule_edit_service import ScheduleEditService
from app.services.tournament_management_service import TournamentManagementService
from app.utils.result_helpers import result_response

from . import bp


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


def _success(_value) -> dict:
    return {"success": True}


def _created_id(row) -> dict:
    return {"success": True, "id": row.id}


@bp.route("/tournaments/<tournament_url>/matches/<match_id>", methods=["PUT"])
@login_required
def update_match_api(tournament_url, match_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(ScheduleEditService.update_match(tournament_url, match_id, data), _success)


@bp.route("/tournaments/<tournament_url>/fields/<int:field_id>", methods=["PUT"])
@login_required
def update_field_api(tournament_url, field_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(
        TournamentManagementService.rename_field(tournament_url, field_id, data.get("name", "")), _success
    )


@bp.route("/tournaments/<tournament_url>/matches", methods=["POST"])
@login_required
def create_match_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(
        ScheduleEditService.create_match(tournament_url, data),
        lambda match: {"success": True, "uuid": match.uuid},
    )


@bp.route("/tournaments/<tournament_url>/matches/<match_id>", methods=["DELETE"])
@login_required
def delete_match_api(tournament_url, match_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return result_response(ScheduleEditService.delete_match(tournament_url, match_id), _success)


@bp.route("/tournaments/<tournament_url>/matches/bulk-length", methods=["POST"])
@login_required
def bulk_match_length_api(tournament_url):
    """Set ``nominal_length`` on many matches at once; see :meth:`ScheduleEditService.set_match_lengths`."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(
        ScheduleEditService.set_match_lengths(tournament_url, data), lambda outcome: {"success": True, **outcome}
    )


@bp.route("/tournaments/<tournament_url>/break-groups", methods=["POST"])
@login_required
def create_break_group_api(tournament_url):
    """Create one BREAK/STATBREAK/JOIN row per field, sharing name/length."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(
        ScheduleEditService.create_break_group(tournament_url, data),
        lambda rows: {"success": True, "name": rows[0].name, "uuids": [m.uuid for m in rows]},
    )


@bp.route("/tournaments/<tournament_url>/break-groups/<name>", methods=["PUT"])
@login_required
def update_break_group_api(tournament_url, name):
    """Edit every same-name structural row at once (length/start_time/fields)."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    return result_response(ScheduleEditService.update_break_group(tournament_url, name, data), _success)


@bp.route("/tournaments/<tournament_url>/break-groups/<name>", methods=["DELETE"])
@login_required
def delete_break_group_api(tournament_url, name):
    """Delete every same-name structural row in the group."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return result_response(ScheduleEditService.delete_break_group(tournament_url, name), _success)


@bp.route("/tournaments/<tournament_url>/fields", methods=["POST"])
@login_required
def create_field_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400
    return result_response(TournamentManagementService.create_field(tournament_url, data["name"]), _created_id)


@bp.route("/tournaments/<tournament_url>/fields/<int:field_id>", methods=["DELETE"])
@login_required
def delete_field_api(tournament_url, field_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return result_response(TournamentManagementService.delete_field(tournament_url, field_id), _success)


@bp.route("/tournaments/<tournament_url>/tags", methods=["POST"])
@login_required
def create_tag_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400
    return result_response(
        TournamentManagementService.create_tag(tournament_url, data["name"], data.get("expression")), _created_id
    )


@bp.route("/tournaments/<tournament_url>/tags/<int:tag_id>", methods=["DELETE"])
@login_required
def delete_tag_api(tournament_url, tag_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return result_response(TournamentManagementService.delete_tag(tournament_url, tag_id), _success)


@bp.route("/tournaments/<tournament_url>/tags/<int:tag_id>", methods=["PUT"])
@login_required
def update_tag_api(tournament_url, tag_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400
    return result_response(TournamentManagementService.update_tag(tournament_url, tag_id, data), _success)


@bp.route("/tournaments/<tournament_url>/script-variables", methods=["POST"])
@login_required
def create_script_variable_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "Name required"}), 400
    return result_response(
        TournamentManagementService.create_script_variable(
            tournament_url, data.get("name") or "", data.get("expression") or ""
        ),
        _created_id,
    )


@bp.route("/tournaments/<tournament_url>/script-variables/<int:var_id>", methods=["PUT"])
@login_required
def update_script_variable_api(tournament_url, var_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    return result_response(
        TournamentManagementService.update_script_variable(
            tournament_url, var_id, data.get("name"), data.get("expression")
        ),
        _success,
    )


@bp.route("/tournaments/<tournament_url>/script-variables/<int:var_id>", methods=["DELETE"])
@login_required
def delete_script_variable_api(tournament_url, var_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return result_response(TournamentManagementService.delete_script_variable(tournament_url, var_id), _success)
