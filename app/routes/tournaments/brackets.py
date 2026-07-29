"""Tournament bracket endpoints (canvas builder + legacy image setup).

Part of the ``tournaments`` blueprint. Uses the same Blueprint object
defined in :mod:`app.routes.tournaments.__init__`.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from flask import current_app, g, jsonify, request
from flask_login import current_user, login_required

from app.domain.enums import BracketPortMode, MatchStatus, ScheduleType, TeamRegistrationStatus, parse_enum
from app.models.bracket import (
    DEFAULT_BRACKET_HEIGHT,
    DEFAULT_BRACKET_WIDTH,
    DEFAULT_IMAGE_HEIGHT,
    DEFAULT_IMAGE_WIDTH,
    DEFAULT_TEXT_SIZE,
    BracketImage,
    BracketLabeledTeam,
    BracketPlacement,
    BracketText,
)
from app.serializers.tournament_serializer import team_name_for_match, tournament_to_dict
from app.services.permission_service import PermissionService
from app.services.registration_resolver import team_registration_for_tournament
from app.utils.decorators import require_json_body
from app.utils.helpers import check_tournament_access
from models import (
    Match,
    Tag,
    Team,
    TeamRegistration,
    Tournament,
    db,
)

from . import bp

_MATCH_REF_RE = re.compile(r"^(?P<name>.+)::(?P<qual>winner|loser)$", re.IGNORECASE)


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


def _parse_match_ref(initial: str | None) -> tuple[str, str] | None:
    """Return ``(match_name, 'winner'|'loser')`` if *initial* is a match ref."""
    if not initial:
        return None
    m = _MATCH_REF_RE.match(initial.strip())
    if not m:
        return None
    return m.group("name").strip(), m.group("qual").lower()


def _port_mode(value) -> BracketPortMode:
    parsed = parse_enum(BracketPortMode, value)
    if parsed.is_some():
        return parsed.unwrap()
    if isinstance(value, str) and value.strip().upper() == "NET":
        return BracketPortMode.NET
    return BracketPortMode.LABEL


def _team_display(tournament, team_id: str | None) -> dict | None:
    if not team_id:
        return None
    reg = team_registration_for_tournament(tournament, team_id)
    team = Team.query.get(team_id)
    return {
        "id": team_id,
        "pseudonym": reg.pseudonym if reg else (team.name if team else team_id),
        "shortname": getattr(reg, "shortname", None) if reg else None,
        "profile_photo": team.profile_photo if team else None,
    }


def _match_payload(tournament, match: Match, placement: BracketPlacement | None) -> dict:
    """Serialize a match plus optional placement for the canvas API."""
    t1 = _team_display(tournament, match.team1)
    t2 = _team_display(tournament, match.team2)
    place = None
    if placement is not None:
        place = {
            "x_pos": placement.x_pos,
            "y_pos": placement.y_pos,
            "width": placement.width if placement.width is not None else DEFAULT_BRACKET_WIDTH,
            "height": placement.height if placement.height is not None else DEFAULT_BRACKET_HEIGHT,
            "team1": (placement.team1.value if isinstance(placement.team1, BracketPortMode) else str(placement.team1)),
            "team2": (placement.team2.value if isinstance(placement.team2, BracketPortMode) else str(placement.team2)),
            "inputs_flipped": bool(getattr(placement, "inputs_flipped", False)),
            "placed": placement.is_placed,
        }
    return {
        "uuid": match.uuid,
        "name": match.name,
        "team1": match.team1,
        "team2": match.team2,
        "team1_name": team_name_for_match(tournament, match, "team1"),
        "team2_name": team_name_for_match(tournament, match, "team2"),
        "team1_shortname": t1.get("shortname") if t1 else None,
        "team2_shortname": t2.get("shortname") if t2 else None,
        "team1_photo": t1.get("profile_photo") if t1 else None,
        "team2_photo": t2.get("profile_photo") if t2 else None,
        "team1_initial": match.team1_initial,
        "team2_initial": match.team2_initial,
        "status": match.status.value if match.status else MatchStatus.NOT_STARTED.value,
        "match_winner": match.match_winner.value if match.match_winner else None,
        "schedule_type": match.schedule_type.value if match.schedule_type else None,
        "placement": place,
    }


def _playable_matches(tournament_url: str) -> list[Match]:
    """Matches that can appear on a bracket (exclude BREAK/JOIN)."""
    rows = Match.query.filter_by(event=tournament_url).order_by(Match.name).all()
    out = []
    for m in rows:
        st = m.schedule_type
        if st in (ScheduleType.BREAK, ScheduleType.JOIN):
            continue
        # Also skip stringy legacy values just in case
        if isinstance(st, str) and st.upper() in ("BREAK", "JOIN"):
            continue
        out.append(m)
    return out


def _default_port_modes(
    match: Match,
    placed_by_name: dict[str, Match],
) -> tuple[BracketPortMode, BracketPortMode]:
    """Choose LABEL vs NET for a newly placed match.

    Inputs default to LABEL unless they reference a match that is already
    on the canvas, in which case they become NET (wire).
    """

    def mode_for(initial: str | None) -> BracketPortMode:
        ref = _parse_match_ref(initial)
        if ref is None:
            return BracketPortMode.LABEL
        name, _qual = ref
        src = placed_by_name.get(name.lower())
        if src is None:
            return BracketPortMode.LABEL
        return BracketPortMode.NET

    return mode_for(match.team1_initial), mode_for(match.team2_initial)


def _team_options_and_tags(tournament_url: str) -> tuple[list[dict], list[dict]]:
    regs = TeamRegistration.query.filter_by(
        event=tournament_url,
        status=TeamRegistrationStatus.CONFIRMED,
    ).all()
    team_options = []
    for reg in regs:
        team = Team.query.get(reg.team)
        team_options.append(
            {
                "id": reg.team,
                "pseudonym": reg.pseudonym,
                "shortname": getattr(reg, "shortname", None),
                "profile_photo": team.profile_photo if team else None,
            }
        )
    tags = Tag.query.filter_by(event=tournament_url).all()
    tag_list = [{"id": t.id, "name": t.name, "team": t.team} for t in tags]
    return team_options, tag_list


def _text_payload(row: BracketText) -> dict:
    return {
        "id": row.id,
        "text": row.text or "",
        "x_pos": row.x_pos,
        "y_pos": row.y_pos,
        "size": row.size if row.size is not None else DEFAULT_TEXT_SIZE,
    }


def _labeled_team_payload(tournament, row: BracketLabeledTeam) -> dict:
    team_token = (row.team or "").strip()
    kind = row.kind.value if isinstance(row.kind, BracketPortMode) else str(row.kind or "LABEL")
    # Resolve display info when the token maps to a concrete team.
    resolved = None
    display_text = team_token.replace("::", " ") if team_token else ""
    photo = None
    shortname = None
    team_id = None

    if team_token.lower().startswith("tag::"):
        tag_name = team_token[5:].strip()
        tag = Tag.query.filter_by(event=tournament.url, name=tag_name).first()
        if tag and tag.team:
            info = _team_display(tournament, tag.team)
            if info:
                team_id = info["id"]
                display_text = info["pseudonym"] or team_id
                photo = info.get("profile_photo")
                shortname = info.get("shortname")
        else:
            display_text = f"tag::{tag_name}" if tag_name else team_token
    elif "::" in team_token:
        ref = _parse_match_ref(team_token)
        if ref:
            name, qual = ref
            m = Match.query.filter_by(event=tournament.url, name=name).first()
            if m and m.status == MatchStatus.COMPLETED and m.match_winner:
                tid = m.winner_team_id if qual == "winner" else m.loser_team_id
                info = _team_display(tournament, tid)
                if info:
                    team_id = info["id"]
                    display_text = info["pseudonym"] or team_id
                    photo = info.get("profile_photo")
                    shortname = info.get("shortname")
            else:
                display_text = f"{name} {qual}"
    elif team_token:
        info = _team_display(tournament, team_token)
        if info is None:
            # try pseudonym match via confirmed regs
            regs = TeamRegistration.query.filter_by(event=tournament.url).all()
            for reg in regs:
                if reg.pseudonym == team_token or reg.team == team_token:
                    info = _team_display(tournament, reg.team)
                    break
        if info:
            team_id = info["id"]
            display_text = info["pseudonym"] or team_id
            photo = info.get("profile_photo")
            shortname = info.get("shortname")

    label = (getattr(row, "label", None) or "").strip()[:50]
    return {
        "id": row.id,
        "label": label,
        "team": team_token,
        "kind": kind,
        "x_pos": row.x_pos,
        "y_pos": row.y_pos,
        "team_id": team_id,
        "display_text": display_text,
        "profile_photo": photo,
        "shortname": shortname,
        # True when the token has resolved to a concrete registered team.
        "resolved": team_id is not None,
    }


def _image_payload(row: BracketImage) -> dict:
    return {
        "id": row.id,
        "image": row.image or "",
        "x_pos": row.x_pos,
        "y_pos": row.y_pos,
        "width": row.width if row.width is not None else DEFAULT_IMAGE_WIDTH,
        "height": row.height if row.height is not None else DEFAULT_IMAGE_HEIGHT,
    }


def _delete_image_file(rel_path: str | None) -> None:
    """Best-effort delete of a bracket image under static/."""
    if not rel_path:
        return
    # Only allow paths under uploads/brackets/
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm.startswith("uploads/brackets/"):
        return
    root = os.path.join(current_app.root_path, "../static")
    full = os.path.normpath(os.path.join(root, norm))
    static_root = os.path.normpath(root)
    if not full.startswith(static_root + os.sep):
        return
    try:
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass


def _bracket_response(tournament, is_to: bool) -> dict:
    tournament_url = tournament.url
    matches = _playable_matches(tournament_url)
    placements = {p.match: p for p in BracketPlacement.query.filter_by(event=tournament_url).all()}
    team_options, tags = _team_options_and_tags(tournament_url)
    texts = [_text_payload(t) for t in BracketText.query.filter_by(event=tournament_url).order_by(BracketText.id).all()]
    labeled = [
        _labeled_team_payload(tournament, t)
        for t in BracketLabeledTeam.query.filter_by(event=tournament_url).order_by(BracketLabeledTeam.id).all()
    ]
    images = [
        _image_payload(i) for i in BracketImage.query.filter_by(event=tournament_url).order_by(BracketImage.id).all()
    ]
    return {
        "tournament": tournament_to_dict(tournament),
        "is_to": is_to,
        "team_options": team_options,
        "tags": tags,
        "matches": [_match_payload(tournament, m, placements.get(m.uuid)) for m in matches],
        "texts": texts,
        "labeled_teams": labeled,
        "images": images,
    }


@bp.route("/tournaments/<tournament_url>/bracket", methods=["GET"])
def tournament_bracket_api(tournament_url):
    """Canvas bracket data: playable matches + placements + token metadata.

    Available when the schedule is published, or always for TOs.  Returns an
    empty match list rather than 404 when nothing is configured yet so TOs
    can open edit mode and start placing matches.
    """
    has_access, tournament = check_tournament_access(tournament_url)
    if not has_access or not tournament:
        return jsonify({"error": "Not found"}), 404

    is_to = _check_to(tournament_url)
    if not tournament.schedule_published and not is_to:
        return jsonify({"error": "Bracket is not available"}), 403

    return jsonify(_bracket_response(tournament, is_to))


@bp.route("/tournaments/<tournament_url>/bracket-placements", methods=["PUT"])
@login_required
@require_json_body()
def tournament_bracket_placements_save_api(tournament_url):
    """Replace bracket placements for a tournament (TO only).

    Body::

        {
          "placements": [
            {
              "match": "<uuid>",
              "x_pos": 120.0 | null,
              "y_pos": 40.0 | null,
              "width": 280.0,
              "height": 100.0,
              "team1": "LABEL" | "NET",
              "team2": "LABEL" | "NET",
              "inputs_flipped": bool
            },
            ...
          ]
        }

    Placements with null coordinates unplace the match (row kept so port
    modes can be preserved, or omitted entirely to delete).  Matches not
    listed are left untouched.  Pass ``"clear_missing": true`` to delete
    placements for matches not present in the payload.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    items = data.get("placements")
    if not isinstance(items, list):
        return jsonify({"error": "placements must be a list"}), 400

    clear_missing = bool(data.get("clear_missing", False))

    playable = {m.uuid: m for m in _playable_matches(tournament_url)}
    existing = {p.match: p for p in BracketPlacement.query.filter_by(event=tournament_url).all()}
    seen: set[str] = set()

    for raw in items:
        if not isinstance(raw, dict):
            continue
        match_id = (raw.get("match") or raw.get("uuid") or "").strip()
        if not match_id or match_id not in playable:
            continue
        seen.add(match_id)

        x_raw = raw.get("x_pos", raw.get("x"))
        y_raw = raw.get("y_pos", raw.get("y"))
        try:
            x_pos = float(x_raw) if x_raw is not None else None
            y_pos = float(y_raw) if y_raw is not None else None
        except (TypeError, ValueError):
            x_pos, y_pos = None, None

        # Both-or-neither: partial coords mean unplaced.
        if x_pos is None or y_pos is None:
            x_pos, y_pos = None, None

        try:
            width = float(raw.get("width", DEFAULT_BRACKET_WIDTH) or DEFAULT_BRACKET_WIDTH)
        except (TypeError, ValueError):
            width = DEFAULT_BRACKET_WIDTH
        try:
            height = float(raw.get("height", DEFAULT_BRACKET_HEIGHT) or DEFAULT_BRACKET_HEIGHT)
        except (TypeError, ValueError):
            height = DEFAULT_BRACKET_HEIGHT
        width = max(160.0, min(width, 800.0))
        height = max(70.0, min(height, 400.0))

        team1 = _port_mode(raw.get("team1", "LABEL"))
        team2 = _port_mode(raw.get("team2", "LABEL"))
        inputs_flipped = bool(raw.get("inputs_flipped", False))

        # NET is only valid for winner/loser refs; coerce otherwise.
        m = playable[match_id]
        if team1 == BracketPortMode.NET and _parse_match_ref(m.team1_initial) is None:
            team1 = BracketPortMode.LABEL
        if team2 == BracketPortMode.NET and _parse_match_ref(m.team2_initial) is None:
            team2 = BracketPortMode.LABEL

        row = existing.get(match_id)
        if row is None:
            row = BracketPlacement(event=tournament_url, match=match_id)
            db.session.add(row)
            existing[match_id] = row
        row.x_pos = x_pos
        row.y_pos = y_pos
        row.width = width
        row.height = height
        row.team1 = team1
        row.team2 = team2
        row.inputs_flipped = inputs_flipped

    if clear_missing:
        for mid, row in list(existing.items()):
            if mid not in seen:
                db.session.delete(row)

    # --- texts ---
    texts_in = data.get("texts")
    if isinstance(texts_in, list):
        existing_texts = {t.id: t for t in BracketText.query.filter_by(event=tournament_url).all()}
        seen_t: set[str] = set()
        for raw in texts_in:
            if not isinstance(raw, dict):
                continue
            tid = (raw.get("id") or "").strip()
            if not tid:
                continue
            seen_t.add(tid)
            row = existing_texts.get(tid)
            if row is None:
                row = BracketText(id=tid, event=tournament_url)
                db.session.add(row)
            row.text = str(raw.get("text") or "")
            try:
                row.x_pos = float(raw.get("x_pos", 40) or 40)
                row.y_pos = float(raw.get("y_pos", 40) or 40)
                row.size = float(raw.get("size", DEFAULT_TEXT_SIZE) or DEFAULT_TEXT_SIZE)
            except (TypeError, ValueError):
                row.x_pos = row.x_pos or 40.0
                row.y_pos = row.y_pos or 40.0
                row.size = row.size or DEFAULT_TEXT_SIZE
            row.size = max(8.0, min(row.size, 200.0))
        if clear_missing:
            for tid, row in list(existing_texts.items()):
                if tid not in seen_t:
                    db.session.delete(row)

    # --- labeled teams ---
    lts_in = data.get("labeled_teams")
    if isinstance(lts_in, list):
        existing_lt = {t.id: t for t in BracketLabeledTeam.query.filter_by(event=tournament_url).all()}
        seen_lt: set[str] = set()
        for raw in lts_in:
            if not isinstance(raw, dict):
                continue
            tid = (raw.get("id") or "").strip()
            if not tid:
                continue
            seen_lt.add(tid)
            row = existing_lt.get(tid)
            if row is None:
                row = BracketLabeledTeam(id=tid, event=tournament_url)
                db.session.add(row)
            team_token = str(raw.get("team") or "").strip()
            kind = _port_mode(raw.get("kind", "LABEL"))
            if kind == BracketPortMode.NET and _parse_match_ref(team_token) is None:
                kind = BracketPortMode.LABEL
            row.label = str(raw.get("label") or "")[:50]
            row.team = team_token
            row.kind = kind
            try:
                row.x_pos = float(raw.get("x_pos", 40) or 40)
                row.y_pos = float(raw.get("y_pos", 40) or 40)
            except (TypeError, ValueError):
                row.x_pos = row.x_pos or 40.0
                row.y_pos = row.y_pos or 40.0
        if clear_missing:
            for tid, row in list(existing_lt.items()):
                if tid not in seen_lt:
                    db.session.delete(row)

    # --- images ---
    imgs_in = data.get("images")
    if isinstance(imgs_in, list):
        existing_img = {i.id: i for i in BracketImage.query.filter_by(event=tournament_url).all()}
        seen_i: set[str] = set()
        for raw in imgs_in:
            if not isinstance(raw, dict):
                continue
            iid = (raw.get("id") or "").strip()
            if not iid:
                continue
            seen_i.add(iid)
            row = existing_img.get(iid)
            if row is None:
                row = BracketImage(id=iid, event=tournament_url)
                db.session.add(row)
            new_path = str(raw.get("image") or row.image or "").strip()
            if row.image and new_path and new_path != row.image:
                _delete_image_file(row.image)
            row.image = new_path
            try:
                row.x_pos = float(raw.get("x_pos", 40) or 40)
                row.y_pos = float(raw.get("y_pos", 40) or 40)
                row.width = float(raw.get("width", DEFAULT_IMAGE_WIDTH) or DEFAULT_IMAGE_WIDTH)
                row.height = float(raw.get("height", DEFAULT_IMAGE_HEIGHT) or DEFAULT_IMAGE_HEIGHT)
            except (TypeError, ValueError):
                pass
            row.width = max(20.0, min(row.width or DEFAULT_IMAGE_WIDTH, 4000.0))
            row.height = max(20.0, min(row.height or DEFAULT_IMAGE_HEIGHT, 4000.0))
        if clear_missing:
            for iid, row in list(existing_img.items()):
                if iid not in seen_i:
                    _delete_image_file(row.image)
                    db.session.delete(row)

    db.session.commit()

    return jsonify({"success": True, **_bracket_response(tournament, True)})


