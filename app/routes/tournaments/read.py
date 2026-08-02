"""Tournament read endpoints (list, detail, schedule, results, match, ...).

Part of the ``tournaments`` blueprint. Uses the same Blueprint object
defined in :mod:`app.routes.tournaments.__init__`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import collections
import json

from flask import jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func

from app.domain.enums import (
    MatchStatus,
    RegistrationStatus,
    SetType,
    WinnerSide,
)
from app.serializers.tournament_serializer import (
    team_name_for_match,
    tournament_to_dict,
)
from app.services._common import Scope, current_user_type
from app.services.dual_write import (
    get_camera_timepoint_arrays,
    get_match_player_ids,
    get_match_ref_team_ids,
    get_match_refs_csv,
    get_match_refs_initial_csv,
)
from app.services.match_start_eligibility import (
    get_can_start_and_reasons,
    get_conflicting_match_on_field,
    why_sections_to_dict,
)
from app.services.permission_service import PermissionService
from app.services.registration_resolver import (
    is_player_registered,
    is_team_registered,
    player_registrations_for_scope,
    player_registrations_for_tournament,
    team_registration_for_tournament,
    team_registrations_for_scope,
    team_registrations_for_tournament,
    to_entries_for_tournament,
)
from app.services.team_stats_service import compute_team_stats
from app.services.tournament_service import TournamentService
from app.utils.datetime_helpers import to_iso_z
from app.utils.helpers import (
    can_head_ref_match,
    check_tournament_access,
    get_penalty_types_for_tournament,
    get_registrable_config,
    match_event_urls_for_penalties,
)
from app.utils.player_helpers import (
    get_player_display_from_registration,
)
from app.utils.user_helpers import is_player, is_team
from models import (
    Camera,
    Field,
    League,
    Match,
    MatchNote,
    Player,
    PlayerRegistration,
    Point,
    Tag,
    Team,
    TeamRegistration,
    Tournament,
    db,
)

from . import bp


def dt_iso(dt) -> str | None:
    """Serialise a datetime-like value to an ISO-8601 string.

    Args:
        dt: A :class:`~datetime.datetime` instance or any object with an
            ``isoformat()`` method, or ``None``.

    Returns:
        ISO-8601 string when *dt* is non-null, otherwise ``None``.
    """
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def player_reg_waiver_api(reg, cfg):
    """Waiver fields for API given a PlayerRegistration and RegistrableConfig (or None)."""
    waiver_required = bool(getattr(cfg, "waiver_filepath", None)) if cfg else False
    fp = getattr(cfg, "waiver_filepath", None) if cfg else None
    sha = getattr(cfg, "waiver_sha256", None) if cfg else None
    stored = getattr(reg, "waiver_legal_name_signature_sha256", None) if reg else None
    legal = getattr(reg, "waiver_legal_name_signature", None) if reg else None

    if not waiver_required:
        waiver_status = None
        signature_valid = True
    elif not stored:
        waiver_status = "NOT_SIGNED"
        signature_valid = False
    elif sha is not None and stored == sha:
        waiver_status = "VALID"
        signature_valid = True
    else:
        waiver_status = "OUT_OF_DATE"
        signature_valid = False

    return {
        "waiver_required": waiver_required,
        "waiver_filepath": fp,
        "waiver_sha256": sha,
        "waiver_status": waiver_status,
        "waiver_signature_valid": signature_valid,
        "waiver_legal_name_signature": legal,
    }


def serialize_manage(scope, search_query: str, search_type: str, cfg) -> dict:
    """Build the manage-API payload for *scope*.

    Handles search filtering, registration loading, and the team/player
    summary construction shared between tournament_manage_api and
    league_manage_api.

    Args:
        scope: :class:`~app.services._common.Scope` identifying event vs league.
        search_query: Search string from request query args.
        search_type: ``"team"`` / ``"player"`` / ``"both"``.
        cfg: registrable_config object (shared between scopes).

    Returns:
        The manage payload dict ready to be jsonified.
    """
    team_registrations = team_registrations_for_scope(scope, exclude_cancelled=True)
    teams_with_registrations = []
    for team_reg in team_registrations:
        team = Team.query.get(team_reg.team)
        if team:
            teams_with_registrations.append({"registration": team_reg, "team": team})

    player_registrations = player_registrations_for_scope(
        scope,
        statuses=[
            RegistrationStatus.PENDING_TEAM_APPROVAL,
            RegistrationStatus.CONFIRMED,
            RegistrationStatus.REJECTED,
        ],
    )
    players_with_registrations = []
    for player_reg in player_registrations:
        player = Player.query.get(player_reg.player)
        team = Team.query.get(player_reg.team) if player_reg.team else None
        if player:
            players_with_registrations.append({"registration": player_reg, "player": player, "team": team})

    if search_query:
        q = search_query.lower()
        if search_type in ("both", "teams"):
            teams_with_registrations = [
                t
                for t in teams_with_registrations
                if (
                    (t["team"].name or "").lower().find(q) != -1
                    or (t["registration"].pseudonym or "").lower().find(q) != -1
                )
            ]
        else:
            teams_with_registrations = []

        if search_type in ("both", "players"):
            players_with_registrations = [
                p
                for p in players_with_registrations
                if (
                    (p["player"].name or "").lower().find(q) != -1
                    or (p["registration"].jersey_name or "").lower().find(q) != -1
                )
            ]
        else:
            players_with_registrations = []

    if scope.is_league:
        league = League.query.get(scope.league_url)
        wf = getattr(cfg, "waiver_filepath", None) if cfg else None
        tournament_dict = {
            "url": league.url,
            "name": league.name,
            "start_date": "",
            "end_date": None,
            "location": None,
            "published": league.published,
            "league": {"league_url": league.url, "name": league.name},
            "waiver_required": bool(wf),
            "waiver_filepath": wf,
            "waiver_sha256": getattr(cfg, "waiver_sha256", None) if cfg else None,
        }
    else:
        tournament = Tournament.query.filter_by(url=scope.event_url).first()
        tournament_dict = tournament_to_dict(tournament)

    player_rows = []
    for pr in players_with_registrations:
        w = player_reg_waiver_api(pr["registration"], cfg)
        player_rows.append(
            {
                "registration": {
                    "id": pr["registration"].id,
                    "player": pr["registration"].player,
                    "team": pr["registration"].team,
                    "jersey_name": pr["registration"].jersey_name,
                    "jersey_number": pr["registration"].jersey_number,
                    "status": (
                        pr["registration"].status.value
                        if hasattr(pr["registration"].status, "value")
                        else str(pr["registration"].status)
                    ),
                    "paid": bool(pr["registration"].paid),
                    "amount_paid": pr["registration"].amount_paid or 0.0,
                    "registered_at": dt_iso(pr["registration"].registered_at),
                    "paid_at": dt_iso(pr["registration"].paid_at),
                    "waiver_required": w["waiver_required"],
                    "waiver_status": w["waiver_status"],
                    "waiver_legal_name_signature": w["waiver_legal_name_signature"],
                },
                "player": {
                    "id": pr["player"].id,
                    "name": pr["player"].name,
                },
                "team": (
                    {
                        "id": pr["team"].id,
                        "name": pr["team"].name,
                    }
                    if pr["team"]
                    else None
                ),
            }
        )

    return {
        "tournament": tournament_dict,
        "search_query": search_query,
        "search_type": search_type,
        "team_registrations": [
            {
                "registration": {
                    "id": tr["registration"].id,
                    "team": tr["registration"].team,
                    "pseudonym": tr["registration"].pseudonym,
                    "status": (
                        tr["registration"].status.value
                        if hasattr(tr["registration"].status, "value")
                        else str(tr["registration"].status)
                    ),
                    "paid": bool(tr["registration"].paid),
                    "amount_paid": tr["registration"].amount_paid or 0.0,
                    "registered_at": dt_iso(tr["registration"].registered_at),
                    "paid_at": dt_iso(tr["registration"].paid_at),
                },
                "team": {
                    "id": tr["team"].id,
                    "name": tr["team"].name,
                },
            }
            for tr in teams_with_registrations
        ],
        "player_registrations": player_rows,
    }


def _require_tournament(tournament_url):
    has_access, tournament = check_tournament_access(tournament_url)
    if not has_access or not tournament:
        return None, 404
    return tournament, None


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


def _schedule_published_check(tournament_url, tournament):
    if tournament.schedule_published:
        return True
    if not current_user.is_authenticated:
        return False
    if PermissionService.is_tournament_organizer(tournament_url, current_user):
        return True
    if is_player(current_user) and can_head_ref_match(tournament_url, current_user.id, match=None):
        return True
    return False


def _team_pseudonym_and_photo(tournament, team_id):
    """Return (pseudonym, profile_photo, shortname) for a team in a tournament context.

    Returns ``(None, None, None)`` if ``team_id`` is falsy.
    ``shortname`` is ``None`` if the team has no registration or no shortname.
    """

    if not team_id:
        return None, None, None
    reg = team_registration_for_tournament(tournament, team_id)
    pseudonym = reg.pseudonym if reg and reg.pseudonym else None
    team = Team.query.get(team_id)
    profile_photo = team.profile_photo if team else None
    if not pseudonym and team:
        pseudonym = team.name
    if not pseudonym:
        pseudonym = team_id
    shortname = reg.shortname if (reg and reg.shortname) else None
    return pseudonym, profile_photo, shortname


def _team_display_name(tournament, team_id):
    """Resolve a team id to display name (pseudonym preferred, else team name)."""

    if not team_id or not str(team_id).strip():
        return None
    reg = team_registration_for_tournament(tournament, team_id)
    if reg and reg.pseudonym:
        return reg.pseudonym
    t = Team.query.get(team_id)
    return t.name if t else team_id


def _refs_display_for_match(tournament, match):
    """Refs as comma-separated display names (pseudonym for each ref team), like team1_name/team2_name."""
    team_ids = get_match_ref_team_ids(match)
    initials_csv = get_match_refs_initial_csv(match) or None
    if not any(team_ids):
        return initials_csv
    parts = []
    for tid in team_ids:
        tid = tid.strip()
        if not tid:
            continue
        name = _team_display_name(tournament, tid)
        if name:
            parts.append(name)
    return ",".join(parts) if parts else initials_csv


@bp.route("/tournaments", methods=["GET"])
def tournaments():
    """List tournaments (same visibility as homepage). Returns { tournaments, team_counts, user_reg_status }."""
    ctx = TournamentService.get_homepage_context(current_user)
    team_counts = ctx["team_counts"]
    user_reg_status = ctx["user_reg_status"]
    all_tournaments = [tournament_to_dict(t) for t in ctx["tournaments"]]
    return jsonify(
        {
            "tournaments": all_tournaments,
            "team_counts": team_counts,
            "user_reg_status": user_reg_status,
        }
    )


@bp.route("/tournaments/<tournament_url>", methods=["GET"])
def tournament_detail(tournament_url):
    """Tournament detail: teams with counts, unattached players, to_entries, is_current_*_registered."""

    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err
    team_regs = team_registrations_for_tournament(tournament)
    team_ids = [tr.team for tr in team_regs]
    teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
    all_prs = player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED])
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
                "player_count": counts_by_team.get(team_reg.team, 0),
                "registered_at": dt_iso(getattr(team_reg, "registered_at", None)),
                "profile_photo": team.profile_photo if team else None,
            }
        )
    unattached_prs = list(
        player_registrations_for_tournament(
            tournament,
            unattached_only=True,
            statuses=[RegistrationStatus.CONFIRMED],
        )
    )
    unattached_player_ids = [pr.player for pr in unattached_prs]
    unattached_players_by_id = (
        {p.id: p for p in Player.query.filter(Player.id.in_(unattached_player_ids)).all()}
        if unattached_player_ids
        else {}
    )
    unattached = []
    for pr in unattached_prs:
        p = unattached_players_by_id.get(pr.player)
        unattached.append(
            {
                "player_id": pr.player,
                "player_name": p.name if p else pr.player,
                "jersey_number": getattr(pr, "jersey_number", None),
                "jersey_name": getattr(pr, "jersey_name", None),
                "registered_at": dt_iso(getattr(pr, "registered_at", None)),
                "profile_photo": getattr(p, "profile_photo", None) if p else None,
            }
        )
    to_rows = to_entries_for_tournament(tournament)
    to_player_ids = [e.user_id for e in to_rows if e.user_type == "player"]
    to_team_ids = [e.user_id for e in to_rows if e.user_type == "team"]
    to_players_by_id = (
        {p.id: p for p in Player.query.filter(Player.id.in_(to_player_ids)).all()} if to_player_ids else {}
    )
    to_teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(to_team_ids)).all()} if to_team_ids else {}
    to_entries = []
    for e in to_rows:
        if e.user_type == "player":
            user = to_players_by_id.get(e.user_id)
        else:
            user = to_teams_by_id.get(e.user_id)
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
            is_current_team_registered = is_team_registered(tournament, current_user.id)
        else:
            is_current_player_registered = is_player_registered(tournament, current_user.id)

    penalty_types = get_penalty_types_for_tournament(tournament)
    penalty_types_data = [{"id": t.id, "name": t.name, "color": t.color, "desc": (t.desc or "")} for t in penalty_types]

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "teams_with_counts": teams_with_counts,
            "unattached_players": unattached,
            "to_entries": to_entries,
            "is_current_team_registered": is_current_team_registered,
            "is_current_player_registered": is_current_player_registered,
            "penalty_types": penalty_types_data,
        }
    )


@bp.route("/tournaments/<tournament_url>/manage", methods=["GET"])
@login_required
def tournament_manage_api(tournament_url):
    """Tournament registration management (TO only)."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.league_id:
        return (
            jsonify(
                {
                    "error": "Registration management for league events is on the league page.",
                }
            ),
            403,
        )

    search_query = (request.args.get("search") or "").strip()
    search_type = (request.args.get("type") or "both").lower()
    cfg = get_registrable_config(tournament)
    return jsonify(serialize_manage(Scope.event(tournament_url), search_query, search_type, cfg))


