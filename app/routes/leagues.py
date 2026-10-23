"""League listing, detail, results, and management routes.

Hosts the ``leagues`` blueprint at ``/_api``:

Read:
- ``/leagues`` (GET), ``/leagues/organized`` (GET)
- ``/leagues/<league_url>`` (GET)
- ``/leagues/<league_url>/results`` (GET)
- ``/leagues/<league_url>/results/team/<team_id>`` (GET)

Management:
- ``/leagues/<league_url>/settings`` (POST)
- ``/leagues/<league_url>/banner-photo`` (POST, DELETE)
- ``/leagues/<league_url>/add-to`` (POST), ``/leagues/<league_url>/remove-to`` (POST)
- ``/leagues/<league_url>/delete`` (POST)
- ``/leagues/<league_url>/manage`` (GET)
- ``/leagues/<league_url>/invitations`` (GET)

Registration verbs, penalty-types, and waiver uploads for leagues live
in their dedicated blueprints, not here.
"""

from __future__ import annotations

import collections

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app.domain.enums import (
    MatchStatus,
    RegistrationStatus,
    TeamRegistrationStatus,
)
from app.serializers.registration_serializer import player_reg_waiver_api, serialize_manage
from app.serializers.tournament_serializer import (
    team_name_for_match,
    team_shortname_for_match,
    tournament_to_dict,
)
from app.utils.datetime_helpers import dt_iso
from app.serializers.league_serializer import league_to_dict, require_league
from app.services._common import Scope, current_user_type
from app.services.permission_service import PermissionService
from app.services.registration_resolver import (
    is_player_registered,
    is_team_registered,
    player_registrations_for_scope,
    team_registrations_for_scope,
    to_entries_for_tournament,
)
from app.services.team_stats_service import compute_team_stats
from app.utils.user_helpers import is_player, is_team
from app.utils.profile_banner_helpers import (
    BannerUploadError,
    read_banner_upload,
    remove_banner,
    replace_banner,
)
from models import (
    League,
    Match,
    PenaltyType,
    Player,
    PlayerRegistration,
    Point,
    Team,
    TeamRegistration,
    TO,
    Tournament,
    db,
)

bp = Blueprint("leagues", __name__, url_prefix="/_api")


@bp.route("/leagues/<league_url>/banner-photo", methods=["POST", "DELETE"])
@login_required
def league_banner_photo(league_url):
    """Replace or remove a banner using the league's typed TO permissions."""
    league = League.query.get_or_404(league_url)
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Only league organizers can change this banner."}), 403
    try:
        if request.method == "DELETE":
            remove_banner(league, "league", league.url)
            return jsonify({"success": True})
        path = replace_banner(league, "league", league.url, read_banner_upload())
        return jsonify({"success": True, "path": path})
    except BannerUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/leagues", methods=["GET"])
