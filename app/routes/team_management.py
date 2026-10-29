"""Team management with an explicit team and event in every URL."""

from datetime import date

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.services._common import Scope
from app.services.permission_service import PermissionService
from app.services.registration_service import RegistrationService
from app.services.team_management_service import change_roster
from app.utils.datetime_helpers import dt_iso, now_utc_naive
from app.utils.name_validation import team_pseudonym_char_error
from app.utils.result_helpers import json_from_result
from app.utils.user_helpers import is_team
from models import League, Player, PlayerRegistration, Team, TeamManager, TeamRegistration, Tournament, db

bp = Blueprint("team_management", __name__, url_prefix="/_api/teams")


@bp.errorhandler(HTTPException)
def request_error(error):
    return jsonify({"success": False, "error": error.description}), error.code


def _team(team_id, owner=False):
    team = db.get_or_404(Team, team_id)
    if owner:
        allowed = is_team(current_user) and current_user.id == team_id
    else:
        allowed = PermissionService.can_manage_team(team_id, current_user)
    if not allowed:
        abort(
            403, description="Only the team account can change managers." if owner else "You do not manage this team."
        )
    return team


def _player(player):
    return {"id": player.id, "name": player.name, "profile_photo": player.profile_photo}


def _scope(kind, slug):
    if kind == "leagues":
        parent = db.get_or_404(League, slug)
        visible = parent.published or PermissionService.is_league_organizer(slug, current_user)
        scope = Scope.league(slug)
    elif kind == "events":
        parent = db.get_or_404(Tournament, slug)
        visible = PermissionService.can_view_tournament(slug, current_user)
        if not visible:
            abort(403)
        if parent.league_id:
            return _scope("leagues", parent.league_id)
        scope = Scope.event(slug)
    else:
        abort(404)
    if not visible:
        abort(403)
    return scope, parent, parent.registrable_config


def _filters(scope):
    return {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}


@bp.get("/<team_id>/management")
@login_required
def management(team_id):
    team = _team(team_id)
    registrations = []
    for reg in TeamRegistration.query.filter_by(team=team_id, status=TeamRegistrationStatus.CONFIRMED).all():
        parent = db.session.get(League, reg.league_id) if reg.league_id else db.session.get(Tournament, reg.event)
        visible = parent is not None and (
            parent.published
            or (
                PermissionService.is_league_organizer(parent.url, current_user)
                if reg.league_id
                else PermissionService.is_tournament_organizer_of(parent, current_user)
            )
        )
        if visible:
            scope = Scope.league(reg.league_id) if reg.league_id else Scope.event(reg.event)
            registrations.append(
                {
                    "slug": parent.url,
                    "name": parent.name,
                    "start_date": dt_iso(parent.start_date) if reg.event else None,
                    "kind": "leagues" if reg.league_id else "events",
                    "pseudonym": reg.pseudonym,
                    "pending_count": PlayerRegistration.query.filter_by(
                        **_filters(scope), team=team_id, status=RegistrationStatus.PENDING_TEAM_APPROVAL
                    ).count(),
                }
            )
    today = now_utc_naive().date()

    def registration_order(row):
        day = date.fromisoformat(row["start_date"][:10]) if row["start_date"] else today
        past = day < today
        return (row["pending_count"] == 0, past, -day.toordinal() if past else day.toordinal(), row["name"])

    registrations.sort(key=registration_order)
    return jsonify(
        {
            "team": {"id": team.id, "name": team.name, "profile_photo": team.profile_photo},
            "is_owner": is_team(current_user) and current_user.id == team_id,
            "registrations": registrations,
        }
    )


@bp.get("/<team_id>/managers")
@login_required
def managers(team_id):
    _team(team_id, owner=True)
    rows = (
        Player.query.join(TeamManager, TeamManager.player_id == Player.id)
        .filter(TeamManager.team_id == team_id)
        .order_by(Player.name, Player.id)
        .all()
    )
    return jsonify({"players": [_player(player) for player in rows]})


@bp.get("/<team_id>/manager-candidates")
@login_required
def manager_candidates(team_id):
    _team(team_id, owner=True)
    search = request.args.get("search", "").strip()
    if not search:
        return jsonify({"players": []})
    assigned = TeamManager.query.with_entities(TeamManager.player_id).filter_by(team_id=team_id)
    rows = (
        Player.query.filter(
            or_(Player.name.contains(search, autoescape=True), Player.id.contains(search, autoescape=True)),
            ~Player.id.in_(assigned),
        )
        .order_by(Player.name, Player.id)
        .limit(20)
        .all()
    )
    return jsonify({"players": [_player(player) for player in rows]})


