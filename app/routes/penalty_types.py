"""Penalty-type CRUD and penalty-history routes.

Hosts the ``penalty_types`` blueprint at ``/_api``. Tournament-scoped and
league-scoped penalty types are conceptually parallel concepts, so they
share this module:

- ``/<tournament_url>/penalty-types`` (GET, POST) - list and create.
- ``/<tournament_url>/penalty-types/<pt_id>`` (PATCH, DELETE) - update and delete.
- ``/<tournament_url>/players/<player_id>/penalty-history`` (GET) - player's penalty events.
- ``/leagues/<league_url>/penalty-types`` (GET, POST) - list and create.
- ``/leagues/<league_url>/penalty-types/<pt_id>`` (PATCH, DELETE) - update and delete.

The writes live in :class:`~app.services.penalty_type_service.PenaltyTypeService`.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.utils.decorators import check_tournament_organizer
from app.serializers.league_serializer import require_league
from app.services._common import Scope
from app.services.penalty_type_service import PenaltyTypeService, penalty_type_payload
from app.services.permission_service import PermissionService
from app.utils.helpers import match_event_urls_for_penalties
from app.utils.result_helpers import result_response
from models import Match, MatchNote, PenaltyType, Point, Tournament, db

bp = Blueprint("penalty_types", __name__, url_prefix="/_api")


def _tournament_scope(tournament) -> Scope:
    return Scope.league(tournament.league_id) if tournament.league_id else Scope.event(tournament.url)


def _created_response(result):
    return result_response(
        result, lambda penalty_type: {"success": True, "penalty_type": penalty_type_payload(penalty_type)}
    )


def _success_response(result):
    return result_response(result, lambda _: {"success": True})


@bp.route("/leagues/<league_url>/penalty-types", methods=["GET"])
def get_league_penalty_types(league_url):
    """Get all penalty types for a league."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    return jsonify(
        {
            "penalty_types": [
                penalty_type_payload(t) for t in PenaltyTypeService.list_for_scope(Scope.league(league_url))
            ]
        }
    )


@bp.route("/leagues/<league_url>/penalty-types", methods=["POST"])
@login_required
def create_league_penalty_type(league_url):
    """Create a new penalty type for a league."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Forbidden"}), 403
    return _created_response(PenaltyTypeService.create(Scope.league(league_url), request.get_json()))


@bp.route("/leagues/<league_url>/penalty-types/<int:pt_id>", methods=["PATCH"])
@login_required
def update_league_penalty_type(league_url, pt_id):
    """Update a league penalty type."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Forbidden"}), 403
    return _success_response(PenaltyTypeService.update(Scope.league(league_url), pt_id, request.get_json()))


@bp.route("/leagues/<league_url>/penalty-types/<int:pt_id>", methods=["DELETE"])
@login_required
def delete_league_penalty_type(league_url, pt_id):
    """Delete a league penalty type."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Forbidden"}), 403
    return _success_response(PenaltyTypeService.delete(Scope.league(league_url), pt_id))


@bp.route("/<tournament_url>/penalty-types", methods=["GET"])
def get_penalty_types(tournament_url):
    """Get all penalty types for a tournament (league's if league event, else event's)."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    return jsonify(
        {
            "penalty_types": [
                penalty_type_payload(t) for t in PenaltyTypeService.list_for_scope(_tournament_scope(tournament))
            ]
        }
    )


@bp.route("/<tournament_url>/penalty-types", methods=["POST"])
@login_required
def create_penalty_type(tournament_url):
    """Create a new penalty type (league or event scope)."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if not check_tournament_organizer(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return _created_response(PenaltyTypeService.create(_tournament_scope(tournament), request.get_json()))


@bp.route("/<tournament_url>/penalty-types/<int:pt_id>", methods=["PATCH"])
@login_required
def update_penalty_type(tournament_url, pt_id):
    """Update a penalty type."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if not check_tournament_organizer(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return _success_response(PenaltyTypeService.update(_tournament_scope(tournament), pt_id, request.get_json()))


@bp.route("/<tournament_url>/penalty-types/<int:pt_id>", methods=["DELETE"])
@login_required
def delete_penalty_type(tournament_url, pt_id):
    """Delete a penalty type."""
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if not check_tournament_organizer(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    return _success_response(PenaltyTypeService.delete(_tournament_scope(tournament), pt_id))


@bp.route("/<tournament_url>/players/<player_id>/penalty-history", methods=["GET"])
@login_required
def get_player_penalty_history(tournament_url, player_id):
    """List all penalties for a player in this tournament (chronological).
    For league events, includes penalties from all matches in the league.
    point_id: the point row from which the user opened the penalties modal; notes
    for that point get is_current_point=True so the UI can show delete only for them.
    """
    if not check_tournament_organizer(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    event_urls = match_event_urls_for_penalties(tournament)
    current_match_id = request.args.get("match_id")
    current_point_id = request.args.get("point_id")  # point from which Penalties button was clicked
    notes = (
        db.session.query(MatchNote, Match.name, Point.set_number, MatchNote.created_at)
        .join(Match, MatchNote.match == Match.uuid)
        .filter(
            Match.event.in_(event_urls),
            MatchNote.target == "player",
            MatchNote.player_id == player_id,
        )
        .outerjoin(Point, MatchNote.point_id == Point.uuid)
        .order_by(MatchNote.created_at.asc())
        .all()
    )
    penalty_type_ids = {n[0].penalty_type_id for n in notes if n[0].penalty_type_id}
    pt_map = {}
    if penalty_type_ids:
        for pt in PenaltyType.query.filter(PenaltyType.id.in_(penalty_type_ids)).all():
            pt_map[pt.id] = pt.name
    rows = []
    for note, match_name, set_number, created_at in notes:
        pt_name = pt_map.get(note.penalty_type_id) if note.penalty_type_id else (note.text or "Other")
        point_label = f"Set {set_number}" if set_number else "-"
        date_str = created_at.strftime("%m/%d") if created_at else "-"
        is_current = str(note.match) == current_match_id if current_match_id else False
        is_current_point = str(note.point_id) == current_point_id if current_point_id and note.point_id else False
        rows.append(
            {
                "penalty_type_name": pt_name,
                "match_name": match_name or "-",
                "point_label": point_label,
                "date": date_str,
                "is_current_match": is_current,
                "is_current_point": is_current_point,
                "note_uuid": note.uuid,
            }
        )
    return jsonify({"penalties": rows})