@bp.route("/tournaments/<tournament_url>/invitations", methods=["GET"])
@login_required
def tournament_invitations_api(tournament_url):
    if not is_team(current_user):
        return jsonify({"error": "Only teams can view invitations"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    # League events register at the league scope — point teams there.
    if tournament.league_id:
        return (
            jsonify(
                {
                    "error": "Registration management for league events is on the league page.",
                    "league_url": tournament.league_id,
                }
            ),
            403,
        )

    team_registration = TeamRegistration.query.filter_by(
        event=tournament_url, team=current_user.id, status=RegistrationStatus.CONFIRMED
    ).first()
    if not team_registration:
        return jsonify({"error": "Not registered"}), 404

    pending_regs = PlayerRegistration.query.filter_by(
        event=tournament_url,
        team=current_user.id,
        status=RegistrationStatus.PENDING_TEAM_APPROVAL,
    ).all()
    pending_with_players = []
    for reg in pending_regs:
        player = Player.query.get(reg.player)
        if player:
            pending_with_players.append({"registration": reg, "player": player})

    current_team_size = PlayerRegistration.query.filter_by(
        event=tournament_url, team=current_user.id, status=RegistrationStatus.CONFIRMED
    ).count()

    all_player_registrations = PlayerRegistration.query.filter_by(event=tournament_url, team=current_user.id).all()
    team_roster = []
    for reg in all_player_registrations:
        player = Player.query.get(reg.player)
        if player:
            team_roster.append({"player": player, "registration": reg})

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
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


@bp.route("/tournaments/<tournament_url>/schedule", methods=["GET"])
def tournament_schedule(tournament_url):
    """Schedule: matches, fields, team_options. Requires schedule_published or TO/head_ref."""
    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err
    if not _schedule_published_check(tournament_url, tournament):
        return jsonify({"error": "Schedule not published"}), 403
    matches = Match.query.filter_by(event=tournament_url).order_by(Match.nominal_start_time).all()
    fields = [
        {"id": f.id, "name": f.name} for f in Field.query.filter_by(event=tournament_url).order_by(Field.name).all()
    ]
    team_options = []
    seen = set()

    for tr in team_registrations_for_tournament(tournament):
        if tr.team not in seen:
            team = Team.query.get(tr.team)
            team_options.append(
                {
                    "id": tr.team,
                    "pseudonym": tr.pseudonym,
                    "shortname": tr.shortname,
                    "profile_photo": team.profile_photo if team else None,
                }
            )
            seen.add(tr.team)
    for m in matches:
        for initial, key in [(m.team1_initial, "team1"), (m.team2_initial, "team2")]:
            if not initial or initial in seen:
                continue
            if "::winner" in initial or "::loser" in initial or " winner" in initial or " loser" in initial:
                continue
            team_options.append({"id": initial, "pseudonym": initial, "shortname": None, "profile_photo": None})
            seen.add(initial)
    match_list = []
    for m in matches:
        match_list.append(
            {
                "uuid": m.uuid,
                "name": m.name,
                "field": m.field,
                "team1": m.team1,
                "team2": m.team2,
                "team1_initial": m.team1_initial,
                "team2_initial": m.team2_initial,
                "status": (m.status.value if hasattr(m.status, "value") else str(m.status)),
                "scheduled_start_time": dt_iso(m.scheduled_start_time),
                "nominal_start_time": dt_iso(m.nominal_start_time),
                "confirmed_start_time": dt_iso(m.confirmed_start_time),
                "completed_time": dt_iso(m.completed_time),
                "schedule_type": m.schedule_type.value if m.schedule_type else None,
                "set_type": m.set_type.value if m.set_type else None,
            }
        )
    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "matches": match_list,
            "fields": fields,
            "team_options": team_options,
        }
    )


