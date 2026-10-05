"""Side competition routes."""

from flask import Blueprint, g, jsonify
from flask_login import login_required, current_user  # type: ignore[import-untyped]

from app.services._common import current_user_type
from app.services.permission_service import PermissionService
from app.services.sidecomp_service import SideCompService
from app.utils.decorators import require_json_body
from app.utils.result_helpers import json_from_result
from app.utils.user_helpers import is_player

bp = Blueprint("sidecomps", __name__, url_prefix="/_api")


def _sc_summary(sc, *, registrant_count=None, viewer_reg=None):
    payload = {
        "id": sc.id,
        "name": sc.name,
        "type": str(sc.type),
        "registration_open": bool(sc.registration_open),
        "active": bool(sc.active),
        "finalized": bool(sc.finalized),
        "allowed_weapons": sc.allowed_weapon_names(),
        "only_show_top_n_results": sc.only_show_top_n_results,
        "results_page_enabled": sc.results_page_enabled(),
        "created_at": sc.created_at.isoformat() if sc.created_at else None,
    }
    if registrant_count is not None:
        payload["registrant_count"] = registrant_count
    if viewer_reg is not None:
        payload["viewer_is_registered"] = True
        payload["viewer_entry_number"] = viewer_reg.entry_number
        payload["viewer_weapon"] = viewer_reg.weapon_name()
    else:
        payload["viewer_is_registered"] = False
        payload["viewer_entry_number"] = None
        payload["viewer_weapon"] = None
    return payload


def _sc_payload(sc):
    return {
        "id": sc.id,
        "event": sc.event,
        "name": sc.name,
        "type": str(sc.type),
        "description": sc.description,
        "registration_open": bool(sc.registration_open),
        "active": bool(sc.active),
        "finalized": bool(sc.finalized),
        "allowed_weapons": sc.allowed_weapon_names(),
        "only_show_top_n_results": sc.only_show_top_n_results,
        "results_page_enabled": sc.results_page_enabled(),
        "created_at": sc.created_at.isoformat() if sc.created_at else None,
    }


@bp.route("/<tournament_url>/sidecomps", methods=["GET"])
def list_for_event(tournament_url: str):
    """Public: list side competitions for a tournament.

    Returns a JSON array of summaries including viewer registration context
    when the current user is a registered player.
    """
    from sqlalchemy import func
    from models import SideComp, SideCompRegistration, db

    rows = (
        db.session.query(SideComp, func.count(SideCompRegistration.id))
        .outerjoin(SideCompRegistration, SideCompRegistration.comp == SideComp.id)
        .filter(SideComp.event == tournament_url)
        .group_by(SideComp.id)
        .order_by(SideComp.created_at.asc())
        .all()
    )

    viewer_regs_by_comp = {}
    if current_user.is_authenticated and is_player(current_user):
        comp_ids = [sc.id for sc, _ in rows]
        if comp_ids:
            for reg in SideCompRegistration.query.filter(
                SideCompRegistration.comp.in_(comp_ids),
                SideCompRegistration.player == current_user.id,
            ).all():
                viewer_regs_by_comp[reg.comp] = reg

    out = [_sc_summary(sc, registrant_count=count, viewer_reg=viewer_regs_by_comp.get(sc.id)) for sc, count in rows]
    return jsonify(out)


def _detail_payload(sc, registrants):
    viewer_is_to = False
    viewer_can_register = False
    viewer_is_registered_in_comp = False
    viewer_entry_number = None
    viewer_weapon = None
    if current_user.is_authenticated:
        viewer_is_to = PermissionService.is_tournament_organizer(sc.event, current_user)
        if is_player(current_user):
            for reg, _ in registrants:
                if reg.player == current_user.id:
                    viewer_is_registered_in_comp = True
                    viewer_entry_number = reg.entry_number
                    viewer_weapon = reg.weapon_name()
                    break
            if not viewer_is_registered_in_comp and sc.registration_open:
                event_reg = SideCompService._confirmed_player_registration_for_tournament(sc.event, current_user.id)
                viewer_can_register = event_reg is not None

    return {
        **_sc_payload(sc),
        "registrants": [
            {
                "player_id": reg.player,
                "player_name": (player.name if player else reg.player),
                "entry_number": reg.entry_number,
                "weapon": reg.weapon_name(),
                "registered_at": reg.registered_at.isoformat() if reg.registered_at else None,
                "registered_by_to": bool(reg.registered_by_to),
            }
            for reg, player in registrants
        ],
        "viewer_is_to": viewer_is_to,
        "viewer_can_register": viewer_can_register,
        "viewer_is_registered_in_comp": viewer_is_registered_in_comp,
        "viewer_entry_number": viewer_entry_number,
        "viewer_weapon": viewer_weapon,
    }