def leagues_list():
    """List public leagues and the caller's drafts, with public round metadata."""
    visible = League.published == True
    if current_user.is_authenticated:
        organized_ids = db.session.query(TO.league_id).filter(
            TO.user_id == current_user.id,
            TO.user_type == current_user_type(),
            TO.league_id.isnot(None),
        )
        visible = or_(visible, League.url.in_(organized_ids))
    leagues = League.query.filter(visible).all()

    team_counts = {l.url: 0 for l in leagues}
    event_dates = {l.url: {"start_date": None, "end_date": None, "event_count": 0} for l in leagues}
    if leagues:
        league_ids = [l.url for l in leagues]
        # Derive season ranges from public rounds only, in one query for the
        # entire directory. An empty league has no inferred end date.
        ranges = (
            db.session.query(
                Tournament.league_id,
                func.min(Tournament.start_date),
                func.max(func.coalesce(Tournament.end_date, Tournament.start_date)),
                func.count(Tournament.url),
            )
            .filter(Tournament.league_id.in_(league_ids), Tournament.published == True)
            .group_by(Tournament.league_id)
            .all()
        )
        for lid, start_date, end_date, event_count in ranges:
            event_dates[lid] = {
                "start_date": dt_iso(start_date),
                "end_date": dt_iso(end_date),
                "event_count": int(event_count),
            }
        # Team counts per league (confirmed registrations only).
        counts = (
            db.session.query(TeamRegistration.league_id, func.count(TeamRegistration.id))
            .filter(TeamRegistration.status == TeamRegistrationStatus.CONFIRMED)
            .filter(TeamRegistration.league_id.in_(league_ids))
            .group_by(TeamRegistration.league_id)
            .all()
        )
        for lid, count in counts:
            if lid:
                team_counts[lid] = int(count or 0)

    # Current user registration status per league (team or player)
    user_reg_status = {}
    if current_user.is_authenticated:
        for l in leagues:
            reg = None
            if is_team(current_user):
                reg = TeamRegistration.query.filter_by(league_id=l.url, team=current_user.id).first()
                if reg:
                    user_reg_status[l.url] = {
                        "type": "team",
                        "status": (reg.status.value if hasattr(reg.status, "value") else str(reg.status or "")),
                        "paid": bool(reg.paid),
                        "amount_paid": reg.amount_paid or 0.0,
                        "waiver_required": False,
                        "waiver_status": None,
                    }
            elif is_player(current_user):
                reg = PlayerRegistration.query.filter_by(league_id=l.url, player=current_user.id).first()
                if reg:
                    rc = l.registrable_config
                    w = player_reg_waiver_api(reg, rc)
                    user_reg_status[l.url] = {
                        "type": "player",
                        "status": (reg.status.value if hasattr(reg.status, "value") else str(reg.status or "")),
                        "paid": bool(reg.paid),
                        "amount_paid": reg.amount_paid or 0.0,
                        "waiver_required": w["waiver_required"],
                        "waiver_status": w["waiver_status"],
                    }

    return jsonify(
        {
            "leagues": [league_to_dict(l) for l in leagues],
            "event_dates": event_dates,
            "team_counts": team_counts,
            "user_reg_status": user_reg_status,
        }
    )


@bp.route("/leagues/organized", methods=["GET"])
@login_required
def leagues_organized():
    """List leagues that the current user organizes (TO). For tournament create/edit league selector."""
    user_id = current_user.id
    user_type = current_user_type()
    to_entries = TO.query.filter(
        TO.league_id.isnot(None),
        TO.user_id == user_id,
        TO.user_type == user_type,
    ).all()
    result = []
    seen = set()
    for to_entry in to_entries:
        lid = to_entry.league_id
        if not lid or lid in seen:
            continue
        seen.add(lid)
        league = League.query.get(lid)
        if league:
            result.append(league_to_dict(league))
    return jsonify({"leagues": result})


