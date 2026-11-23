"""JSON for Stripe checkouts of registration fees."""

from __future__ import annotations

from app.domain.enums import UserType
from app.services import checkout_service, paid_entry_service
from models import CheckoutHold, League, Tournament, db


def checkout_hold_payload(hold: CheckoutHold) -> dict:
    scope = checkout_service.scope_of(hold)
    parent = (
        db.session.get(League, scope.league_url) if scope.is_league else db.session.get(Tournament, scope.event_url)
    )
    registered = paid_entry_service.active_registration(scope, UserType(hold.kind), hold.holder).is_some()
    return {
        "status": hold.status.value.lower(),
        "failure": hold.failure_message,
        "registered": registered,
        "registrant": hold.kind,
        "team_id": hold.holder_team,
        "amount_cents": hold.amount_cents,
        "currency": hold.currency,
        "scope": {
            "kind": "leagues" if scope.is_league else "events",
            "slug": scope.url,
            "name": parent.name if parent is not None else scope.url,
        },
    }
