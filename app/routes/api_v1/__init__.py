"""Public API, version 1 - blueprint package.

Hosts the ``api_v1`` blueprint at ``/api/v1``: stable read access to
everything an anonymous visitor of the website can see, plus footage
upload (``footage`` scope) and tournament and league management
(``manage`` scope) for organizers. Documented in ``docs/developers.md``;
every route is a contract with third-party apps, so change response shapes
only additively (see :mod:`app.serializers.api_v1_serializer`).

Every route authenticates with an OAuth access token via
:func:`~app.utils.oauth_server.require_bearer` and passes ``g.oauth_user``
to services explicitly; nothing here reads ``current_user``. Each request
is logged on the ``arctos.api`` logger once its response is ready.

Submodules, one per resource area:

- :mod:`~app.routes.api_v1.me` - the token's user.
- :mod:`~app.routes.api_v1.tournaments` - tournaments, fields, rosters, results, side competitions.
- :mod:`~app.routes.api_v1.matches` - schedule, match detail, live field scoreboards.
- :mod:`~app.routes.api_v1.footage` - footage upload (``footage`` scope).
- :mod:`~app.routes.api_v1.manage_tournaments`, :mod:`~app.routes.api_v1.manage_schedule`,
  :mod:`~app.routes.api_v1.manage_leagues` - organizer writes (``manage`` scope).
- :mod:`~app.routes.api_v1.manage_registrations` - organizer registration lists, payments, and
  deregistration (``manage`` scope).
- :mod:`~app.routes.api_v1.manage_penalty_types`, :mod:`~app.routes.api_v1.manage_side_competitions`,
  :mod:`~app.routes.api_v1.manage_schedule_items`, :mod:`~app.routes.api_v1.manage_brackets` - event setup
  (reads public, writes ``manage`` scope).
- :mod:`~app.routes.api_v1.leagues`, :mod:`~app.routes.api_v1.players`, :mod:`~app.routes.api_v1.teams`.
"""

from __future__ import annotations

import functools
import logging
import math
import time

from flask import Blueprint, g, jsonify, request

from app.services.permission_service import PermissionService
from app.utils.result_helpers import result_response  # noqa: F401 - shared by submodules
from models import League, Tournament

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

usage_logger = logging.getLogger("arctos.api")

PAGE_SIZE = 50


def error(message: str, status_code: int):
    return jsonify({"error": message}), status_code


def json_object_body() -> dict | None:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def token_has_scope(scope: str) -> bool:
    return scope in g.oauth_token.scope.split()


def token_actor() -> dict:
    """The token's user as the ``actor_user_id`` / ``actor_user_type`` service arguments."""
    return {"actor_user_id": g.oauth_user.id, "actor_user_type": g.oauth_token.user_type}


def sees_unpublished_organized() -> bool:
    """Whether the token may see unpublished tournaments and leagues its user organizes."""
    return g.oauth_user is not None and (token_has_scope("footage") or token_has_scope("manage"))


def _is_organizer_view(tournament) -> bool:
    return sees_unpublished_organized() and PermissionService.is_tournament_organizer_of(tournament, g.oauth_user)


def can_view_tournament(tournament) -> bool:
    return bool(tournament.published) or _is_organizer_view(tournament)


def can_view_schedule(tournament) -> bool:
    return bool(tournament.schedule_published) or _is_organizer_view(tournament)


def can_view_bracket(tournament) -> bool:
    return bool(tournament.bracket_published) or _is_organizer_view(tournament)


def can_view_league(league) -> bool:
    return bool(league.published) or (
        sees_unpublished_organized() and PermissionService.is_league_organizer(league.url, g.oauth_user)
    )


def with_tournament(view):
    """Resolve the ``tournament_id`` URL segment to a tournament the token may see; 404 otherwise."""

    @functools.wraps(view)
    def wrapper(tournament_id: str, **kwargs):
        tournament = Tournament.query.get(tournament_id)
        if tournament is None or not can_view_tournament(tournament):
            return error("Tournament not found", 404)
        return view(tournament, **kwargs)

    return wrapper


def with_league(view):
    """Resolve the ``league_id`` URL segment to a league the token may see; 404 otherwise."""

    @functools.wraps(view)
    def wrapper(league_id: str, **kwargs):
        league = League.query.get(league_id)
        if league is None or not can_view_league(league):
            return error("League not found", 404)
        return view(league, **kwargs)

    return wrapper


def with_organized_tournament(view):
    """Like :func:`with_tournament`, then 403 unless the token's user organizes the tournament."""

    @with_tournament
    @functools.wraps(view)
    def wrapper(tournament, **kwargs):
        if not PermissionService.is_tournament_organizer_of(tournament, g.oauth_user):
            return error("Only organizers of this tournament can do this", 403)
        return view(tournament, **kwargs)

    return wrapper


def with_organized_league(view):
    """Like :func:`with_league`, then 403 unless the token's user organizes the league."""

    @with_league
    @functools.wraps(view)
    def wrapper(league, **kwargs):
        if not PermissionService.is_league_organizer(league.url, g.oauth_user):
            return error("Only organizers of this league can do this", 403)
        return view(league, **kwargs)

    return wrapper


def paginated(query, order_by) -> tuple[list, dict]:
    """One ``?page=`` (1-based) of *query*, plus ``{page, total_pages, total}``."""
    page = max(1, request.args.get("page", type=int) or 1)
    total = query.count()
    rows = query.order_by(order_by).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return rows, {"page": page, "total_pages": math.ceil(total / PAGE_SIZE), "total": total}


def flag_argument(name: str) -> bool:
    return request.args.get(name, "").lower() in ("1", "true", "yes")


@bp.before_request
def start_request_timer():
    g.api_request_started_at = time.perf_counter()


@bp.after_request
def log_request(response):
    started_at = g.pop("api_request_started_at", None)
    if started_at is None:
        return response
    token = g.get("oauth_token")
    fields = {
        "client_id": token.client_id if token else None,
        "client_name": token.client.name if token else None,
        "user_id": token.user_id if token else None,
        "user_type": token.user_type if token else None,
        "method": request.method,
        "path": request.path,
        "status_code": response.status_code,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 1),
    }
    usage_logger.info(
        "api_request client_id=%s client_name=%r user=%s method=%s path=%s status_code=%d duration_ms=%.1f",
        fields["client_id"],
        fields["client_name"],
        f"{fields['user_type']}:{fields['user_id']}" if fields["user_id"] else None,
        fields["method"],
        fields["path"],
        fields["status_code"],
        fields["duration_ms"],
        extra={"api_request": fields},
    )
    return response


def _is_api_v1_path() -> bool:
    return request.path == bp.url_prefix or request.path.startswith(bp.url_prefix + "/")


@bp.app_errorhandler(404)
def not_found(exception):
    return error("Not found", 404) if _is_api_v1_path() else exception


@bp.app_errorhandler(405)
def method_not_allowed(exception):
    return error("Method not allowed", 405) if _is_api_v1_path() else exception


# Register submodule handlers by importing them. This MUST be at the bottom
# so that bp and the helpers above exist when submodules import them.
from app.routes.api_v1 import (  # noqa: E402, F401
    footage,
    leagues,
    manage_brackets,
    manage_leagues,
    manage_penalty_types,
    manage_registrations,
    manage_schedule,
    manage_schedule_items,
    manage_side_competitions,
    manage_tournaments,
    matches,
    me,
    players,
    teams,
    tournaments,
)