@bp.route("/tournaments/<tournament_url>/bracket-placements/add", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_placement_add_api(tournament_url):
    """Place one match on the canvas (TO only).

    Body: ``{"match": "<uuid>", "x_pos": 40, "y_pos": 40}`` (coords optional).
    Auto-wires inputs to NET when the referenced source match is already placed.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    match_id = (data.get("match") or data.get("uuid") or "").strip()
    if not match_id:
        return jsonify({"error": "match is required"}), 400

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
    if match is None:
        return jsonify({"error": "Match not found"}), 404
    if match.schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        return jsonify({"error": "BREAK/JOIN matches cannot be placed on the bracket"}), 400

    existing_placed = {p.match: p for p in BracketPlacement.query.filter_by(event=tournament_url).all() if p.is_placed}
    # name → match for auto-wire decisions
    playable = _playable_matches(tournament_url)
    placed_by_name: dict[str, Match] = {}
    uuid_to_match = {m.uuid: m for m in playable}
    for mid in existing_placed:
        src = uuid_to_match.get(mid)
        if src is not None:
            placed_by_name[src.name.lower()] = src

    team1_mode, team2_mode = _default_port_modes(match, placed_by_name)

    try:
        x_pos = float(data["x_pos"]) if data.get("x_pos") is not None else 40.0
        y_pos = float(data["y_pos"]) if data.get("y_pos") is not None else 40.0
    except (TypeError, ValueError):
        x_pos, y_pos = 40.0, 40.0

    # Cascade-place missing NET sources near the new match.
    cascade: list[Match] = []
    for initial, mode in ((match.team1_initial, team1_mode), (match.team2_initial, team2_mode)):
        # Even if mode is LABEL because source wasn't placed, if it's a ref we may
        # still only place on explicit convert-to-wire. Here for add we only auto-NET
        # when already placed — no cascade on add unless already placed.
        _ = (initial, mode)

    width = DEFAULT_BRACKET_WIDTH
    height = DEFAULT_BRACKET_HEIGHT
    try:
        if data.get("width") is not None:
            width = float(data["width"])
        if data.get("height") is not None:
            height = float(data["height"])
    except (TypeError, ValueError):
        pass

    row = BracketPlacement.query.filter_by(event=tournament_url, match=match_id).first()
    if row is None:
        row = BracketPlacement(event=tournament_url, match=match_id)
        db.session.add(row)
    row.x_pos = x_pos
    row.y_pos = y_pos
    row.width = width
    row.height = height
    row.team1 = team1_mode
    row.team2 = team2_mode

    for src in cascade:
        if src.uuid in existing_placed:
            continue
        src_row = BracketPlacement.query.filter_by(event=tournament_url, match=src.uuid).first()
        if src_row is None:
            src_row = BracketPlacement(event=tournament_url, match=src.uuid)
            db.session.add(src_row)
        if not src_row.is_placed:
            src_row.x_pos = max(0.0, x_pos - (DEFAULT_BRACKET_WIDTH + 80))
            src_row.y_pos = y_pos
            src_row.width = DEFAULT_BRACKET_WIDTH
            src_row.height = DEFAULT_BRACKET_HEIGHT
            s1, s2 = _default_port_modes(src, placed_by_name)
            src_row.team1 = s1
            src_row.team2 = s2
            placed_by_name[src.name.lower()] = src

    db.session.commit()

    return jsonify({"success": True, **_bracket_response(tournament, True)})


@bp.route("/tournaments/<tournament_url>/bracket-placements/convert-port", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_convert_port_api(tournament_url):
    """Toggle a match input between LABEL and NET (TO only).

    Body: ``{"match": "<uuid>", "side": "team1"|"team2", "mode": "LABEL"|"NET"}``.

    Converting to NET auto-places the referenced source match if needed.
    Converting to LABEL leaves all matches as they are.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    match_id = (data.get("match") or "").strip()
    side = (data.get("side") or "").strip().lower()
    mode = _port_mode(data.get("mode"))

    if side not in ("team1", "team2"):
        return jsonify({"error": "side must be team1 or team2"}), 400

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
    if match is None:
        return jsonify({"error": "Match not found"}), 404

    initial = match.team1_initial if side == "team1" else match.team2_initial
    ref = _parse_match_ref(initial)

    row = BracketPlacement.query.filter_by(event=tournament_url, match=match_id).first()
    if row is None or not row.is_placed:
        return jsonify({"error": "Match is not placed on the bracket"}), 400

    if mode == BracketPortMode.NET:
        if ref is None:
            return jsonify({"error": "Only match winner/loser inputs can be wired"}), 400
        src_name, _qual = ref
        src = Match.query.filter_by(event=tournament_url, name=src_name).first()
        if src is None:
            return jsonify({"error": f"Referenced match '{src_name}' not found"}), 404

        src_row = BracketPlacement.query.filter_by(event=tournament_url, match=src.uuid).first()
        if src_row is None:
            src_row = BracketPlacement(event=tournament_url, match=src.uuid)
            db.session.add(src_row)
        if not src_row.is_placed:
            # Place source to the left of the current match.
            src_row.x_pos = max(0.0, (row.x_pos or 0.0) - (DEFAULT_BRACKET_WIDTH + 100))
            src_row.y_pos = row.y_pos or 40.0
            src_row.width = DEFAULT_BRACKET_WIDTH
            src_row.height = DEFAULT_BRACKET_HEIGHT
            playable = _playable_matches(tournament_url)
            placed = {
                p.match: p
                for p in BracketPlacement.query.filter_by(event=tournament_url).all()
                if p.is_placed or p.match == src.uuid
            }
            placed_by_name = {m.name.lower(): m for m in playable if m.uuid in placed}
            s1, s2 = _default_port_modes(src, placed_by_name)
            src_row.team1 = s1
            src_row.team2 = s2

        if side == "team1":
            row.team1 = BracketPortMode.NET
        else:
            row.team2 = BracketPortMode.NET
    else:
        if side == "team1":
            row.team1 = BracketPortMode.LABEL
        else:
            row.team2 = BracketPortMode.LABEL

    db.session.commit()

    return jsonify({"success": True, **_bracket_response(tournament, True)})


@bp.route("/tournaments/<tournament_url>/bracket-elements/text", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_add_text_api(tournament_url):
    """Create a text annotation on the canvas (TO only)."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    import uuid as _uuid

    row = BracketText(
        id=str(_uuid.uuid4()),
        event=tournament_url,
        text=str(data.get("text") or "Text"),
        x_pos=float(data.get("x_pos", 40) or 40),
        y_pos=float(data.get("y_pos", 40) or 40),
        size=float(data.get("size", DEFAULT_TEXT_SIZE) or DEFAULT_TEXT_SIZE),
    )
    row.size = max(8.0, min(row.size, 200.0))
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "id": row.id, **_bracket_response(tournament, True)})


@bp.route("/tournaments/<tournament_url>/bracket-elements/labeled-team", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_add_labeled_team_api(tournament_url):
    """Create a labeled-team element on the canvas (TO only)."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    import uuid as _uuid

    team_token = str(data.get("team") or "").strip()
    kind = _port_mode(data.get("kind", "LABEL"))
    if kind == BracketPortMode.NET and _parse_match_ref(team_token) is None:
        kind = BracketPortMode.LABEL
    row = BracketLabeledTeam(
        id=str(_uuid.uuid4()),
        event=tournament_url,
        label=str(data.get("label") or "Label")[:50],
        team=team_token,
        kind=kind,
        x_pos=float(data.get("x_pos", 40) or 40),
        y_pos=float(data.get("y_pos", 40) or 40),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "id": row.id, **_bracket_response(tournament, True)})


@bp.route("/tournaments/<tournament_url>/bracket-elements/image", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_add_image_api(tournament_url):
    """Create an image element after upload (TO only).

    Body: ``{"image": "uploads/brackets/...", "x_pos", "y_pos", "width", "height"}``.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    import uuid as _uuid

    image_path = str(data.get("image") or "").strip()
    if not image_path.startswith("uploads/brackets/"):
        return jsonify({"error": "image path must be under uploads/brackets/"}), 400
    row = BracketImage(
        id=str(_uuid.uuid4()),
        event=tournament_url,
        image=image_path,
        x_pos=float(data.get("x_pos", 40) or 40),
        y_pos=float(data.get("y_pos", 40) or 40),
        width=float(data.get("width", DEFAULT_IMAGE_WIDTH) or DEFAULT_IMAGE_WIDTH),
        height=float(data.get("height", DEFAULT_IMAGE_HEIGHT) or DEFAULT_IMAGE_HEIGHT),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "id": row.id, **_bracket_response(tournament, True)})


@bp.route(
    "/tournaments/<tournament_url>/bracket-elements/labeled-team/<element_id>/convert",
    methods=["POST"],
)
@login_required
@require_json_body()
def tournament_bracket_convert_labeled_team_api(tournament_url, element_id):
    """Toggle a labeled-team element between LABEL and NET."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    data = g.json_body or {}
    row = BracketLabeledTeam.query.filter_by(event=tournament_url, id=element_id).first()
    if row is None:
        return jsonify({"error": "Not found"}), 404
    mode = _port_mode(data.get("mode", "LABEL"))
    if mode == BracketPortMode.NET:
        ref = _parse_match_ref(row.team)
        if ref is None:
            return jsonify({"error": "Only match winner/loser tokens can be wired"}), 400
        src_name, _qual = ref
        src = Match.query.filter_by(event=tournament_url, name=src_name).first()
        if src is None:
            return jsonify({"error": f"Referenced match '{src_name}' not found"}), 404
        src_row = BracketPlacement.query.filter_by(event=tournament_url, match=src.uuid).first()
        if src_row is None:
            src_row = BracketPlacement(event=tournament_url, match=src.uuid)
            db.session.add(src_row)
        if not src_row.is_placed:
            src_row.x_pos = max(0.0, (row.x_pos or 0.0) - (DEFAULT_BRACKET_WIDTH + 100))
            src_row.y_pos = row.y_pos or 40.0
            src_row.width = DEFAULT_BRACKET_WIDTH
            src_row.height = DEFAULT_BRACKET_HEIGHT
        row.kind = BracketPortMode.NET
    else:
        row.kind = BracketPortMode.LABEL
    db.session.commit()
    return jsonify({"success": True, **_bracket_response(tournament, True)})


# ---------------------------------------------------------------------------
# Legacy image-bracket setup (kept for backward compatibility)
# ---------------------------------------------------------------------------


@bp.route("/tournaments/<tournament_url>/bracket-setup-data", methods=["GET"])
@login_required
def tournament_bracket_setup_data_api(tournament_url):
    """Raw bracket configuration for the SPA bracket-setup page.

    This returns the underlying TOML data (already parsed) so that the
    Dioxus frontend can render and edit bracket annotations while the
    existing HTML form endpoint continues to handle multipart uploads.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    brackets_data = []
    if tournament.bracket:
        try:
            import tomli

            parsed = tomli.loads(tournament.bracket)
            brackets_data = parsed.get("brackets", [])
        except Exception:
            brackets_data = []

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "brackets": brackets_data,
        }
    )


@bp.route("/tournaments/<tournament_url>/bracket-setup", methods=["POST"])
@login_required
@require_json_body()
def tournament_bracket_setup_save_api(tournament_url):
    """Save bracket configuration from the SPA."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    data = g.json_body
    brackets = data.get("brackets", [])

    def escape_toml_string(s):
        """Escape special characters in TOML strings."""
        s = str(s)
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "\\n")
        s = s.replace("\t", "\\t")
        return s

    toml_lines = []
    for bracket in brackets:
        name = (bracket.get("name") or "").strip()
        image = (bracket.get("image") or "").strip()
        if not name or not image:
            continue

        toml_lines.append("[[brackets]]")
        toml_lines.append(f'name = "{escape_toml_string(name)}"')
        toml_lines.append(f'image = "{escape_toml_string(image)}"')
        toml_lines.append("")

        teams = bracket.get("teams") or []
        for team in teams:
            team_ref = (team.get("team") or "").strip()
            if not team_ref:
                continue
            try:
                x = int(team.get("x", 0) or 0)
                y = int(team.get("y", 0) or 0)
                halign = (team.get("halign") or "center").strip() or "center"
                valign = (team.get("valign") or "center").strip() or "center"
                size = int(team.get("size", 20) or 20)
            except (ValueError, TypeError):
                continue

            toml_lines.append("[[brackets.teams]]")
            toml_lines.append(f'team = "{escape_toml_string(team_ref)}"')
            toml_lines.append(f"x = {x}")
            toml_lines.append(f"y = {y}")
            toml_lines.append(f'halign = "{escape_toml_string(halign)}"')
            toml_lines.append(f'valign = "{escape_toml_string(valign)}"')
            toml_lines.append(f"size = {size}")
            toml_lines.append("")

    tournament.bracket = "\n".join(toml_lines)
    db.session.commit()

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/bracket-upload-bytes", methods=["POST"])
@login_required
def tournament_bracket_upload_bytes_api(tournament_url):
    """Upload a single bracket image from the SPA using raw bytes.

    The client sends the file contents as the request body and passes
    `filename` and `bracket_index` as query parameters.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    Tournament.query.filter_by(url=tournament_url).first_or_404()
    db.session.remove()

    original_name = request.args.get("filename", "bracket.png")
    bracket_index = request.args.get("bracket_index", "0")

    _, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".png"

    safe_index = "".join(ch for ch in bracket_index if ch.isdigit()) or "0"

    upload_dir = os.path.join(current_app.root_path, "../static", "uploads", "brackets")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"bracket_{tournament_url}_{safe_index}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}{ext}"
    file_path = os.path.join(upload_dir, filename)

    try:
        data = request.get_data() or b""
        # Canvas image elements are capped at 10 MB.
        max_bytes = 10 * 1024 * 1024
        if len(data) > max_bytes:
            return jsonify({"error": "Image must be under 10 MB"}), 400
        if not data:
            return jsonify({"error": "Empty image upload"}), 400
        with open(file_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return jsonify({"error": f"Error saving image: {e}"}), 500

    rel_path = f"uploads/brackets/{filename}"
    return jsonify({"success": True, "path": rel_path})
