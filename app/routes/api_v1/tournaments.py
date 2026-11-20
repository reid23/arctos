"""``/api/v1/tournaments``: tournaments, fields, rosters, results, and side competitions."""

from __future__ import annotations

from flask import g, jsonify, request
from sqlalchemy import func, or_

from app.domain.enums import MatchStatus
from app.error_values import Ok
from app.serializers.api_v1_serializer import (
    TeamNames,
    organizers_json,
    scope_of,
    scope_players_json,
    scope_teams_json,
    side_competition_detail_json,
    side_competition_json,
    side_competition_results_json,
    standings_json,
    tournament_json,
)
from app.services.permission_service import PermissionService
from app.services.registration_resolver import to_entries_for_tournament
from app.services.sidecomp_service import SideCompService
from app.services.team_stats_service import compute_team_stats
from app.utils.datetime_helpers import now_utc_naive
from app.utils.oauth_server import require_bearer
from models import Field, Match, SideComp, Tournament

from . import bp, error, flag_argument, result_response, sees_unpublished_organized, with_tournament


@bp.get("/tournaments")
@require_bearer()
def list_tournaments():
    """Published tournaments by start date. Query: ``league``, ``when=upcoming|past``."""
    when = request.args.get("when")
    if when not in (None, "upcoming", "past"):
        return error("when must be 'upcoming' or 'past'", 400)

    visible = Tournament.published.is_(True)
    if sees_unpublished_organized():
        tournament_urls, league_urls = PermissionService.organized_scopes(g.oauth_user)
        visible = or_(visible, Tournament.url.in_(tournament_urls), Tournament.league_id.in_(league_urls))
    query = Tournament.query.filter(visible)
    if league := request.args.get("league"):
        query = query.filter(Tournament.league_id == league)
    effective_end = func.coalesce(Tournament.end_date, Tournament.start_date)
    if when == "upcoming":
        query = query.filter(effective_end >= now_utc_naive())
    elif when == "past":
        query = query.filter(effective_end < now_utc_naive())
    tournaments = query.order_by(Tournament.start_date.asc()).all()
    return jsonify({"tournaments": [tournament_json(t) for t in tournaments]})


@bp.get("/tournaments/<tournament_id>")
@require_bearer()
@with_tournament
def get_tournament(tournament):
    organizers = organizers_json(to_entries_for_tournament(tournament))
    return jsonify({"tournament": tournament_json(tournament) | {"organizers": organizers}})


@bp.get("/tournaments/<tournament_id>/fields")
@require_bearer()
@with_tournament
def list_fields(tournament):
    fields = Field.query.filter_by(event=tournament.url).order_by(Field.name).all()
    return jsonify({"fields": [{"id": field.id, "name": field.name} for field in fields]})


@bp.get("/tournaments/<tournament_id>/teams")
@require_bearer()
@with_tournament
def list_tournament_teams(tournament):
    """Confirmed teams (league-wide for league events)."""
    return jsonify({"teams": scope_teams_json(scope_of(tournament))})


@bp.get("/tournaments/<tournament_id>/players")
@require_bearer()
@with_tournament
def list_tournament_players(tournament):
    """Confirmed players (league-wide for league events). Query: ``team`` for one team's roster."""
    return jsonify({"players": scope_players_json(scope_of(tournament), team_id=request.args.get("team"))})


@bp.get("/tournaments/<tournament_id>/results")
@require_bearer()
@with_tournament
def tournament_results(tournament):
    """Standings from finished matches. Query: ``include_ribbon``."""
    matches = Match.query.filter(
        Match.event == tournament.url,
        Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]),
    ).all()
    team_stats = compute_team_stats(matches, tournament, include_ribbon=flag_argument("include_ribbon"))
    return jsonify({"standings": standings_json(team_stats)})


@bp.get("/tournaments/<tournament_id>/side-competitions")
@require_bearer()
@with_tournament
def list_side_competitions(tournament):
    return jsonify(
        {
            "side_competitions": [
                side_competition_json(side_competition, registrant_count)
                for side_competition, registrant_count in SideCompService.list_with_registrant_counts(tournament.url)
            ]
        }
    )


@bp.get("/tournaments/<tournament_id>/side-competitions/<int:side_competition_id>")
@require_bearer()
@with_tournament
def get_side_competition(tournament, side_competition_id):
    match SideCompService.get_with_registrants(side_competition_id):
        case Ok((side_competition, registrants)) if side_competition.event == tournament.url:
            return jsonify(side_competition_detail_json(side_competition, registrants))
    return error("Side competition not found", 404)


@bp.get("/tournaments/<tournament_id>/side-competitions/<int:side_competition_id>/results")
@require_bearer()
@with_tournament
def side_competition_results(tournament, side_competition_id):
    side_competition = SideComp.query.get(side_competition_id)
    if side_competition is None or side_competition.event != tournament.url:
        return error("Side competition not found", 404)
    if not side_competition.results_page_enabled():
        return error("The organizers have hidden this side competition's results", 404)
    return result_response(
        SideCompService.standings(side_competition_id),
        lambda standings: side_competition_results_json(standings, TeamNames(scope_of(tournament))),
    )
