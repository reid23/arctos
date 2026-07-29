"""Tournament-related serializer helpers.

- ``tournament_to_dict(t)`` - serialise a Tournament to the API dict.
- ``team_name_for_match(tournament, match, team_key)`` - resolve a team
  reference (id or slot ref) to a display name for the given match.
"""

from __future__ import annotations

from app.services.dual_write import get_head_ref_allowlist_ids
from app.utils.helpers import get_registrable_config
from models import BracketPlacement, League, Team


def _tournament_has_bracket(t) -> bool:
    """True when the tournament has a legacy image bracket or canvas placements."""
    if getattr(t, "bracket", None):
        return True
    try:
        return (
            BracketPlacement.query.filter(
                BracketPlacement.event == t.url,
                BracketPlacement.x_pos.isnot(None),
                BracketPlacement.y_pos.isnot(None),
            ).first()
            is not None
        )
    except Exception:
        # Table may not exist yet during early migrations / tests without the model.
        return False


def tournament_to_dict(t) -> dict:
    """Serialise a :class:`~app.models.tournament.Tournament` to an API dict.

    Includes registration status, fee, waiver, head-ref policy, and league
    membership information.  Falls back gracefully when the tournament's
    :class:`~app.models.registrable_config.RegistrableConfig` is not found.

    Args:
        t: The tournament ORM instance to serialise.

    Returns:
        A JSON-serialisable dictionary suitable for the SPA.
    """
    cfg = get_registrable_config(t)
    end = t.end_date.isoformat() if t.end_date else None
    start = t.start_date.isoformat() if t.start_date else None
    team_reg_open = bool(cfg.team_registration_open) if cfg else False
    player_reg_open = bool(cfg.player_registration_open) if cfg else False

    out = {
        "url": t.url,
        "name": t.name,
        "start_date": start,
        "end_date": end,
        "location": t.location,
        "published": t.published,
        "n_max_teams": getattr(cfg, "n_max_teams", None) if cfg else None,
        "schedule_published": getattr(t, "schedule_published", False),
        # Legacy aggregate flag kept for compatibility: true if either team or player registration open.
        "registration_open": bool(team_reg_open or player_reg_open),
        "team_registration_open": bool(team_reg_open),
        "player_registration_open": bool(player_reg_open),
        "bracket": _tournament_has_bracket(t),
        "about": getattr(t, "about", None),
        "team_reg_fee": cfg.team_reg_fee if cfg else None,
        "player_reg_fee": cfg.player_reg_fee if cfg else None,
        "max_team_size_roster": (getattr(cfg, "max_team_size_roster", None) if cfg else None),
        "max_team_size_field": (getattr(cfg, "max_team_size_field", None) if cfg else None),
        "terms_link": cfg.terms_link if cfg else None,
        "head_refs_allowed_list": ",".join(get_head_ref_allowlist_ids(t)) or None,
        "head_refs_allow_reffing_teams": bool(getattr(t, "head_refs_allow_reffing_teams", False)),
        "head_refs_allow_anyone": bool(getattr(t, "head_refs_allow_anyone", False)),
    }
    if cfg:
        wf = getattr(cfg, "waiver_filepath", None)
        out["waiver_required"] = bool(wf)
        out["waiver_filepath"] = wf
        out["waiver_sha256"] = getattr(cfg, "waiver_sha256", None)
    else:
        out["waiver_required"] = False
        out["waiver_filepath"] = None
        out["waiver_sha256"] = None
    if getattr(t, "league_id", None):
        league = League.query.get(t.league_id)
        if league and league.registrable_config:
            rc = league.registrable_config
            l_team_open = bool(rc.team_registration_open)
            l_player_open = bool(rc.player_registration_open)
            out["league"] = {
                "league_url": league.url,
                "name": league.name,
                "registration_open": l_team_open or l_player_open,
                "team_registration_open": l_team_open,
                "player_registration_open": l_player_open,
                "team_reg_fee": rc.team_reg_fee,
                "player_reg_fee": rc.player_reg_fee,
            }
        elif league:
            out["league"] = {"league_url": league.url, "name": league.name}
        else:
            out["league"] = None
    else:
        out["league"] = None
    return out


def team_name_for_match(tournament, match, team_key):
    from app.services.registration_resolver import team_registration_for_tournament

    team_id = getattr(match, team_key)
    if not team_id:
        initial = getattr(match, f"{team_key}_initial", None)
        return initial or f"Team {team_key[-1]}"
    reg = team_registration_for_tournament(tournament, team_id)
    if reg and reg.pseudonym:
        return reg.pseudonym
    t = Team.query.get(team_id)
    return t.name if t else team_id