@bp.route("/sidecomps/<int:comp_id>", methods=["GET"])
def detail(comp_id: int):
    """Public: side competition detail with registrants and viewer-context flags."""
    res = SideCompService.get_with_registrants(comp_id)
    return json_from_result(res, ok_to_payload=lambda v: _detail_payload(v[0], v[1]))


@bp.route("/<tournament_url>/sidecomps", methods=["POST"])
@login_required
@require_json_body()
def create(tournament_url: str):
    """TO-only: create a side competition."""
    data = g.json_body

    res = SideCompService.create(
        tournament_url,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        name=data.get("name", ""),
        type=data.get("type", ""),
        description=data.get("description"),
        allowed_weapons=data.get("allowed_weapons"),
        only_show_top_n_results=data.get("only_show_top_n_results"),
    )
    return json_from_result(res, ok_to_payload=_sc_payload)


@bp.route("/sidecomps/<int:comp_id>", methods=["PATCH"])
@login_required
@require_json_body()
def update(comp_id: int):
    """TO-only: rename or change type of a side competition."""
    data = g.json_body

    # only_show_top_n_results: omit key to leave untouched; null clears; 0 hides.
    top_n_arg = ...
    if "only_show_top_n_results" in data:
        top_n_arg = data.get("only_show_top_n_results")

    res = SideCompService.update(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        name=data.get("name"),
        type=data.get("type"),
        description=data.get("description"),
        registration_open=data.get("registration_open"),
        active=data.get("active"),
        allowed_weapons=data.get("allowed_weapons"),
        only_show_top_n_results=top_n_arg,
    )
    return json_from_result(
        res,
        ok_to_payload=lambda sc: {
            "id": sc.id,
            "event": sc.event,
            "name": sc.name,
            "type": str(sc.type),
            "description": sc.description,
            "registration_open": bool(sc.registration_open),
            "active": bool(sc.active),
            "finalized": bool(sc.finalized),
            "allowed_weapons": sc.allowed_weapon_names(),
            "only_show_top_n_results": sc.only_show_top_n_results,
            "results_page_enabled": sc.results_page_enabled(),
        },
    )


@bp.route("/sidecomps/<int:comp_id>", methods=["DELETE"])
@login_required
def delete(comp_id: int):
    """TO-only: hard-delete a side competition and its registrations/results."""
    res = SideCompService.delete(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
    )
    return json_from_result(res, ok_to_payload=lambda _: {})


@bp.route("/sidecomps/<int:comp_id>/register", methods=["POST"])
@login_required
@require_json_body()
def player_register(comp_id: int):
    """Player self-registration for a side competition."""
    if not is_player(current_user):
        return jsonify({"success": False, "error": "Only players can register"}), 403

    data = g.json_body
    res = SideCompService.register_player(
        comp_id,
        player_id=current_user.id,
        weapon=data.get("weapon"),
    )
    return json_from_result(
        res,
        ok_to_payload=lambda reg: {
            "comp": reg.comp,
            "player_id": reg.player,
            "entry_number": reg.entry_number,
            "weapon": reg.weapon_name(),
            "registered_at": reg.registered_at.isoformat() if reg.registered_at else None,
        },
    )


@bp.route("/sidecomps/<int:comp_id>/registration", methods=["PATCH"])
@login_required
@require_json_body()
def player_update_registration(comp_id: int):
    """Player self-update of selected weapon for a side competition."""
    if not is_player(current_user):
        return jsonify({"success": False, "error": "Only players can update their registration"}), 403

    data = g.json_body
    res = SideCompService.update_player_weapon(
        comp_id,
        player_id=current_user.id,
        weapon=data.get("weapon"),
    )
    return json_from_result(
        res,
        ok_to_payload=lambda reg: {
            "comp": reg.comp,
            "player_id": reg.player,
            "entry_number": reg.entry_number,
            "weapon": reg.weapon_name(),
            "registered_at": reg.registered_at.isoformat() if reg.registered_at else None,
        },
    )


