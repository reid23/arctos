"""
Tournament registration workflows.

This service centralizes the multi-model workflow logic for registering and
de-registering teams/players for a tournament.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select

from app.domain.enums import MatchStatus, RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import (
    ArctosError,
    RegistrationClosedError,
    UnauthorizedError,
    ValidationError,
)
from app.utils.name_validation import team_pseudonym_char_error
from app.utils.datetime_helpers import now_utc_naive
from app.services._common import Scope

if TYPE_CHECKING:  # pragma: no cover
    from models import PlayerRegistration, TeamRegistration, Tournament


@dataclass(frozen=True)
class ScopeContext:
    """Loaded context for a Scope: parent (Tournament or League) + cfg."""

    scope: Scope
    parent: object  # Tournament or League
    cfg: object  # registrable_config


class _CapExceeded(Exception):
    """Internal sentinel for n_max savepoint rollback."""

    def __init__(self, n_max: int, confirmed_count: int) -> None:
        self.n_max = n_max
        self.confirmed_count = confirmed_count


@dataclass(frozen=True)
class RegistrationService:
    """Service encapsulating tournament registration workflows.

    All methods are static; the class acts as a typed namespace.  Each
    public method returns a :class:`~app.error_values.Result` so callers
    can map errors to HTTP responses without raising exceptions.
    """

    @staticmethod
    def _load_scope_context(scope: Scope) -> "Result[ScopeContext, ArctosError]":
        """Load parent (Tournament or League) and registrable_config for a Scope."""
        from app.services._common import get_tournament_or_err
        from app.utils.helpers import get_registrable_config

        if scope.is_league:
            from models import League

            league = League.query.filter_by(url=scope.league_url).first()
            if league is None:
                return Err(ArctosError("League not found", status_code=404, public=True))
            cfg = league.registrable_config
            return Ok(ScopeContext(scope=scope, parent=league, cfg=cfg))

        match get_tournament_or_err(scope.event_url):
            case Ok(tournament):
                cfg = get_registrable_config(tournament)
                return Ok(ScopeContext(scope=scope, parent=tournament, cfg=cfg))
            case Err(_) as err:
                return err

    @staticmethod
    def _get_tournament(tournament_url: str) -> Result["Tournament", ArctosError]:
        """Fetch a tournament by URL slug, returning an error if not found.

        Args:
            tournament_url: The tournament URL slug to look up.

        Returns:
            :class:`~app.error_values.Ok` wrapping the tournament, or
            :class:`~app.error_values.Err` wrapping a
            :class:`~app.exceptions.TournamentNotFoundError`.
        """
        from app.services._common import get_tournament_or_err

        return get_tournament_or_err(tournament_url)

    @staticmethod
    def _tournament_team_reg_open(tournament) -> bool:
        """Return whether team registration is currently open for *tournament*."""
        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament)
        return bool(cfg.team_registration_open) if cfg else False

    @staticmethod
    def _tournament_player_reg_open(tournament) -> bool:
        """Return whether player registration is currently open for *tournament*."""
        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament)
        return bool(cfg.player_registration_open) if cfg else False

    @staticmethod
    def _require_team_registration_open_for_register(
        tournament,
    ) -> Result[None, ArctosError]:
        if not RegistrationService._tournament_team_reg_open(tournament):
            return Err(RegistrationClosedError("Team registration is not open for this tournament"))
        return Ok(None)

    @staticmethod
    def _require_player_registration_open_for_register(
        tournament,
    ) -> Result[None, ArctosError]:
        if not RegistrationService._tournament_player_reg_open(tournament):
            return Err(RegistrationClosedError("Player registration is not open for this tournament"))
        return Ok(None)

    @staticmethod
    def _require_team_registration_open_for_deregister(
        tournament,
    ) -> Result[None, ArctosError]:
        if not RegistrationService._tournament_team_reg_open(tournament):
            return Err(ValidationError("Registration changes are locked. You can no longer deregister teams."))
        return Ok(None)

    @staticmethod
    def _require_player_registration_open_for_deregister(
        tournament,
    ) -> Result[None, ArctosError]:
        if not RegistrationService._tournament_player_reg_open(tournament):
            return Err(ValidationError("Registration changes are locked. You can no longer deregister players."))
        return Ok(None)

    @staticmethod
    @allow_Q
    def register_team(
        scope: Scope,
        team_id: str,
        pseudonym: str,
        shortname: str | None = None,
    ) -> Result["TeamRegistration", ArctosError]:
        from models import TeamRegistration, db

        match RegistrationService._load_scope_context(scope):
            case Ok(ctx):
                pass
            case Err(_) as err:
                return err

        cfg = ctx.cfg
        scope_label = "league" if scope.is_league else "tournament"

        if scope.is_league:
            rc = cfg
            if not (rc and rc.team_registration_open):
                return Err(RegistrationClosedError(f"Registration is not open for this {scope_label}"))
        else:
            RegistrationService._require_team_registration_open_for_register(ctx.parent).Q()

        pseudonym = (pseudonym or "").strip()
        normalised_shortname: str | None = (shortname or "").strip() or None
        if not pseudonym:
            return Err(ValidationError("Team pseudonym is required"))
        pn_err = team_pseudonym_char_error(pseudonym)
        if pn_err:
            return Err(ValidationError(pn_err))

        if scope.is_league:
            existing_reg = TeamRegistration.query.filter_by(league_id=scope.league_url, team=team_id).first()
        else:
            existing_reg = TeamRegistration.query.filter_by(event=scope.event_url, team=team_id).first()
        if existing_reg:
            if existing_reg.status != TeamRegistrationStatus.CANCELLED:
                return Err(ValidationError(f"Your team is already registered for this {scope_label}"))
            team_registration = existing_reg
            team_registration.pseudonym = pseudonym
            team_registration.shortname = normalised_shortname
            team_registration.registered_at = now_utc_naive()
        else:
            if scope.is_league:
                team_registration = TeamRegistration(
                    event=None,
                    league_id=scope.league_url,
                    team=team_id,
                    pseudonym=pseudonym,
                    shortname=normalised_shortname,
                )
            else:
                team_registration = TeamRegistration(
                    event=scope.event_url,
                    team=team_id,
                    pseudonym=pseudonym,
                    shortname=normalised_shortname,
                )

        n_max = getattr(cfg, "n_max_teams", None) if cfg else None

        team_registration.status = TeamRegistrationStatus.PENDING

        try:
            with db.session.begin_nested():
                if not existing_reg:
                    db.session.add(team_registration)
                db.session.flush()  # row visible to the recount

                if n_max is not None:
                    if scope.is_league:
                        confirmed_count = TeamRegistration.query.filter_by(
                            league_id=scope.league_url,
                            status=TeamRegistrationStatus.CONFIRMED,
                        ).count()
                    elif getattr(ctx.parent, "league_id", None):
                        # For league tournaments, team registrations are stored on league_id.
                        confirmed_count = TeamRegistration.query.filter_by(
                            league_id=ctx.parent.league_id,
                            status=TeamRegistrationStatus.CONFIRMED,
                        ).count()
                    else:
                        confirmed_count = TeamRegistration.query.filter_by(
                            event=scope.event_url,
                            status=TeamRegistrationStatus.CONFIRMED,
                        ).count()
                    if confirmed_count >= n_max:
                        raise _CapExceeded(n_max=n_max, confirmed_count=confirmed_count)

                team_registration.status = TeamRegistrationStatus.CONFIRMED

                # Auto-mark as paid if registration fee is zero
                if not cfg or not cfg.team_reg_fee or cfg.team_reg_fee == 0:
                    team_registration.paid = True
                    team_registration.amount_paid = 0.0
                    team_registration.paid_at = now_utc_naive()
        except _CapExceeded as exc:
            return Err(ValidationError(f"Maximum number of teams ({exc.n_max}) already registered"))

        db.session.commit()
        return Ok(team_registration)

    @staticmethod
    @allow_Q
    def register_player(
        scope: Scope,
        player_id: str,
        team_id: Optional[str],
        *,
        jersey_number: str = "",
        jersey_name: str = "",
        waiver_legal_name_signature: str = "",
    ) -> Result["PlayerRegistration", ArctosError]:
        from models import PlayerRegistration, db

        match RegistrationService._load_scope_context(scope):
            case Ok(ctx):
                pass
            case Err(_) as err:
                return err

        cfg = ctx.cfg
        scope_label = "league" if scope.is_league else "tournament"

        if scope.is_league:
            rc = cfg
            if not (rc and rc.player_registration_open):
                return Err(RegistrationClosedError(f"Registration is not open for this {scope_label}"))
        else:
            RegistrationService._require_player_registration_open_for_register(ctx.parent).Q()

        team_id = (team_id or "").strip() or None
        status = RegistrationStatus.CONFIRMED if not team_id else RegistrationStatus.PENDING_TEAM_APPROVAL

        if scope.is_league:
            existing_reg = PlayerRegistration.query.filter_by(league_id=scope.league_url, player=player_id).first()
        else:
            existing_reg = PlayerRegistration.query.filter_by(event=scope.event_url, player=player_id).first()

        # Enforce exactly one PlayerRegistration row per (scope, player).
        # If a player was previously rejected/cancelled, allow resubmission by updating that row.
        if existing_reg:
            if existing_reg.status not in (
                RegistrationStatus.REJECTED,
                RegistrationStatus.CANCELLED,
            ):
                return Err(ValidationError(f"You already have a registration for this {scope_label}"))
            player_registration = existing_reg
            player_registration.team = team_id
            player_registration.jersey_number = jersey_number or ""
            player_registration.jersey_name = jersey_name or ""
            player_registration.status = status
        else:
            if scope.is_league:
                player_registration = PlayerRegistration(
                    event=None,
                    league_id=scope.league_url,
                    player=player_id,
                    team=team_id,
                    jersey_number=jersey_number or "",
                    jersey_name=jersey_name or "",
                    status=status,
                )
            else:
                player_registration = PlayerRegistration(
                    event=scope.event_url,
                    player=player_id,
                    team=team_id,
                    jersey_number=jersey_number or "",
                    jersey_name=jersey_name or "",
                    status=status,
                )

        # Auto-mark as paid if registration fee is zero
        if not cfg or not cfg.player_reg_fee or cfg.player_reg_fee == 0:
            player_registration.paid = True
            player_registration.amount_paid = 0.0
            player_registration.paid_at = now_utc_naive()

        # If a waiver is configured, players must sign it.
        waiver_filepath = getattr(cfg, "waiver_filepath", None) if cfg else None
        if waiver_filepath:
            signature = (waiver_legal_name_signature or "").strip()
            if not signature:
                return Err(ValidationError("Waiver signature is required"))
            current_waiver_sha = getattr(cfg, "waiver_sha256", None)
            if not current_waiver_sha:
                return Err(ValidationError("Waiver is missing its checksum"))

            now = now_utc_naive()
            player_registration.waiver_legal_name_signature = signature
            player_registration.waiver_legal_name_signature_sha256 = current_waiver_sha
            player_registration.waiver_signature_submitted_at = now

        db.session.add(player_registration)

        db.session.commit()
        return Ok(player_registration)

    @staticmethod
    @allow_Q
    def register_player_as_to(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
        team_id: Optional[str],
        jersey_number: str = "",
        jersey_name: str = "",
        waiver_legal_name_signature: str = "",
    ) -> Result["PlayerRegistration", ArctosError]:
        """Tournament-organizer-driven player registration.

        The actor must be a TO (tournament organizer) for ``tournament_url``.
        The target player (``player_id``) must already exist. Returns the
        created or updated :class:`~app.models.registration.PlayerRegistration`
        on success.
        """
        from models import PlayerRegistration, TeamRegistration, db

        tournament = RegistrationService._get_tournament(tournament_url).Q()

        from app.services._common import resolve_actor
        from app.services.permission_service import PermissionService
        from models import Player

        actor = resolve_actor(actor_user_id, actor_user_type)
        if not PermissionService.is_tournament_organizer_of(tournament, actor):
            return Err(UnauthorizedError("Only tournament organizers can register players on behalf"))

        target = Player.query.get(player_id)
        if target is None:
            return Err(ValidationError("Player not found"))

        jersey_name_value = (jersey_name or "").strip() or "N/A"
        jersey_number_value = (jersey_number or "").strip() or "0"

        if team_id:
            team_reg = TeamRegistration.query.filter_by(
                event=tournament_url,
                team=team_id,
                status=TeamRegistrationStatus.CONFIRMED,
            ).first()
            if not team_reg:
                return Err(ValidationError("Selected team is not registered for this event"))

        now = now_utc_naive()

        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament)
        waiver_filepath = getattr(cfg, "waiver_filepath", None) if cfg else None
        waiver_signature_data = None
        if waiver_filepath:
            signature = (waiver_legal_name_signature or "").strip()
            if not signature:
                return Err(ValidationError("Waiver signature is required"))
            current_waiver_sha = getattr(cfg, "waiver_sha256", None)
            if not current_waiver_sha:
                return Err(ValidationError("Waiver is missing its checksum"))
            waiver_signature_data = (signature, current_waiver_sha, now)

        existing_reg = PlayerRegistration.query.filter_by(event=tournament_url, player=player_id).first()
        if existing_reg:
            if existing_reg.status not in (
                RegistrationStatus.CANCELLED,
                RegistrationStatus.REJECTED,
            ):
                return Err(ValidationError("This player is already registered"))
            registration = existing_reg
            registration.team = team_id
            registration.jersey_number = jersey_number_value
            registration.jersey_name = jersey_name_value
            registration.status = RegistrationStatus.CONFIRMED
            registration.paid = True
            registration.amount_paid = 0
            registration.paid_at = now
        else:
            registration = PlayerRegistration(
                event=tournament_url,
                player=player_id,
                team=team_id,
                jersey_number=jersey_number_value,
                jersey_name=jersey_name_value,
                status=RegistrationStatus.CONFIRMED,
                paid=True,
                amount_paid=0,
                paid_at=now,
            )
            db.session.add(registration)
        if waiver_signature_data is not None:
            sig, sha, ts = waiver_signature_data
            registration.waiver_legal_name_signature = sig
            registration.waiver_legal_name_signature_sha256 = sha
            registration.waiver_signature_submitted_at = ts
        db.session.commit()
        return Ok(registration)

    @staticmethod
    @allow_Q
    def register_team_as_to(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        team_id: str,
        pseudonym: str = "",
        shortname: str | None = None,
    ) -> Result["TeamRegistration", ArctosError]:
        """Tournament-organizer-driven team registration.

        The actor must be a TO for ``tournament_url``. The target team must
        already exist. Returns the created or updated
        :class:`~app.models.registration.TeamRegistration` on success.
        """
        from models import Team, TeamRegistration, db

        tournament = RegistrationService._get_tournament(tournament_url).Q()

        from app.services._common import resolve_actor
        from app.services.permission_service import PermissionService

        actor = resolve_actor(actor_user_id, actor_user_type)
        if not PermissionService.is_tournament_organizer_of(tournament, actor):
            return Err(UnauthorizedError("Only tournament organizers can register teams"))

        team = Team.query.get(team_id)
        if team is None:
            return Err(ValidationError("Team not found"))

        pseudonym_value = (pseudonym or "").strip() or team.name
        normalised_shortname: str | None = (shortname or "").strip() or None
        pn_err = team_pseudonym_char_error(pseudonym_value)
        if pn_err:
            return Err(ValidationError(pn_err))

        from app.utils.helpers import get_registrable_config

        cfg = get_registrable_config(tournament)
        n_max = getattr(cfg, "n_max_teams", None) if cfg else None

        now = now_utc_naive()

        existing_reg = TeamRegistration.query.filter_by(event=tournament_url, team=team_id).first()
        if existing_reg and existing_reg.status != TeamRegistrationStatus.CANCELLED:
            return Err(ValidationError("This team is already registered"))

        if existing_reg:
            registration = existing_reg
        else:
            registration = TeamRegistration(
                event=tournament_url,
                team=team_id,
                pseudonym=pseudonym_value,
                shortname=normalised_shortname,
            )

        try:
            with db.session.begin_nested():
                registration.status = TeamRegistrationStatus.PENDING
                if not existing_reg:
                    db.session.add(registration)
                db.session.flush()

                if n_max is not None:
                    if getattr(tournament, "league_id", None):
                        confirmed_count = TeamRegistration.query.filter_by(
                            league_id=tournament.league_id,
                            status=TeamRegistrationStatus.CONFIRMED,
                        ).count()
                    else:
                        confirmed_count = TeamRegistration.query.filter_by(
                            event=tournament_url,
                            status=TeamRegistrationStatus.CONFIRMED,
                        ).count()
                    if confirmed_count >= n_max:
                        raise _CapExceeded(n_max=n_max, confirmed_count=confirmed_count)

                registration.pseudonym = pseudonym_value
                registration.shortname = normalised_shortname
                registration.status = TeamRegistrationStatus.CONFIRMED
                registration.paid = True
                registration.amount_paid = 0
                registration.paid_at = now
        except _CapExceeded as exc:
            return Err(ValidationError(f"Maximum teams reached ({exc.confirmed_count}/{exc.n_max})"))

        db.session.commit()
        return Ok(registration)

    @staticmethod
    @allow_Q
    def deregister_team(scope: Scope, team_id: str) -> Result[None, ArctosError]:
        from models import Match, TeamRegistration, PlayerRegistration, db

        match RegistrationService._load_scope_context(scope):
            case Ok(ctx):
                pass
            case Err(_) as err:
                return err

        cfg = ctx.cfg
        scope_label = "league" if scope.is_league else "tournament"

        if scope.is_league:
            rc = cfg
            if not (rc and rc.team_registration_open):
                return Err(ValidationError(f"Registration changes are locked for this {scope_label}"))
        else:
            RegistrationService._require_team_registration_open_for_deregister(ctx.parent).Q()

        if scope.is_league:
            team_registration = TeamRegistration.query.filter_by(
                league_id=scope.league_url, team=team_id, status=RegistrationStatus.CONFIRMED
            ).first()
        else:
            team_registration = TeamRegistration.query.filter_by(
                event=scope.event_url, team=team_id, status=RegistrationStatus.CONFIRMED
            ).first()
        if not team_registration:
            return Err(ValidationError(f"You are not registered for this {scope_label}"))

        if scope.is_league:
            from models import Tournament

            tournament_urls_subq = select(Tournament.url).where(Tournament.league_id == scope.league_url)
            in_progress = (
                Match.query.filter(
                    Match.event.in_(tournament_urls_subq),
                    Match.status == MatchStatus.IN_PROGRESS,
                )
                .filter((Match.team1 == team_id) | (Match.team2 == team_id))
                .first()
            )
        else:
            in_progress = (
                Match.query.filter_by(event=scope.event_url, status=MatchStatus.IN_PROGRESS)
                .filter((Match.team1 == team_id) | (Match.team2 == team_id))
                .first()
            )
        if in_progress:
            return Err(ValidationError("Cannot deregister once your team has played in a match that is in progress."))

        team_registration.status = RegistrationStatus.CANCELLED
        pseudonym = getattr(team_registration, "pseudonym", None)

        if scope.is_league:
            PlayerRegistration.query.filter_by(league_id=scope.league_url, team=team_id).update(
                {"status": RegistrationStatus.CANCELLED}
            )
            from app.services.bracket_cleanup_service import scrub_deleted_teams_for_league

            scrub_deleted_teams_for_league(
                scope.league_url,
                [team_id],
                pseudonyms=[pseudonym] if pseudonym else None,
            )
        else:
            affected_player_ids = [
                r.player for r in PlayerRegistration.query.filter_by(event=scope.event_url, team=team_id).all()
            ]

            PlayerRegistration.query.filter_by(event=scope.event_url, team=team_id).update(
                {"status": RegistrationStatus.CANCELLED}
            )

            # Cascade: hard-delete side-competition registrations for the players
            # whose event registration was just cancelled.
            from app.services.sidecomp_service import SideCompService

            SideCompService.cancel_players_in_event(scope.event_url, affected_player_ids)

            from app.services.bracket_cleanup_service import scrub_deleted_teams

            scrub_deleted_teams(
                scope.event_url,
                [team_id],
                pseudonyms=[pseudonym] if pseudonym else None,
            )

        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def deregister_player(scope: Scope, player_id: str) -> Result[None, ArctosError]:
        from models import Match, PlayerRegistration, db

        match RegistrationService._load_scope_context(scope):
            case Ok(ctx):
                pass
            case Err(_) as err:
                return err

        cfg = ctx.cfg
        scope_label = "league" if scope.is_league else "tournament"

        if scope.is_league:
            rc = cfg
            if not (rc and rc.player_registration_open):
                return Err(ValidationError(f"Registration changes are locked for this {scope_label}"))
        else:
            RegistrationService._require_player_registration_open_for_deregister(ctx.parent).Q()

        if scope.is_league:
            player_registration = (
                PlayerRegistration.query.filter_by(league_id=scope.league_url, player=player_id)
                .filter(
                    PlayerRegistration.status.in_(
                        [
                            RegistrationStatus.PENDING_TEAM_APPROVAL,
                            RegistrationStatus.CONFIRMED,
                        ]
                    )
                )
                .first()
            )
        else:
            player_registration = (
                PlayerRegistration.query.filter_by(event=scope.event_url, player=player_id)
                .filter(
                    PlayerRegistration.status.in_(
                        [
                            RegistrationStatus.PENDING_TEAM_APPROVAL,
                            RegistrationStatus.CONFIRMED,
                        ]
                    )
                )
                .first()
            )
        if not player_registration:
            return Err(ValidationError(f"You are not registered for this {scope_label}"))

        player_team = player_registration.team
        if player_team:
            if scope.is_league:
                from models import Tournament

                tournament_urls_subq = select(Tournament.url).where(Tournament.league_id == scope.league_url)
                in_progress = (
                    Match.query.filter(
                        Match.event.in_(tournament_urls_subq),
                        Match.status == MatchStatus.IN_PROGRESS,
                    )
                    .filter((Match.team1 == player_team) | (Match.team2 == player_team))
                    .first()
                )
            else:
                in_progress = (
                    Match.query.filter_by(event=scope.event_url, status=MatchStatus.IN_PROGRESS)
                    .filter((Match.team1 == player_team) | (Match.team2 == player_team))
                    .first()
                )
            if in_progress:
                return Err(ValidationError("Cannot deregister once you have played in a match that is in progress."))

        player_registration.status = RegistrationStatus.CANCELLED

        if not scope.is_league:
            # Cascade: hard-delete any side-competition registrations for this
            # (event, player) since side comp participation requires an active
            # event registration.
            from app.services.sidecomp_service import SideCompService

            SideCompService.cancel_player_registrations_in_event(scope.event_url, player_id)

        db.session.commit()
        return Ok(None)
