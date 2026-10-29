"""Historical tournament routes: the records editor, placeholder claims, and rosters.

Every records-editor mutation responds with the refreshed editor state, so
the SPA can redraw from one round trip; saving a match can re-resolve the
teams of other matches that reference it.
"""

from flask import Blueprint, g, request
from flask_login import current_user, login_required  # type: ignore[import-untyped]

from app.serializers.tournament_serializer import tournament_to_dict
from app.services._common import current_user_type
from app.services.historical_claims_service import HistoricalClaimsService
from app.services.historical_records_service import HistoricalRecordsService
from app.utils.decorators import require_json_body
from app.utils.result_helpers import json_from_result
from app.utils.responses import json_error, json_success
from app.utils.user_helpers import is_player, is_team
from models import Tournament, db

bp = Blueprint("historical", __name__, url_prefix="/_api")


def _actor() -> dict:
    return {"actor_user_id": current_user.id, "actor_user_type": current_user_type()}


def _editor_payload(tournament) -> dict:
    return {"tournament": tournament_to_dict(tournament), **HistoricalRecordsService.editor_state(tournament)}


def _editor_response(tournament_url: str, result):
    return json_from_result(result, ok_to_payload=lambda _: _editor_payload(Tournament.query.get(tournament_url)))


@bp.route("/create-historical-tournament", methods=["POST"])
@login_required
@require_json_body()
def create_historical_tournament():
    """Create a historical tournament, with its attending teams as placeholders."""
    data = g.json_body
    result = HistoricalRecordsService.create_tournament(
        **_actor(),
        name=data.get("name", ""),
        url=data.get("url", ""),
        location=data.get("location"),
        start_date=data.get("start_date", ""),
        end_date=data.get("end_date"),
        team_names=data.get("teams"),
    )
    return json_from_result(result, ok_to_payload=lambda tournament: {"url": tournament.url})


@bp.route("/tournaments/<tournament_url>/records", methods=["GET"])
@login_required
def records(tournament_url: str):
    """TO-only: the records editor state."""
    result = HistoricalRecordsService.organizer_tournament(tournament_url, current_user.id, current_user_type())
    return json_from_result(result, ok_to_payload=_editor_payload)


@bp.route("/tournaments/<tournament_url>/records/details", methods=["PUT"])
@login_required
@require_json_body()
def update_details(tournament_url: str):
    """TO-only: name, location, dates, description, and whether the record is published."""
    data = g.json_body
    result = HistoricalRecordsService.update_details(
        tournament_url,
        **_actor(),
        name=data.get("name", ""),
        location=data.get("location"),
        start_date=data.get("start_date", ""),
        end_date=data.get("end_date"),
        about=data.get("about"),
        published=bool(data.get("published")),
    )
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams", methods=["POST"])
@login_required
@require_json_body()
def add_teams(tournament_url: str):
    """TO-only: add placeholder teams.

    Body: ``{"names": [...]}`` or ``{"teams": [{"name", "shortname"?}, ...]}``.
    """
    payload = g.json_body.get("teams")
    if payload is None:
        payload = g.json_body.get("names")
    result = HistoricalRecordsService.add_teams(tournament_url, **_actor(), names=payload)
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams/<team_id>", methods=["PUT"])
@login_required
@require_json_body()
def rename_team(tournament_url: str, team_id: str):
    """TO-only: rename a team within this tournament.

    Body: ``{"name": ..., "shortname"?: ...}``. Omitting ``shortname`` leaves it.
    """
    result = HistoricalRecordsService.rename_team(
        tournament_url,
        **_actor(),
        team_id=team_id,
        name=g.json_body.get("name", ""),
        shortname=g.json_body.get("shortname"),
        update_shortname="shortname" in g.json_body,
    )
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams/<team_id>", methods=["DELETE"])
@login_required
def remove_team(tournament_url: str, team_id: str):
    """TO-only: remove a team that is in no match."""
    result = HistoricalRecordsService.remove_team(tournament_url, **_actor(), team_id=team_id)
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams/<team_id>/claim-invitation", methods=["PUT"])
@login_required
@require_json_body()
def invite_team_to_claim(tournament_url: str, team_id: str):
    """TO-only: invite a real team to claim a placeholder. Body: ``{"team_id": ...}``."""
    result = HistoricalClaimsService.invite_team_to_claim(
        tournament_url,
        **_actor(),
        placeholder_team_id=team_id,
        invited_team_id=g.json_body.get("team_id", ""),
    )
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams/<team_id>/claim-invitation", methods=["DELETE"])
@login_required
def cancel_claim_invitation(tournament_url: str, team_id: str):
    """TO-only: withdraw a placeholder's claim invitation."""
    result = HistoricalClaimsService.cancel_claim_invitation(tournament_url, **_actor(), placeholder_team_id=team_id)
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/records/teams/<team_id>/unclaim", methods=["POST"])
@login_required
def unclaim_team(tournament_url: str, team_id: str):
    """TO or the claimed team: put this spot back on a placeholder."""
    result = HistoricalClaimsService.unclaim_team(tournament_url, team_id, **_actor())
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/claim-requests/<int:request_id>/approve", methods=["POST"])
@login_required
def approve_claim_request(tournament_url: str, request_id: int):
    """TO-only: merge a placeholder into the team that asked for it."""
    result = HistoricalClaimsService.decide_claim_request(tournament_url, request_id, **_actor(), approve=True)
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/claim-requests/<int:request_id>/decline", methods=["POST"])
@login_required
def decline_claim_request(tournament_url: str, request_id: int):
    """TO-only: decline a team's claim request."""
    result = HistoricalClaimsService.decide_claim_request(tournament_url, request_id, **_actor(), approve=False)
    return _editor_response(tournament_url, result)


