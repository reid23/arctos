"""Claiming placeholder teams and building rosters in historical tournaments.

Organisers invite a real team to claim a placeholder team
(:class:`~app.models.historical.TeamClaimInvitation`). When the team accepts,
the placeholder is merged into it: every record the placeholder holds in its
tournament is re-pointed at the real team and the placeholder row is deleted,
so results, profiles, and brackets need no placeholder-aware read paths.

A team, or the tournament's organisers, can invite players onto the team's
roster for a historical tournament. The invitation is a
:class:`~app.models.registration.PlayerRegistration` in the ``INVITED``
status, which the player accepts (``CONFIRMED``) or declines (``REJECTED``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import ArctosError, NotFoundError, UnauthorizedError, ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from models import Player, PlayerRegistration, Team, TeamClaimInvitation, TeamRegistration, Tournament


def _roster_rows(event: str, player_id: str) -> list:
    from models import PlayerRegistration

    return PlayerRegistration.query.filter_by(event=event, player=player_id).all()


def _decline_other_open_roster_rows(event: str, player_id: str, keep_id: int) -> None:
    """Reject this player's other invitation and request for the event."""
    from app.domain.enums import RegistrationStatus

    for row in _roster_rows(event, player_id):
        if row.id != keep_id and row.status in (RegistrationStatus.INVITED, RegistrationStatus.REQUESTED):
            row.status = RegistrationStatus.REJECTED


def _apply_jersey(
    registration, player, jersey_name: str | None, jersey_number: str | None, *, fill_blanks: bool
) -> None:
    """Copy jersey fields onto a roster row.

    When *fill_blanks* is false, an empty request leaves that field as it was.
    """
    name = (jersey_name or "").strip()
    number = (jersey_number or "").strip()
    if name:
        registration.jersey_name = name
    elif fill_blanks:
        registration.jersey_name = player.name
    if number:
        registration.jersey_number = number
    elif fill_blanks:
        registration.jersey_number = None


