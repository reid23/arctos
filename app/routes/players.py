"""Player profile, profile-photo, and injury routes.

Hosts the ``players`` blueprint at ``/_api``:

- ``/players`` (GET) - list players.
- ``/players/<player_id>`` (GET, PUT) - read and edit player profile.
- ``/players/<player_id>/profile-photo`` (POST, DELETE) - upload and
  remove the player's profile photo.
- ``/players/<player_id>/banner-photo`` (POST, DELETE) - replace or remove
  the player's validated profile banner.
- ``/players/<player_id>/injuries`` (GET, POST) - list and add injuries.
- ``/players/<player_id>/injuries/<injury_id>`` (GET, PUT, DELETE) -
  read, edit, and remove a single injury record.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.serializers.registration_serializer import player_reg_waiver_api
from app.services.profile_stats_service import past_event_counts
from app.utils.datetime_helpers import dt_iso
from app.utils.helpers import can_head_ref_match, get_registrable_config
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
from app.utils.user_helpers import is_player
from models import (
    Injury,
    League,
    Match,
    MatchNote,
    PenaltyType,
    Player,
    PlayerRegistration,
    Point,
    TeamRegistration,
    Tournament,
    db,
)

bp = Blueprint("players", __name__, url_prefix="/_api")


def _injury_json(inj):
    return {
        "id": inj.id,
        "message": inj.message,
        "stamp": dt_iso(inj.stamp),
        "active": bool(inj.active),
        "show": bool(inj.show),
    }


@bp.route("/players", methods=["GET"])
def players_list():
    """List players with optional search and pagination."""
    search = request.args.get("search", "").strip()
    page = max(1, request.args.get("page", type=int) or 1)
    per_page = 50
    if search:
        q = Player.query.filter(Player.name.contains(search) | Player.id.contains(search))
    else:
        q = Player.query
    total = q.count()
    total_pages = (total + per_page - 1) // per_page
    players = q.order_by(Player.name.asc()).offset((page - 1) * per_page).limit(per_page).all()
    event_counts = past_event_counts("player", (player.id for player in players))
    return jsonify(
        {
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "profile_photo": p.profile_photo,
                    "location": p.location,
                    "past_event_count": event_counts[p.id],
                }
                for p in players
            ],
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }
    )


@bp.route("/players/<player_id>", methods=["GET"])
def player_profile(player_id):
    """Player profile (public)."""
    player = Player.query.get(player_id)
    if not player:
        return jsonify({"error": "Not found"}), 404
    regs = PlayerRegistration.query.filter_by(player=player_id).all()
    can_see_private = current_user.is_authenticated and current_user.id == player_id
    injuries_query = Injury.query.filter_by(player=player_id)
    if not can_see_private:
        injuries_query = injuries_query.filter_by(show=True)
    injuries = injuries_query.order_by(Injury.stamp.desc()).all()
    player_notes = []
    if current_user.is_authenticated:
        try:
            all_player_notes = (
                MatchNote.query.filter_by(player_id=player_id)
                .options(joinedload(MatchNote.penalty_type))
                .order_by(MatchNote.created_at.desc())
                .all()
            )
            _all_note_match_ids = [n.match for n in all_player_notes if n.match]
            _all_matches_by_id = (
                {m.uuid: m for m in Match.query.filter(Match.uuid.in_(_all_note_match_ids)).all()}
                if _all_note_match_ids
                else {}
            )
            for note in all_player_notes:
                can_see_note = False
                if current_user.id == player_id:
                    can_see_note = True
                elif is_player(current_user):
                    match_obj = _all_matches_by_id.get(note.match) if note.match else None
                    if match_obj and can_head_ref_match(match_obj.event, current_user.id, match=match_obj):
                        can_see_note = True
                if can_see_note:
                    player_notes.append(note)
        except Exception:
            player_notes = []

    note_match_ids = [n.match for n in player_notes if n.match]
    matches_by_id = (
        {m.uuid: m for m in Match.query.filter(Match.uuid.in_(note_match_ids)).all()} if note_match_ids else {}
    )

    penalty_type_ids = {
        getattr(n, "penalty_type_id", None) for n in player_notes if getattr(n, "penalty_type_id", None)
    }
    pt_map = {}
    if penalty_type_ids:
        for pt in PenaltyType.query.filter(PenaltyType.id.in_(penalty_type_ids)).all():
            pt_map[pt.id] = {"name": pt.name, "color": pt.color, "desc": pt.desc or ""}

    player_note_rows = []
    if player_notes:
        match_to_points = {}
        for note in player_notes:
            idx = "-"
            match_obj = matches_by_id.get(note.match) if note.match else None
            if match_obj and note.point_id:
                match_id = match_obj.uuid
                if match_id not in match_to_points:
                    pts = Point.query.filter_by(match=match_id).order_by(Point.stamp).all()
                    match_to_points[match_id] = [p.uuid for p in pts]
                order = match_to_points.get(match_id, [])
                if note.point_id in order:
                    idx = order.index(note.point_id) + 1
            pt_id = getattr(note, "penalty_type_id", None)
            pt_rel = getattr(note, "penalty_type", None)
            if pt_rel is not None:
                pt_info = {
                    "name": pt_rel.name,
                    "color": pt_rel.color,
                    "desc": pt_rel.desc or "",
                }
            else:
                pt_info = pt_map.get(pt_id) if pt_id else None
            if pt_info is None and pt_id:
                _pt = PenaltyType.query.get(pt_id)
                if _pt:
                    pt_info = {
                        "name": _pt.name,
                        "color": _pt.color,
                        "desc": _pt.desc or "",
                    }
            player_note_rows.append(
                {
                    "created_at": dt_iso(note.created_at),
                    "text": note.text or "",
                    "point_index": str(idx),
                    "penalty_type_id": pt_id,
                    "penalty_type_name": pt_info["name"] if pt_info else None,
                    "penalty_type_color": pt_info["color"] if pt_info else None,
                    "penalty_type_desc": pt_info.get("desc", "") if pt_info else None,
                    "match": (
                        {
                            "event": match_obj.event if match_obj else None,
                            "uuid": match_obj.uuid if match_obj else None,
                            "name": match_obj.name if match_obj else None,
                        }
                        if match_obj
                        else None
                    ),
                }
            )

    event_urls = {r.event for r in regs if r.event}
    league_ids = {r.league_id for r in regs if r.league_id and not r.event}
    tournaments_by_url = (
        {t.url: t for t in Tournament.query.filter(Tournament.url.in_(event_urls)).all()} if event_urls else {}
    )
    leagues_by_id = {lg.url: lg for lg in League.query.filter(League.url.in_(league_ids)).all()} if league_ids else {}

    team_keys_needed = set()
    for r in regs:
        if not r.team:
            continue
        if r.event:
            team_keys_needed.add(("event", r.event, r.team))
        elif r.league_id:
            team_keys_needed.add(("league", r.league_id, r.team))

    team_pseudonym_by_key = {}
    event_team_pairs = [(e, t) for kind, e, t in team_keys_needed if kind == "event"]
    league_team_pairs = [(lg, t) for kind, lg, t in team_keys_needed if kind == "league"]
    if event_team_pairs:
        ev_urls = {e for e, t in event_team_pairs}
        ev_team_ids = {t for e, t in event_team_pairs}
        for tr in TeamRegistration.query.filter(
            TeamRegistration.event.in_(ev_urls),
            TeamRegistration.team.in_(ev_team_ids),
        ).all():
            team_pseudonym_by_key[("event", tr.event, tr.team)] = tr.pseudonym
    if league_team_pairs:
        lg_ids = {lg for lg, t in league_team_pairs}
        lg_team_ids = {t for lg, t in league_team_pairs}
        for tr in TeamRegistration.query.filter(
            TeamRegistration.league_id.in_(lg_ids),
            TeamRegistration.team.in_(lg_team_ids),
        ).all():
            team_pseudonym_by_key[("league", tr.league_id, tr.team)] = tr.pseudonym

    def _team_pseudonym(r):
        if not r.team:
            return None
        if r.event:
            return team_pseudonym_by_key.get(("event", r.event, r.team))
        if r.league_id:
            return team_pseudonym_by_key.get(("league", r.league_id, r.team))
        return None

    registration_rows = []
    for r in regs:
        rcfg = None
        if r.event:
            tour = tournaments_by_url.get(r.event)
            if tour:
                rcfg = get_registrable_config(tour)
        elif r.league_id:
            lg = leagues_by_id.get(r.league_id)
            if lg:
                rcfg = lg.registrable_config
        w = player_reg_waiver_api(r, rcfg)
        registration_rows.append(
            {
                "event": r.event or (f"league:{r.league_id}" if r.league_id else ""),
                "team": r.team,
                "team_pseudonym": _team_pseudonym(r),
                "status": (r.status.value if hasattr(r.status, "value") else str(r.status)),
                "jersey_name": r.jersey_name,
                "jersey_number": r.jersey_number,
                "paid": bool(r.paid),
                "amount_paid": r.amount_paid or 0.0,
                "waiver_required": w["waiver_required"],
                "waiver_status": w["waiver_status"],
            }
        )

    return jsonify(
        {
            "player": {
                "id": player.id,
                "name": player.name,
                "profile_photo": player.profile_photo,
                "banner_photo": player.banner_photo,
                "phone": (player.phone if (current_user.is_authenticated and current_user.id == player_id) else None),
                "location": player.location,
                "bio": player.bio,
                "pronouns": player.pronouns,
                "pompfen": player.pompfen or [],
            },
            "registrations": registration_rows,
            "past_event_count": past_event_counts("player", [player_id])[player_id],
            "injuries": [
                {
                    "id": inj.id,
                    "message": inj.message,
                    "stamp": dt_iso(inj.stamp),
                    "active": bool(inj.active),
                    "show": bool(inj.show),
                }
                for inj in injuries
            ],
            "player_notes": player_note_rows,
        }
    )


@bp.route("/players/<player_id>/injuries/<int:injury_id>", methods=["GET"])
@login_required
def get_injury(player_id, injury_id):
    if current_user.id != player_id:
        return jsonify({"error": "Forbidden"}), 403
    injury = Injury.query.filter_by(id=injury_id, player=player_id).first_or_404()
    return jsonify(
        {
            "id": injury.id,
            "message": injury.message,
            "stamp": dt_iso(injury.stamp),
            "active": bool(injury.active),
            "show": bool(injury.show),
        }
    )


@bp.route("/players/<player_id>/injuries", methods=["GET"])
@login_required
def list_injuries(player_id):
    if current_user.id != player_id:
        return jsonify({"error": "Forbidden"}), 403
    injuries = Injury.query.filter_by(player=player_id).order_by(Injury.stamp.desc()).all()
    return jsonify([_injury_json(inj) for inj in injuries])


@bp.route("/players/<player_id>/injuries", methods=["POST"])
@login_required
def create_injury(player_id):
    if current_user.id != player_id:
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    injury = Injury(
        player=player_id,
        message=message,
        show=bool(data.get("show")),
        active=bool(data.get("active")),
    )

    custom_date = data.get("custom_date")
    if custom_date:
        try:
            injury.stamp = datetime.strptime(custom_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400
    db.session.add(injury)
    db.session.commit()
    return jsonify(
        {
            "id": injury.id,
            "message": injury.message,
            "stamp": dt_iso(injury.stamp),
            "active": bool(injury.active),
            "show": bool(injury.show),
        }
    )


@bp.route("/players/<player_id>/injuries/<int:injury_id>", methods=["PUT"])
@login_required
def update_injury(player_id, injury_id):
    if current_user.id != player_id:
        return jsonify({"error": "Forbidden"}), 403
    injury = Injury.query.filter_by(id=injury_id, player=player_id).first_or_404()
    data = request.get_json() or {}
    message = data.get("message")
    if message is not None:
        message = message.strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        injury.message = message
    if "show" in data:
        injury.show = bool(data.get("show"))
    if "active" in data:
        injury.active = bool(data.get("active"))
    if "custom_date" in data:
        custom_date = data.get("custom_date")
        if custom_date:
            try:
                injury.stamp = datetime.strptime(custom_date, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400
        else:
            injury.stamp = None
    db.session.commit()
    return jsonify(
        {
            "id": injury.id,
            "message": injury.message,
            "stamp": dt_iso(injury.stamp),
            "active": bool(injury.active),
            "show": bool(injury.show),
        }
    )


@bp.route("/players/<player_id>/injuries/<int:injury_id>", methods=["DELETE"])
@login_required
def delete_injury_api(player_id, injury_id):
    if current_user.id != player_id:
        return jsonify({"error": "Forbidden"}), 403
    injury = Injury.query.filter_by(id=injury_id, player=player_id).first_or_404()
    db.session.delete(injury)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/players/<player_id>", methods=["PUT"])
@login_required
def update_player_profile(player_id):
    """Save profile text and optional multipart images together."""
    if current_user.id != player_id or not is_player(current_user):
        return jsonify({"error": "You can only edit your own profile"}), 403

    player = Player.query.get_or_404(player_id)
    try:
        save_profile_edit(player, "player")
    except ProfileEditError as error:
        return jsonify({"error": str(error)}), error.status
    return jsonify({"success": True})


@bp.route("/players/<player_id>/banner-photo", methods=["POST", "DELETE"])
@login_required
def player_banner_photo(player_id):
    """Replace or remove a banner belonging to this player account only."""
    if not is_player(current_user) or current_user.id != player_id:
        return jsonify({"error": "You can only change the banner on your own player profile"}), 403
    player = Player.query.get_or_404(player_id)
    try:
        if request.method == "DELETE":
            remove_profile_banner(player, "player")
            return jsonify({"success": True})
        path = replace_profile_banner(player, "player", read_banner_upload())
        return jsonify({"success": True, "path": path})
    except BannerUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/players/<player_id>/profile-photo", methods=["POST"])
@login_required
def upload_player_profile_photo(player_id):
    """Validate and replace the player's avatar without overwriting a cached URL."""
    if current_user.id != player_id or not is_player(current_user):
        return (
            jsonify({"error": "You can only upload a photo for your own profile"}),
            403,
        )
    player = Player.query.get_or_404(player_id)
    try:
        path = replace_profile_photo(player, "player", read_profile_photo_upload())
        return jsonify({"success": True, "path": path})
    except PhotoUploadError as error:
        return jsonify({"error": str(error)}), error.status


@bp.route("/players/<player_id>/profile-photo", methods=["DELETE"])
@login_required
def delete_player_profile_photo(player_id):
    """Remove player profile photo."""
    if current_user.id != player_id or not is_player(current_user):
        return (
            jsonify({"error": "You can only remove a photo from your own profile"}),
            403,
        )
    player = Player.query.get_or_404(player_id)
    try:
        remove_profile_photo(player, "player")
        return jsonify({"success": True})
    except PhotoUploadError as error:
        return jsonify({"error": str(error)}), error.status
