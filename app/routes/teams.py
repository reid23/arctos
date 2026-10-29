"""Team profile and profile-photo routes.

Hosts the ``teams`` blueprint at ``/_api``:

- ``/teams`` (GET) - list teams.
- ``/teams/<team_id>`` (GET, PUT) - read and edit team profile.
- ``/teams/<team_id>/players`` (GET) - list team members.
- ``/teams/<team_id>/profile-photo`` (POST, DELETE) - upload and
  remove the team's profile photo.
- ``/teams/<team_id>/banner-photo`` (POST, DELETE) - replace or remove
  the team's validated profile banner.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from app.domain.enums import RegistrationStatus
from app.services.profile_stats_service import past_event_counts
from app.utils.datetime_helpers import dt_iso
from app.utils.helpers import can_head_ref_match, check_tournament_access, unpublished_historical_urls
from app.utils.profile_banner_helpers import (
    BannerUploadError,
    read_banner_upload,
    remove_profile_banner,
    replace_profile_banner,
)
from app.utils.profile_photo_helpers import (
    PhotoUploadError,
    read_profile_photo_upload,
    remove_profile_photo,
    replace_profile_photo,
)
from app.utils.profile_edit_helpers import ProfileEditError, save_profile_edit
from app.utils.user_helpers import is_player, is_team
from models import (
    Match,
    MatchNote,
    Player,
    PlayerRegistration,
    Point,
    Team,
    TeamRegistration,
    Tournament,
)

bp = Blueprint("teams", __name__, url_prefix="/_api")


@bp.route("/teams", methods=["GET"])
def teams_list():
    """List team accounts (not historical placeholders) with optional search."""
    search = request.args.get("search", "").strip()
    query = Team.query.filter(Team.placeholder_event.is_(None))
    if search:
        query = query.filter(Team.name.contains(search) | Team.id.contains(search))
    teams = query.all()
    event_counts = past_event_counts("team", (team.id for team in teams))
    return jsonify(
        {
            "teams": [
                {
                    "id": t.id,
                    "name": t.name,
                    "profile_photo": t.profile_photo,
                    "location": t.location,
                    "past_event_count": event_counts[t.id],
                }
                for t in teams
            ]
        }
    )


@bp.route("/teams/<team_id>/players", methods=["GET"])
def team_registration_players(team_id):
    """Players registered for a team in an event or league (public). Event via query param ?event=.
    For leagues use ?event=league:league_url."""
    event_arg = request.args.get("event")
    if not event_arg:
        return jsonify({"error": "event required"}), 400
    team = Team.query.get(team_id)
    if not team:
        return jsonify({"error": "Not found"}), 404
    if event_arg.startswith("league:"):
        league_id = event_arg[7:]
        team_reg = TeamRegistration.query.filter_by(
            team=team_id, league_id=league_id, status=RegistrationStatus.CONFIRMED
        ).first()
        if not team_reg:
            return jsonify({"error": "Not found"}), 404
        accepted_players = PlayerRegistration.query.filter_by(
            league_id=league_id, team=team_id, status=RegistrationStatus.CONFIRMED
        ).all()
    else:
        team_reg = TeamRegistration.query.filter_by(
            team=team_id, event=event_arg, status=RegistrationStatus.CONFIRMED
        ).first()
        if not team_reg:
            return jsonify({"error": "Not found"}), 404
        accepted_players = PlayerRegistration.query.filter_by(
            event=event_arg, team=team_id, status=RegistrationStatus.CONFIRMED
        ).all()
    players_with_data = []
    for player_reg in accepted_players:
        player = Player.query.get(player_reg.player)
        players_with_data.append(
            {
                "registration": {
                    "player": player_reg.player,
                    "jersey_name": player_reg.jersey_name,
                    "jersey_number": player_reg.jersey_number,
                },
                "player": (
                    {
                        "id": player.id,
                        "name": player.name,
                        "profile_photo": player.profile_photo,
                    }
                    if player
                    else None
                ),
            }
        )
    return jsonify(players_with_data)


@bp.route("/teams/<team_id>", methods=["GET"])
def team_profile(team_id):
    """Team profile (public)."""
    team = Team.query.get(team_id)
    if not team:
        return jsonify({"error": "Not found"}), 404
    if team.placeholder_event:
        has_access, _tournament = check_tournament_access(team.placeholder_event)
        if not has_access:
            return jsonify({"error": "Not found"}), 404
    regs = TeamRegistration.query.filter_by(team=team_id, status=RegistrationStatus.CONFIRMED).all()
    hidden_events = unpublished_historical_urls(r.event for r in regs)
    regs = [r for r in regs if r.event not in hidden_events]
    event_urls = [r.event for r in regs if r.event]
    tournaments = Tournament.query.filter(Tournament.url.in_(event_urls)).all() if event_urls else []
    tournament_start = {t.url: t.start_date for t in tournaments}
    historical_events = {t.url for t in tournaments if t.historical}

    tournament_players = {}
    if current_user.is_authenticated and current_user.id == team_id and is_team(current_user):
        all_player_ids = set()
        team_reg_to_players = {}
        for team_reg in regs:
            event_key = (
                team_reg.event if team_reg.event else (f"league:{team_reg.league_id}" if team_reg.league_id else None)
            )
            if event_key is None:
                continue
            if team_reg.event:
                accepted_players = PlayerRegistration.query.filter_by(
                    event=team_reg.event,
                    team=team_id,
                    status=RegistrationStatus.CONFIRMED,
                ).all()
            else:
                accepted_players = PlayerRegistration.query.filter_by(
                    league_id=team_reg.league_id,
                    team=team_id,
                    status=RegistrationStatus.CONFIRMED,
                ).all()
            team_reg_to_players[event_key] = accepted_players
            all_player_ids.update(pr.player for pr in accepted_players)

        players_by_id = (
            {p.id: p for p in Player.query.filter(Player.id.in_(list(all_player_ids))).all()} if all_player_ids else {}
        )

        for event_key, accepted_players in team_reg_to_players.items():
            players_with_data = []
            for player_reg in accepted_players:
                player = players_by_id.get(player_reg.player)
                players_with_data.append(
                    {
                        "registration": {
                            "player": player_reg.player,
                            "jersey_name": player_reg.jersey_name,
                            "jersey_number": player_reg.jersey_number,
                        },
                        "player": (
                            {
                                "id": player.id,
                                "name": player.name,
                                "profile_photo": player.profile_photo,
                            }
                            if player
                            else None
                        ),
                    }
                )
            tournament_players[event_key] = players_with_data

    team_notes = []
    player_played_with_team = False
    player_tournament_registrations = set()
    if current_user.is_authenticated and is_player(current_user):
        player_regs = PlayerRegistration.query.filter_by(
            player=current_user.id, team=team_id, status=RegistrationStatus.CONFIRMED
        ).all()
        player_tournament_registrations = {reg.event for reg in player_regs}
        player_played_with_team = len(player_tournament_registrations) > 0

    if current_user.is_authenticated:
        try:
            candidate_notes = (
                MatchNote.query.join(Match, Match.uuid == MatchNote.match)
                .filter(
                    or_(
                        and_(MatchNote.target == "team1", Match.team1 == team_id),
                        and_(MatchNote.target == "team2", Match.team2 == team_id),
                    )
                )
                .order_by(MatchNote.created_at.desc())
                .all()
            )
            match_ids = list({n.match for n in candidate_notes})
            matches_by_id = (
                {m.uuid: m for m in Match.query.filter(Match.uuid.in_(match_ids)).all()} if match_ids else {}
            )
            hidden_note_events = unpublished_historical_urls(m.event for m in matches_by_id.values())
            match_to_points = {}
            for n in candidate_notes:
                m = matches_by_id.get(n.match)
                if not m or m.event in hidden_note_events:
                    continue

                can_see_note = False
                if current_user.id == team_id:
                    can_see_note = True
                elif player_played_with_team and current_user.id != team_id:
                    if m.event in player_tournament_registrations:
                        can_see_note = True
                elif is_player(current_user):
                    if can_head_ref_match(m.event, current_user.id, match=m):
                        can_see_note = True

                if not can_see_note:
                    continue

                idx = "-"
                if n.point_id:
                    mid = m.uuid
                    if mid not in match_to_points:
                        pts = Point.query.filter_by(match=mid).order_by(Point.stamp).all()
                        match_to_points[mid] = [p.uuid for p in pts]
                    order = match_to_points.get(mid, [])
                    if n.point_id in order:
                        idx = order.index(n.point_id) + 1
                team_notes.append(
                    {
                        "created_at": dt_iso(n.created_at),
                        "text": n.text,
                        "point_index": str(idx),
                        "match": {
                            "event": m.event,
                            "uuid": m.uuid,
                            "name": m.name,
                        },
                    }
                )
        except Exception:
            team_notes = []
    return jsonify(
        {
            "team": {
                "id": team.id,
                "name": team.name,
                "profile_photo": team.profile_photo,
                "banner_photo": team.banner_photo,
                "location": team.location,
                "email": team.email,
                "website": team.website,
                "about": team.about,
                "placeholder_event": team.placeholder_event,
            },
            "past_event_count": past_event_counts("team", [team_id])[team_id],
            "registrations": [
                {
                    "event": r.event or (f"league:{r.league_id}" if r.league_id else ""),
                    "pseudonym": r.pseudonym,
                    "status": (r.status.value if hasattr(r.status, "value") else str(r.status)),
                    "paid": bool(r.paid),
                    "amount_paid": r.amount_paid,
                    "start_date": (dt_iso(tournament_start.get(r.event)) if r.event else None),
                    "historical": r.event in historical_events,
                }
                for r in regs
            ],
            "team_notes": team_notes,
            "tournament_players": tournament_players,
        }
    )


@bp.route("/teams/<team_id>", methods=["PUT"])
@login_required
def update_team_profile(team_id):
    """Save profile text and optional multipart images together."""
    if current_user.id != team_id or not is_team(current_user):
        return jsonify({"error": "You can only edit your own team profile"}), 403

    team = Team.query.get_or_404(team_id)
    try:
        save_profile_edit(team, "team")
    except ProfileEditError as error:
        return jsonify({"error": str(error)}), error.status
    return jsonify({"success": True})


@bp.route("/teams/<team_id>/banner-photo", methods=["POST", "DELETE"])
@login_required
def team_banner_photo(team_id):
    """Replace or remove a banner belonging to this team account only."""
    if not is_team(current_user) or current_user.id != team_id:
        return jsonify({"error": "You can only change the banner on your own team profile"}), 403
    team = Team.query.get_or_404(team_id)
    try:
        if request.method == "DELETE":
            remove_profile_banner(team, "team")
            return jsonify({"success": True})
        path = replace_profile_banner(team, "team", read_banner_upload())
        return jsonify({"success": True, "path": path})
    except BannerUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/teams/<team_id>/profile-photo", methods=["POST"])
@login_required
def upload_team_profile_photo(team_id):
    """Validate and replace the team's avatar without overwriting a cached URL."""
    if current_user.id != team_id or not is_team(current_user):
        return (
            jsonify({"error": "You can only upload a photo for your own team profile"}),
            403,
        )
    team = Team.query.get_or_404(team_id)
    try:
        path = replace_profile_photo(team, "team", read_profile_photo_upload())
        return jsonify({"success": True, "path": path})
    except PhotoUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/teams/<team_id>/profile-photo", methods=["DELETE"])
@login_required
def delete_team_profile_photo(team_id):
    """Remove team profile photo."""
    if current_user.id != team_id or not is_team(current_user):
        return (
            jsonify({"error": "You can only remove a photo from your own team profile"}),
            403,
        )
    team = Team.query.get_or_404(team_id)
    try:
        remove_profile_photo(team, "team")
        return jsonify({"success": True})
    except PhotoUploadError as error:
        return jsonify({"error": str(error)}), error.status
