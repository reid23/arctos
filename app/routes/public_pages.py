"""Document routes for public reading and explicit interactive application entry.

Flask owns the initial status, publication decision and data selection. Dioxus
owns the routes classified as application workflows. Unknown API/static paths
never fall through to an HTML application shell.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import re
from urllib.parse import quote, unquote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.error_values import Err, Ok
from app.services.public_league_service import PublicLeagueService, league_directory_url
from app.services.public_page_service import Link, PublicPageService, directory_url, local_url
from app.utils.user_helpers import is_team

bp = Blueprint("public_pages", __name__)

# Keep this list aligned with frontend/src/main.rs. An explicit list prevents
# misspelled or missing resources from looking like successful HTML documents.
_APP_TOP_LEVEL = frozenset(
    {
        "login",
        "register",
        "leagues",
        "create-event",
        "players",
        "teams",
        "stones",
        "scoreboard-render",
        "about",
        "new-tournament",
        "thanks",
        "docs.md",
        "privacy-policy.md",
        "terms.md",
        "license.md",
        "arctos-schedule-script.md",
        "data-accessibility-guide.md",
        "developers.md",
    }
)
_APP_NESTED = re.compile(
    r"^(?:register/(?:player|team)|auth/google/(?:choose-account-type|complete-profile)|"
    r"leagues/(?:new|[^/]+(?:/(?:register|results|settings|new-tournament|manage|invitations))?)|"
    r"players/[^/]+(?:/(?:edit|injuries/new|injuries/[0-9]+/edit))?|"
    r"teams/[^/]+(?:/edit)?|developers/apps|oauth/authorize)$"
)
_EVENT_WORKFLOW = re.compile(
    r"^(?P<slug>[^/]+)/(?P<workflow>schedule(?:/edit)?|results|bracket|legacy-bracket|settings|register|"
    r"manage|manage-footage|invitations|(?:start-match|run-match|finalize-match)/[^/]+|scoreboard|"
    r"match(?:/[^/]+)?|sidecomps/(?:new|[0-9]+(?:/(?:edit|register-player-as-to|enter-results|results|"
    r"manage-results))?))$"
)
_RESERVED_PREFIXES = frozenset({"_api", "api", "static", "assets", "auth", "oauth", "developers"})


def _common_context(*, anonymous: bool = False) -> dict:
    prefix = request.script_root
    authenticated = not anonymous and bool(current_user.is_authenticated)
    path = request.path.strip("/")
    first = path.split("/", 1)[0]
    active_section = {"leagues": "Leagues", "teams": "Teams", "players": "Players", "stones": "Stones"}.get(first)
    if active_section is None and (
        not path or first in {"create-event", "new-tournament"} or first not in _APP_TOP_LEVEL | _RESERVED_PREFIXES
    ):
        active_section = "Events"
    return {
        "authenticated": authenticated,
        "account_label": getattr(current_user, "name", "Account") if authenticated else "Log in",
        "home_url": local_url("", prefix),
        "active_section": active_section,
        "directory_page": path in {"", "leagues", "teams", "players", "stones"},
        "public_banner_key": "events" if not path else "leagues" if path == "leagues" else None,
        "global_nav": tuple(
            Link(label, local_url(path, prefix))
            for label, path in (
                ("Events", ""),
                ("Leagues", "leagues"),
                ("Teams", "teams"),
                ("Players", "players"),
                ("Stones", "stones"),
            )
        ),
        "login_url": local_url("login", prefix) + "?" + urlencode({"next": prefix + request.full_path.rstrip("?")}),
        "account_url": local_url(
            ("teams/" if is_team(current_user) else "players/") + str(current_user.id),
            prefix,
        )
        if authenticated
        else local_url("login", prefix),
        "create_event_url": local_url("create-event", prefix),
    }


def _document(template: str, *, status: int = 200, **context):
    from app.services.frontend_shell import _entry, render_anonymous_template

    render = render_anonymous_template if status == 503 else render_template
    response = make_response(
        render(
            template,
            status=status,
            background_entry=_entry() if status == 200 else None,
            **_common_context(anonymous=status == 503),
            **context,
        ),
        status,
    )
    return _private_response(response, noindex=context.get("noindex", False))


def _private_response(response, *, noindex: bool = False):
    # Documents vary by timezone/session cookies, and publication can change
    # between requests. Evaluate each document against current permissions.
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    if response.status_code == 503:
        response.headers["Retry-After"] = "30"
    if noindex:
        response.headers["X-Robots-Tag"] = "noindex"
    return response


def _error(status: int = 404):
    section = "Leagues" if request.path.strip("/").split("/", 1)[0] == "leagues" else "Events"
    title = "Page not found" if status == 404 else f"{section} are temporarily unavailable"
    message = (
        "This page may have moved or may not be public. You can return to the events directory."
        if status == 404
        else "Please try again shortly. Your registration has not been changed."
    )
    return _document(
        "public/error.html",
        status=status,
        title=title,
        message=message,
        description=message,
        canonical=None,
        noindex=True,
    )


def _today():
    """Use a validated viewer preference only to choose the calendar boundary.

    A no-JavaScript first visit uses PUBLIC_CALENDAR_TIMEZONE (UTC by default).
    Dates printed on cards retain their entered calendar days in every zone.
    """
    name = unquote(request.cookies.get("arctos_timezone", "")[:100])
    fallback = current_app.config["PUBLIC_CALENDAR_TIMEZONE"]
    try:
        zone = ZoneInfo(name) if name else ZoneInfo(fallback)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(fallback)
    return datetime.now(zone).date()


def _canonical(path: str) -> str | None:
    # Never trust a request Host header as the site's canonical identity.
    origin = current_app.config.get("PUBLIC_CANONICAL_ORIGIN")
    return origin + request.script_root + path if origin else None


# Public reading routes rendered by Dioxus are indexable when their resources
# are published. API permissions govern their content; privileged panels and
# organizer previews receive noindex.
_PUBLIC_APP_ROOTS = frozenset(
    {
        "",
        "players",
        "teams",
        "leagues",
        "about",
        "thanks",
        "docs.md",
        "privacy-policy.md",
        "terms.md",
        "license.md",
        "arctos-schedule-script.md",
        "data-accessibility-guide.md",
        "developers.md",
    }
)


def _public_app_indexable(page=None, *, league: bool = False) -> bool:
    if request.args.get("app") == "1":
        return False
    path = request.path.strip("/")
    tab = request.args.get("tab", "")
    if page is not None:
        parts = path.split("/")
        if league:
            parts = parts[1:]
        section = (tab or "info") if len(parts) == 1 else parts[1] if len(parts) == 2 else ""
        return section in page.indexable_app_sections
    if path in _PUBLIC_APP_ROOTS and not tab:
        return True
    parts = path.split("/")
    try:
        from models import League, Player, Team, Tournament

        if len(parts) == 2 and parts[0] in {"players", "teams"} and tab in {"", "info", "history"}:
            model = Player if parts[0] == "players" else Team
            return model.query.with_entities(model.id).filter(model.id == parts[1]).first() is not None
        if (
            len(parts) in {2, 3}
            and parts[0] == "leagues"
            and parts[1] != "new"
            and (len(parts) == 2 or parts[2] == "results")
            and tab in {"", "info", "events", "teams"}
        ):
            return League.query.with_entities(League.url).filter_by(url=parts[1], published=True).first() is not None
        # The rollout flag can leave event reading in the app. Preserve its
        # existing indexing only after proving public publication, without
        # loading the full overview or any account data for metadata alone.
        if len(parts) <= 2 and parts[0] not in _APP_TOP_LEVEL:
            section = (tab or "info") if len(parts) == 1 else parts[1]
            if section not in {"info", "teams", "sidecomps", "results", "schedule", "bracket"}:
                return False
            row = (
                Tournament.query.with_entities(Tournament.schedule_published, Tournament.bracket_published)
                .filter_by(
                    url=parts[0],
                    published=True,
                )
                .first()
            )
            return bool(
                row is not None
                and (section != "schedule" or row.schedule_published)
                and (section != "bracket" or row.bracket_published)
            )
    except SQLAlchemyError:
        # Metadata classification must not turn a recoverable app entry into a
        # server error, or assume visibility during a database failure.
        current_app.logger.exception("Application indexing visibility check failed")
    return False


def _application(page=None, workflow: str = "application", *, league: bool = False):
    from app.services.frontend_shell import render_application_shell

    noindex = not _public_app_indexable(page, league=league)
    context = {
        "noindex": noindex,
        "title": "Arctos",
        "workflow_label": workflow,
        "return_url": local_url("", request.script_root),
        "return_label": "Back to events",
    }
    if page is not None:
        context.update(
            title=page.card.name,
            event_name=page.card.name,
            date_label=page.card.date_label,
            return_url=page.card.url,
            return_label="Back to league" if league else "Back to event",
        )
    return _private_response(render_application_shell(context), noindex=noindex)


def _event(slug: str, *, application: bool = False, workflow: str = "event tools"):
    match PublicPageService.event(slug, user=current_user, prefix=request.script_root):
        case Ok(page):
            if application or not page.card.published:
                return _application(page, workflow)
            return _document(
                "public/event.html",
                page=page,
                title=page.card.name,
                description=f"{page.card.name}. {page.card.date_label}. {page.card.location}.",
                canonical=_canonical("/" + quote(slug, safe="")),
                noindex=not page.card.published,
            )
        case Err(_):
            return _error()


def _league(slug: str, *, application: bool = False, workflow: str = "league tools"):
    match PublicLeagueService.league(slug, user=current_user, prefix=request.script_root):
        case Ok(page):
            if application or not page.card.published:
                return _application(page, workflow, league=True)
            return _document(
                "public/league.html",
                page=page,
                title=page.card.name,
                description=f"{page.card.name}. Shared registration, league information and events on Arctos.",
                canonical=_canonical("/leagues/" + quote(slug, safe="")),
                noindex=False,
            )
        case Err(_):
            return _error()


@bp.route("/", methods=["GET"])
def index():
    if not current_app.config["PUBLIC_HTML_ENABLED"] or request.args.get("app") == "1":
        return _application(workflow="events")
    try:
        page = _directory_page()
        path = directory_url(page.query, page.view, page.page)
        return _document(
            "public/index.html",
            page=page,
            title="Events",
            description="Find jugger events, registration details, schedules and results on Arctos.",
            canonical=_canonical(path),
            noindex=bool(page.query) or page.page > 1,
        )
    except SQLAlchemyError:
        current_app.logger.exception("Public events document failed")
        return _error(503)


def _directory_page():
    """The HTML and interactive directory use the same projection and rules."""
    raw_page = request.args.get("page", "1")
    page_number = int(raw_page) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 9 else 1
    return PublicPageService.directory(
        query=request.args.get("q", ""),
        view=request.args.get("view", "upcoming"),
        page=page_number,
        page_size=current_app.config["PUBLIC_HTML_PAGE_SIZE"],
        user=current_user,
        today=_today(),
        prefix=request.script_root,
    )


@bp.get("/leagues")
@bp.get("/leagues/")
def leagues():
    if not current_app.config["PUBLIC_HTML_ENABLED"] or request.args.get("app") == "1":
        return _application(workflow="leagues")
    try:
        raw_page = request.args.get("page", "1")
        page_number = int(raw_page) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 9 else 1
        page = PublicLeagueService.directory(
            query=request.args.get("q", ""),
            view=request.args.get("view", "active"),
            page=page_number,
            page_size=current_app.config["PUBLIC_HTML_PAGE_SIZE"],
            user=current_user,
            today=_today(),
            prefix=request.script_root,
        )
        return _document(
            "public/leagues.html",
            page=page,
            title="Leagues",
            description="Find jugger leagues, shared registration and league information on Arctos.",
            canonical=_canonical(league_directory_url(page.query, page.view, page.page)),
            noindex=bool(page.query) or page.page > 1,
        )
    except SQLAlchemyError:
        current_app.logger.exception("Public leagues document failed")
        return _error(503)


@bp.get("/_api/public/events")
def directory_data():
    """Paginated public facts plus only the requesting account's own status."""
    try:
        return _private_response(jsonify(asdict(_directory_page())))
    except SQLAlchemyError:
        current_app.logger.exception("Public events data failed")
        return _private_response(make_response(jsonify({"error": "Events are temporarily unavailable"}), 503))