@bp.route("/tournaments/<tournament_url>/results", methods=["GET"])
def tournament_results(tournament_url):
    """Tournament results: teams with aggregate stats (no per-match data)."""

    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err
    matches = Match.query.filter(
        Match.event == tournament_url,
        Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]),
    ).all()
    include_ribbon = request.args.get("include_ribbon", "").lower() in (
        "1",
        "true",
        "yes",
    )
    teams_list = compute_team_stats(matches, tournament, include_ribbon=include_ribbon)
    return jsonify({"tournament": tournament_to_dict(tournament), "teams": teams_list})


@bp.route("/tournaments/<tournament_url>/results/team/<team_id>", methods=["GET"])
def tournament_results_team_matches(tournament_url, team_id):
    """Matches for one team in this tournament (for expandable row)."""
    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err
    matches = (
        Match.query.filter(
            Match.event == tournament_url,
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
    match_list = []
    for m in matches:
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
                "match_winner": m.match_winner.value if m.match_winner else None,
                "your_side": your_side,
                "sets": sets_list,
                "ribbon": getattr(m, "ribbon", False),
            }
        )
    return jsonify({"matches": match_list})


@bp.route("/tournaments/<tournament_url>/fields", methods=["GET"])
def tournament_fields(tournament_url):
    """List fields for a tournament."""
    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err
    fields = Field.query.filter_by(event=tournament_url).order_by(Field.name).all()
    return jsonify({"fields": [{"id": f.id, "name": f.name} for f in fields]})


