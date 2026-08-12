"""Cross-cutting helpers used by routes, services, and serializers.

Anything that has a clearly named workflow belongs in a service; this
module is the home for small, generally-applicable helpers that don't.
"""

import re
from flask_login import current_user
from app.domain.enums import RegistrationStatus
from app.services._common import current_user_type
from models import Tournament, Team


def get_registrable_config(tournament):
    """Return the effective :class:`~app.models.registrable_config.RegistrableConfig` for a tournament.

    League tournaments inherit the league's config; standalone tournaments
    have their own config.

    Args:
        tournament: A :class:`~app.models.tournament.Tournament` instance.

    Returns:
        The :class:`~app.models.registrable_config.RegistrableConfig` object,
        or ``None`` if neither the league nor the tournament has one.
    """
    if getattr(tournament, "league_id", None):
        from models import League

        league = League.query.get(tournament.league_id)
        return league.registrable_config if league else None
    return getattr(tournament, "registrable_config", None)


def get_penalty_types_for_tournament(tournament):
    """
    Get penalty types for a tournament.

    When tournament.league_id is set, returns the league's penalty types.
    When league_id is null (standalone tournament), returns the tournament's penalty types.
    """
    from models import PenaltyType

    if getattr(tournament, "league_id", None):
        return PenaltyType.query.filter_by(league_id=tournament.league_id).all()
    return PenaltyType.query.filter_by(event=tournament.url).all()


def match_event_urls_for_penalties(tournament):
    """
    Return list of event URLs to use when querying Match for penalties/notes.

    For league events, returns all event URLs in the league so penalty counts and
    penalty lists include matches from every event in the league. For standalone
    tournaments, returns just this event's URL.
    """
    if getattr(tournament, "league_id", None):
        return [t.url for t in Tournament.query.filter_by(league_id=tournament.league_id).all()]
    return [tournament.url]


DEFAULT_PENALTY_COLORS = [
    "FF0000",  # Red
    "FF8C00",  # Dark Orange
    "FFD700",  # Gold
    "32CD32",  # Lime Green
    "008000",  # Green
    "00CED1",  # Dark Turquoise
    "1E90FF",  # Dodger Blue
    "0000FF",  # Blue
    "8A2BE2",  # Blue Violet
    "FF00FF",  # Magenta
    "C71585",  # Medium Violet Red
    "A52A2A",  # Brown
    "808080",  # Gray
    "000000",  # Black
]


def get_next_penalty_color(existing_colors: set[str]) -> str:
    """Return the first default penalty colour not already in use.

    Iterates :data:`DEFAULT_PENALTY_COLORS` in order and returns the first
    colour absent from *existing_colors*.

    Args:
        existing_colors: Set of 6-character hex colour strings already
            assigned to existing penalty types.

    Returns:
        A 6-character hex colour string (no ``#``).  Falls back to
        ``"000000"`` when all defaults are taken.
    """
    for color in DEFAULT_PENALTY_COLORS:
        if color not in existing_colors:
            return color
    return "000000"  # Default fallback


def can_head_ref_match(tournament_url: str, player_id: str, match=None) -> bool:
    """
    Check if a player can head ref matches for a tournament.

    Args:
        tournament_url: The tournament URL
        player_id: The player ID to check
        match: Optional Match object for match-specific checks (reffing teams)

    Returns:
        True if the player can head ref, False otherwise

    Registration checks honour both standalone (event-scoped) and league
    tournaments (league-scoped player registrations).
    """
    from app.services.dual_write import get_head_ref_allowlist_ids, get_match_ref_team_ids
    from app.services.registration_resolver import player_registrations_for_tournament

    tournament = Tournament.query.get(tournament_url)
    if not tournament:
        return False

    # Check explicit allowed list
    if player_id in get_head_ref_allowlist_ids(tournament):
        return True

    # If allow anyone is enabled, check if player is registered for this
    # tournament's registration scope (event or parent league).
    if tournament.head_refs_allow_anyone:
        regs = player_registrations_for_tournament(
            tournament,
            statuses=[RegistrationStatus.CONFIRMED],
        )
        return any(pr.player == player_id for pr in regs)

    # Check reffing teams (requires match context)
    if tournament.head_refs_allow_reffing_teams and match:
        for team_id in get_match_ref_team_ids(match):
            if not team_id:
                continue
            regs = player_registrations_for_tournament(
                tournament,
                team_id=team_id,
                statuses=[RegistrationStatus.CONFIRMED],
            )
            if any(pr.player == player_id for pr in regs):
                return True

    return False


def resolve_team_name_to_id(team_name, tournament_url):
    """Resolve a team name/pseudonym to (team_id, initial_display) for a tournament.

    Routes through :func:`app.services.registration_resolver.team_registration_for_tournament`
    so the lookup honours both standalone tournaments (event-scoped registrations)
    and league tournaments (league-scoped registrations). Without this delegation,
    league-event matches see their teams as "not registered" because the raw
    ``event=tournament_url`` filter never matches league rows.

    Match refs (MatchName::winner/loser) and tag refs (tag::Name) are not resolved
    here. Returns ``(id, None)`` when found; ``(None, team_name)`` otherwise.
    """
    from models import Team, Tournament
    from app.services.registration_resolver import (
        team_registration_for_tournament,
        team_registrations_for_tournament,
    )

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if tournament is None:
        return (None, team_name)

    # Try exact team-id match first; only accept if registered for this scope.
    team = Team.query.filter_by(id=team_name).first()
    if team is not None:
        if team_registration_for_tournament(tournament, team.id) is not None:
            return (team.id, None)
        return (None, team_name)

    # Pseudonym lookup against this scope's confirmed registrations.
    for reg in team_registrations_for_tournament(tournament):
        if reg.pseudonym == team_name:
            return (reg.team, None)

    return (None, team_name)


