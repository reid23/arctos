"""Organizer setup of a league: creation, deletion, and organizers.

Callers authorize the league organizer first; the SPA's session routes in
:mod:`app.routes.leagues` / :mod:`~app.routes.tournaments.management` and the
public API's ``manage`` routes share these methods.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.models.validators import URL_SLUG_ALLOWED_HINT, is_valid_url_slug
from app.services._common import resolve_actor
from app.services.license_service import LicenseService
from app.services.permission_service import PermissionService
from models import (
    TO,
    League,
    PenaltyType,
    Player,
    PlayerRegistration,
    RegistrableConfig,
    Team,
    TeamRegistration,
    Tournament,
    db,
)


@dataclass(frozen=True)
class LeagueManagementService:
    """Organizer league setup. All methods are static; the class acts as a typed namespace."""

    @staticmethod
    def create_league(*, actor_user_id: str, actor_user_type: str, name: str, url: str) -> Result[League, ArctosError]:
        """Create a league with its own registration config and the actor as its organizer."""
        name = name.strip()
        url = url.strip()
        if not name or not url:
            return Err(ValidationError(f"League name and URL slug are required. {URL_SLUG_ALLOWED_HINT}"))
        if not is_valid_url_slug(url):
            return Err(ValidationError(URL_SLUG_ALLOWED_HINT))
        if League.query.filter_by(url=url).first():
            return Err(ValidationError("League URL already exists"))

        config = RegistrableConfig(team_reg_fee=0.0, player_reg_fee=0.0)
        db.session.add(config)
        db.session.flush()
        league = League(url=url, name=name, registrable_config_id=config.id)
        db.session.add(league)
        db.session.flush()
        db.session.add(TO(user_id=actor_user_id, user_type=actor_user_type, event=None, league_id=url))
        db.session.commit()
        return Ok(league)

    @staticmethod
    def delete_league(league_url: str) -> Result[str, ArctosError]:
        """Permanently delete a league that has no tournaments left; returns its name."""
        league = League.query.get(league_url)
        if league is None:
            return Err(NotFoundError("League not found"))
        if Tournament.query.filter_by(league_id=league_url).first():
            return Err(ValidationError("Remove or delete all events (tournaments) in this league first."))

        # Delete in order: registrations, penalty types, TOs, league, registrable_config
        TeamRegistration.query.filter_by(league_id=league_url).delete(synchronize_session=False)
        PlayerRegistration.query.filter_by(league_id=league_url).delete(synchronize_session=False)
        PenaltyType.query.filter_by(league_id=league_url).delete(synchronize_session=False)
        TO.query.filter_by(league_id=league_url).delete(synchronize_session=False)
        config_id = league.registrable_config_id
        name = league.name
        LicenseService.release_league(league_url)
        db.session.delete(league)
        if config_id:
            config = RegistrableConfig.query.get(config_id)
            if config:
                db.session.delete(config)
        db.session.commit()
        return Ok(name)

    @staticmethod
    def add_organizer(league_url: str, user_id: str, user_type: str) -> Result[str, ArctosError]:
        """Make a player or team an organizer of the league; returns their name."""
        user_id = user_id.strip()
        user_type = user_type.strip().lower()
        if not user_id or user_type not in ("player", "team"):
            return Err(ValidationError("Invalid user ID or type"))

        if user_type == "player":
            user = Player.query.get(user_id)
            if not user:
                return Err(NotFoundError(f'Player "{user_id}" not found'))
        else:
            user = Team.query.get(user_id)
            if not user:
                return Err(NotFoundError(f'Team "{user_id}" not found'))

        if TO.query.filter_by(user_id=user_id, user_type=user_type, league_id=league_url).first():
            return Err(ValidationError("This user is already a TO for this league"))

        db.session.add(TO(user_id=user_id, user_type=user_type, event=None, league_id=league_url))
        db.session.commit()
        return Ok(user.name)

    @staticmethod
    def remove_organizer(
        league_url: str, organizer_id, *, actor_user_id: str, actor_user_type: str
    ) -> Result[str, ArctosError]:
        """Remove TO entry *organizer_id* from the league; returns the removed user's name.

        Actors can't remove themselves or a team they manage.
        """
        organizer = TO.query.get(organizer_id)
        if organizer is None:
            return Err(NotFoundError("Organizer not found"))
        if organizer.league_id != league_url:
            return Err(ValidationError("Invalid TO entry"))

        actor = resolve_actor(actor_user_id, actor_user_type)
        if (organizer.user_id == actor_user_id and organizer.user_type == actor_user_type) or (
            organizer.user_type == "team" and PermissionService.can_manage_team(organizer.user_id, actor)
        ):
            return Err(ValidationError("You cannot remove yourself as a TO"))

        user = resolve_actor(organizer.user_id, organizer.user_type)
        name = user.name if user else organizer.user_id
        db.session.delete(organizer)
        db.session.commit()
        return Ok(name)