@bp.route("/tournaments/<tournament_url>/schedule-setup", methods=["GET"])
def tournament_schedule_setup(tournament_url):
    """
    Combined schedule and setup data for the unified page.
    Returns tournament, matches (full details), fields, tags, team_options, etc.
    Overlap/conflict detection is done in the frontend.
    """
    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err

    is_to = _check_to(tournament_url)
    if not _schedule_published_check(tournament_url, tournament) and not is_to:
        return jsonify({"error": "Schedule not published"}), 403

    # Fields
    fields_query = Field.query.filter_by(event=tournament_url).order_by(Field.name).all()
    fields_data = [{"id": f.id, "name": f.name} for f in fields_query]

    # Tags
    tags_query = Tag.query.filter_by(event=tournament_url).order_by(Tag.name).all()
    tags_data = [{"id": t.id, "name": t.name, "team": t.team} for t in tags_query]

    # Matches
    matches_query = Match.query.filter_by(event=tournament_url).order_by(Match.nominal_start_time).all()
    match_list = []
    for m in matches_query:
        match_list.append(
            {
                "uuid": m.uuid,
                "name": m.name,
                "field": m.field,
                "team1": m.team1,
                "team2": m.team2,
                "team1_initial": m.team1_initial,
                "team2_initial": m.team2_initial,
                "status": (m.status.value if hasattr(m.status, "value") else str(m.status)),
                "scheduled_start_time": dt_iso(m.scheduled_start_time),
                "nominal_start_time": dt_iso(m.nominal_start_time),
                "confirmed_start_time": dt_iso(m.confirmed_start_time),
                "completed_time": dt_iso(m.completed_time),
                "schedule_type": m.schedule_type.value if m.schedule_type else None,
                "set_type": m.set_type.value if m.set_type else None,
                "nominal_length": m.nominal_length,
                "previous_match": m.previous_match,
                "next_match": m.next_match,
                "refs": get_match_refs_csv(m) or None,
                "refs_initial": get_match_refs_initial_csv(m) or None,
                "ribbon": m.ribbon,
                "skip_condition": m.skip_condition,
                "nsets": m.nsets,
                "stones_per_set": m.stones_per_set,
                "stones_remaining": m.stones_remaining,
                "match_winner": m.match_winner.value if m.match_winner else None,
            }
        )

    # Team Options: only teams with valid (confirmed) registration for this tournament.
    # Create/edit match modals use this; match refs (MatchName::winner/loser) and tags (tag::Name) are offered separately.
    team_options = []
    seen = set()

    for tr in team_registrations_for_tournament(tournament):
        if tr.team not in seen:
            team = Team.query.get(tr.team)
            team_options.append(
                {
                    "id": tr.team,
                    "pseudonym": tr.pseudonym,
                    "shortname": tr.shortname,
                    "profile_photo": team.profile_photo if team else None,
                }
            )
            seen.add(tr.team)

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "matches": match_list,
            "fields": fields_data,
            "tags": tags_data,
            "team_options": team_options,
            "is_to": is_to,
        }
    )


