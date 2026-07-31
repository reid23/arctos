"""Tournament bracket endpoints (setup, upload, display).

Part of the ``tournaments`` blueprint. Uses the same Blueprint object
defined in :mod:`app.routes.tournaments.__init__`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import current_app, g, jsonify, request
from flask_login import current_user, login_required

from app.domain.enums import MatchStatus
from app.serializers.tournament_serializer import tournament_to_dict
from app.services.permission_service import PermissionService
from app.utils.decorators import require_json_body
from app.services.registration_resolver import team_registration_for_tournament
from app.utils.helpers import check_tournament_access
from models import (
    Match,
    Tag,
    Team,
    Tournament,
    db,
)

from . import bp


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


def _team_info_payload(tournament, team_id: str) -> dict | None:
    """Build display dict for a confirmed team registration (event or league)."""
    if not team_id:
        return None
    team_reg = team_registration_for_tournament(tournament, team_id)
    if not team_reg:
        return None
    team = Team.query.get(team_id)
    return {
        "id": team_id,
        "pseudonym": team_reg.pseudonym,
        "shortname": team_reg.shortname,
        "profile_photo": team.profile_photo if team else None,
        "display_text": team_reg.pseudonym,
    }


@bp.route("/tournaments/<tournament_url>/bracket-setup-data", methods=["GET"])
@login_required
def tournament_bracket_setup_data_api(tournament_url):
    """Raw bracket configuration for the SPA bracket-setup page.

    This returns the underlying TOML data (already parsed) so that the
    Dioxus frontend can render and edit bracket annotations while the
    existing HTML form endpoint continues to handle multipart uploads.
    """
    # Only TOs may access bracket setup data
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
            # If parsing fails, just return an empty brackets list so the UI
            # can present a clean state rather than a hard error.
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

    # Derive a safe extension from the original filename
    _, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".png"

    # Normalize bracket index to digits only
    safe_index = "".join(ch for ch in bracket_index if ch.isdigit()) or "0"

    upload_dir = os.path.join(current_app.root_path, "../static", "uploads", "brackets")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"bracket_{tournament_url}_{safe_index}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}{ext}"
    file_path = os.path.join(upload_dir, filename)

    try:
        data = request.get_data() or b""
        with open(file_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return jsonify({"error": f"Error saving image: {e}"}), 500

    rel_path = f"uploads/brackets/{filename}"
    return jsonify({"success": True, "path": rel_path})


@bp.route("/tournaments/<tournament_url>/bracket", methods=["GET"])
def tournament_bracket_api(tournament_url):
    has_access, tournament = check_tournament_access(tournament_url)
    if not has_access or not tournament:
        return jsonify({"error": "Not found"}), 404

    is_to = False
    if current_user.is_authenticated:
        is_to = PermissionService.is_tournament_organizer(tournament_url, current_user)

    if not tournament.bracket:
        return jsonify({"error": "Bracket is not available"}), 404
    if not tournament.schedule_published and not is_to:
        return jsonify({"error": "Bracket is not available"}), 403

    try:
        import tomli

        bracket_data = tomli.loads(tournament.bracket)
    except Exception:
        return jsonify({"error": "Error parsing bracket data"}), 400

    processed_brackets = []
    brackets = bracket_data.get("brackets", [])
    for bracket in brackets:
        bracket_name = bracket.get("name", "")
        bracket_image = bracket.get("image", "")
        teams = bracket.get("teams", [])
        processed_teams = []
        for team_entry in teams:
            team_ref = team_entry.get("team", "")
            x = team_entry.get("x", 0)
            y = team_entry.get("y", 0)
            halign = team_entry.get("halign", "center")
            valign = team_entry.get("valign", "center")
            size = team_entry.get("size", 20)

            team_info = None
            is_reference = False
            is_tag = False
            match_name = None

            if team_ref.lower().startswith("tag::"):
                tag_name = team_ref[5:].strip()
                if tag_name:
                    tag = Tag.query.filter_by(event=tournament_url, name=tag_name).first()
                    if tag and tag.team:
                        team_info = _team_info_payload(tournament, tag.team)
                        if not team_info:
                            team_info = {"display_text": f"tag::{tag_name}"}
                            is_tag = True
                    elif tag:
                        team_info = {"display_text": f"tag::{tag_name}"}
                        is_tag = True
            elif "::" in team_ref:
                parts = team_ref.split("::", 1)
                match_name = parts[0].strip()
                ref_type = parts[1].strip() if len(parts) > 1 else ""
                match = Match.query.filter_by(event=tournament_url, name=match_name).first()
                if match and match.status == MatchStatus.COMPLETED and match.match_winner:
                    if ref_type == "winner":
                        team_id = match.team1 if match.match_winner == "TEAM1" else match.team2
                    elif ref_type == "loser":
                        team_id = match.team2 if match.match_winner == "TEAM1" else match.team1
                    else:
                        team_id = None
                    if team_id:
                        team_info = _team_info_payload(tournament, team_id)
                        if team_info:
                            is_reference = True
                elif match:
                    team_info = {"display_text": team_ref.replace("::", " ")}
                    is_reference = True
                else:
                    team_info = {"display_text": team_ref.replace("::", " ")}
                    is_reference = True
            elif team_ref:
                team_info = _team_info_payload(tournament, team_ref)
                if not team_info:
                    tag = Tag.query.filter_by(event=tournament_url, name=team_ref).first()
                    if tag and tag.team:
                        team_info = _team_info_payload(tournament, tag.team)
                        if not team_info:
                            team_info = {"display_text": f"tag::{tag.name}"}
                            is_tag = True
                    elif tag:
                        team_info = {"display_text": f"tag::{tag.name}"}
                        is_tag = True

            processed_teams.append(
                {
                    "team_info": team_info,
                    "x": x,
                    "y": y,
                    "halign": halign,
                    "valign": valign,
                    "size": size,
                    "is_reference": is_reference,
                    "is_tag": is_tag,
                    "match_name": match_name if is_reference else None,
                }
            )

        processed_brackets.append({"name": bracket_name, "image": bracket_image, "teams": processed_teams})

    return jsonify({"tournament": tournament_to_dict(tournament), "brackets": processed_brackets})
