"""League lookup and serializer helpers.

- ``require_league(league_url)`` - league-lookup-with-403 helper. Returns
  a ``(league, error_code)`` tuple. Used as a permission gate by routes
  that need to verify the caller can see the league.
- ``league_to_dict(league)`` - serialise a League to the API dict shape.
"""

from __future__ import annotations

from flask_login import current_user

from app.services.permission_service import PermissionService
from models import League


def require_league(league_url: str):
    """Fetch a league by URL slug and verify access rights.

    A league is accessible when it is published, or when the current user is
    a TO for it.  Unpublished leagues return an HTTP 403 code to authenticated
    non-TO users.

    Args:
        league_url: The URL slug to look up.

    Returns:
        A ``(league, error_code)`` tuple where *error_code* is ``None`` on
        success, ``404`` if the league does not exist, or ``403`` if the
        caller lacks access.
    """
    league = League.query.filter_by(url=league_url).first()
    if not league:
        return None, 404
    if league.published:
        return league, None
    if not current_user.is_authenticated:
        return league, 403
    if not PermissionService.is_league_organizer(league_url, current_user):
        return league, 403
    return league, None


def league_to_dict(league) -> dict:
    """Serialise a :class:`~app.models.league.League` to an API dict.

    Includes registration status, fees, waiver info, and payment instructions
    drawn from the league's
    :class:`~app.models.registrable_config.RegistrableConfig`.

    Args:
        league: The league ORM instance to serialise.

    Returns:
        A JSON-serialisable dictionary suitable for the SPA.
    """
    rc = league.registrable_config
    team_reg_open = bool(rc.team_registration_open) if rc else False
    player_reg_open = bool(rc.player_registration_open) if rc else False
    wf = getattr(rc, "waiver_filepath", None) if rc else None
    return {
        "league_url": league.url,
        "name": league.name,
        "banner_photo": league.banner_photo,
        "about": getattr(league, "about", None),
        "team_reg_fee": rc.team_reg_fee if rc else None,
        "player_reg_fee": rc.player_reg_fee if rc else None,
        "registration_open": bool(team_reg_open or player_reg_open),
        "team_registration_open": bool(team_reg_open),
        "player_registration_open": bool(player_reg_open),
        "published": getattr(league, "published", False),
        "terms_link": rc.terms_link if rc else None,
        "n_max_teams": getattr(rc, "n_max_teams", None) if rc else None,
        "max_team_size_roster": (getattr(rc, "max_team_size_roster", None) if rc else None),
        "max_team_size_field": getattr(rc, "max_team_size_field", None) if rc else None,
        "waiver_required": bool(wf),
        "waiver_filepath": wf,
        "waiver_sha256": getattr(rc, "waiver_sha256", None) if rc else None,
    }