@bp.route("/tournaments/<tournament_url>/match", methods=["GET"])
def tournament_match_detail(tournament_url):
    """Match detail by id= or name=. Returns match metadata and points."""
    tournament, err = _require_tournament(tournament_url)
    if err:
        return jsonify({"error": "Not found"}), err

    event_urls = match_event_urls_for_penalties(tournament)
    match_id = request.args.get("id", "").strip()
    match_name = request.args.get("name", "").strip()
    if not match_id and not match_name:
        return jsonify({"error": "Match id or name required"}), 400
    if match_id:
        match = Match.query.filter(Match.uuid == match_id, Match.event.in_(event_urls)).first()
    else:
        match = Match.query.filter(Match.name == match_name, Match.event.in_(event_urls)).first()
    if not match:
        return jsonify({"error": "Match not found"}), 404
    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()
    team1_name = team_name_for_match(tournament, match, "team1")
    team2_name = team_name_for_match(tournament, match, "team2")
    _, team1_photo, team1_shortname = _team_pseudonym_and_photo(tournament, match.team1)
    _, team2_photo, team2_shortname = _team_pseudonym_and_photo(tournament, match.team2)
    points_data = [
        {
            "uuid": p.uuid,
            "set_number": p.set_number,
            "winner": p.winner,
            "rerolled": p.rerolled,
            "stamp": dt_iso(p.stamp),
            "end_stamp": dt_iso(p.end_stamp),
            "stones_at_start": (p.stones_at_start if match.set_type == SetType.STONES else None),
        }
        for p in points
    ]

    # Get camera data. New sources come from the `Camera` table; legacy
    # recorded point timestamps are read from `Match.camera_stream_starts`.
    available_cameras = []
    camera_url = None

    legacy_point_timestamps_by_camera_name = {}
    if match.camera_stream_starts:
        try:
            legacy_data = json.loads(match.camera_stream_starts) or {}
            if isinstance(legacy_data, dict):
                for cam_name, recording_data in legacy_data.items():
                    if isinstance(recording_data, dict):
                        pts = recording_data.get("point_timestamps")
                        if pts is not None:
                            legacy_point_timestamps_by_camera_name[cam_name] = pts
        except (json.JSONDecodeError, TypeError):
            pass

    # Match-scoped cameras from the Camera table.
    camera_rows = (
        Camera.query.filter_by(match_uuid=match.uuid).filter_by(event=tournament_url).order_by(Camera.name.asc()).all()
    )
    for idx, cam in enumerate(camera_rows):
        cam_type = "youtube" if (cam.source_type or "").strip() == "youtube_livestream" else "recorded"
        worlds, videos = get_camera_timepoint_arrays(cam)
        time_world = worlds or None
        time_video = videos or None

        # Only provide YouTube URL/id once upload succeeded.
        url = cam.link if cam.status == "SUCCESS" else None

        video_path = cam.file

        available_cameras.append(
            {
                "index": idx,
                "url": url,
                "stream_start_time": None,
                "type": cam_type,
                "video_path": video_path,
                "camera_id": cam.name,
                "session_id": None,
                "point_timestamps": legacy_point_timestamps_by_camera_name.get(cam.name),
                "status": cam.status,
                "source_type": cam.source_type,
                "time_world": time_world,
                "time_video": time_video,
            }
        )

    if available_cameras:
        first_cam = available_cameras[0]
        if first_cam.get("type") == "youtube":
            camera_url = first_cam.get("url")

    # Get match notes
    initial_notes = match.initial_notes or ""
    final_notes = match.final_notes or ""
    match_notes = []
    point_notes_map = {}

    # Check if user is head ref
    is_head_ref = False
    if current_user.is_authenticated:
        if is_player(current_user):
            is_head_ref = can_head_ref_match(tournament_url, current_user.id, match=match)

    # Can start and blocking reasons (for "why?" UX)

    _user = current_user if current_user.is_authenticated else None
    can_start, block_reasons, why_sections = get_can_start_and_reasons(tournament_url, match, _user)

    # Conflicting match on same field (for force-start modal)
    conflicting_match = None
    other_match = get_conflicting_match_on_field(tournament_url, match)
    if other_match:
        from app.services.registration_resolver import team_registration_for_tournament

        reg1 = team_registration_for_tournament(tournament, other_match.team1) if other_match.team1 else None
        reg2 = team_registration_for_tournament(tournament, other_match.team2) if other_match.team2 else None
        conflicting_match = {
            "uuid": other_match.uuid,
            "name": getattr(other_match, "name", other_match.uuid),
            "team1_name": team_name_for_match(tournament, other_match, "team1"),
            "team2_name": team_name_for_match(tournament, other_match, "team2"),
            "team1_shortname": reg1.shortname if reg1 else None,
            "team2_shortname": reg2.shortname if reg2 else None,
        }

    # Get match-level notes (point_id is None) - only for head refs
    if is_head_ref:
        notes = MatchNote.query.filter_by(match=match.uuid, point_id=None).order_by(MatchNote.created_at.desc()).all()
        from app.utils.player_helpers import get_player_display_name

        for note in notes:
            player_name = None
            player_display = None
            if note.player_id:
                player_name, player_display = get_player_display_name(note.player_id, tournament_url)
            team_id = None
            if note.target == "team1":
                team_id = match.team1
            elif note.target == "team2":
                team_id = match.team2

            match_notes.append(
                {
                    "text": note.text,
                    "target": note.target,
                    "player_id": note.player_id,
                    "player_name": player_name,
                    "player_display": player_display,
                    "team_id": team_id,
                    "created_at": dt_iso(note.created_at),
                }
            )

    # Build match_players for player-targeted notes (jersey/name search + profile photo)
    match_players = []

    # Parse selected players for "in_this_match" check
    team1_selected = set(get_match_player_ids(match, WinnerSide.TEAM1))
    team2_selected = set(get_match_player_ids(match, WinnerSide.TEAM2))

    # Helper to add players from a team (registration). Skip any player whose id is in exclude_ids (e.g. playing for the other team).
    from app.services.registration_resolver import (
        player_registration_for_tournament,
        player_registrations_for_tournament,
    )

    def add_team_players(team_id, team_side, selected_ids, exclude_ids=None):
        exclude_ids = exclude_ids or set()
        if not team_id:
            return
        regs = player_registrations_for_tournament(
            tournament,
            team_id=team_id,
            statuses=[RegistrationStatus.CONFIRMED],
        )

        for pr in regs:
            if pr.player in exclude_ids:
                continue
            player = Player.query.get(pr.player)
            if player:
                display = get_player_display_from_registration(player, pr)
                match_players.append(
                    {
                        "player_id": player.id,
                        "name": player.name or "",
                        "display": display,
                        "profile_photo": getattr(player, "profile_photo", None),
                        "team_side": team_side,
                        "in_this_match": player.id in selected_ids,
                    }
                )

    # Team1: don't list players who are playing for team2 (in team2_selected).
    add_team_players(match.team1, "team1", team1_selected, exclude_ids=team2_selected)
    # Team2: don't list players who are playing for team1 (in team1_selected).
    add_team_players(match.team2, "team2", team2_selected, exclude_ids=team1_selected)

    # Include players who are in team2_selected but not on team2's roster (added via search on start-match).
    existing_player_ids = {p["player_id"] for p in match_players}
    for pid in team2_selected:
        if pid in existing_player_ids:
            continue
        player = Player.query.get(pid)
        if player:
            pr = player_registration_for_tournament(
                tournament,
                pid,
                statuses=[RegistrationStatus.CONFIRMED],
            )
            display = get_player_display_from_registration(player, pr) if pr else (player.name or pid)
            match_players.append(
                {
                    "player_id": player.id,
                    "name": player.name or "",
                    "display": display,
                    "profile_photo": getattr(player, "profile_photo", None),
                    "team_side": "team2",
                    "in_this_match": True,
                }
            )
            existing_player_ids.add(pid)
    # Same for team1_selected: players added via search to team1 only.
    for pid in team1_selected:
        if pid in existing_player_ids:
            continue
        player = Player.query.get(pid)
        if player:
            pr = player_registration_for_tournament(
                tournament,
                pid,
                statuses=[RegistrationStatus.CONFIRMED],
            )
            display = get_player_display_from_registration(player, pr) if pr else (player.name or pid)
            match_players.append(
                {
                    "player_id": player.id,
                    "name": player.name or "",
                    "display": display,
                    "profile_photo": getattr(player, "profile_photo", None),
                    "team_side": "team1",
                    "in_this_match": True,
                }
            )
            existing_player_ids.add(pid)

    # Calculate penalty counts for match players
    player_ids_in_match = [p["player_id"] for p in match_players]
    penalty_counts_map = {}

    if player_ids_in_match:
        # Count per player and penalty type (league: all matches in league; standalone: this event only)
        results = (
            db.session.query(
                MatchNote.player_id,
                MatchNote.penalty_type_id,
                func.count(MatchNote.uuid),
            )
            .join(Match)
            .filter(
                Match.event.in_(event_urls),
                MatchNote.target == "player",
                MatchNote.player_id.in_(player_ids_in_match),
            )
            .group_by(MatchNote.player_id, MatchNote.penalty_type_id)
            .all()
        )

        for pid, pt_id, count in results:
            if pid not in penalty_counts_map:
                penalty_counts_map[pid] = {}
            # Key: penalty_type_id (or "other" if None) -> count
            key = str(pt_id) if pt_id is not None else "other"
            penalty_counts_map[pid][key] = count

    # Add counts to match_players
    for p in match_players:
        p["penalty_counts"] = penalty_counts_map.get(p["player_id"], {})

    # Sort: in_this_match first, then by name
    match_players.sort(key=lambda p: (not p["in_this_match"], p["display"]))

    # Get penalty types (league's if league event, else event's)
    from app.utils.helpers import get_penalty_types_for_tournament

    penalty_types = get_penalty_types_for_tournament(tournament)
    penalty_types_data = [{"id": t.id, "name": t.name, "color": t.color, "desc": (t.desc or "")} for t in penalty_types]

    # Get point-specific notes - point notes (target='match') visible to everyone
    if points:
        point_ids = [p.uuid for p in points]
        if point_ids:
            point_notes_query = (
                MatchNote.query.filter_by(match=match.uuid)
                .filter(MatchNote.point_id.in_(point_ids))
                .order_by(MatchNote.created_at.asc())
            )
            if not is_head_ref:
                point_notes_query = point_notes_query.filter_by(target="match")

            point_notes = point_notes_query.all()
            from app.utils.player_helpers import get_player_display_name

            for n in point_notes:
                if not is_head_ref and n.target != "match":
                    continue

                player_name = None
                player_display = None
                if n.player_id:
                    player_name, player_display = get_player_display_name(n.player_id, tournament_url)
                team_id = None
                if n.target == "team1":
                    team_id = match.team1
                elif n.target == "team2":
                    team_id = match.team2

                point_notes_map.setdefault(n.point_id, []).append(
                    {
                        "text": n.text,
                        "target": n.target,
                        "player_id": n.player_id,
                        "player_name": player_name,
                        "player_display": player_display,
                        "team_id": team_id,
                        "created_at": dt_iso(n.created_at),
                        "penalty_type_id": getattr(n, "penalty_type_id", None),
                    }
                )

    return jsonify(
        {
            "match": {
                "uuid": match.uuid,
                "name": match.name,
                "field": match.field,
                "team1": match.team1,
                "team2": match.team2,
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_photo": team1_photo,
                "team2_photo": team2_photo,
                "team1_shortname": team1_shortname,
                "team2_shortname": team2_shortname,
                "team1_initial": match.team1_initial,
                "team2_initial": match.team2_initial,
                "status": (match.status.value if hasattr(match.status, "value") else str(match.status)),
                "nominal_start_time": dt_iso(match.nominal_start_time),
                "confirmed_start_time": dt_iso(match.confirmed_start_time),
                "completed_time": dt_iso(match.completed_time),
                "set_type": match.set_type.value if match.set_type else None,
                "stones_per_set": match.stones_per_set,
                "stones_remaining": match.stones_remaining,
                "match_winner": (match.match_winner.value if match.match_winner else None),
                "schedule_type": (match.schedule_type.value if match.schedule_type else None),
                "nominal_length": match.nominal_length,
                "previous_match": match.previous_match,
                "refs": get_match_refs_csv(match) or None,
                "refs_initial": get_match_refs_initial_csv(match) or None,
                "refs_display": _refs_display_for_match(tournament, match),
                "ribbon": match.ribbon,
                "skip_condition": match.skip_condition,
                "nsets": match.nsets,
                "initial_notes": initial_notes,
                "final_notes": final_notes,
            },
            "points": points_data,
            "available_cameras": available_cameras,
            "camera_url": camera_url,
            "match_notes": match_notes,
            "point_notes_map": point_notes_map,
            "is_head_ref": is_head_ref,
            "can_start": can_start,
            "block_reasons": block_reasons,
            "why_sections": why_sections_to_dict(why_sections),
            "conflicting_match": conflicting_match,
            "match_players": match_players,
            "penalty_types": penalty_types_data,
        }
    )