@bp.route("/sidecomps/<int:comp_id>/deregister", methods=["POST"])
@login_required
def player_deregister(comp_id: int):
    """Player self-deregistration from a side competition."""
    if not is_player(current_user):
        return jsonify({"success": False, "error": "Only players can deregister"}), 403

    res = SideCompService.deregister_player(comp_id, player_id=current_user.id)
    return json_from_result(res, ok_to_payload=lambda _: {})


@bp.route("/sidecomps/<int:comp_id>/register-player-as-to", methods=["POST"])
@login_required
@require_json_body()
def register_player_as_to(comp_id: int):
    """TO-only: register a player into a side competition on their behalf."""
    data = g.json_body
    player_id = (data.get("player_id") or "").strip()
    if not player_id:
        return jsonify({"success": False, "error": "player_id is required"}), 400

    res = SideCompService.register_player_as_to(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        player_id=player_id,
        weapon=data.get("weapon"),
    )

    def _checkin_payload(reg):
        from models import Player

        player = Player.query.get(reg.player)
        return {
            "player_id": reg.player,
            "player_name": player.name if player else reg.player,
            "entry_number": reg.entry_number,
            "weapon": reg.weapon_name(),
            "registered_at": reg.registered_at.isoformat() if reg.registered_at else None,
        }

    return json_from_result(res, ok_to_payload=_checkin_payload)


@bp.route("/sidecomps/<int:comp_id>/update-player-as-to", methods=["POST"])
@login_required
@require_json_body()
def update_player_as_to(comp_id: int):
    """TO-only: update a player's selected weapon on their behalf."""
    data = g.json_body
    player_id = (data.get("player_id") or "").strip()
    if not player_id:
        return jsonify({"success": False, "error": "player_id is required"}), 400

    res = SideCompService.update_player_weapon_as_to(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        player_id=player_id,
        weapon=data.get("weapon"),
    )

    def _payload(reg):
        from models import Player

        player = Player.query.get(reg.player)
        return {
            "player_id": reg.player,
            "player_name": player.name if player else reg.player,
            "entry_number": reg.entry_number,
            "weapon": reg.weapon_name(),
            "registered_at": reg.registered_at.isoformat() if reg.registered_at else None,
        }

    return json_from_result(res, ok_to_payload=_payload)


@bp.route("/sidecomps/<int:comp_id>/deregister-player-as-to", methods=["POST"])
@login_required
@require_json_body()
def deregister_player_as_to(comp_id: int):
    """TO-only: deregister a player from a side competition on their behalf."""
    data = g.json_body
    player_id = (data.get("player_id") or "").strip()
    if not player_id:
        return jsonify({"success": False, "error": "player_id is required"}), 400

    res = SideCompService.deregister_player_as_to(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
        player_id=player_id,
    )
    return json_from_result(res, ok_to_payload=lambda _: {})