@bp.route("/tournaments/<tournament_url>/claim-requests", methods=["POST"])
@login_required
@require_json_body()
def request_claim(tournament_url: str):
    """Team: ask to claim a placeholder. Body: ``{"placeholder_team_id": ...}``."""
    if not is_team(current_user):
        return json_error("Only teams can claim a spot", status_code=403)
    result = HistoricalClaimsService.request_claim(
        tournament_url, team_id=current_user.id, placeholder_team_id=g.json_body.get("placeholder_team_id", "")
    )
    return json_from_result(result, ok_to_payload=lambda _: {})


@bp.route("/tournaments/<tournament_url>/claim-requests/mine", methods=["DELETE"])
@login_required
def withdraw_claim_request(tournament_url: str):
    """Team: withdraw its open claim request."""
    if not is_team(current_user):
        return json_error("Only teams can withdraw a claim request", status_code=403)
    result = HistoricalClaimsService.withdraw_claim_request(tournament_url, team_id=current_user.id)
    return json_from_result(result, ok_to_payload=lambda _: {})


@bp.route("/tournaments/<tournament_url>/roster-requests", methods=["POST"])
@login_required
@require_json_body()
def request_roster(tournament_url: str):
    """Player: ask to join a roster. Body: ``{"team_id", "jersey_name"?, "jersey_number"?}``."""
    if not is_player(current_user):
        return json_error("Only players can request a roster spot", status_code=403)
    data = g.json_body
    result = HistoricalClaimsService.request_roster(
        tournament_url,
        player_id=current_user.id,
        team_id=data.get("team_id", ""),
        jersey_name=data.get("jersey_name"),
        jersey_number=data.get("jersey_number"),
    )
    return json_from_result(result, ok_to_payload=lambda _: {})


@bp.route("/tournaments/<tournament_url>/roster-requests/mine", methods=["DELETE"])
@login_required
def withdraw_roster_request(tournament_url: str):
    """Player: withdraw an open roster request."""
    if not is_player(current_user):
        return json_error("Only players can withdraw a roster request", status_code=403)
    result = HistoricalClaimsService.withdraw_roster_request(tournament_url, player_id=current_user.id)
    return json_from_result(result, ok_to_payload=lambda _: {})


def _save_match(tournament_url: str, match_uuid: str | None):
    data = g.json_body
    result = HistoricalRecordsService.save_match(
        tournament_url,
        **_actor(),
        match_uuid=match_uuid,
        name=data.get("name", ""),
        team1=data.get("team1", ""),
        team2=data.get("team2", ""),
        referees=data.get("referees"),
        winner=data.get("winner", ""),
        points=data.get("points"),
        set_type=data.get("set_type"),
    )
    return json_from_result(
        result,
        ok_to_payload=lambda match: {
            "match_uuid": match.uuid,
            **_editor_payload(Tournament.query.get(tournament_url)),
        },
    )


@bp.route("/tournaments/<tournament_url>/records/matches", methods=["POST"])
@login_required
@require_json_body()
def create_match(tournament_url: str):
    """TO-only: record a finished match. Body: see ``HistoricalRecordsService.save_match``."""
    return _save_match(tournament_url, None)


@bp.route("/tournaments/<tournament_url>/records/matches/<match_uuid>", methods=["PUT"])
@login_required
@require_json_body()
def update_match(tournament_url: str, match_uuid: str):
    """TO-only: replace a recorded match."""
    return _save_match(tournament_url, match_uuid)


@bp.route("/tournaments/<tournament_url>/records/matches/<match_uuid>", methods=["DELETE"])
@login_required
def delete_match(tournament_url: str, match_uuid: str):
    """TO-only: delete a recorded match that no other match references."""
    from app.routes.tournaments import delete_matches_with_children

    result = HistoricalRecordsService.deletable_match(tournament_url, **_actor(), match_uuid=match_uuid)
    if result.is_ok():
        delete_matches_with_children([match_uuid])
        db.session.commit()
    return _editor_response(tournament_url, result)


