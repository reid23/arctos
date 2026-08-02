"""
Player-related helper utilities.

Keep these helpers free of Flask request/session globals so they can be reused in
services, serializers, and routes.
"""

from __future__ import annotations

from typing import Optional, Tuple


def format_jersey_display(
    *,
    jersey_name: Optional[str],
    jersey_number: Optional[str],
    fallback: str,
) -> str:
    """Format a player's display name using their jersey information.

    Priority:

    1. ``jersey_name`` + ``#jersey_number`` (e.g. ``"Alice #7"``).
    2. ``jersey_name`` alone.
    3. ``#jersey_number`` alone.
    4. *fallback* (typically the player's account name).

    Args:
        jersey_name: Player's jersey / tournament name, or ``None``.
        jersey_number: Jersey number string, or ``None``.
        fallback: Value returned when both jersey fields are empty.

    Returns:
        Formatted display string.
    """
    jn = (jersey_name or "").strip()
    jnum = (jersey_number or "").strip()
    if jn and jnum:
        return f"{jn} #{jnum}"
    if jn:
        return jn
    if jnum:
        return f"#{jnum}"
    return fallback


def get_player_display_from_registration(player, registration) -> str:
    """Return a player's tournament display string from pre-fetched objects.

    Avoids additional database queries because *player* and *registration*
    are already in memory.

    Args:
        player: A :class:`~app.models.user.Player` ORM instance.
        registration: A :class:`~app.models.registration.PlayerRegistration`
            ORM instance, or ``None`` for unregistered players.

    Returns:
        Formatted display string (see :func:`format_jersey_display`).
    """
    fallback = getattr(player, "name", "") or getattr(player, "id", "") or ""
    jersey_name = getattr(registration, "jersey_name", None) if registration is not None else None
    jersey_number = getattr(registration, "jersey_number", None) if registration is not None else None
    return format_jersey_display(jersey_name=jersey_name, jersey_number=jersey_number, fallback=fallback)


def get_player_display_name(player_id: str, tournament_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Look up a player's canonical name and tournament display string.

    Display string priority:

    1. ``jersey_name`` + ``#jersey_number`` (e.g. ``"Alice #7"``).
    2. ``jersey_name`` alone.
    3. ``#jersey_number`` alone.
    4. :attr:`~app.models.user.Player.name`.

    Args:
        player_id: The player's account ID.
        tournament_url: Tournament URL slug used to find the
            :class:`~app.models.registration.PlayerRegistration`.

    Returns:
        A ``(player_name, player_display)`` tuple, or ``(None, None)`` when
        the player account is not found.
    """
    if not player_id:
        return None, None

    # Local imports to avoid heavy import chains during app startup.
    from models import Player

    player = Player.query.get(player_id)
    if not player:
        return None, None

    player_name = player.name
    player_display: Optional[str] = None

    if tournament_url:
        from models import Tournament
        from app.services.registration_resolver import player_registration_for_tournament

        tournament = Tournament.query.get(tournament_url)
        reg = player_registration_for_tournament(tournament, player_id) if tournament is not None else None
        if reg:
            player_display = format_jersey_display(
                jersey_name=getattr(reg, "jersey_name", None),
                jersey_number=getattr(reg, "jersey_number", None),
                fallback=player.name,
            )

    if not player_display:
        player_display = player.name

    return player_name, player_display
