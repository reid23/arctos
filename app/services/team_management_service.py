"""Roster changes shared by team accounts and their managers."""

from sqlalchemy import update

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err, Ok
from app.exceptions import ArctosError, ValidationError
from app.services.registration_service import RegistrationService
from models import PlayerRegistration, TeamRegistration, db


def change_roster(scope, team_id: str, registration_id: int, action: str):
    """Assign, approve, decline, or detach a registered player without changing their waiver or payment."""
    context = RegistrationService._load_scope_context(scope)
    if context.is_err():
        return context
    ctx = context.unwrap()
    if action in {"add", "remove"} and (not ctx.cfg or not ctx.cfg.player_registration_open):
        return Err(ValidationError("Roster changes are closed. Contact the event organizer."))
    filters = {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}
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
            reg = PlayerRegistration.query.filter_by(id=registration_id, **filters).with_for_update().first()
            if not reg:
                raise ArctosError("Player registration not found.", status_code=404, public=True)
            if action == "add":
                if reg.team is not None or reg.status != RegistrationStatus.CONFIRMED:
                    raise ValidationError("Choose an unattached player who is registered for this event.")
            elif action in {"approve", "decline"}:
                if reg.team != team_id or reg.status != RegistrationStatus.PENDING_TEAM_APPROVAL:
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
