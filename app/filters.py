"""
Jinja2 template filters for the tournament site.
"""

import json
from datetime import timezone, timedelta
from flask import Blueprint, url_for
from markupsafe import Markup
from models import TeamRegistration
from app.utils.helpers import can_head_ref_match
import markdown
import bleach

bp = Blueprint("filters", __name__)


@bp.app_template_filter("team_registration_for_tournament")
def team_registration_for_tournament(team_id: str | None, tournament_url: str) -> "TeamRegistration | None":
    """Return the team registration record for the given team and tournament.

    Args:
        team_id: The team's unique identifier, or ``None`` / falsy to skip
            the database lookup.
        tournament_url: The URL slug of the tournament.

    Returns:
        The matching :class:`~app.models.registration.TeamRegistration`, or
        ``None`` if not found or *team_id* is falsy.
    """
    if not team_id:
        return None
    return TeamRegistration.query.filter_by(team=team_id, event=tournament_url).first()


@bp.app_template_filter("team_by_pseudonym_for_tournament")
def team_by_pseudonym_for_tournament(pseudonym: str | None, tournament_url: str) -> "TeamRegistration | None":
    """Return the team registration matching a pseudonym within a tournament.

    Args:
        pseudonym: The pseudonym string to search, or ``None`` / falsy to
            skip the lookup.
        tournament_url: The URL slug of the tournament.

    Returns:
        The matching :class:`~app.models.registration.TeamRegistration`, or
        ``None`` if not found or *pseudonym* is falsy.
    """
    if not pseudonym:
        return None
    return TeamRegistration.query.filter_by(pseudonym=pseudonym, event=tournament_url).first()


@bp.app_template_filter("is_head_ref")
def is_head_ref(tournament_url: str, player_id: str) -> bool:
    """Return whether *player_id* is a head ref for the tournament.

    Does not require a match context; checks the player's global head-ref
    status for the tournament.

    Args:
        tournament_url: The URL slug of the tournament.
        player_id: The player's unique identifier.

    Returns:
        ``True`` if the player can head-ref matches in this tournament.
    """
    return can_head_ref_match(tournament_url, player_id, match=None)


@bp.app_template_filter("can_head_ref_match")
def can_head_ref_match_filter(tournament_url: str, player_id: str, match=None) -> bool:
    """Return whether *player_id* can head-ref a specific match.

    Args:
        tournament_url: The URL slug of the tournament.
        player_id: The player's unique identifier.
        match: The :class:`~app.models.match.Match` to check, or ``None``
            to test general head-ref eligibility.

    Returns:
        ``True`` if the player is permitted to head-ref *match* (or any
        match when *match* is ``None``).
    """
    return can_head_ref_match(tournament_url, player_id, match=match)


@bp.app_template_filter("from_json")
def from_json(json_string: str | None) -> dict:
    """Parse a JSON string into a Python dictionary.

    Args:
        json_string: A JSON-encoded string, or ``None`` / falsy value.

    Returns:
        The parsed dictionary, or an empty dict on parse failure or if
        *json_string* is falsy.
    """
    if not json_string:
        return {}
    try:
        return json.loads(json_string)
    except (json.JSONDecodeError, TypeError):
        return {}


@bp.app_template_filter("markdown")
def render_markdown(text: str | None) -> str:
    """Render Markdown to sanitised HTML.

    Converts *text* to HTML using python-markdown with the ``extra``,
    ``sane_lists``, ``smarty``, and ``admonition`` extensions, then
    sanitises the result with bleach to prevent XSS.  The output is
    wrapped in a ``<div class="markdown-content">`` container so CSS
    can scope styles (e.g., scaling images to fit).

    Args:
        text: Raw Markdown string, or ``None`` / falsy value.

    Returns:
        A :class:`~markupsafe.Markup` instance containing the sanitised
        HTML, or an empty string when *text* is falsy.
    """
    if not text:
        return ""

    # Convert markdown to HTML
    html = markdown.markdown(
        text,
        extensions=[
            "extra",  # tables, fenced code, etc.
            "sane_lists",
            "smarty",
            "toc",
            "admonition",  # !!! note "Title" style callouts
        ],
        output_format="html5",
    )

    html = bleach.linkify(html)
    # Wrap in a class so CSS can scale images to fit their container
    html = f'<div class="markdown-content">{html}</div>'
    return Markup(html)


