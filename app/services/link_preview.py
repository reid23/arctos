"""Absolute Open Graph images for documents crawlers fetch.

The image is the Arctos logo unless the route names an event, league, team,
or player that has its own picture. Stored upload paths stay on this site;
a remote or traversable value never becomes the preview.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import current_app, request
from sqlalchemy.exc import SQLAlchemyError


def absolute_preview_url(path: str | None) -> str | None:
    """Prefix a same-site path with the configured public origin.

    An empty origin omits the image. The request host is not a substitute,
    matching canonical links.
    """
    origin = (current_app.config.get("PUBLIC_CANONICAL_ORIGIN") or "").rstrip("/")
    if not origin or not path or path.startswith(("http://", "https://", "//")):
        return None
    if not path.startswith("/"):
        path = "/" + path
    return origin + path


def logo_preview_url() -> str | None:
    from app.services.frontend_shell import public_asset

    return absolute_preview_url(public_asset("arctos-logo.png"))


def stored_preview_url(stored: str | None) -> str | None:
    """Turn a static-relative upload path into an absolute preview URL."""
    if not stored:
        return None
    relative = stored.removeprefix("/static/").removeprefix("static/")
    if not relative or relative.startswith("/") or ":" in relative or "\\" in relative or ".." in relative.split("/"):
        return None
    prefix = request.script_root.rstrip("/")
    return absolute_preview_url(f"{prefix}/static/{quote(relative, safe='/')}")


def account_preview(kind: str, identifier: str) -> tuple[str | None, str | None]:
    """Profile image and display name for a player or team id.

    A missing account returns ``(None, None)``. An account with no photo
    returns ``(None, name)``.
    """
    from models import Player, Team, db

    model = Player if kind == "players" else Team
    try:
        account = db.session.get(model, identifier)
    except SQLAlchemyError:
        current_app.logger.exception("Link preview account lookup failed")
        return None, None
    if account is None:
        return None, None
    return stored_preview_url(account.profile_photo), account.name


def holder_preview(kind: str, slug: str) -> tuple[str | None, str | None]:
    """Banner image and name for an event or league slug."""
    from models import League, Tournament, db

    model = Tournament if kind == "event" else League
    try:
        holder = db.session.get(model, slug)
    except SQLAlchemyError:
        current_app.logger.exception("Link preview holder lookup failed")
        return None, None
    if holder is None:
        return None, None
    return stored_preview_url(holder.banner_photo), holder.name