def _roster_payload(roster) -> dict:
    tournament, team_registration, rows = roster
    return {
        "tournament": tournament_to_dict(tournament),
        "team_id": team_registration.team,
        "team_name": team_registration.pseudonym,
        "players": [
            {
                "id": registration.id,
                "player_id": registration.player,
                "player_name": player.name if player else registration.player,
                "profile_photo": player.profile_photo if player else None,
                "jersey_name": registration.jersey_name,
                "jersey_number": registration.jersey_number,
                "status": str(registration.status),
            }
            for registration, player in rows
        ],
    }


def _roster_response(tournament_url: str, team_id: str, result):
    if result.is_err():
        return json_from_result(result)
    return json_from_result(
        HistoricalClaimsService.roster(tournament_url, team_id, **_actor()), ok_to_payload=_roster_payload
    )


@bp.route("/tournaments/<tournament_url>/rosters/<team_id>", methods=["GET"])
@login_required
def roster(tournament_url: str, team_id: str):
    """The team or a TO: a team's historical roster, including open invitations."""
    result = HistoricalClaimsService.roster(tournament_url, team_id, **_actor())
    return json_from_result(result, ok_to_payload=_roster_payload)


@bp.route("/tournaments/<tournament_url>/rosters/<team_id>/players", methods=["POST"])
@login_required
@require_json_body()
def invite_player(tournament_url: str, team_id: str):
    """The team or a TO: invite a player. Body: ``{"player_id", "jersey_name"?, "jersey_number"?}``."""
    data = g.json_body
    result = HistoricalClaimsService.invite_player(
        tournament_url,
        team_id,
        **_actor(),
        player_id=data.get("player_id", ""),
        jersey_name=data.get("jersey_name"),
        jersey_number=data.get("jersey_number"),
    )
    return _roster_response(tournament_url, team_id, result)


@bp.route("/tournaments/<tournament_url>/rosters/<team_id>/players/<player_id>", methods=["DELETE"])
@login_required
def remove_player(tournament_url: str, team_id: str, player_id: str):
    """The team or a TO: remove a player or withdraw their invitation."""
    result = HistoricalClaimsService.remove_player(tournament_url, team_id, **_actor(), player_id=player_id)
    return _roster_response(tournament_url, team_id, result)


@bp.route(
    "/tournaments/<tournament_url>/rosters/<team_id>/requests/<int:registration_id>/approve",
    methods=["POST"],
)
@login_required
def approve_roster_request(tournament_url: str, team_id: str, registration_id: int):
    """The claimed team or a TO: accept a player's request to join."""
    result = HistoricalClaimsService.decide_roster_request(
        tournament_url, team_id, registration_id, **_actor(), approve=True
    )
    return _roster_response(tournament_url, team_id, result)


@bp.route(
    "/tournaments/<tournament_url>/rosters/<team_id>/requests/<int:registration_id>/decline",
    methods=["POST"],
)
@login_required
def decline_roster_request(tournament_url: str, team_id: str, registration_id: int):
    """The claimed team or a TO: decline a player's request to join."""
    result = HistoricalClaimsService.decide_roster_request(
        tournament_url, team_id, registration_id, **_actor(), approve=False
    )
    return _roster_response(tournament_url, team_id, result)


@bp.route("/me/invitations", methods=["GET"])
@login_required
def my_invitations():
    """Open claim invitations (teams) or roster invitations (players). Query: ``tournament``."""
    return json_success(
        HistoricalClaimsService.pending_invitations(
            current_user.id, current_user_type(), request.args.get("tournament") or None
        )
    )


def _respond_to_claim(invitation_id: int, accept: bool):
    if not is_team(current_user):
        return json_error("Only teams can claim records", status_code=403)
    result = HistoricalClaimsService.respond_to_claim(invitation_id, team_id=current_user.id, accept=accept)
    return json_from_result(result, ok_to_payload=lambda tournament: {"tournament_url": tournament.url})


@bp.route("/me/invitations/claims/<int:invitation_id>/accept", methods=["POST"])
@login_required
def accept_claim(invitation_id: int):
    """Team: claim a placeholder's records."""
    return _respond_to_claim(invitation_id, True)


@bp.route("/me/invitations/claims/<int:invitation_id>/decline", methods=["POST"])
@login_required
def decline_claim(invitation_id: int):
    """Team: decline a claim invitation."""
    return _respond_to_claim(invitation_id, False)


def _respond_to_roster_invitation(registration_id: int, accept: bool):
    if not is_player(current_user):
        return json_error("Only players can join rosters", status_code=403)
    result = HistoricalClaimsService.respond_to_roster_invitation(
        registration_id, player_id=current_user.id, accept=accept
    )
    return json_from_result(result, ok_to_payload=lambda registration: {"tournament_url": registration.event})


@bp.route("/me/invitations/rosters/<int:registration_id>/accept", methods=["POST"])
@login_required
def accept_roster_invitation(registration_id: int):
    """Player: join a historical roster."""
    return _respond_to_roster_invitation(registration_id, True)


@bp.route("/me/invitations/rosters/<int:registration_id>/decline", methods=["POST"])
@login_required
def decline_roster_invitation(registration_id: int):
    """Player: decline a historical roster invitation."""
    return _respond_to_roster_invitation(registration_id, False)
