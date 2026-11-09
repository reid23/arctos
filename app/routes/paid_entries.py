"""Prepaid registration entries held by the signed-in account or a team it manages.

Hosts the ``paid_entries`` blueprint: listing entries, transferring an
entry that isn't covering a registration, and revoking or declining an
unclaimed transfer. The workflow lives in
:mod:`app.services.paid_entry_service`.
"""

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app.domain.enums import PaidEntryStatus, UserType
from app.serializers.paid_entry_serializer import entry_payload
from app.services import paid_entry_service
from app.services.permission_service import PermissionService
from app.utils.helpers import get_registrable_config
from app.utils.result_helpers import json_from_result
from app.utils.team_access import requested_team_id
from app.utils.user_helpers import is_player
from models import League, PaidEntry, Tournament, db

bp = Blueprint("paid_entries", __name__, url_prefix="/_api/paid-entries")


@bp.errorhandler(HTTPException)
def request_error(error):
    return jsonify({"success": False, "error": error.description}), error.code


def _can_act_for(kind: str, account_id: str | None) -> bool:
    if account_id is None:
        return False
    if kind == UserType.TEAM.value:
        return PermissionService.can_manage_team(account_id, current_user)
    return is_player(current_user) and current_user.id == account_id


def _registration_open(entry: PaidEntry) -> bool:
    if entry.league_id:
        league = db.session.get(League, entry.league_id)
        cfg = league.registrable_config if league is not None else None
    else:
        tournament = db.session.get(Tournament, entry.event)
        cfg = get_registrable_config(tournament) if tournament is not None else None
    if cfg is None:
        return False
    return bool(cfg.team_registration_open if entry.kind == UserType.TEAM.value else cfg.player_registration_open)


def _entry_or_404(entry_id: int) -> PaidEntry:
    entry = db.session.get(PaidEntry, entry_id)
    if entry is None or entry.status == PaidEntryStatus.VOIDED:
        abort(404, description="Paid entry not found.")
    return entry


@bp.get("")
@login_required
def list_entries():
    """Entries held by this account (or ``?team_id=``), and transfers it sent that are unclaimed."""
    if request.args.get("team_id") or not is_player(current_user):
        kind, account_id = UserType.TEAM, requested_team_id()
        held = PaidEntry.query.filter_by(kind=kind.value, holder_team=account_id)
    else:
        kind, account_id = UserType.PLAYER, current_user.id
        held = PaidEntry.query.filter_by(kind=kind.value, holder_player=account_id)
    sent = PaidEntry.query.filter_by(kind=kind.value, status=PaidEntryStatus.AVAILABLE, transferred_from=account_id)
    return jsonify(
        {
            "held": [
                entry_payload(entry)
                for entry in held.filter(PaidEntry.status != PaidEntryStatus.VOIDED).order_by(PaidEntry.id).all()
            ],
            "sent": [entry_payload(entry) for entry in sent.order_by(PaidEntry.id).all()],
        }
    )


@bp.post("/<int:entry_id>/transfer")
@login_required
def transfer(entry_id: int):
    """Give an entry that isn't covering a registration to another account of the same kind."""
    entry = _entry_or_404(entry_id)
    if not _can_act_for(entry.kind, entry.holder):
        abort(403, description="You do not hold this paid entry.")
    if not _registration_open(entry):
        abort(403, description="Registration changes are closed. Contact the event organizer.")
    data = request.get_json(silent=True) or {}
    recipient = data.get("recipient")
    if not isinstance(recipient, str) or not recipient.strip():
        abort(400, description="Choose who to transfer this entry to.")
    result = paid_entry_service.transfer_entry(entry, recipient, actor=current_user)
    if result.is_ok():
        db.session.commit()
    else:
        db.session.rollback()
    return json_from_result(result, ok_to_payload=lambda transferred: {"entry": entry_payload(transferred)})


@bp.post("/<int:entry_id>/revoke")
@login_required
def revoke(entry_id: int):
    """Take back an unclaimed transfer this account sent."""
    entry = _entry_or_404(entry_id)
    if not _can_act_for(entry.kind, entry.transferred_from):
        abort(403, description="You did not send this transfer.")
    result = paid_entry_service.revoke_transfer(entry, actor=current_user)
    if result.is_ok():
        db.session.commit()
    return json_from_result(result, ok_to_payload=lambda returned: {"entry": entry_payload(returned)})


@bp.post("/<int:entry_id>/decline")
@login_required
def decline(entry_id: int):
    """Refuse an unclaimed transfer sent to this account."""
    entry = _entry_or_404(entry_id)
    if not _can_act_for(entry.kind, entry.holder):
        abort(403, description="This transfer was not sent to you.")
    result = paid_entry_service.decline_transfer(entry, actor=current_user)
    if result.is_ok():
        db.session.commit()
    return json_from_result(result, ok_to_payload=lambda returned: {"entry": entry_payload(returned)})
