"""JSON shapes for :class:`~app.models.payments.PaidEntry` rows.

Only the holder, the sender of a transfer, and organizers see these.
"""

from app.domain.enums import UserType
from app.error_values import Some
from app.services import paid_entry_service
from app.services._common import Scope
from app.utils.datetime_helpers import dt_iso
from models import League, PaidEntry, Player, Team, Tournament, db


def _account_name(kind: str, account_id: str) -> str:
    account = db.session.get(Team if kind == UserType.TEAM.value else Player, account_id)
    return account.name if account is not None else account_id


def entry_payload(entry: PaidEntry) -> dict:
    parent = db.session.get(League, entry.league_id) if entry.league_id else db.session.get(Tournament, entry.event)
    return {
        "id": entry.id,
        "kind": entry.kind,
        "event": {
            "kind": "leagues" if entry.league_id else "events",
            "slug": entry.league_id or entry.event,
            "name": parent.name if parent is not None else None,
        },
        "status": entry.status.value,
        "source": entry.source.value,
        "holder": {"id": entry.holder, "name": _account_name(entry.kind, entry.holder)},
        "transferred_from": (
            {"id": entry.transferred_from, "name": _account_name(entry.kind, entry.transferred_from)}
            if entry.transferred_from
            else None
        ),
        "reserves_team_slot": entry.reserves_team_slot,
        "created_at": dt_iso(entry.created_at),
        "applied_at": dt_iso(entry.applied_at),
    }


def held_entry_payload(scope: Scope, kind: UserType, holder_id: str) -> dict | None:
    """The holder's entry for *scope*, for registration pages."""
    match paid_entry_service.held_entry(scope, kind, holder_id):
        case Some(entry):
            return entry_payload(entry)
    return None
