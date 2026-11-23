"""Online payments: payout accounts, organizers' payment settings, registration checkout, and Stripe webhooks.

Hosts the ``payments`` blueprint. The workflows live in
:mod:`app.services.payout_account_service` and
:mod:`app.services.checkout_service`; Stripe calls go through
:mod:`app.utils.stripe_gateway`.
"""

import logging

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app.domain.enums import UserType
from app.serializers.checkout_serializer import checkout_hold_payload
from app.serializers.payout_account_serializer import online_payments_payload, payout_account_payload
from app.services import checkout_service, payout_account_service
from app.services._common import Scope
from app.services.permission_service import PermissionService
from app.utils.result_helpers import json_from_result
from app.utils.stripe_gateway import InvalidWebhookError, gateway
from app.utils.user_helpers import is_player, is_team
from models import CheckoutHold, League, PayoutAccount, Team, Tournament, db

logger = logging.getLogger(__name__)

bp = Blueprint("payments", __name__, url_prefix="/_api/payments")


@bp.errorhandler(HTTPException)
def request_error(error):
    return jsonify({"success": False, "error": error.description}), error.code


def _require_player():
    if not is_player(current_user):
        abort(403, description="Only player accounts can receive payouts.")


def _own_account_or_404() -> PayoutAccount:
    account = payout_account_service.payout_account_of(current_user.id).unwrap_or(None)
    if account is None:
        abort(404, description="You haven't set up payouts yet.")
    return account


def _profile_payouts_url() -> str:
    return f"{_public_base_url()}/players/{current_user.id}/edit#payouts"


@bp.get("/payout-account")
@login_required
def payout_account():
    """The signed-in player's payout account, if any."""
    _require_player()
    account = payout_account_service.payout_account_of(current_user.id).unwrap_or(None)
    return jsonify({"available": gateway() is not None, "account": payout_account_payload(account)})


@bp.post("/payout-account/onboarding")
@login_required
def start_onboarding():
    """A link to Stripe's hosted onboarding, creating the player's connected account first if needed."""
    _require_player()
    url = _profile_payouts_url()
    result = payout_account_service.onboarding_url(current_user, return_url=url, refresh_url=url)
    db.session.commit()
    return json_from_result(result, ok_to_payload=lambda onboarding: {"url": onboarding})


@bp.post("/payout-account/refresh")
@login_required
def refresh_payout_account():
    """Re-read the player's account status from Stripe after onboarding."""
    _require_player()
    result = payout_account_service.refresh(_own_account_or_404())
    if result.is_ok():
        db.session.commit()
    return json_from_result(result, ok_to_payload=lambda account: {"account": payout_account_payload(account)})


def _organized_config(scope_kind: str, slug: str):
    if scope_kind == "leagues":
        league = db.session.get(League, slug)
        if league is None:
            abort(404, description="League not found.")
        if not PermissionService.is_league_organizer(slug, current_user):
            abort(403, description="Only league organizers can change payment settings.")
        config = league.registrable_config
    else:
        tournament = db.session.get(Tournament, slug)
        if tournament is None:
            abort(404, description="Tournament not found.")
        if not PermissionService.is_tournament_organizer(slug, current_user):
            abort(403, description="Only tournament organizers can change payment settings.")
        if tournament.league_id:
            abort(400, description="Set online payments for league events in league settings.")
        if tournament.historical:
            abort(400, description="Historical tournaments don't take payments.")
        config = tournament.registrable_config
    if config is None:
        abort(404, description="Registration is not configured for this event.")
    return config


def _own_account_if_player() -> PayoutAccount | None:
    if not is_player(current_user):
        return None
    return payout_account_service.payout_account_of(current_user.id).unwrap_or(None)


@bp.get("/settings/<any(events, leagues):scope_kind>/<slug>")
@login_required
def online_payment_settings(scope_kind: str, slug: str):
    """Whether registrants pay online, which payout account receives it, and the organizer's own account."""
    config = _organized_config(scope_kind, slug)
    return jsonify(online_payments_payload(config, _own_account_if_player()))


@bp.put("/settings/<any(events, leagues):scope_kind>/<slug>")
@login_required
def update_online_payment_settings(scope_kind: str, slug: str):
    """Attach or detach a payout account and turn online payments on or off."""
    config = _organized_config(scope_kind, slug)
    if gateway() is None:
        abort(404, description=payout_account_service.UNAVAILABLE_MESSAGE)
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    payout_account_id = data.get("payout_account_id")
    if not isinstance(enabled, bool):
        abort(400, description="Enabled must be true or false.")
    if payout_account_id is not None and (
        isinstance(payout_account_id, bool) or not isinstance(payout_account_id, int)
    ):
        abort(400, description="Choose a payout account.")
    result = payout_account_service.update_online_payments(
        config, enabled=enabled, payout_account_id=payout_account_id, actor=current_user
    )
    if result.is_ok():
        db.session.commit()
    else:
        db.session.rollback()
    return json_from_result(
        result, ok_to_payload=lambda updated: online_payments_payload(updated, _own_account_if_player())
    )


