"""
Public waiver-serving routes.

These routes are intentionally outside of /_api so the frontend can link to a stable URL:
  - Tournament: /<tournament_url>/waiver
  - League (canonical): /leagues/<league_url>/waiver
  - League (alias): /<league_url>/waiver redirects to the canonical route
"""

from __future__ import annotations

import os

from flask import Blueprint, current_app, redirect, send_file, url_for
from flask_login import current_user

from app.services.permission_service import PermissionService

from models import League, Tournament

bp = Blueprint("waivers", __name__)


@bp.after_request
def private_waiver_response(response):
    # Publication and organizer access can change between requests, including
    # redirects and missing/private responses. None may be stored or shared.
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _can_view_league(league) -> bool:
    return bool(league and (league.published or PermissionService.is_league_organizer(league.url, current_user)))


def _waiver_disk_path(scope_url: str) -> str:
    """Resolve the absolute path to the waiver file for *scope_url*.

    Searches ``static/uploads/waivers/<scope_url>/`` for a file named
    ``waiver`` (no extension) or ``waiver.<ext>`` (any extension).  Extension
    files are preferred over the legacy extensionless variant so that
    browsers receive a correct ``Content-Type`` header.

    Args:
        scope_url: URL slug of the tournament or league.

    Returns:
        Absolute filesystem path to the best matching waiver file.  If the
        directory does not exist or contains no candidates, returns a
        sentinel path (which will not pass :func:`os.path.isfile`).
    """
    # Prefer any extension variant (waiver.<ext>) and fall back to legacy extensionless file.
    base_dir = os.path.join(
        current_app.static_folder,
        "uploads",
        "waivers",
        scope_url,
    )
    if not os.path.isdir(base_dir):
        return os.path.join(base_dir, "waiver")

    # Find deterministic candidate waiver files.
    candidates = []
    for name in os.listdir(base_dir):
        if name == "waiver" or name.startswith("waiver."):
            path = os.path.join(base_dir, name)
            if os.path.isfile(path):
                candidates.append(name)
    if not candidates:
        return os.path.join(base_dir, "waiver")

    # Prefer extension files first (better content-type), then extensionless.
    candidates.sort(key=lambda n: (0 if n.startswith("waiver.") else 1, n))
    return os.path.join(base_dir, candidates[0])


@bp.route("/leagues/<league_url>/waiver", methods=["GET"])
def league_waiver(league_url: str):
    """Serve the waiver document for a league.

    ``GET /leagues/<league_url>/waiver``

    Returns:
        The waiver file as an inline attachment (content-type determined by
        file extension), or a 404 JSON error when the league or waiver file
        is not found.
    """
    league = League.query.filter_by(url=league_url).first()
    if not _can_view_league(league):
        return {"error": "Not found"}, 404

    file_path = _waiver_disk_path(league_url)
    if not os.path.isfile(file_path):
        return {"error": "Waiver not found"}, 404

    return send_file(file_path, as_attachment=False, conditional=False)


@bp.route("/<event_url>/waiver", methods=["GET"])
def event_waiver_alias(event_url: str):
    """Serve or redirect to the waiver document for a tournament or league alias.

    ``GET /<event_url>/waiver``

    * If *event_url* is a league URL slug, redirects (302) to the canonical
      ``/leagues/<event_url>/waiver`` route.
    * If the tournament belongs to a league, redirects to the league waiver.
    * Otherwise serves the tournament's own waiver file inline.

    Returns:
        A 302 redirect, an inline file response, or a 404 JSON error when
        the event or waiver file cannot be found.
    """
    # If the slug matches a league, redirect to the canonical league URL.
    league = League.query.filter_by(url=event_url).first()
    if league:
        if not _can_view_league(league):
            return {"error": "Not found"}, 404
        return redirect(url_for("waivers.league_waiver", league_url=event_url), code=302)

    tournament = Tournament.query.filter_by(url=event_url).first()
    if not tournament or not PermissionService.can_view_tournament(event_url, current_user):
        return {"error": "Not found"}, 404

    # If this tournament belongs to a league, redirect to the league's canonical waiver.
    if getattr(tournament, "league_id", None):
        if not _can_view_league(League.query.filter_by(url=tournament.league_id).first()):
            return {"error": "Not found"}, 404
        return redirect(url_for("waivers.league_waiver", league_url=tournament.league_id), code=302)

    file_path = _waiver_disk_path(event_url)
    if not os.path.isfile(file_path):
        return {"error": "Waiver not found"}, 404

    return send_file(file_path, as_attachment=False, conditional=False)