@bp.route("/leagues/<league_url>", methods=["GET"])
def league_detail(league_url):
    """League detail: events (tournaments), teams, TOs, is_current_*_registered."""

    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err

    # Tournaments in this league
    tournaments_in_league = Tournament.query.filter_by(league_id=league_url).order_by(Tournament.start_date).all()
    events = [tournament_to_dict(t) for t in tournaments_in_league]

    # Create a simple object with league_id for resolvers that lack a _for_scope variant
    class LeagueContext:
        def __init__(self, league):
            self.league_id = league.url
            self.url = None

    ctx = LeagueContext(league)
    scope = Scope.league(league_url)

    team_regs = team_registrations_for_scope(scope)
    team_ids = [tr.team for tr in team_regs]
    teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
    all_prs = player_registrations_for_scope(scope, statuses=[RegistrationStatus.CONFIRMED])
    counts_by_team = collections.Counter(pr.team for pr in all_prs if pr.team)
    teams_with_counts = []
    for team_reg in team_regs:
        team = teams_by_id.get(team_reg.team)
        teams_with_counts.append(
            {
                "team_id": team_reg.team,
                "team_name": team.name if team else team_reg.team,
                "pseudonym": team_reg.pseudonym,
                "shortname": team_reg.shortname,
                "player_count": counts_by_team[team_reg.team],
                "registered_at": dt_iso(getattr(team_reg, "registered_at", None)),
                "profile_photo": team.profile_photo if team else None,
            }
        )

    unattached_prs = player_registrations_for_scope(
        scope, unattached_only=True, statuses=[RegistrationStatus.CONFIRMED]
    )
    unattached_player_ids = [pr.player for pr in unattached_prs]
    unattached_players_by_id = (
        {p.id: p for p in Player.query.filter(Player.id.in_(unattached_player_ids)).all()}
        if unattached_player_ids
        else {}
    )
    unattached = [
        {
            "player_id": pr.player,
            "player_name": unattached_players_by_id[pr.player].name
            if pr.player in unattached_players_by_id
            else pr.player,
            "jersey_number": getattr(pr, "jersey_number", None),
            "jersey_name": getattr(pr, "jersey_name", None),
            "registered_at": dt_iso(getattr(pr, "registered_at", None)),
            "profile_photo": getattr(unattached_players_by_id.get(pr.player), "profile_photo", None),
        }
        for pr in unattached_prs
    ]

    # to_entries_for_tournament has no _for_scope variant
    to_rows = to_entries_for_tournament(ctx)
    to_player_ids = [e.user_id for e in to_rows if e.user_type == "player"]
    to_team_ids = [e.user_id for e in to_rows if e.user_type == "team"]
    to_players_by_id = (
        {p.id: p for p in Player.query.filter(Player.id.in_(to_player_ids)).all()} if to_player_ids else {}
    )
    to_teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(to_team_ids)).all()} if to_team_ids else {}
    to_entries = []
    for e in to_rows:
        user = (to_players_by_id if e.user_type == "player" else to_teams_by_id).get(e.user_id)
        user_name = user.name if user else e.user_id
        is_current = (
            current_user.is_authenticated and current_user.id == e.user_id and current_user_type() == e.user_type
        )
        to_entries.append(
            {
                "id": e.id,
                "user_id": e.user_id,
                "user_type": e.user_type,
                "user_name": user_name,
                "is_current_user": is_current,
            }
        )
    is_current_team_registered = False
    is_current_player_registered = False
    if current_user.is_authenticated:
        if is_team(current_user):
            is_current_team_registered = is_team_registered(ctx, current_user.id)
        else:
            is_current_player_registered = is_player_registered(ctx, current_user.id)

    penalty_types = PenaltyType.query.filter_by(league_id=league_url).all()
    penalty_types_data = [{"id": t.id, "name": t.name, "color": t.color, "desc": (t.desc or "")} for t in penalty_types]

    return jsonify(
        {
            "league": league_to_dict(league),
            "events": events,
            "teams_with_counts": teams_with_counts,
            "unattached_players": unattached,
            "to_entries": to_entries,
            "is_current_team_registered": is_current_team_registered,
            "is_current_player_registered": is_current_player_registered,
            "penalty_types": penalty_types_data,
        }
    )


@bp.route("/leagues/<league_url>/results", methods=["GET"])
def league_results(league_url):
    """League standings: aggregate stats across all tournaments in the league."""

    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err

    tournament_urls = [t.url for t in Tournament.query.filter_by(league_id=league_url).all()]
    if not tournament_urls:
        return jsonify(
            {
                "league": league_to_dict(league),
                "teams": [],
            }
        )
    matches = Match.query.filter(
        Match.event.in_(tournament_urls),
        Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]),
    ).all()
    # Use first tournament for pseudonym lookup (any league tournament works)
    first_tournament = Tournament.query.filter_by(league_id=league_url).first()
    include_ribbon = request.args.get("include_ribbon", "").lower() in (
        "1",
        "true",
        "yes",
    )
    teams_list = compute_team_stats(matches, first_tournament, include_ribbon=include_ribbon)
    return jsonify(
        {
            "league": league_to_dict(league),
            "teams": teams_list,
        }
    )