@bp.route("/sidecomps/<int:comp_id>/eligible-players", methods=["GET"])
@login_required
def eligible_players(comp_id: int):
    """TO-only: list event-registered players with side competition status."""
    from app.domain.enums import RegistrationStatus
    from app.services.registration_resolver import (
        player_registrations_for_tournament,
        team_registrations_for_tournament,
    )
    from models import (
        Player,
        SideComp,
        SideCompRegistration,
        Tournament,
    )

    sc = SideComp.query.get(comp_id)
    if sc is None:
        return jsonify({"success": False, "error": "Side competition not found"}), 404

    auth_check = SideCompService._require_to(sc.event, current_user.id, current_user_type())
    if auth_check.is_err():
        return json_from_result(auth_check)

    tournament = Tournament.query.get(sc.event)

    sidecomp_regs = {r.player: r for r in SideCompRegistration.query.filter_by(comp=comp_id).all()}

    event_regs = player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED])

    player_ids = [er.player for er in event_regs]
    team_ids = {er.team for er in event_regs if er.team}

    players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
    team_pseudonyms = {}
    team_shortnames = {}
    if team_ids:
        for tr in team_registrations_for_tournament(tournament):
            if tr.team in team_ids:
                team_pseudonyms[tr.team] = tr.pseudonym
                team_shortnames[tr.team] = tr.shortname

    out = [
        {
            "player_id": er.player,
            "player_name": players_by_id[er.player].name if er.player in players_by_id else er.player,
            "team_id": er.team,
            "team_pseudonym": team_pseudonyms.get(er.team) if er.team else None,
            "team_shortname": team_shortnames.get(er.team) if er.team else None,
            "jersey_name": er.jersey_name,
            "sidecomp_registered": er.player in sidecomp_regs,
            "entry_number": sidecomp_regs[er.player].entry_number if er.player in sidecomp_regs else None,
            "weapon": sidecomp_regs[er.player].weapon_name() if er.player in sidecomp_regs else None,
        }
        for er in event_regs
    ]
    # Also return the side comp's allowed weapons for the registration modal.
    return jsonify({"players": out, "allowed_weapons": sc.allowed_weapon_names(), "name": sc.name})


@bp.route("/sidecomps/<int:comp_id>/enter-results-roster", methods=["GET"])
@login_required
def enter_results_roster(comp_id: int):
    """Authenticated: full registrant roster for the enter-results UI."""
    res = SideCompService.enter_results_roster(comp_id)
    return json_from_result(res, ok_to_payload=lambda payload: payload)


@bp.route("/sidecomps/<int:comp_id>/results", methods=["POST"])
@login_required
@require_json_body()
def log_result(comp_id: int):
    """Authenticated: log a +1 / -1 point against a registration."""
    data = g.json_body
    try:
        registration_id = int(data.get("registration_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "registration_id is required"}), 400
    try:
        points = int(data.get("points"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "points must be +1 or -1"}), 400

    res = SideCompService.log_result(
        comp_id,
        registration_id=registration_id,
        points=points,
        ref_user_id=current_user.id,
    )
    return json_from_result(res, ok_to_payload=lambda result: result.to_payload())


@bp.route("/sidecomps/results/<result_uuid>", methods=["PATCH"])
@login_required
@require_json_body()
def patch_result(result_uuid: str):
    """Update flagged (any signed-in user) or valid (TO-only) on a result."""
    data = g.json_body
    if "valid" in data:
        res = SideCompService.set_result_valid(
            result_uuid,
            valid=bool(data.get("valid")),
            actor_user_id=current_user.id,
            actor_user_type=current_user_type(),
        )
        return json_from_result(res, ok_to_payload=lambda result: result.to_payload())

    if "flagged" not in data:
        return jsonify({"success": False, "error": "flagged or valid is required"}), 400

    res = SideCompService.set_result_flagged(
        result_uuid,
        flagged=bool(data.get("flagged")),
        actor_user_id=current_user.id,
    )
    return json_from_result(res, ok_to_payload=lambda result: result.to_payload())


@bp.route("/sidecomps/<int:comp_id>/manage-results", methods=["GET"])
@login_required
def manage_results(comp_id: int):
    """TO-only: chronological list of every logged point for management."""
    res = SideCompService.manage_results(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
    )
    return json_from_result(res, ok_to_payload=lambda payload: payload)


@bp.route("/sidecomps/<int:comp_id>/finalize", methods=["POST"])
@login_required
def finalize(comp_id: int):
    """TO-only: permanently finalize side-comp results."""
    res = SideCompService.finalize(
        comp_id,
        actor_user_id=current_user.id,
        actor_user_type=current_user_type(),
    )
    return json_from_result(
        res,
        ok_to_payload=lambda sc: {
            "id": sc.id,
            "active": bool(sc.active),
            "finalized": bool(sc.finalized),
        },
    )


@bp.route("/sidecomps/<int:comp_id>/standings", methods=["GET"])
def standings(comp_id: int):
    """Public: standings tables for a side competition."""
    res = SideCompService.standings(comp_id)
    return json_from_result(res, ok_to_payload=lambda payload: payload)
