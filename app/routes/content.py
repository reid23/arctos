"""Reference-data and content routes.

Hosts the ``content`` blueprint - small, mostly stateless endpoints
that don't fit a more specific topical blueprint:

- ``/server-time`` - server clock for client time sync.
- ``/stones`` - list of stone audio files for the in-app stones player.
- ``/markdown/<slug>`` - render a fixed set of markdown docs (privacy
  policy, terms, etc.) to HTML.
- ``/render-markdown`` - render arbitrary markdown content to HTML.
- ``/<tournament_url>/upload-waiver`` - TO-only waiver PDF upload for a
  tournament.
- ``/leagues/<league_url>/upload-waiver`` - league-organiser-only
  waiver PDF upload for a league.

The waiver-upload endpoints live here (rather than in ``waivers.py``)
because the existing ``waivers`` blueprint is currently unregistered
and is mounted without the ``/_api`` prefix. Moving the upload routes
into it would change their URLs; co-locating them with the existing
content routes keeps the URL surface stable.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.filters import render_markdown
from app.utils.decorators import check_tournament_organizer
from app.serializers.league_serializer import require_league
from app.services.permission_service import PermissionService
from app.utils.helpers import get_registrable_config
from app.utils.settings_save import SettingsSaveError, persist_settings, read_waiver
from models import Tournament

bp = Blueprint("content", __name__, url_prefix="/_api")


@bp.route("/server-time", methods=["GET"])
def server_time():
    """Return current server time in unix timestamp format."""
    return jsonify(
        {
            "server_time": time.time(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.route("/stones", methods=["GET"])
def stones_list():
    """List stone audio files (for stones player)."""
    static_folder = current_app.static_folder
    stones_dir = os.path.join(static_folder, "stones")
    ALLOWED_USERS = os.environ.get("SILLY_USERS", "").split(":")
    mp3_files = []
    if os.path.exists(stones_dir) and os.path.isdir(stones_dir):
        for filename in os.listdir(stones_dir):
            if filename.lower().endswith(".mp3"):
                name_without_ext = os.path.splitext(filename)[0]
                display_name = re.sub(r"^\d+_", "", name_without_ext)
                match = re.match(r"^(\d+)_", name_without_ext)
                sort_order = int(match.group(1)) if match else 999999
                filename_encoded = quote(filename, safe="")
                mp3_files.append(
                    {
                        "filename": filename,
                        "filename_encoded": filename_encoded,
                        "display_name": display_name,
                        "sort_order": sort_order,
                    }
                )
        mp3_files.sort(key=lambda x: (x["sort_order"], x["filename"]))
    user_can_see_all = current_user.is_authenticated and current_user.id in ALLOWED_USERS
    if not user_can_see_all:
        mp3_files = [f for f in mp3_files if f["display_name"].lower() in ["classic", "snare"]]
    return jsonify({"stones": mp3_files})


@bp.route("/markdown/<slug>", methods=["GET"])
def markdown_page(slug):
    """Return markdown page content by slug, rendered to HTML with the markdown filter."""
    mapping = {
        "docs": ("docs.md", "User Docs"),
        "privacy-policy": ("privacy-policy.md", "Privacy Policy"),
        "data-accessibility-guide": (
            "data-accessibility-guide.md",
            "Data Accessibility Guide",
        ),
        "arctos-schedule-script": (
            "arctos-schedule-script.md",
            "Arctos Schedule Script",
        ),
        "license": ("license.md", "License"),
        "terms": ("terms.md", "Terms and Conditions"),
        "developers": ("developers.md", "For Developers"),
    }
    if slug not in mapping:
        return jsonify({"error": "Not found"}), 404
    filename, title = mapping[slug]
    path = Path(__file__).parent.parent.parent / "docs" / filename
    if not path.exists():
        return jsonify({"error": "Not found"}), 404
    content = path.read_text(encoding="utf-8")
    html = str(render_markdown(content))
    return jsonify({"title": title, "html": html})


# CSS for .markdown-content (matches python-markdown output: headings, lists, code, tables, etc.)
MARKDOWN_CONTENT_CSS = """
.markdown-content { line-height: 1.6; }
.markdown-content h1, .markdown-content h2, .markdown-content h3,
.markdown-content h4, .markdown-content h5, .markdown-content h6 {
    margin-top: 1em; margin-bottom: 0.5em; font-weight: 600;
}
.markdown-content h1 { font-size: 1.5em; }
.markdown-content h2 { font-size: 1.3em; }
.markdown-content h3 { font-size: 1.15em; }
.markdown-content p { margin-bottom: 0.75em; }
.markdown-content ul, .markdown-content ol { margin-bottom: 0.75em; padding-left: 1.5em; }
.markdown-content li { margin-bottom: 0.25em; }
.markdown-content blockquote {
    border-left: 4px solid var(--bs-secondary, #6c757d);
    padding-left: 1em; margin: 0.75em 0; color: var(--bs-secondary);
}
.markdown-content code { padding: 0.2em 0.4em; font-size: 0.9em; background: rgba(0,0,0,0.06); border-radius: 4px; }
.markdown-content pre { padding: 0.75em; overflow-x: auto; background: rgba(0,0,0,0.06); border-radius: 4px; margin-bottom: 0.75em; }
.markdown-content pre code { padding: 0; background: none; }
.markdown-content table { border-collapse: collapse; margin-bottom: 0.75em; width: 100%; }
.markdown-content th, .markdown-content td { border: 1px solid var(--bs-border-color, #dee2e6); padding: 0.4em 0.6em; text-align: left; }
.markdown-content th { font-weight: 600; background: rgba(0,0,0,0.04); }
.markdown-content a { color: var(--bs-link-color, #0d6efd); text-decoration: none; }
.markdown-content a:hover { text-decoration: underline; }
.markdown-content img { max-width: 100%; height: auto; }
.markdown-content hr { margin: 1em 0; border: 0; border-top: 1px solid var(--bs-border-color, #dee2e6); }
.markdown-content .admonition { margin: 1em 0; padding: 0; border-radius: 6px; border: 1px solid; overflow: hidden; }
.markdown-content .admonition .admonition-title { margin: 0; padding: 0.5em 0.75em; font-weight: 600; }
.markdown-content .admonition p:not(.admonition-title) { padding: 0.5em 0.75em; margin-bottom: 0.5em; }
.markdown-content .admonition p:not(.admonition-title):last-child { margin-bottom: 0; }
.markdown-content .admonition.note { border-color: #0d6efd; background: rgba(13, 110, 253, 0.08); }
.markdown-content .admonition.note .admonition-title { background: rgba(13, 110, 253, 0.2); color: #0a58ca; }
.markdown-content .admonition.warning { border-color: #ffc107; background: rgba(255, 193, 7, 0.12); }
.markdown-content .admonition.warning .admonition-title { background: rgba(255, 193, 7, 0.25); color: #856404; }
.markdown-content .admonition.attention { border-color: #ffc107; background: rgba(255, 193, 7, 0.12); }
.markdown-content .admonition.attention .admonition-title { background: rgba(255, 193, 7, 0.25); color: #856404; }
.markdown-content .admonition.caution { border-color: #fd7e14; background: rgba(253, 126, 20, 0.1); }
.markdown-content .admonition.caution .admonition-title { background: rgba(253, 126, 20, 0.2); color: #b35a0e; }
.markdown-content .admonition.danger { border-color: #dc3545; background: rgba(220, 53, 69, 0.08); }
.markdown-content .admonition.danger .admonition-title { background: rgba(220, 53, 69, 0.2); color: #b02a37; }
.markdown-content .admonition.important { border-color: #fd7e14; background: rgba(253, 126, 20, 0.1); }
.markdown-content .admonition.important .admonition-title { background: rgba(253, 126, 20, 0.2); color: #b35a0e; }
.markdown-content .admonition.tip { border-color: #198754; background: rgba(25, 135, 84, 0.08); }
.markdown-content .admonition.tip .admonition-title { background: rgba(25, 135, 84, 0.2); color: #146c43; }
.markdown-content .admonition.hint { border-color: #198754; background: rgba(25, 135, 84, 0.08); }
.markdown-content .admonition.hint .admonition-title { background: rgba(25, 135, 84, 0.2); color: #146c43; }
"""


@bp.route("/render-markdown", methods=["POST"])
def render_markdown_api():
    """Render markdown to HTML using the same filter as templates (python-markdown + sanitization)."""
    data = request.get_json()
    if not data or "markdown" not in data:
        return jsonify({"error": "JSON body must include 'markdown'"}), 400
    text = data.get("markdown")
    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)
    html = str(render_markdown(text))
    return jsonify({"html": html, "css": MARKDOWN_CONTENT_CSS})


@bp.route("/<tournament_url>/upload-waiver", methods=["POST"])
@login_required
def tournament_upload_waiver(tournament_url):
    """Store waiver PDF for this event's registrable config (TO only)."""
    if not check_tournament_organizer(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    cfg = get_registrable_config(tournament)
    if not cfg:
        return jsonify({"error": "Registration is not configured for this event"}), 400

    try:
        upload = read_waiver(required=True)
        persist_settings(tournament, {}, cfg, {}, upload, "tournament", tournament_url)
    except SettingsSaveError as error:
        return jsonify({"error": str(error)}), error.status
    return jsonify({"success": True, "waiver_filepath": cfg.waiver_filepath, "waiver_sha256": cfg.waiver_sha256})


@bp.route("/leagues/<league_url>/upload-waiver", methods=["POST"])
@login_required
def league_upload_waiver_api(league_url):
    """Store waiver PDF on the league registrable config (league TO only)."""
    league, err = require_league(league_url)
    if err:
        return jsonify({"error": "Not found" if err == 404 else "Forbidden"}), err
    if not PermissionService.is_league_organizer(league_url, current_user):
        return jsonify({"error": "Only league organizers can upload waivers"}), 403

    rc = league.registrable_config
    if not rc:
        return jsonify({"error": "Registration is not configured"}), 400

    try:
        upload = read_waiver(required=True)
        persist_settings(league, {}, rc, {}, upload, "league", league_url)
    except SettingsSaveError as error:
        return jsonify({"error": str(error)}), error.status
    return jsonify({"success": True, "waiver_filepath": rc.waiver_filepath, "waiver_sha256": rc.waiver_sha256})