@bp.route("/leagues/<league_url>/results/team/<team_id>", methods=["GET"])
def league_results_team_matches(league_url, team_id):
    """Matches for one team across all tournaments in this league (for expandable row)."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    tournament_urls = [t.url for t in Tournament.query.filter_by(league_id=league_url).all()]
    if not tournament_urls:
        return jsonify({"matches": []})
    matches = (
        Match.query.filter(
            Match.event.in_(tournament_urls),
            Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]),
            (Match.team1 == team_id) | (Match.team2 == team_id),
        )
        .order_by(Match.completed_time, Match.uuid)
        .all()
    )
    match_ids = [m.uuid for m in matches]
    points_by_match = {}
    if match_ids:
        for p in Point.query.filter(Point.match.in_(match_ids)).all():
            points_by_match.setdefault(p.match, []).append(p)
    if matches:
        event_urls = {m.event for m in matches}
        tournaments_by_url = {t.url: t for t in Tournament.query.filter(Tournament.url.in_(event_urls)).all()}
    else:
        tournaments_by_url = {}
    match_list = []
    for m in matches:
        tournament = tournaments_by_url.get(m.event)
        if not tournament:
            continue
        team1_name = team_name_for_match(tournament, m, "team1")
        team2_name = team_name_for_match(tournament, m, "team2")
        points_list = points_by_match.get(m.uuid, [])
        set_scores = {}
        for p in points_list:
            if getattr(p, "rerolled", False):
                continue
            sn = getattr(p, "set_number", None) or 1
            set_scores.setdefault(sn, {"set_number": sn, "team1_points": 0, "team2_points": 0})
            w = getattr(p, "winner", None)
            if w == "TEAM1":
                set_scores[sn]["team1_points"] += 1
            elif w == "TEAM2":
                set_scores[sn]["team2_points"] += 1
        sets_list = sorted(set_scores.values(), key=lambda x: x["set_number"])
        your_side = "TEAM1" if m.team1 == team_id else "TEAM2"
        match_list.append(
            {
                "uuid": m.uuid,
                "name": m.name,
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_shortname": team_shortname_for_match(tournament, m, "team1"),
                "team2_shortname": team_shortname_for_match(tournament, m, "team2"),
                "match_winner": m.match_winner.value if m.match_winner else None,
                "your_side": your_side,
                "sets": sets_list,
                "ribbon": getattr(m, "ribbon", False),
                "event": m.event,
            }
        )
    return jsonify({"matches": match_list})


@bp.route("/leagues/<league_url>/settings", methods=["POST"])
@login_required
def league_update_settings(league_url):
    """Update league settings (TO only)."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Only league organizers can update settings"}), 403

    data = request.get_json() or {}
    if "about" in data:
        league.about = data["about"]
    rc = league.registrable_config
    if rc:
        if "team_reg_fee" in data:
            rc.team_reg_fee = float(data["team_reg_fee"]) if data["team_reg_fee"] is not None else 0.0
        if "player_reg_fee" in data:
            rc.player_reg_fee = float(data["player_reg_fee"]) if data["player_reg_fee"] is not None else 0.0
        if data.get("require_waiver_signature") is False:
            rc.waiver_filepath = None
            rc.waiver_sha256 = None
        if "team_registration_open" in data:
            rc.team_registration_open = bool(data["team_registration_open"])
        if "player_registration_open" in data:
            rc.player_registration_open = bool(data["player_registration_open"])
        if "terms_link" in data:
            rc.terms_link = data["terms_link"] or None
        if "payment_info" in data:
            rc.payment_info = data["payment_info"] or None
        if "n_max_teams" in data:
            v = data["n_max_teams"]
            rc.n_max_teams = int(v) if v is not None and (v != "" if isinstance(v, str) else True) else None
        if "max_team_size_roster" in data:
            v = data["max_team_size_roster"]
            rc.max_team_size_roster = int(v) if v is not None and (v != "" if isinstance(v, str) else True) else None
        if "max_team_size_field" in data:
            v = data["max_team_size_field"]
            rc.max_team_size_field = int(v) if v is not None and (v != "" if isinstance(v, str) else True) else None
    if "published" in data:
        league.published = bool(data["published"])

    db.session.commit()
    return jsonify({"success": True})