@bp.app_template_filter("localtime")
def localtime(dt, format_str: str = "%Y-%m-%d %H:%M") -> str:
    """Render a UTC datetime as a client-side localisation span.

    Because the server does not know the user's timezone, the datetime is
    embedded in a ``<span data-utc="…">`` element; JavaScript on the client
    converts it to local time.

    Args:
        dt: A :class:`~datetime.datetime` object (naive datetimes are assumed
            UTC), or ``None`` / falsy.
        format_str: ``strftime`` format string used as the server-side
            fallback text inside the span.

    Returns:
        A :class:`~markupsafe.Markup` ``<span>`` element with ``data-utc``
        and ``data-format`` attributes, or an empty string when *dt* is
        falsy.
    """
    if not dt:
        return ""

    # If datetime is naive (no timezone), assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Output ISO format for JavaScript to parse
    iso_str = dt.isoformat()
    # Also provide a server-side formatted version as fallback
    formatted = dt.strftime(format_str)

    # Store the format string in a data attribute so JS knows how to format
    return Markup(f'<span class="utc-timestamp" data-utc="{iso_str}" data-format="{format_str}">{formatted}</span>')


@bp.app_template_filter("utc_iso")
def utc_iso(dt) -> str:
    """Convert a datetime to a UTC ISO-8601 string with a ``Z`` suffix.

    Ensures the datetime is timezone-aware (treating naive datetimes as UTC)
    and formats it with a ``Z`` suffix so JavaScript's ``Date.parse()``
    interprets it as UTC without ambiguity.

    Args:
        dt: A :class:`~datetime.datetime` object, or ``None`` / falsy.

    Returns:
        An ISO-8601 string ending with ``Z`` (e.g.
        ``"2024-06-01T14:30:00Z"``), or an empty string when *dt* is falsy.
    """
    if not dt:
        return ""

    # If datetime is naive (no timezone), assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Return ISO format with 'Z' suffix for UTC
    return dt.isoformat().replace("+00:00", "Z")


@bp.app_template_filter("add_minutes")
def add_minutes(dt, minutes: int | str | None):
    """Add *minutes* to a datetime, preserving timezone naiveness.

    Args:
        dt: A :class:`~datetime.datetime` object, or ``None`` / falsy.
        minutes: Number of minutes to add (coerced to ``int``), or
            ``None`` / falsy to return *dt* unchanged.

    Returns:
        A new :class:`~datetime.datetime` with *minutes* added, retaining
        the original timezone state (naive or aware), or *dt* unchanged
        when either argument is falsy.
    """
    if not dt or not minutes:
        return dt
    # Store original timezone state
    was_naive = dt.tzinfo is None
    # Ensure naive datetimes are treated as UTC for consistency
    if was_naive:
        dt = dt.replace(tzinfo=timezone.utc)
    result = dt + timedelta(minutes=int(minutes))
    # Return naive datetime if original was naive (for compatibility)
    if was_naive:
        return result.replace(tzinfo=None)
    return result


@bp.app_template_filter("to_utc")
def to_utc(dt):
    """Return *dt* as a timezone-aware UTC datetime.

    Args:
        dt: A :class:`~datetime.datetime` object, or ``None`` / falsy.

    Returns:
        A timezone-aware UTC :class:`~datetime.datetime` (naive inputs are
        stamped as UTC; aware inputs are converted), or ``None`` when *dt*
        is falsy.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    # If already timezone-aware, convert to UTC
    return dt.astimezone(timezone.utc)


@bp.app_template_filter("camera_url")
def camera_url(tournament_url: str, field_name: str) -> str:
    """Generate a camera recording URL with an embedded HMAC access key.

    The URL points to the camera page for *field_name* in *tournament_url*
    and includes a signed ``camera_key`` query parameter so that camera
    operators can access the page without a login.

    Args:
        tournament_url: The URL slug of the tournament.
        field_name: The name of the field (court) to record.

    Returns:
        The fully-qualified camera-page URL, or a fallback URL string if
        URL generation fails.
    """
    if not tournament_url or not field_name:
        return ""

    try:
        from flask_login.utils import urlencode
        from app.utils.camera_helpers import generate_camera_key

        access_key = generate_camera_key(tournament_url, field_name)

        # Generate the full URL
        base_url = url_for(
            "tournaments.camera_page",
            tournament_url=tournament_url,
            field=urlencode(field_name),
            camera_key=access_key,
            _external=True,
        )
        return base_url
    except Exception:
        # Fallback if there's an error
        return f"/{tournament_url}/camera?field={field_name}&key="


@bp.app_template_filter("merge_refs")
def merge_refs(match) -> str:
    """Merge confirmed and initial referee lists into a single display string.

    Iterates over the ``MatchReferee`` rows for *match* in slot order and
    emits the resolved ``team_id`` when present, falling back to the
    original ``initial`` expression otherwise.

    Args:
        match: A :class:`~app.models.match.Match` instance, or
            ``None`` / falsy.

    Returns:
        A comma-and-space–separated string of merged referee names, or an
        empty string when *match* is falsy.
    """
    if not match:
        return ""

    from app.services.dual_write import get_match_referee_rows

    merged = []
    for row in get_match_referee_rows(match):
        if row.team_id:
            merged.append(row.team_id)
        elif row.initial:
            merged.append(row.initial)

    return ", ".join(merged)