def get_team_display_name_for_event(tournament_url: str, team_id: str) -> str:
    """Return the best display name for a team within a specific tournament.

    Priority:

    1. :class:`~app.models.registration.TeamRegistration` pseudonym (if
       confirmed and non-empty), including league-scoped registrations.
    2. :attr:`~app.models.user.Team.name`.
    3. *team_id* as a fallback.

    Args:
        tournament_url: Tournament URL slug used to look up the registration.
        team_id: The team's unique identifier.

    Returns:
        A non-empty display string.
    """
    if not team_id:
        return ""
    from app.services.registration_resolver import team_registration_for_tournament

    tournament = Tournament.query.get(tournament_url)
    reg = team_registration_for_tournament(tournament, team_id) if tournament else None
    if reg and getattr(reg, "pseudonym", None):
        return reg.pseudonym
    team = Team.query.get(team_id)
    if team and getattr(team, "name", None):
        return team.name
    return team_id


def resolve_tag_to_team(tag_ref: str, tournament_url: str) -> str | None:
    """Resolve a tag reference (tag::TAG_NAME) to a team ID by querying the Tag table.

    Args:
        tag_ref: Tag reference string (e.g., "tag::Pool A")
        tournament_url: Tournament URL

    Returns:
        Team ID if tag exists and has a team assigned, None otherwise
    """
    from models import Tag

    if not tag_ref or not tag_ref.strip().lower().startswith("tag::"):
        return None

    tag_name = tag_ref[5:].strip()  # Remove "tag::" prefix
    if not tag_name:
        return None

    tag = Tag.query.filter_by(event=tournament_url, name=tag_name).first()
    if tag and tag.team:
        return tag.team
    return None


def resolve_match_winner_loser_ref(initial: str, tournament_url: str) -> str | None:
    """Resolve ``MatchName::winner`` / ``MatchName::loser`` to a team id.

    Returns the winner/loser team id of the referenced match if and only if the
    match's outcome is already decided. Returns ``None`` for anything else —
    not a winner/loser reference, the named match doesn't exist in this
    tournament, or the match hasn't been finalised yet (in which case the
    cache fill-in happens later via ``apply_match_dependencies``).

    Args:
        initial: A team-slot ``_initial`` token, e.g. ``"Final::winner"``.
        tournament_url: Tournament URL slug for the lookup.

    Returns:
        Resolved team id, or ``None`` when not currently resolvable.
    """
    from app.models.match import Match

    if not initial:
        return None
    initial = initial.strip()
    if not initial:
        return None
    lower = initial.lower()
    for suffix, qualifier in (("::winner", "winner"), ("::loser", "loser")):
        if lower.endswith(suffix):
            base = initial[: -len(suffix)].strip()
            if not base:
                return None
            ref = Match.query.filter_by(name=base, event=tournament_url).first()
            if ref is None:
                return None
            return ref.winner_team_id if qualifier == "winner" else ref.loser_team_id
    return None


def check_tournament_access(tournament_url: str):
    """Check whether the current Flask user may view a tournament.

    A tournament is accessible when it is published, or when the current
    user is a Tournament Organiser for the event or its league.

    Args:
        tournament_url: The URL slug of the tournament to check.

    Returns:
        A ``(has_access, tournament)`` tuple.  *has_access* is ``True`` when
        access is granted; *tournament* is the
        :class:`~app.models.tournament.Tournament` instance (or ``None`` when
        the tournament does not exist).
    """
    from models import Tournament, TO

    tournament = Tournament.query.get(tournament_url)
    if not tournament:
        return False, None

    # If published, anyone can access
    if tournament.published:
        return True, tournament

    # If not published, only TOs can access
    if not current_user.is_authenticated:
        return False, tournament

    is_to = None
    if tournament.league_id:
        is_to = TO.query.filter_by(
            user_id=current_user.id,
            user_type=current_user_type(),
            league_id=tournament.league_id,
        ).first()
    if not is_to:
        is_to = TO.query.filter_by(
            user_id=current_user.id,
            user_type=current_user_type(),
            event=tournament_url,
        ).first()

    if not is_to:
        return False, tournament

    return True, tournament


def is_valid_url_username(username):
    """
    Validate that a username is URL-safe.

    Rules:
    - Only alphanumeric characters, hyphens, and underscores
    - Must be at least 1 character long
    - Cannot start or end with hyphen or underscore
    - Cannot contain spaces or special characters

    Args:
        username: The username to validate

    Returns:
        True if valid, False otherwise
    """
    if not username or len(username) == 0:
        return False

    # Check length (reasonable limit)
    if len(username) > 50:
        return False

    # Must start and end with alphanumeric
    if not (username[0].isalnum() and username[-1].isalnum()):
        return False

    # Only allow alphanumeric, hyphens, and underscores
    pattern = r"^[a-zA-Z0-9_-]+$"
    if not re.match(pattern, username):
        return False

    return True