@bp.route("/leagues/<league_url>/add-to", methods=["POST"])
@login_required
def league_add_to(league_url):
    """Add a TO to the league."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return (
            jsonify({"success": False, "error": "Only league organizers can add TOs"}),
            403,
        )

    user_id = request.form.get("user_id", "").strip()
    user_type = request.form.get("user_type", "").strip().lower()

    if not user_id or user_type not in ("player", "team"):
        return jsonify({"success": False, "error": "Invalid user ID or type"}), 400

    if user_type == "player":
        user = Player.query.get(user_id)
        if not user:
            return (
                jsonify({"success": False, "error": f'Player "{user_id}" not found'}),
                404,
            )
    else:
        user = Team.query.get(user_id)
        if not user:
            return (
                jsonify({"success": False, "error": f'Team "{user_id}" not found'}),
                404,
            )

    existing = TO.query.filter_by(user_id=user_id, user_type=user_type, league_id=league_url).first()
    if existing:
        return (
            jsonify({"success": False, "error": "This user is already a TO for this league"}),
            400,
        )

    new_to = TO(
        user_id=user_id,
        user_type=user_type,
        event=None,
        league_id=league_url,
    )
    db.session.add(new_to)
    db.session.commit()
    user_name = user.name if user else user_id
    return jsonify({"success": True, "message": f"Added {user_name} as a TO"}), 200


@bp.route("/leagues/<league_url>/remove-to", methods=["POST"])
@login_required
def league_remove_to(league_url):
    """Remove a TO from the league."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return (
            jsonify({"success": False, "error": "Only league organizers can remove TOs"}),
            403,
        )

    to_id = request.form.get("to_id")
    if not to_id:
        return jsonify({"success": False, "error": "TO ID is required"}), 400

    to_to_remove = TO.query.get_or_404(to_id)
    if to_to_remove.league_id != league_url:
        return jsonify({"success": False, "error": "Invalid TO entry"}), 400

    if to_to_remove.user_id == current_user.id and to_to_remove.user_type == current_user_type():
        return (
            jsonify({"success": False, "error": "You cannot remove yourself as a TO"}),
            400,
        )

    if to_to_remove.user_type == "player":
        user = Player.query.get(to_to_remove.user_id)
    else:
        user = Team.query.get(to_to_remove.user_id)
    user_name = user.name if user else to_to_remove.user_id

    db.session.delete(to_to_remove)
    db.session.commit()
    return jsonify({"success": True, "message": f"Removed {user_name} as a TO"}), 200


@bp.route("/leagues/<league_url>/delete", methods=["POST"])
@login_required
def delete_league(league_url):
    """Delete a league. Only league organizers can delete. League must have no events (tournaments)."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err

    if not PermissionService.is_league_organizer(league_url, current_user):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only league organizers can delete the league",
                }
            ),
            403,
        )

    confirm_url = request.form.get("confirm_url", "").strip()
    if confirm_url != league_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Confirmation URL does not match. League not deleted.",
                }
            ),
            400,
        )

    from models import Tournament

    if Tournament.query.filter_by(league_id=league_url).first():
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Remove or delete all events (tournaments) in this league first.",
                }
            ),
            400,
        )

    # Delete in order: registrations, penalty types, TOs, league, registrable_config
    TeamRegistration.query.filter_by(league_id=league_url).delete(synchronize_session=False)
    PlayerRegistration.query.filter_by(league_id=league_url).delete(synchronize_session=False)
    PenaltyType.query.filter_by(league_id=league_url).delete(synchronize_session=False)
    TO.query.filter_by(league_id=league_url).delete(synchronize_session=False)
    rc_id = league.registrable_config_id
    league_name = league.name
    db.session.delete(league)
    if rc_id:
        from models import RegistrableConfig

        rc = RegistrableConfig.query.get(rc_id)
        if rc:
            db.session.delete(rc)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'League "{league_name}" has been permanently deleted.',
            }
        ),
        200,
    )


@bp.route("/leagues/<league_url>/manage", methods=["GET"])
@login_required
def league_manage_api(league_url):
    """League registration management (TO only)."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Forbidden"}), 403

    search_query = (request.args.get("search") or "").strip()
    search_type = (request.args.get("type") or "both").lower()
    return jsonify(serialize_manage(Scope.league(league_url), search_query, search_type, league.registrable_config))


