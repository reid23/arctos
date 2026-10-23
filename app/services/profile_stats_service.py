"""Public profile statistics based on registrations, not verified attendance."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Literal

from sqlalchemy import func, or_

from app.utils.datetime_helpers import now_utc_naive
from models import PlayerRegistration, TeamRegistration, Tournament


def past_event_counts(
    account_type: Literal["player", "team"], owner_ids: Iterable[str], *, today: date | None = None
) -> dict[str, int]:
    """Count unique published past events for confirmed registrations in two queries.

    Event dates have day precision in the UI, so an event counts only after its
    end date (or start date if no end is set). League registrations expand to
    events, never an extra count for the league itself. Events that ended before
    a league registration began are excluded; an unknown registration date does
    not establish eligibility for an earlier league event. Direct registrations
    also support organizers recording a past event retrospectively.
    """
    counts = dict.fromkeys(owner_ids, 0)
    if not counts:
        return counts
    model = PlayerRegistration if account_type == "player" else TeamRegistration
    owner_column = model.player if account_type == "player" else model.team
    registrations = (
        model.query.with_entities(owner_column, model.event, model.league_id, model.registered_at)
        .filter(owner_column.in_(counts), model.status == "CONFIRMED")
        .all()
    )
    event_ids = {registration.event for registration in registrations if registration.event}
    league_ids = {registration.league_id for registration in registrations if registration.league_id}
    if not event_ids and not league_ids:
        return counts
    cutoff = datetime.combine(today or now_utc_naive().date(), time.min)
    events = (
        Tournament.query.with_entities(
            Tournament.url,
            Tournament.league_id,
            func.coalesce(Tournament.end_date, Tournament.start_date).label("ends"),
        )
        .filter(
            Tournament.published.is_(True),
            func.coalesce(Tournament.end_date, Tournament.start_date) < cutoff,
            or_(Tournament.url.in_(event_ids), Tournament.league_id.in_(league_ids)),
        )
        .all()
    )
    past_event_ids = {event.url for event in events}
    league_events = defaultdict(list)
    for event in events:
        if event.league_id:
            league_events[event.league_id].append(event)
    owner_events = defaultdict(set)
    for owner_id, event_id, league_id, registered_at in registrations:
        if event_id in past_event_ids:
            owner_events[owner_id].add(event_id)
        elif league_id and registered_at is not None:
            owner_events[owner_id].update(
                event.url for event in league_events[league_id] if registered_at.date() <= event.ends.date()
            )
    counts.update({owner_id: len(events) for owner_id, events in owner_events.items()})
    return counts