@bp.route("/<path:path>", methods=["GET"])
def public_or_application(path: str):
    # Specific API, static, and waiver rules win Flask's URL matching before this
    # fallback. Unknown paths in those namespaces must still remain a real 404.
    path = path.rstrip("/")
    first = path.split("/", 1)[0]
    if first in {"_api", "api"}:
        return jsonify({"error": "Not found"}), 404
    if first in {"static", "assets"}:
        abort(404)
    if first == "leagues" and current_app.config["PUBLIC_HTML_ENABLED"] and _APP_NESTED.fullmatch(path):
        parts = path.split("/")
        if parts[1] != "new":
            try:
                application = (
                    len(parts) > 2 or request.args.get("app") == "1" or request.args.get("tab", "") not in {"", "info"}
                )
                workflow = "registration" if len(parts) > 2 and parts[2] == "register" else "league tools"
                return _league(parts[1], application=application, workflow=workflow)
            except SQLAlchemyError:
                current_app.logger.exception("Public league document failed")
                return _error(503)
    if path in _APP_TOP_LEVEL or _APP_NESTED.fullmatch(path):
        return _application(workflow="application")
    if first in _RESERVED_PREFIXES:
        return _error()
    workflow = _EVENT_WORKFLOW.fullmatch(path)
    if "/" in path and not workflow:
        return _error()
    if not current_app.config["PUBLIC_HTML_ENABLED"]:
        return _application(workflow="application")
    try:
        if workflow:
            name = workflow.group("workflow")
            label = "registration" if name == "register" else "schedule" if name == "schedule" else "event tools"
            return _event(workflow.group("slug"), application=True, workflow=label)
        # Query tabs remain application routes, including future/unknown tabs.
        # A typo must never silently replace a requested panel with event Info.
        tab = request.args.get("tab", "")
        return _event(path, application=request.args.get("app") == "1" or tab not in {"", "info"})
    except SQLAlchemyError:
        current_app.logger.exception("Public event document failed")
        return _error(503)