@dataclass(frozen=True)
class HistoricalClaimsService:
    """Placeholder claims and roster invitations for historical tournaments."""

    @staticmethod
    @allow_Q
    def invite_team_to_claim(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        placeholder_team_id: str,
        invited_team_id: str,
    ) -> Result["TeamClaimInvitation", ArctosError]:
        """Invite a real team to claim a placeholder, replacing any open invitation for it."""
        from app.services.historical_records_service import HistoricalRecordsService
        from app.services.registration_resolver import team_registration_for_tournament
        from app.utils.datetime_helpers import now_utc_naive
        from models import Team, TeamClaimInvitation, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        placeholder = Team.query.get(placeholder_team_id)
        if placeholder is None or placeholder.placeholder_event != tournament.url:
            return Err(NotFoundError("Unclaimed team not found in this tournament"))
        invited = Team.query.get((invited_team_id or "").strip())
        if invited is None or invited.placeholder_event is not None:
            return Err(NotFoundError("Team not found"))
        if team_registration_for_tournament(tournament, invited.id) is not None:
            return Err(ValidationError(f"{invited.name} is already in this tournament"))

        invitation = TeamClaimInvitation.query.filter_by(placeholder_team=placeholder.id).first()
        if invitation is None:
            invitation = TeamClaimInvitation(placeholder_team=placeholder.id)
            db.session.add(invitation)
        invitation.invited_team = invited.id
        invitation.created_at = now_utc_naive()
        db.session.commit()
        return Ok(invitation)

    @staticmethod
    @allow_Q
    def cancel_claim_invitation(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        placeholder_team_id: str,
    ) -> Result[None, ArctosError]:
        """Withdraw the open claim invitation for a placeholder."""
        from app.services.historical_records_service import HistoricalRecordsService
        from models import Team, TeamClaimInvitation, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        placeholder = Team.query.get(placeholder_team_id)
        if placeholder is None or placeholder.placeholder_event != tournament.url:
            return Err(NotFoundError("Unclaimed team not found in this tournament"))
        invitation = TeamClaimInvitation.query.filter_by(placeholder_team=placeholder.id).first()
        if invitation is None:
            return Err(NotFoundError("This team has no open invitation"))
        db.session.delete(invitation)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def respond_to_claim(invitation_id: int, *, team_id: str, accept: bool) -> Result["Tournament", ArctosError]:
        """Accept or decline a claim invitation sent to *team_id*.

        Accepting merges the placeholder into the team; see the module docstring.

        Returns:
            ``Ok(tournament)`` for the placeholder's tournament.
        """
        from app.services.registration_resolver import team_registration_for_tournament
        from models import Team, TeamClaimInvitation, Tournament, db

        invitation = TeamClaimInvitation.query.get(invitation_id)
        if invitation is None or invitation.invited_team != team_id:
            return Err(NotFoundError("Invitation not found"))
        placeholder = Team.query.get(invitation.placeholder_team)
        tournament = Tournament.query.get(placeholder.placeholder_event)
        if not accept:
            db.session.delete(invitation)
            db.session.commit()
            return Ok(tournament)
        if team_registration_for_tournament(tournament, team_id) is not None:
            return Err(ValidationError("Your team is already in this tournament"))
        HistoricalClaimsService._merge_placeholder(placeholder, team_id)
        db.session.commit()
        return Ok(tournament)

    @staticmethod
    def _merge_placeholder(placeholder: "Team", team_id: str) -> None:
        """Re-point every row *placeholder* holds in its tournament at *team_id*, then delete it."""
        from sqlalchemy import select

        from models import (
            BracketLabeledTeam,
            Match,
            MatchReferee,
            PlayerRegistration,
            Tag,
            Team,
            TeamClaimInvitation,
            TeamClaimRequest,
            TeamRegistration,
            db,
        )

        event = placeholder.placeholder_event
        old_id = placeholder.id
        for column in (Match.team1, Match.team2, Match.team1_initial, Match.team2_initial):
            Match.query.filter(Match.event == event, column == old_id).update(
                {column: team_id}, synchronize_session=False
            )
        event_match_uuids = select(Match.uuid).where(Match.event == event)
        for column in (MatchReferee.team_id, MatchReferee.initial):
            MatchReferee.query.filter(MatchReferee.match_uuid.in_(event_match_uuids), column == old_id).update(
                {column: team_id}, synchronize_session=False
            )
        for model in (TeamRegistration, PlayerRegistration, Tag, BracketLabeledTeam):
            model.query.filter_by(event=event, team=old_id).update({"team": team_id}, synchronize_session=False)

        event_placeholder_ids = select(Team.id).where(Team.placeholder_event == event)
        TeamClaimRequest.query.filter(
            (TeamClaimRequest.placeholder_team == old_id)
            | (
                (TeamClaimRequest.requesting_team == team_id)
                & TeamClaimRequest.placeholder_team.in_(event_placeholder_ids)
            )
        ).delete(synchronize_session=False)
        TeamClaimInvitation.query.filter(
            (TeamClaimInvitation.placeholder_team == old_id)
            | (
                (TeamClaimInvitation.invited_team == team_id)
                & TeamClaimInvitation.placeholder_team.in_(event_placeholder_ids)
            )
        ).delete(synchronize_session=False)
        db.session.flush()
        db.session.delete(placeholder)

    @staticmethod
    @allow_Q
    def _roster_access(
        tournament_url: str, team_id: str, actor_user_id: str, actor_user_type: str
    ) -> Result[tuple["Tournament", "TeamRegistration"], ArctosError]:
        """Load a historical tournament and team, requiring the actor to be that team or an organiser."""
        from app.domain.enums import UserType
        from app.services._common import get_tournament_or_err, resolve_actor
        from app.services.permission_service import PermissionService
        from app.services.registration_resolver import team_registration_for_tournament

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        registration = team_registration_for_tournament(tournament, team_id)
        if registration is None:
            return Err(NotFoundError("Team not found in this tournament"))
        is_the_team = actor_user_type == UserType.TEAM.value and actor_user_id == team_id
        actor = resolve_actor(actor_user_id, actor_user_type)
        if not is_the_team and not PermissionService.is_tournament_organizer_of(tournament, actor):
            return Err(UnauthorizedError("Only this team or the tournament organizers can manage this roster"))
        return Ok((tournament, registration))

    @staticmethod
    @allow_Q
    def roster(
        tournament_url: str,
        team_id: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
    ) -> Result[
        tuple["Tournament", "TeamRegistration", list[tuple["PlayerRegistration", "Player | None"]]], ArctosError
    ]:
        """A team's roster for a historical tournament: confirmed players and open invitations."""
        from app.domain.enums import RegistrationStatus
        from models import Player, PlayerRegistration

        tournament, team_registration = HistoricalClaimsService._roster_access(
            tournament_url, team_id, actor_user_id, actor_user_type
        ).Q()
        registrations = (
            PlayerRegistration.query.filter_by(event=tournament.url, team=team_id)
            .filter(
                PlayerRegistration.status.in_(
                    [RegistrationStatus.CONFIRMED, RegistrationStatus.INVITED, RegistrationStatus.REQUESTED]
                )
            )
            .all()
        )
        player_ids = [registration.player for registration in registrations]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
        rows = [(registration, players_by_id.get(registration.player)) for registration in registrations]
        status_order = {
            RegistrationStatus.CONFIRMED: 0,
            RegistrationStatus.INVITED: 1,
            RegistrationStatus.REQUESTED: 2,
        }
        rows.sort(key=lambda row: (status_order.get(row[0].status, 3), (row[0].jersey_name or "").casefold()))
        return Ok((tournament, team_registration, rows))

    @staticmethod
    @allow_Q
    def invite_player(
        tournament_url: str,
        team_id: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
        jersey_name: str | None,
        jersey_number: str | None,
    ) -> Result["PlayerRegistration", ArctosError]:
        """Invite a player onto a team's roster for a historical tournament."""
        from app.domain.enums import RegistrationStatus
        from app.utils.datetime_helpers import now_utc_naive
        from models import Player, PlayerRegistration, db

        tournament, _ = HistoricalClaimsService._roster_access(
            tournament_url, team_id, actor_user_id, actor_user_type
        ).Q()
        player = Player.query.get((player_id or "").strip())
        if player is None:
            return Err(NotFoundError("Player not found"))
        rows = _roster_rows(tournament.url, player.id)
        occupied = next(
            (row for row in rows if row.status in (RegistrationStatus.CONFIRMED, RegistrationStatus.INVITED)),
            None,
        )
        if occupied is not None:
            if occupied.team == team_id:
                return Err(ValidationError(f"{player.name} is already on this roster"))
            return Err(ValidationError(f"{player.name} is already on another team's roster for this tournament"))
        registration = next(
            (row for row in rows if row.status == RegistrationStatus.REQUESTED and row.team == team_id), None
        )
        if registration is None:
            registration = next(
                (row for row in rows if row.status in (RegistrationStatus.REJECTED, RegistrationStatus.CANCELLED)),
                None,
            )
        if registration is None:
            registration = PlayerRegistration(event=tournament.url, player=player.id)
            db.session.add(registration)
        registration.team = team_id
        registration.status = RegistrationStatus.INVITED
        registration.jersey_name = (jersey_name or "").strip() or player.name
        registration.jersey_number = (jersey_number or "").strip() or None
        registration.registered_at = now_utc_naive()
        registration.paid = True
        registration.amount_paid = 0
        db.session.commit()
        return Ok(registration)

    @staticmethod
    @allow_Q
    def remove_player(
        tournament_url: str,
        team_id: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
    ) -> Result[None, ArctosError]:
        """Take a player off a team's roster, or withdraw their invitation."""
        from app.domain.enums import RegistrationStatus
        from models import PlayerRegistration, db

        tournament, _ = HistoricalClaimsService._roster_access(
            tournament_url, team_id, actor_user_id, actor_user_type
        ).Q()
        registration = (
            PlayerRegistration.query.filter_by(event=tournament.url, team=team_id, player=player_id)
            .filter(PlayerRegistration.status.in_([RegistrationStatus.CONFIRMED, RegistrationStatus.INVITED]))
            .first()
        )
        if registration is None:
            return Err(NotFoundError("Player is not on this roster"))
        registration.status = RegistrationStatus.CANCELLED
        db.session.commit()
        return Ok(None)

    @staticmethod
    def respond_to_roster_invitation(
        registration_id: int, *, player_id: str, accept: bool
    ) -> Result["PlayerRegistration", ArctosError]:
        """Accept or decline an invitation onto a historical tournament roster."""
        from app.domain.enums import RegistrationStatus
        from models import PlayerRegistration, db

        registration = PlayerRegistration.query.get(registration_id)
        if (
            registration is None
            or registration.player != player_id
            or registration.status != RegistrationStatus.INVITED
        ):
            return Err(NotFoundError("Invitation not found"))
        if accept:
            _decline_other_open_roster_rows(registration.event, registration.player, registration.id)
            db.session.flush()
            registration.status = RegistrationStatus.CONFIRMED
        else:
            registration.status = RegistrationStatus.REJECTED
        db.session.commit()
        return Ok(registration)

    @staticmethod
    @allow_Q
    def unclaim_team(
        tournament_url: str, team_id: str, *, actor_user_id: str, actor_user_type: str
    ) -> Result[None, ArctosError]:
        """Give a claimed spot back to a new placeholder.

        This tournament's matches, referees, roster, and registration move onto
        the placeholder. The team account is left as it was. The actor must be
        that team or an organiser.
        """
        import uuid

        from sqlalchemy import select

        from app.domain.enums import UserType
        from app.services._common import get_tournament_or_err, resolve_actor
        from app.services.historical_records_service import PLACEHOLDER_ID_PREFIX
        from app.services.permission_service import PermissionService
        from app.services.registration_resolver import team_registration_for_tournament
        from models import (
            BracketLabeledTeam,
            Match,
            MatchReferee,
            PlayerRegistration,
            Tag,
            Team,
            TeamRegistration,
            db,
        )

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        registration = team_registration_for_tournament(tournament, team_id)
        team = Team.query.get(team_id)
        if registration is None or team is None or team.placeholder_event:
            return Err(ValidationError("This spot is not claimed by a team account"))
        is_the_team = actor_user_type == UserType.TEAM.value and actor_user_id == team_id
        actor = resolve_actor(actor_user_id, actor_user_type)
        if not is_the_team and not PermissionService.is_tournament_organizer_of(tournament, actor):
            return Err(UnauthorizedError("Only this team or the tournament organizers can un-claim this spot"))

        placeholder = Team(
            id=f"{PLACEHOLDER_ID_PREFIX}{uuid.uuid4().hex[:12]}",
            name=registration.pseudonym or team.name,
            placeholder_event=tournament.url,
        )
        db.session.add(placeholder)
        db.session.flush()

        event = tournament.url
        for column in (Match.team1, Match.team2, Match.team1_initial, Match.team2_initial):
            Match.query.filter(Match.event == event, column == team_id).update(
                {column: placeholder.id}, synchronize_session=False
            )
        event_match_uuids = select(Match.uuid).where(Match.event == event)
        for column in (MatchReferee.team_id, MatchReferee.initial):
            MatchReferee.query.filter(MatchReferee.match_uuid.in_(event_match_uuids), column == team_id).update(
                {column: placeholder.id}, synchronize_session=False
            )
        for model in (TeamRegistration, PlayerRegistration, Tag, BracketLabeledTeam):
            model.query.filter_by(event=event, team=team_id).update({"team": placeholder.id}, synchronize_session=False)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def unclaim_player(tournament_url: str, player_id: str) -> Result[None, ArctosError]:
        """Remove a confirmed player from a historical tournament's roster."""
        from app.domain.enums import RegistrationStatus
        from app.services._common import get_tournament_or_err
        from models import PlayerRegistration, db

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        registration = PlayerRegistration.query.filter_by(
            event=tournament.url, player=player_id, status=RegistrationStatus.CONFIRMED
        ).first()
        if registration is None:
            return Err(ValidationError("You are not on a roster for this tournament"))
        registration.status = RegistrationStatus.CANCELLED
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def request_claim(tournament_url: str, *, team_id: str, placeholder_team_id: str) -> Result[None, ArctosError]:
        """Ask to claim a placeholder. Replaces this team's other request for the tournament."""
        from sqlalchemy import select

        from app.services._common import get_tournament_or_err
        from app.services.registration_resolver import team_registration_for_tournament
        from app.utils.datetime_helpers import now_utc_naive
        from models import Team, TeamClaimRequest, db

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        if not tournament.published:
            return Err(ValidationError("This tournament is not published yet"))
        team = Team.query.get(team_id)
        if team is None or team.placeholder_event is not None:
            return Err(ValidationError("Only a team account can request a spot"))
        if team_registration_for_tournament(tournament, team_id) is not None:
            return Err(ValidationError("Your team is already in this tournament"))
        placeholder = Team.query.get((placeholder_team_id or "").strip())
        if placeholder is None or placeholder.placeholder_event != tournament.url:
            return Err(NotFoundError("Unclaimed team not found in this tournament"))

        event_placeholders = select(Team.id).where(Team.placeholder_event == tournament.url)
        TeamClaimRequest.query.filter(
            TeamClaimRequest.requesting_team == team_id,
            TeamClaimRequest.placeholder_team.in_(event_placeholders),
        ).delete(synchronize_session=False)
        db.session.add(
            TeamClaimRequest(
                placeholder_team=placeholder.id,
                requesting_team=team_id,
                created_at=now_utc_naive(),
            )
        )
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def withdraw_claim_request(tournament_url: str, *, team_id: str) -> Result[None, ArctosError]:
        """Drop the signed-in team's open claim request for this tournament."""
        from sqlalchemy import select

        from app.services._common import get_tournament_or_err
        from models import Team, TeamClaimRequest, db

        tournament = get_tournament_or_err(tournament_url).Q()
        event_placeholders = select(Team.id).where(Team.placeholder_event == tournament.url)
        TeamClaimRequest.query.filter(
            TeamClaimRequest.requesting_team == team_id,
            TeamClaimRequest.placeholder_team.in_(event_placeholders),
        ).delete(synchronize_session=False)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def decide_claim_request(
        tournament_url: str, request_id: int, *, actor_user_id: str, actor_user_type: str, approve: bool
    ) -> Result[None, ArctosError]:
        """Organiser: approve a claim request by merging, or decline it."""
        from app.services.historical_records_service import HistoricalRecordsService
        from app.services.registration_resolver import team_registration_for_tournament
        from models import Team, TeamClaimRequest, db

        tournament = HistoricalRecordsService.organizer_tournament(tournament_url, actor_user_id, actor_user_type).Q()
        claim_request = TeamClaimRequest.query.get(request_id)
        placeholder = Team.query.get(claim_request.placeholder_team) if claim_request else None
        if claim_request is None or placeholder is None or placeholder.placeholder_event != tournament.url:
            return Err(NotFoundError("Request not found"))
        if not approve:
            db.session.delete(claim_request)
            db.session.commit()
            return Ok(None)
        requesting_team_id = claim_request.requesting_team
        if team_registration_for_tournament(tournament, requesting_team_id) is not None:
            return Err(ValidationError("That team is already in this tournament"))
        db.session.delete(claim_request)
        db.session.flush()
        HistoricalClaimsService._merge_placeholder(placeholder, requesting_team_id)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def request_roster(
        tournament_url: str,
        *,
        player_id: str,
        team_id: str,
        jersey_name: str | None,
        jersey_number: str | None,
    ) -> Result[None, ArctosError]:
        """Ask to join a team's historical roster.

        One open request per player. Asking to join the team that already
        invited this player accepts that invitation and uses the jersey from
        the request where it was filled in. Asking to join a different team
        keeps the invitation; accepting either one declines the other.
        """
        from app.domain.enums import RegistrationStatus
        from app.services._common import get_tournament_or_err
        from app.services.registration_resolver import team_registration_for_tournament
        from app.utils.datetime_helpers import now_utc_naive
        from models import Player, PlayerRegistration, db

        tournament = get_tournament_or_err(tournament_url).Q()
        if not tournament.historical:
            return Err(ValidationError("This is not a historical tournament"))
        if not tournament.published:
            return Err(ValidationError("This tournament is not published yet"))
        if team_registration_for_tournament(tournament, (team_id or "").strip()) is None:
            return Err(NotFoundError("Team not found in this tournament"))
        player = Player.query.get(player_id)
        if player is None:
            return Err(NotFoundError("Player not found"))
        team_id = (team_id or "").strip()
        rows = _roster_rows(tournament.url, player.id)
        if any(row.status == RegistrationStatus.CONFIRMED for row in rows):
            return Err(ValidationError("You are already on a roster for this tournament"))
        invited_here = next(
            (row for row in rows if row.status == RegistrationStatus.INVITED and row.team == team_id),
            None,
        )
        if invited_here is not None:
            _apply_jersey(invited_here, player, jersey_name, jersey_number, fill_blanks=False)
            _decline_other_open_roster_rows(tournament.url, player.id, invited_here.id)
            db.session.flush()
            invited_here.status = RegistrationStatus.CONFIRMED
            db.session.commit()
            return Ok(None)
        requested_here = next(
            (row for row in rows if row.status == RegistrationStatus.REQUESTED and row.team == team_id),
            None,
        )
        if requested_here is not None:
            _apply_jersey(requested_here, player, jersey_name, jersey_number, fill_blanks=True)
            db.session.commit()
            return Ok(None)
        registration = next((row for row in rows if row.status == RegistrationStatus.REQUESTED), None)
        if registration is None:
            registration = next(
                (row for row in rows if row.status in (RegistrationStatus.REJECTED, RegistrationStatus.CANCELLED)),
                None,
            )
        if registration is None:
            registration = PlayerRegistration(event=tournament.url, player=player.id)
            db.session.add(registration)
        registration.team = team_id
        registration.status = RegistrationStatus.REQUESTED
        _apply_jersey(registration, player, jersey_name, jersey_number, fill_blanks=True)
        registration.registered_at = now_utc_naive()
        registration.paid = True
        registration.amount_paid = 0
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def withdraw_roster_request(tournament_url: str, *, player_id: str) -> Result[None, ArctosError]:
        """Drop the signed-in player's open roster request."""
        from app.domain.enums import RegistrationStatus
        from app.services._common import get_tournament_or_err
        from models import PlayerRegistration, db

        tournament = get_tournament_or_err(tournament_url).Q()
        registration = PlayerRegistration.query.filter_by(
            event=tournament.url, player=player_id, status=RegistrationStatus.REQUESTED
        ).first()
        if registration is not None:
            registration.status = RegistrationStatus.REJECTED
            db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def decide_roster_request(
        tournament_url: str,
        team_id: str,
        registration_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
        approve: bool,
    ) -> Result[None, ArctosError]:
        """Approve or decline a roster request.

        Organisers can decide any request. The team can decide requests for
        its own spot once that spot is claimed.
        """
        from app.domain.enums import RegistrationStatus
        from models import PlayerRegistration, db

        HistoricalClaimsService._roster_access(tournament_url, team_id, actor_user_id, actor_user_type).Q()
        registration = PlayerRegistration.query.get(registration_id)
        if (
            registration is None
            or registration.event != tournament_url
            or registration.team != team_id
            or registration.status != RegistrationStatus.REQUESTED
        ):
            return Err(NotFoundError("Request not found"))
        if approve:
            _decline_other_open_roster_rows(registration.event, registration.player, registration.id)
            db.session.flush()
            registration.status = RegistrationStatus.CONFIRMED
        else:
            registration.status = RegistrationStatus.REJECTED
        db.session.commit()
        return Ok(None)

    @staticmethod
    def pending_invitations(user_id: str, user_type: str, tournament_url: str | None = None) -> dict:
        """Open invitations and claim requests for the signed-in user.

        Invitations and requests are included only once their tournament is
        published. Organisers still see them in the records editor before that.

        Args:
            user_id: The signed-in user.
            user_type: ``"player"`` or ``"team"``.
            tournament_url: When given, only rows for this tournament.

        Returns:
            Team claim invitations, roster invitations, the user's own
            outgoing requests, and roster requests waiting on this team.
        """
        from app.domain.enums import RegistrationStatus, UserType
        from models import (
            Player,
            PlayerRegistration,
            Team,
            TeamClaimInvitation,
            TeamClaimRequest,
            TeamRegistration,
            Tournament,
        )

        def tournament_json(tournament) -> dict:
            return {
                "url": tournament.url,
                "name": tournament.name,
                "start_date": tournament.start_date.isoformat() if tournament.start_date else None,
            }

        team_claims = []
        if user_type == UserType.TEAM.value:
            query = (
                TeamClaimInvitation.query.join(Team, Team.id == TeamClaimInvitation.placeholder_team)
                .join(Tournament, Tournament.url == Team.placeholder_event)
                .filter(TeamClaimInvitation.invited_team == user_id, Tournament.published.is_(True))
                .with_entities(TeamClaimInvitation, Team, Tournament)
            )
            if tournament_url:
                query = query.filter(Tournament.url == tournament_url)
            for invitation, placeholder, tournament in query.order_by(TeamClaimInvitation.created_at).all():
                team_claims.append(
                    {
                        "id": invitation.id,
                        "placeholder_name": placeholder.name,
                        "tournament": tournament_json(tournament),
                    }
                )

        roster_invitations = []
        if user_type == UserType.PLAYER.value:
            query = (
                PlayerRegistration.query.join(Tournament, Tournament.url == PlayerRegistration.event)
                .join(
                    TeamRegistration,
                    (TeamRegistration.event == PlayerRegistration.event)
                    & (TeamRegistration.team == PlayerRegistration.team),
                )
                .filter(
                    PlayerRegistration.player == user_id,
                    PlayerRegistration.status == RegistrationStatus.INVITED,
                    Tournament.published.is_(True),
                )
                .with_entities(PlayerRegistration, TeamRegistration, Tournament)
            )
            if tournament_url:
                query = query.filter(Tournament.url == tournament_url)
            for registration, team_registration, tournament in query.order_by(PlayerRegistration.registered_at).all():
                roster_invitations.append(
                    {
                        "id": registration.id,
                        "team_id": registration.team,
                        "team_name": team_registration.pseudonym,
                        "jersey_name": registration.jersey_name,
                        "jersey_number": registration.jersey_number,
                        "tournament": tournament_json(tournament),
                    }
                )

        outgoing_team_requests = []
        incoming_roster_requests = []
        if user_type == UserType.TEAM.value:
            request_query = (
                TeamClaimRequest.query.join(Team, Team.id == TeamClaimRequest.placeholder_team)
                .join(Tournament, Tournament.url == Team.placeholder_event)
                .filter(TeamClaimRequest.requesting_team == user_id, Tournament.published.is_(True))
                .with_entities(TeamClaimRequest, Team, Tournament)
            )
            if tournament_url:
                request_query = request_query.filter(Tournament.url == tournament_url)
            for claim_request, placeholder, tournament in request_query.order_by(TeamClaimRequest.created_at).all():
                outgoing_team_requests.append(
                    {
                        "id": claim_request.id,
                        "placeholder_team_id": placeholder.id,
                        "placeholder_name": placeholder.name,
                        "tournament": tournament_json(tournament),
                    }
                )

            incoming_query = (
                PlayerRegistration.query.join(Tournament, Tournament.url == PlayerRegistration.event)
                .join(Player, Player.id == PlayerRegistration.player)
                .join(
                    TeamRegistration,
                    (TeamRegistration.event == PlayerRegistration.event)
                    & (TeamRegistration.team == PlayerRegistration.team),
                )
                .filter(
                    PlayerRegistration.team == user_id,
                    PlayerRegistration.status == RegistrationStatus.REQUESTED,
                    Tournament.published.is_(True),
                )
                .with_entities(PlayerRegistration, Player, TeamRegistration, Tournament)
            )
            if tournament_url:
                incoming_query = incoming_query.filter(Tournament.url == tournament_url)
            for registration, player, team_registration, tournament in incoming_query.order_by(
                PlayerRegistration.registered_at
            ).all():
                incoming_roster_requests.append(
                    {
                        "id": registration.id,
                        "player_id": player.id,
                        "player_name": player.name,
                        "team_id": registration.team,
                        "team_name": team_registration.pseudonym,
                        "jersey_name": registration.jersey_name,
                        "jersey_number": registration.jersey_number,
                        "tournament": tournament_json(tournament),
                    }
                )

        outgoing_roster_requests = []
        if user_type == UserType.PLAYER.value:
            outgoing_query = (
                PlayerRegistration.query.join(Tournament, Tournament.url == PlayerRegistration.event)
                .join(
                    TeamRegistration,
                    (TeamRegistration.event == PlayerRegistration.event)
                    & (TeamRegistration.team == PlayerRegistration.team),
                )
                .filter(
                    PlayerRegistration.player == user_id,
                    PlayerRegistration.status == RegistrationStatus.REQUESTED,
                    Tournament.published.is_(True),
                )
                .with_entities(PlayerRegistration, TeamRegistration, Tournament)
            )
            if tournament_url:
                outgoing_query = outgoing_query.filter(Tournament.url == tournament_url)
            for registration, team_registration, tournament in outgoing_query.order_by(
                PlayerRegistration.registered_at
            ).all():
                outgoing_roster_requests.append(
                    {
                        "id": registration.id,
                        "team_id": registration.team,
                        "team_name": team_registration.pseudonym,
                        "jersey_name": registration.jersey_name,
                        "jersey_number": registration.jersey_number,
                        "tournament": tournament_json(tournament),
                    }
                )

        return {
            "team_claims": team_claims,
            "roster_invitations": roster_invitations,
            "outgoing_team_requests": outgoing_team_requests,
            "outgoing_roster_requests": outgoing_roster_requests,
            "incoming_roster_requests": incoming_roster_requests,
        }