@bp.route("/<team_id>/managers/<player_id>", methods=["PUT", "DELETE"])
@login_required
def set_manager(team_id, player_id):
    _team(team_id, owner=True)
    db.get_or_404(Player, player_id)
    row = db.session.get(TeamManager, (team_id, player_id))
    if request.method == "PUT" and row is None:
        db.session.add(TeamManager(team_id=team_id, player_id=player_id))
    elif request.method == "DELETE" and row:
        db.session.delete(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if request.method != "PUT" or db.session.get(TeamManager, (team_id, player_id)) is None:
            raise
    return jsonify({"success": True})


@bp.route("/<team_id>/<kind>/<slug>/registration", methods=["GET", "POST", "PUT", "DELETE"])
@login_required
def event_registration(team_id, kind, slug):
    team = _team(team_id)
    scope, parent, cfg = _scope(kind, slug)
    filters = _filters(scope)
    reg = TeamRegistration.query.filter_by(team=team_id, **filters).first()
    if request.method in {"POST", "PUT"}:
        data = request.get_json(silent=True)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("pseudonym"), str)
            or not isinstance(data.get("shortname", ""), str)
        ):
            abort(400, description="Provide a team name and optional short name as text.")
    if request.method == "POST":
        return json_from_result(
            RegistrationService.register_team(
                scope, team_id, data.get("pseudonym", ""), shortname=data.get("shortname")
            )
        )
    if request.method == "DELETE":
        return json_from_result(RegistrationService.deregister_team(scope, team_id))
    if request.method == "PUT":
        if not cfg or not cfg.team_registration_open:
            abort(403, description="Registration changes are closed.")
        if not reg or reg.status != TeamRegistrationStatus.CONFIRMED:
            abort(404, description="Team registration not found.")
        data = request.get_json(silent=True) or {}
        name, shortname = data.get("pseudonym"), data.get("shortname", "")
        if not isinstance(name, str) or not name.strip() or not isinstance(shortname, str):
            abort(400, description="Enter a team name and a short name if needed.")
        if message := team_pseudonym_char_error(name.strip()):
            abort(400, description=message)
        reg.pseudonym, reg.shortname = name.strip(), shortname.strip() or None
        db.session.commit()
        return jsonify({"success": True})
    confirmed = reg is not None and reg.status == TeamRegistrationStatus.CONFIRMED
    roster = []
    if confirmed:
        for member, player in (
            db.session.query(PlayerRegistration, Player)
            .join(Player, Player.id == PlayerRegistration.player)
            .filter(
                *(getattr(PlayerRegistration, key) == value for key, value in filters.items()),
                PlayerRegistration.team == team_id,
            )
            .order_by(Player.name, Player.id)
            .all()
        ):
            roster.append(
                {
                    "registration_id": member.id,
                    "player": _player(player),
                    "status": member.status.value,
                    "jersey_name": member.jersey_name,
                    "jersey_number": member.jersey_number,
                    "paid": bool(member.paid),
                    "amount_paid": member.amount_paid or 0,
                }
            )
    roster.sort(
        key=lambda row: (
            {"PENDING_TEAM_APPROVAL": 0, "CONFIRMED": 1}.get(row["status"], 2),
            row["player"]["name"],
            row["player"]["id"],
        )
    )
    return jsonify(
        {
            "team": {"id": team.id, "name": team.name, "profile_photo": team.profile_photo},
            "event": {"name": parent.name, "slug": parent.url, "kind": "leagues" if scope.is_league else "events"},
            "registered": confirmed,
            "pseudonym": reg.pseudonym if confirmed else team.name,
            "shortname": reg.shortname if confirmed else None,
            "registration_open": bool(cfg and cfg.team_registration_open),
            "roster_open": bool(cfg and cfg.player_registration_open),
            "roster_limit": cfg.max_team_size_roster if cfg else None,
            "team_fee": cfg.team_reg_fee if cfg else None,
            "terms_link": cfg.terms_link if cfg else None,
            "payment_info": cfg.payment_info if cfg else None,
            "paid": bool(reg.paid) if confirmed else False,
            "roster": roster,
        }
    )


@bp.get("/<team_id>/<kind>/<slug>/roster-candidates")
@login_required
def roster_candidates(team_id, kind, slug):
    _team(team_id)
    scope, _, _ = _scope(kind, slug)
    search = request.args.get("search", "").strip()
    rows = (
        db.session.query(PlayerRegistration, Player)
        .join(Player, Player.id == PlayerRegistration.player)
        .filter(
            *(getattr(PlayerRegistration, key) == value for key, value in _filters(scope).items()),
            PlayerRegistration.team.is_(None),
            PlayerRegistration.status == RegistrationStatus.CONFIRMED,
            or_(Player.name.contains(search, autoescape=True), Player.id.contains(search, autoescape=True)),
        )
        .order_by(Player.name, Player.id)
        .limit(30)
        .all()
    )
    return jsonify({"players": [(_player(player) | {"registration_id": reg.id}) for reg, player in rows]})


@bp.post("/<team_id>/<kind>/<slug>/roster/<int:registration_id>/<action>")
@login_required
def roster_action(team_id, kind, slug, registration_id, action):
    _team(team_id)
    scope, _, _ = _scope(kind, slug)
    return json_from_result(change_roster(scope, team_id, registration_id, action))
