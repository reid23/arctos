"""Roster changes shared by team accounts and their managers."""

from sqlalchemy import update

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err, Ok
from app.exceptions import ArctosError, ValidationError
from app.services.permission_service import PermissionService
from app.services.registration_form_service import CONFIG_FIELDS, RegistrationFormService
from app.services.registration_service import RegistrationService
from models import PlayerRegistration, TeamRegistration, db


def _form_settings(config):
    """Identify the effective form configuration used for a response check."""
    return tuple(getattr(config, field, None) for field in ("id", *CONFIG_FIELDS))


def change_roster(scope, team_id: str, registration_id: int, action: str, *, actor):
    """Assign, approve, decline, or detach a registered player without changing their waiver or payment."""
    if not PermissionService.can_manage_team(team_id, actor):
        return Err(ArctosError("You do not manage this team.", status_code=403, public=True))
    context = RegistrationService._load_scope_context(scope)
    if context.is_err():
        return context
    ctx = context.unwrap()
    if action in {"add", "remove"} and (not ctx.cfg or not ctx.cfg.player_registration_open):
        return Err(ValidationError("Roster changes are closed. Contact the event organizer."))
    filters = {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}
    verified_player = None
    verified_settings = _form_settings(ctx.cfg)
    if action in {"approve", "decline"}:
        # Read only the identity and state, so Google is never contacted while
        # holding the team's write lock. The transaction rechecks them below.
        pending = (
            db.session.query(PlayerRegistration.player, PlayerRegistration.status)
            .filter_by(id=registration_id, team=team_id, **filters)
            .first()
        )
        if pending is None:
            return Err(ArctosError("Player registration not found.", status_code=404, public=True))
        if pending.status != RegistrationStatus.PENDING_TEAM_APPROVAL:
            return Err(ValidationError("This player request is no longer pending for this team."))
        requirement = RegistrationFormService.require_response(ctx.cfg, pending.player)
        if isinstance(requirement, Err):
            return requirement
        verified_player = pending.player
    try:
        with db.session.begin_nested():
            # Serialize roster writes for this team before counting the places left.
            locked = db.session.execute(
                update(TeamRegistration)
                .filter_by(**filters, team=team_id, status=TeamRegistrationStatus.CONFIRMED)
                .values(paid=TeamRegistration.paid)
            ).rowcount
            if not locked:
                raise ArctosError("The team must be registered first.", status_code=409, public=True)
            # The provider check can take several seconds. Reload cached models
            # and permissions before applying its result to the current roster.
            db.session.expire_all()
            if not PermissionService.can_manage_team(team_id, actor):
                raise ArctosError("You do not manage this team.", status_code=403, public=True)
            context = RegistrationService._load_scope_context(scope)
            if context.is_err():
                raise context.unwrap_err()
            ctx = context.unwrap()
            if action in {"approve", "decline"} and _form_settings(ctx.cfg) != verified_settings:
                raise ValidationError("Registration form settings changed. Try again.")
            if action in {"add", "remove"} and (not ctx.cfg or not ctx.cfg.player_registration_open):
                raise ValidationError("Roster changes are closed. Contact the event organizer.")
            reg = (
                PlayerRegistration.query.filter_by(id=registration_id, **filters)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if not reg:
                raise ArctosError("Player registration not found.", status_code=404, public=True)
            if action == "add":
                if reg.team is not None or reg.status != RegistrationStatus.CONFIRMED:
                    raise ValidationError("Choose an unattached player who is registered for this event.")
            elif action in {"approve", "decline"}:
                if (
                    reg.team != team_id
                    or reg.status != RegistrationStatus.PENDING_TEAM_APPROVAL
                    or reg.player != verified_player
                ):
                    raise ValidationError("This player request is no longer pending for this team.")
            elif action == "remove":
                if reg.team != team_id or reg.status != RegistrationStatus.CONFIRMED:
                    raise ValidationError("This player is no longer on this roster.")
            else:
                raise ValidationError("Choose a valid roster action.")
            if action in {"add", "approve"}:
                limit = ctx.cfg.max_team_size_roster if ctx.cfg else None
                size = PlayerRegistration.query.filter_by(
                    **filters, team=team_id, status=RegistrationStatus.CONFIRMED
                ).count()
                if limit and limit > 0 and size >= limit:
                    raise ValidationError("This team has reached the roster limit.")
                reg.team = team_id
            else:
                reg.team = None
            reg.status = RegistrationStatus.CONFIRMED
            db.session.flush()
        db.session.commit()
        return Ok(None)
    except ArctosError as error:
        db.session.rollback()
        return Err(error)
