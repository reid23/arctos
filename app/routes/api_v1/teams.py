"""``/api/v1/teams``: team directory and profiles."""

from __future__ import annotations

from flask import jsonify, request

from app.serializers.api_v1_serializer import team_profile_json, team_registrations_json, team_summary_json
from app.utils.oauth_server import require_bearer
from models import Team

from . import bp, error, paginated


@bp.get("/teams")
@require_bearer()
def list_teams():
    """Team accounts by name, 50 per page. Query: ``search`` (name or id), ``page``."""
    query = Team.query.filter(Team.placeholder_event.is_(None))
    if search := request.args.get("search", "").strip():
        query = query.filter(Team.name.contains(search) | Team.id.contains(search))
    teams, page = paginated(query, Team.name.asc())
    return jsonify({"teams": [team_summary_json(t) for t in teams]} | page)


@bp.get("/teams/<team_id>")
@require_bearer()
def get_team(team_id: str):
    """Profile with the team's registrations and their rosters."""
    team = Team.query.get(team_id)
    if team is None:
        return error("Team not found", 404)
    if team.placeholder_event:
        from models import Tournament

        tournament = Tournament.query.get(team.placeholder_event)
        if tournament is None or not tournament.published:
            return error("Team not found", 404)
    return jsonify({"team": team_profile_json(team) | {"registrations": team_registrations_json(team.id)}})
