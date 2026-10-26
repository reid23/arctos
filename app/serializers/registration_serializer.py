"""Serializers for registration responses.

Two pure-function builders used by registration routes:

- ``player_reg_waiver_api(reg, cfg)`` - builds the waiver-status sub-dict
  embedded in player-registration API payloads.
- ``serialize_manage(scope, search_query, search_type, cfg)`` - builds
  the full registration-management response payload used by league and
  tournament TO views.
"""

from __future__ import annotations

from app.domain.enums import RegistrationStatus
from app.services.registration_form_service import RegistrationFormService
from app.serializers.tournament_serializer import tournament_to_dict
from app.utils.datetime_helpers import dt_iso
from models import League, Player, Team, Tournament


def player_reg_waiver_api(reg, cfg):
    """Waiver fields for API given a PlayerRegistration and RegistrableConfig (or None)."""
    waiver_required = bool(getattr(cfg, "waiver_filepath", None)) if cfg else False
    fp = getattr(cfg, "waiver_filepath", None) if cfg else None
    sha = getattr(cfg, "waiver_sha256", None) if cfg else None
    stored = getattr(reg, "waiver_legal_name_signature_sha256", None) if reg else None
    legal = getattr(reg, "waiver_legal_name_signature", None) if reg else None

    if not waiver_required:
        waiver_status = None
        signature_valid = True
    elif not stored:
        waiver_status = "NOT_SIGNED"
        signature_valid = False
    elif sha is not None and stored == sha:
        waiver_status = "VALID"
        signature_valid = True
    else:
        waiver_status = "OUT_OF_DATE"
        signature_valid = False

    return {
        "waiver_required": waiver_required,
        "waiver_filepath": fp,
        "waiver_sha256": sha,
        "waiver_status": waiver_status,
        "waiver_signature_valid": signature_valid,
        "waiver_legal_name_signature": legal,
    }


def serialize_manage(scope, search_query: str, search_type: str, cfg) -> dict:
    """Build the manage-API payload for *scope*.

    Handles search filtering, registration loading, and the team/player
    summary construction shared between tournament_manage_api and
    league_manage_api.

    Args:
        scope: :class:`~app.services._common.Scope` identifying event vs league.
        search_query: Search string from request query args.
        search_type: ``"team"`` / ``"player"`` / ``"both"``.
        cfg: registrable_config object (shared between scopes).

    Returns:
        The manage payload dict ready to be jsonified.
    """
    from app.services.registration_resolver import (
        team_registrations_for_scope,
        player_registrations_for_scope,
    )

    team_registrations = team_registrations_for_scope(scope, exclude_cancelled=True)
    teams_with_registrations = []
    for team_reg in team_registrations:
        team = Team.query.get(team_reg.team)
        if team:
            teams_with_registrations.append({"registration": team_reg, "team": team})

    player_registrations = player_registrations_for_scope(
        scope,
        statuses=[
            RegistrationStatus.PENDING_TEAM_APPROVAL,
            RegistrationStatus.CONFIRMED,
            RegistrationStatus.REJECTED,
        ],
    )
    players_with_registrations = []
    for player_reg in player_registrations:
        player = Player.query.get(player_reg.player)
        team = Team.query.get(player_reg.team) if player_reg.team else None
        if player:
            players_with_registrations.append({"registration": player_reg, "player": player, "team": team})

    if search_query:
        q = search_query.lower()
        if search_type in ("both", "teams"):
            teams_with_registrations = [
                t
                for t in teams_with_registrations
                if (
                    (t["team"].name or "").lower().find(q) != -1
                    or (t["registration"].pseudonym or "").lower().find(q) != -1
                )
            ]
        else:
            teams_with_registrations = []

        if search_type in ("both", "players"):
            players_with_registrations = [
                p
                for p in players_with_registrations
                if (
                    (p["player"].name or "").lower().find(q) != -1
                    or (p["registration"].jersey_name or "").lower().find(q) != -1
                )
            ]
        else:
            players_with_registrations = []

    if scope.is_league:
        league = League.query.get(scope.league_url)
        wf = getattr(cfg, "waiver_filepath", None) if cfg else None
        tournament_dict = {
            "url": league.url,
            "name": league.name,
            "start_date": "",
            "end_date": None,
            "location": None,
            "published": league.published,
            "league": {"league_url": league.url, "name": league.name},
            "waiver_required": bool(wf),
            "waiver_filepath": wf,
            "waiver_sha256": getattr(cfg, "waiver_sha256", None) if cfg else None,
        }
    else:
        tournament = Tournament.query.filter_by(url=scope.event_url).first()
        tournament_dict = tournament_to_dict(tournament)

    tournament_dict.update(RegistrationFormService.public_config(cfg))

    player_rows = []
    for pr in players_with_registrations:
        w = player_reg_waiver_api(pr["registration"], cfg)
        player_rows.append(
            {
                "registration": {
                    "id": pr["registration"].id,
                    "player": pr["registration"].player,
                    "team": pr["registration"].team,
                    "jersey_name": pr["registration"].jersey_name,
                    "jersey_number": pr["registration"].jersey_number,
                    "status": (
                        pr["registration"].status.value
                        if hasattr(pr["registration"].status, "value")
                        else str(pr["registration"].status)
                    ),
                    "paid": bool(pr["registration"].paid),
                    "amount_paid": pr["registration"].amount_paid or 0.0,
                    "registered_at": dt_iso(pr["registration"].registered_at),
                    "paid_at": dt_iso(pr["registration"].paid_at),
                    "waiver_required": w["waiver_required"],
                    "waiver_status": w["waiver_status"],
                    "waiver_legal_name_signature": w["waiver_legal_name_signature"],
                },
                "player": {
                    "id": pr["player"].id,
                    "name": pr["player"].name,
                },
                "team": (
                    {
                        "id": pr["team"].id,
                        "name": pr["team"].name,
                    }
                    if pr["team"]
                    else None
                ),
            }
        )

    return {
        "tournament": tournament_dict,
        "search_query": search_query,
        "search_type": search_type,
        "team_registrations": [
            {
                "registration": {
                    "id": tr["registration"].id,
                    "team": tr["registration"].team,
                    "pseudonym": tr["registration"].pseudonym,
                    "shortname": tr["registration"].shortname,
                    "status": (
                        tr["registration"].status.value
                        if hasattr(tr["registration"].status, "value")
                        else str(tr["registration"].status)
                    ),
                    "paid": bool(tr["registration"].paid),
                    "amount_paid": tr["registration"].amount_paid or 0.0,
                    "registered_at": dt_iso(tr["registration"].registered_at),
                    "paid_at": dt_iso(tr["registration"].paid_at),
                },
                "team": {
                    "id": tr["team"].id,
                    "name": tr["team"].name,
                },
            }
            for tr in teams_with_registrations
        ],
        "player_registrations": player_rows,
    }