@bp.post("/webhooks/accounts")
def account_webhook():
    """Thin events about connected accounts. A failed re-read returns an error so Stripe retries."""
    stripe = gateway()
    if stripe is None:
        abort(404)
    try:
        event = stripe.account_event(request.get_data(), request.headers.get("Stripe-Signature"))
    except InvalidWebhookError as error:
        logger.warning("stripe account webhook rejected: error=%s", error)
        abort(400, description="Invalid webhook.")
    result = payout_account_service.apply_account_event(event)
    if result.is_ok():
        db.session.commit()
    return json_from_result(result, ok_to_payload=lambda _account: {"received": True})


def _public_base_url() -> str:
    origin = current_app.config["PUBLIC_CANONICAL_ORIGIN"]
    return f"{origin}{request.script_root}" if origin else request.url_root.rstrip("/")


def _registration_scope(scope_kind: str, slug: str):
    """The scope, parent, and config a registrant pays into. League events pay into their league."""
    if scope_kind == "leagues":
        league = db.session.get(League, slug)
        if league is None or not (league.published or PermissionService.is_league_organizer(slug, current_user)):
            abort(404, description="League not found.")
        return Scope.league(slug), league, league.registrable_config
    tournament = db.session.get(Tournament, slug)
    if tournament is None or not PermissionService.can_view_tournament(slug, current_user):
        abort(404, description="Tournament not found.")
    if tournament.historical:
        abort(400, description="Historical tournaments don't take registrations.")
    if tournament.league_id:
        return _registration_scope("leagues", tournament.league_id)
    return Scope.event(slug), tournament, tournament.registrable_config


def _registrant(registrant_kind, team_id):
    """The team or player the signed-in account is registering."""
    if registrant_kind == "team":
        if team_id is None and is_team(current_user):
            team_id = current_user.id
        if not isinstance(team_id, str) or not PermissionService.can_manage_team(team_id, current_user):
            abort(403, description="You do not manage this team.")
        return UserType.TEAM, db.get_or_404(Team, team_id)
    if registrant_kind == "player":
        if not is_player(current_user):
            abort(403, description="Only players can register as players.")
        return UserType.PLAYER, current_user
    abort(400, description="Choose whether to register a team or a player.")


@bp.get("/checkout/<any(events, leagues):scope_kind>/<slug>")
@login_required
def checkout_options(scope_kind: str, slug: str):
    """Whether the registrant must pay online to register, and how much."""
    scope, _parent, config = _registration_scope(scope_kind, slug)
    kind, holder = _registrant(request.args.get("registrant"), request.args.get("team_id"))
    return jsonify(
        {
            "mode": checkout_service.checkout_mode(scope, config, kind, holder.id),
            "amount_cents": checkout_service.online_fee_cents(config, kind),
        }
    )


@bp.post("/checkout/<any(events, leagues):scope_kind>/<slug>")
@login_required
def start_checkout(scope_kind: str, slug: str):
    """Start a Stripe checkout that registers, or pays for the registrant's unpaid registration."""
    scope, parent, config = _registration_scope(scope_kind, slug)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="Provide the registration details as JSON.")
    kind, holder = _registrant(data.get("registrant"), data.get("team_id"))
    form = data.get("form") or {}
    return_path = data.get("return_path")
    if not isinstance(form, dict):
        abort(400, description="Provide the registration form as an object.")
    if not isinstance(return_path, str) or not return_path.startswith("/") or return_path.startswith("//"):
        abort(400, description="Provide the page to return to if checkout is cancelled.")
    base_url = _public_base_url()
    result = checkout_service.start_checkout(
        scope,
        parent,
        config,
        kind,
        holder,
        payer=current_user,
        form=form,
        success_url=f"{base_url}/checkout/{{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}{return_path}",
    )
    if result.is_ok():
        db.session.commit()
    else:
        db.session.rollback()
    return json_from_result(result, ok_to_payload=lambda url: {"url": url})


def _can_see_hold(hold: CheckoutHold) -> bool:
    if hold.payer_user_id == current_user.id:
        return True
    if hold.kind == UserType.TEAM.value:
        return PermissionService.can_manage_team(hold.holder_team, current_user)
    return is_player(current_user) and current_user.id == hold.holder_player


@bp.post("/checkouts/<session_id>/sync")
@login_required
def sync_checkout(session_id: str):
    """Bring a checkout up to date with Stripe and report it, for the payer's return page."""
    hold = CheckoutHold.query.filter_by(stripe_session_id=session_id).first()
    if hold is None or not _can_see_hold(hold):
        abort(404, description="Checkout not found.")
    result = checkout_service.sync(hold)
    return json_from_result(result, ok_to_payload=checkout_hold_payload)


@bp.post("/webhooks/checkout")
def checkout_webhook():
    """Snapshot events about Checkout Sessions on connected accounts."""
    stripe = gateway()
    if stripe is None:
        abort(404)
    try:
        event = stripe.checkout_event(request.get_data(), request.headers.get("Stripe-Signature"))
    except InvalidWebhookError as error:
        logger.warning("stripe checkout webhook rejected: error=%s", error)
        abort(400, description="Invalid webhook.")
    checkout_service.apply_checkout_event(event.type, event.session)
    return jsonify({"success": True, "received": True})
