"""Organizer registration management: ``/api/v1/{tournaments,leagues}/<id>/registrations``.

Needs a token with the ``manage`` scope whose user organizes the tournament
or league. A league tournament's registrations are the league's. Shares
:class:`~app.services.registration_service.RegistrationService` with the
SPA's session routes.
"""

from __future__ import annotations

from flask import g, jsonify

from app.serializers.api_v1_serializer import (
    TeamNames,
    player_registration_json,
    scope_of,
    scope_registrations_json,
    team_registration_json,
)
from app.error_values import Err, Ok
from app.services import paid_entry_service
from app.services._common import Scope
from app.services.registration_service import Payment, RegistrationService
from app.utils.helpers import get_registrable_config
from app.utils.oauth_server import require_bearer
from models import Player, Team

from . import (
    bp,
    error,
    json_object_body,
    result_response,
    token_actor,
    with_organized_league,
    with_organized_tournament,
)

TOURNAMENT_REGISTRATIONS_PATH = "/tournaments/<tournament_id>/registrations"
LEAGUE_REGISTRATIONS_PATH = "/leagues/<league_id>/registrations"
PAYMENT_TEXT_KEYS = ("payment_method", "payment_reference", "payment_notes")


def _payment_from_body() -> Payment | str:
    """The JSON body as a :class:`Payment`, or an error message."""
    body = json_object_body()
    if body is None:
        return "Expected a JSON object body"
    if not isinstance(body.get("paid"), bool):
        return "paid must be true or false"
    amount_paid = body.get("amount_paid", 0)
    if isinstance(amount_paid, bool) or not isinstance(amount_paid, (int, float)) or amount_paid < 0:
        return "amount_paid must be a nonnegative number"
    match paid_entry_service.parse_amount_cents(str(amount_paid)):
        case Err(_):
            return "amount_paid must be a nonnegative number"
        case Ok(amount_cents):
            pass
    for key in PAYMENT_TEXT_KEYS:
        if not isinstance(body.get(key, ""), str):
            return f"{key} must be text"
    return Payment(
        paid=body["paid"],
        amount_cents=amount_cents,
        payment_method=body.get("payment_method", ""),
        payment_reference=body.get("payment_reference", ""),
        payment_notes=body.get("payment_notes", ""),
    )


def _update_team_payment(scope: Scope, registration_id: int):
    payment = _payment_from_body()
    if isinstance(payment, str):
        return error(payment, 400)
    return result_response(
        RegistrationService.update_team_payment(scope, registration_id, payment, actor=g.oauth_user),
        lambda registration: {"registration": team_registration_json(registration, Team.query.get(registration.team))},
    )


def _update_player_payment(scope: Scope, registration_id: int, config):
    payment = _payment_from_body()
    if isinstance(payment, str):
        return error(payment, 400)
    return result_response(
        RegistrationService.update_player_payment(scope, registration_id, payment, actor=g.oauth_user),
        lambda registration: {
            "registration": player_registration_json(
                registration, Player.query.get(registration.player), TeamNames(scope), config
            )
        },
    )