@bp.route("/leagues/<league_url>/invitations", methods=["GET"])
@login_required
def league_invitations_api(league_url):
    """League roster/invitations for a team. Same structure as tournament invitations."""
    if not is_team(current_user):
        return jsonify({"error": "Only teams can view invitations"}), 403

    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err

    team_registration = TeamRegistration.query.filter_by(
        league_id=league_url, team=current_user.id, status=RegistrationStatus.CONFIRMED
    ).first()
    if not team_registration:
        return jsonify({"error": "Not registered"}), 404

    pending_regs = PlayerRegistration.query.filter_by(
        league_id=league_url,
        team=current_user.id,
        status=RegistrationStatus.PENDING_TEAM_APPROVAL,
    ).all()
    pending_with_players = []
    for reg in pending_regs:
        player = Player.query.get(reg.player)
        if player:
            pending_with_players.append({"registration": reg, "player": player})

    current_team_size = PlayerRegistration.query.filter_by(
        league_id=league_url, team=current_user.id, status=RegistrationStatus.CONFIRMED
    ).count()

    all_player_registrations = PlayerRegistration.query.filter_by(league_id=league_url, team=current_user.id).all()
    team_roster = []
    for reg in all_player_registrations:
        player = Player.query.get(reg.player)
        if player:
            team_roster.append({"player": player, "registration": reg})

    tournament_dict = {
        "url": league.url,
        "name": league.name,
        "start_date": "",
        "end_date": None,
        "location": None,
        "published": league.published,
        "max_team_size_roster": (
            getattr(league.registrable_config, "max_team_size_roster", None) if league.registrable_config else None
        ),
        "league": {"league_url": league.url, "name": league.name},
    }
    return jsonify(
        {
            "tournament": tournament_dict,
            "team_registration": {
                "id": team_registration.id,
                "pseudonym": team_registration.pseudonym,
                "shortname": team_registration.shortname,
            },
            "current_team_size": current_team_size,
            "invitations": [
                {
                    "registration": {
                        "id": inv["registration"].id,
                        "jersey_name": inv["registration"].jersey_name,
                        "jersey_number": inv["registration"].jersey_number,
                    },
                    "player": {
                        "id": inv["player"].id,
                        "name": inv["player"].name,
                        "profile_photo": inv["player"].profile_photo,
                    },
                }
                for inv in pending_with_players
            ],
            "team_roster": [
                {
                    "registration": {
                        "id": r["registration"].id,
                        "jersey_name": r["registration"].jersey_name,
                        "jersey_number": r["registration"].jersey_number,
                        "status": (
                            r["registration"].status.value
                            if hasattr(r["registration"].status, "value")
                            else str(r["registration"].status)
                        ),
                        "paid": bool(r["registration"].paid),
                        "amount_paid": r["registration"].amount_paid or 0.0,
                    },
                    "player": {
                        "id": r["player"].id,
                        "name": r["player"].name,
                        "profile_photo": r["player"].profile_photo,
                    },
                }
                for r in team_roster
            ],
        }
    )
