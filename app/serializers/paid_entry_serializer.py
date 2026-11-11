"""JSON shapes for :class:`~app.models.payments.PaidEntry` rows.

Only the holder, the sender of a transfer, and organizers see these.
"""

from app.domain.enums import UserType
from app.utils.datetime_helpers import dt_iso
from models import League, PaidEntry, Player, Team, Tournament, db


def _account_name(kind: str, account_id: str) -> str:
    account = db.session.get(Team if kind == UserType.TEAM.value else Player, account_id)
    return account.name if account is not None else account_id


def entry_payload(entry: PaidEntry) -> dict:
    slug = entry.league_id or entry.event
    parent = db.session.get(League, entry.league_id) if entry.league_id else db.session.get(Tournament, entry.event)
    return {
        "id": entry.id,
        "kind": entry.kind,
        "event": {
            "kind": "leagues" if entry.league_id else "events",
            "slug": slug,
            "name": parent.name if parent is not None else slug,
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