@bp.get(TOURNAMENT_REGISTRATIONS_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def list_tournament_registrations(tournament):
    return jsonify(scope_registrations_json(scope_of(tournament), get_registrable_config(tournament)))


@bp.post(f"{TOURNAMENT_REGISTRATIONS_PATH}/teams")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def register_team(tournament):
    """JSON ``{team_id, name, shortname}`` -> a confirmed, paid ``{"registration"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    if not isinstance(body.get("team_id"), str) or not body["team_id"].strip():
        return error("team_id is required", 400)
    result = RegistrationService.register_team_as_to(
        tournament.url,
        **token_actor(),
        team_id=body["team_id"].strip(),
        pseudonym=body.get("name") or "",
        shortname=body.get("shortname"),
    )
    return result_response(
        result,
        lambda registration: {"registration": team_registration_json(registration, Team.query.get(registration.team))},
        ok_status_code=201,
    )


@bp.post(f"{TOURNAMENT_REGISTRATIONS_PATH}/players")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def register_player(tournament):
    """JSON ``{player_id, team_id, jersey_name, jersey_number, waiver_legal_name_signature}`` -> ``{"registration"}``."""
    body = json_object_body()
    if body is None:
        return error("Expected a JSON object body", 400)
    if not isinstance(body.get("player_id"), str) or not body["player_id"].strip():
        return error("player_id is required", 400)
    result = RegistrationService.register_player_as_to(
        tournament.url,
        **token_actor(),
        player_id=body["player_id"].strip(),
        team_id=body.get("team_id") or None,
        jersey_number=body.get("jersey_number") or "",
        jersey_name=body.get("jersey_name") or "",
        waiver_legal_name_signature=body.get("waiver_legal_name_signature") or "",
    )
    return result_response(
        result,
        lambda registration: {
            "registration": player_registration_json(
                registration,
                Player.query.get(registration.player),
                TeamNames(scope_of(tournament)),
                get_registrable_config(tournament),
            )
        },
        ok_status_code=201,
    )


@bp.patch(f"{TOURNAMENT_REGISTRATIONS_PATH}/teams/<int:registration_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_tournament_team_payment(tournament, registration_id: int):
    return _update_team_payment(scope_of(tournament), registration_id)


@bp.patch(f"{TOURNAMENT_REGISTRATIONS_PATH}/players/<int:registration_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def update_tournament_player_payment(tournament, registration_id: int):
    return _update_player_payment(scope_of(tournament), registration_id, get_registrable_config(tournament))


@bp.delete(f"{TOURNAMENT_REGISTRATIONS_PATH}/teams/<team_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def deregister_tournament_team(tournament, team_id: str):
    return result_response(
        RegistrationService.deregister_team_as_to(scope_of(tournament), team_id, void_entry=False, actor=g.oauth_user),
        lambda _: {},
        ok_status_code=204,
    )


@bp.delete(f"{TOURNAMENT_REGISTRATIONS_PATH}/players/<player_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_tournament
def deregister_tournament_player(tournament, player_id: str):
    return result_response(
        RegistrationService.deregister_player_as_to(
            scope_of(tournament), player_id, void_entry=False, actor=g.oauth_user
        ),
        lambda _: {},
        ok_status_code=204,
    )


@bp.get(LEAGUE_REGISTRATIONS_PATH)
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def list_league_registrations(league):
    return jsonify(scope_registrations_json(Scope.league(league.url), league.registrable_config))


@bp.patch(f"{LEAGUE_REGISTRATIONS_PATH}/teams/<int:registration_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def update_league_team_payment(league, registration_id: int):
    return _update_team_payment(Scope.league(league.url), registration_id)


@bp.patch(f"{LEAGUE_REGISTRATIONS_PATH}/players/<int:registration_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def update_league_player_payment(league, registration_id: int):
    return _update_player_payment(Scope.league(league.url), registration_id, league.registrable_config)


@bp.delete(f"{LEAGUE_REGISTRATIONS_PATH}/teams/<team_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def deregister_league_team(league, team_id: str):
    return result_response(
        RegistrationService.deregister_team_as_to(
            Scope.league(league.url), team_id, void_entry=False, actor=g.oauth_user
        ),
        lambda _: {},
        ok_status_code=204,
    )


@bp.delete(f"{LEAGUE_REGISTRATIONS_PATH}/players/<player_id>")
@require_bearer(scope="manage", user_required=True)
@with_organized_league
def deregister_league_player(league, player_id: str):
    return result_response(
        RegistrationService.deregister_player_as_to(
            Scope.league(league.url), player_id, void_entry=False, actor=g.oauth_user
        ),
        lambda _: {},
        ok_status_code=204,
    )
