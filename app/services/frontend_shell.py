"""Serve the compiled application with an HTML startup state.

The build manifest selects the module entry. Public reading templates use that
entry to start the runtime after useful HTML paints.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re

from flask import Flask, Response, abort, current_app, make_response, render_template, request, send_file, url_for


def public_asset(name: str) -> str:
    versions = current_app.extensions["public_asset_versions"]
    if name not in versions:
        raise ValueError("Unknown public presentation asset")
    return url_for("static", filename=f"public/{name}", v=versions[name])


def render_anonymous_template(template: str, **context) -> str:
    """Render recovery HTML without retrying a failed account lookup.

    Flask-Login's template context processor loads the signed-in account. During
    a database failure even an explicitly anonymous render_template call would
    retry that lookup. Recovery templates receive only the public values here;
    normal responses retain Flask's usual template processing.
    """
    return current_app.jinja_env.get_template(template).render(**context)


def init_frontend_shell(app: Flask) -> None:
    app.config.setdefault(
        "ARCTOS_FRONTEND_DIST",
        os.environ.get("ARCTOS_FRONTEND_DIST", str(Path(app.root_path).parent / "frontend/dist/public")),
    )
    directory = Path(app.static_folder) / "public"
    app.extensions["public_asset_versions"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".css", ".js", ".png"}
    }
    # Compress fixed presentation files once per worker, never per request. The
    # app's original CSS can paint immediately without waiting for its Wasm.
    app.extensions["public_asset_gzip"] = {
        name: gzip.compress((directory / name).read_bytes(), compresslevel=9, mtime=0)
        for name in app.extensions["public_asset_versions"]
        if Path(name).suffix in {".css", ".js"}
    }
    app.jinja_env.globals["public_asset"] = public_asset
    app.add_url_rule("/assets/<path:filename>", "frontend_asset", _asset)
    app.add_url_rule("/static/public/<filename>", "public_presentation_asset", _presentation_asset)


def _presentation_asset(filename: str):
    versions = current_app.extensions["public_asset_versions"]
    if filename not in versions:
        abort(404)
    compressed = current_app.extensions["public_asset_gzip"].get(filename)
    use_gzip = compressed is not None and request.accept_encodings["gzip"] > 0
    if use_gzip:
        response = make_response(compressed)
        response.mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response.headers["Content-Encoding"] = "gzip"
        response.set_etag(versions[filename] + "-gzip")
        response.make_conditional(request)
    else:
        response = send_file(Path(current_app.static_folder) / "public" / filename, conditional=True)
    response.vary.add("Accept-Encoding")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if request.args.get("v") == versions[filename] else "no-cache"
    )
    _protect_session_asset(response)
    return response


def _protect_session_asset(response):
    if any(
        name in request.cookies
        for name in (
            current_app.config["SESSION_COOKIE_NAME"],
            current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token"),
        )
    ):
        # Flask-Login may refresh cookies. The Flask fallback must not allow a
        # shared cache to replay those headers. A static proxy has no sessions.
        response.cache_control.public = False
        response.cache_control.private = True


def _asset(filename: str):
    """Local/staging asset serving; production can use the documented nginx rule."""
    root = (Path(current_app.config["ARCTOS_FRONTEND_DIST"]) / "assets").resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix == ".gz":
        abort(404)
    mime = "application/wasm" if path.suffix == ".wasm" else mimetypes.guess_type(path.name)[0]
    compressed = path.with_name(path.name + ".gz").resolve()
    use_gzip = request.accept_encodings["gzip"] > 0 and compressed.is_relative_to(root) and compressed.is_file()
    response = send_file(
        compressed if use_gzip else path, mimetype=mime or "application/octet-stream", conditional=True
    )
    if use_gzip:
        response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if re.search(r"-dxh[0-9a-f]+\.", path.name) else "no-cache"
    )
    _protect_session_asset(response)
    return response


def _entry() -> str | None:
    root = Path(current_app.config["ARCTOS_FRONTEND_DIST"]).resolve()
    try:
        manifest = json.loads((root / "arctos-assets.json").read_text())
        entry = manifest["entry"]
        if manifest.get("base_path", "") != request.script_root:
            return None
        if manifest["version"] != 1 or not isinstance(entry, str) or not re.fullmatch(r"/assets/[\w./-]+\.js", entry):
            return None
        path = (root / entry.lstrip("/")).resolve()
        if not path.is_relative_to(root / "assets") or not path.is_file():
            return None
        return request.script_root + entry
    except (OSError, ValueError, KeyError, TypeError):
        return None


def render_application_shell(context: dict | None = None) -> Response:
    from app.routes.public_pages import _common_context
    from sqlalchemy.exc import SQLAlchemyError

    supplied = context or {}
    entry = _entry()
    # OBS and offline rendering contracts must not acquire public-page chrome.
    bare = bool(
        re.fullmatch(r"/(?:scoreboard-render|[^/]+/scoreboard|[^/]+/sidecomps/[^/]+/enter-results)", request.path)
    )
    render = render_template
    try:
        chrome = _common_context()
    except SQLAlchemyError:
        chrome = _common_context(anonymous=True)
        render = render_anonymous_template
    values = {
        **chrome,
        "title": "Arctos",
        "event_name": "",
        "date_label": "",
        "return_url": request.script_root + "/",
        "return_label": "Back to events",
        "workflow_label": "Arctos",
        "noindex": True,
        **supplied,
        "entry": entry,
        "bare": bare,
        "public_enabled": bool(current_app.config.get("PUBLIC_HTML_ENABLED", False)),
    }
    if values["workflow_label"] in {"application", "Arctos"}:
        values["workflow_label"] = {
            "": "Events",
            "login": "Log in",
            "register": "Register",
            "teams": "Teams",
            "players": "Players",
            "leagues": "Leagues",
            "stones": "Stones",
            "about": "About Arctos",
            "create-event": "Create event",
            "new-tournament": "Create event",
            "docs.md": "Documentation",
            "privacy-policy.md": "Privacy policy",
        }.get(request.path.strip("/").split("/", 1)[0], "Arctos")
    else:
        values["workflow_label"] = values["workflow_label"].capitalize()
    response = make_response(render("public/app.html", **values), 200 if entry else 503)
    response.headers["Cache-Control"] = "private, no-store"
    if values["noindex"] or not entry:
        response.headers["X-Robots-Tag"] = "noindex"
    if not entry:
        response.headers["Retry-After"] = "30"
    return response