@bp.route("/tournaments/<tournament_url>/match-state", methods=["GET"])
def tournament_match_state(tournament_url):
    """Get current match state for polling (CORS-friendly). Public endpoint."""
    match_id = request.args.get("match_id") or request.args.get("id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
    if not match:
        return jsonify({"error": "Match not found"}), 404

    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

    team1_score = sum(1 for p in points if p.winner == "TEAM1" and not p.rerolled)
    team2_score = sum(1 for p in points if p.winner == "TEAM2" and not p.rerolled)

    sets = sorted(set(p.set_number for p in points))
    scores_by_set = {}
    for set_num in sets:
        set_points = [p for p in points if p.set_number == set_num]
        scores_by_set[set_num] = {
            "team1_score": sum(1 for p in set_points if p.winner == "TEAM1" and not p.rerolled),
            "team2_score": sum(1 for p in set_points if p.winner == "TEAM2" and not p.rerolled),
        }

    points_data = []
    for p in points:
        stamp_iso = to_iso_z(p.stamp).unwrap_or(None)
        end_stamp_iso = to_iso_z(p.end_stamp).unwrap_or(None)
        points_data.append(
            {
                "uuid": p.uuid,
                "set_number": p.set_number,
                "winner": p.winner,
                "rerolled": p.rerolled,
                "stamp": stamp_iso,
                "end_stamp": end_stamp_iso,
                "stones_at_start": (p.stones_at_start if match.set_type == SetType.STONES else None),
            }
        )

    finalized_at = None
    if match.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED) and match.finalized_at:
        finalized_at = match.finalized_at.isoformat()

    return jsonify(
        {
            "match_id": match.uuid,
            "status": (match.status.value if hasattr(match.status, "value") else str(match.status)),
            "team1_score": team1_score,
            "team2_score": team2_score,
            "scores_by_set": scores_by_set,
            "points": points_data,
            "stones_remaining": (
                match.stones_remaining if getattr(match, "set_type", None) == SetType.STONES else None
            ),
            "finalized_at": finalized_at,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.route("/tournaments/<tournament_url>/fields/<int:field_id>", methods=["GET"])
@login_required
def get_field(tournament_url, field_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    field = Field.query.filter_by(id=field_id, event=tournament_url).first_or_404()

    return jsonify({"id": field.id, "name": field.name})


@bp.route("/tournaments/<tournament_url>/tags", methods=["GET"])
@login_required
def list_tags(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tags = Tag.query.filter_by(event=tournament_url).order_by(Tag.name).all()
    return jsonify({"tags": [{"id": t.id, "name": t.name, "team": t.team} for t in tags]})
