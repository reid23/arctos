"""Public past-event counts reflect registrations without claiming attendance."""

from datetime import date, datetime

import pytest
from sqlalchemy import event

from app.services.profile_stats_service import past_event_counts
from models import League, Player, PlayerRegistration, Team, TeamRegistration, Tournament, db

pytestmark = pytest.mark.integration
TODAY = date(2025, 6, 15)


def add_event(config_id, slug, *, start=1, end=2, league=None, published=True):
    db.session.flush()
    tournament = Tournament(
        url=slug,
        name=slug,
        start_date=datetime(2025, 6, start),
        end_date=datetime(2025, 6, end) if end else None,
        published=published,
        league_id=league,
        registrable_config_id=None if league else config_id,
    )
    db.session.add(tournament)
    db.session.flush()
    return tournament


def add_registration(kind, owner, *, event_id=None, league=None, status="CONFIRMED", registered_day=1):
    model = PlayerRegistration if kind == "player" else TeamRegistration
    args = {kind: owner, "event": event_id, "league_id": league, "status": status}
    if kind == "team":
        args["pseudonym"] = owner
    registration = model(**args, registered_at=datetime(2025, 6, registered_day))
    db.session.add(registration)
    return registration


@pytest.mark.parametrize("kind", ["player", "team"])
def test_past_events_require_confirmation_publication_and_finished_date(tournament, player, team, kind):
    owner = player.id if kind == "player" else team.id
    cases = [
        ("past", 1, 2, True, "CONFIRMED"),
        ("no-end", 3, None, True, "CONFIRMED"),
        ("today", 15, None, True, "CONFIRMED"),
        ("ongoing", 14, 16, True, "CONFIRMED"),
        ("future", 20, 21, True, "CONFIRMED"),
        ("hidden", 1, 2, False, "CONFIRMED"),
        ("cancelled", 1, 2, True, "CANCELLED"),
        ("pending", 1, 2, True, "PENDING_TEAM_APPROVAL" if kind == "player" else "PENDING"),
    ]
    if kind == "player":
        cases.append(("declined", 1, 2, True, "REJECTED"))
    for slug, start, end, published, status in cases:
        add_event(tournament.registrable_config_id, slug, start=start, end=end, published=published)
        add_registration(kind, owner, event_id=slug, status=status)
    # Repeated records and retrospective registration do not inflate the count.
    add_registration(kind, owner, event_id="past", registered_day=10)
    db.session.commit()
    assert past_event_counts(kind, [owner, "no-registrations"], today=TODAY) == {owner: 2, "no-registrations": 0}


@pytest.mark.parametrize("kind", ["player", "team"])
def test_league_counts_each_past_round_once_after_registration(tournament, player, team, kind):
    owner = player.id if kind == "player" else team.id
    league = League(url="season", name="Season", registrable_config_id=tournament.registrable_config_id)
    db.session.add(league)
    for slug, start, end, published in [
        ("missed", 1, 2, True),
        ("same-day", 3, 4, True),
        ("round-two", 8, 9, True),
        ("next-round", 20, 21, True),
        ("hidden-round", 6, 7, False),
    ]:
        add_event(tournament.registrable_config_id, slug, start=start, end=end, league=league.url, published=published)
    add_registration(kind, owner, league=league.url, registered_day=4)
    # Legacy duplicate scopes must not count a round twice or add the season itself.
    add_registration(kind, owner, event_id="round-two")
    db.session.commit()
    assert past_event_counts(kind, [owner], today=TODAY) == {owner: 2}


@pytest.mark.parametrize("kind", ["player", "team"])
def test_unknown_league_join_date_does_not_assume_earlier_rounds(tournament, player, team, kind):
    owner = player.id if kind == "player" else team.id
    league = League(url="season", name="Season", registrable_config_id=tournament.registrable_config_id)
    db.session.add(league)
    add_event(tournament.registrable_config_id, "past-round", league=league.url)
    registration = add_registration(kind, owner, league=league.url)
    db.session.flush()
    registration.registered_at = None
    db.session.commit()
    assert past_event_counts(kind, [owner], today=TODAY) == {owner: 0}


@pytest.mark.parametrize("kind", ["player", "team"])
def test_directory_and_profile_share_count(client, tournament, player, team, monkeypatch, kind):
    monkeypatch.setattr("app.services.profile_stats_service.now_utc_naive", lambda: datetime(2025, 6, 15))
    owner = player.id if kind == "player" else team.id
    add_event(tournament.registrable_config_id, "past-event")
    add_registration(kind, owner, event_id="past-event")
    db.session.commit()
    listing = client.get(f"/_api/{kind}s").get_json()[f"{kind}s"]
    listed = next(item for item in listing if item["id"] == owner)
    profile = client.get(f"/_api/{kind}s/{owner}").get_json()
    assert listed["past_event_count"] == profile["past_event_count"] == 1


@pytest.mark.parametrize("kind", ["player", "team"])
def test_counts_are_batched_for_entire_directory(tournament, player, team, kind):
    model = Player if kind == "player" else Team
    owner = player.id if kind == "player" else team.id
    db.session.add(model(id="second", name="Second", pw_hash="unused"))
    add_event(tournament.registrable_config_id, "past-event")
    for owner_id in (owner, "second"):
        add_registration(kind, owner_id, event_id="past-event")
    db.session.commit()
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        assert past_event_counts(kind, [owner, "second"], today=TODAY) == {owner: 1, "second": 1}
        assert len(statements) == 2
        statements.clear()
        assert past_event_counts(kind, [], today=TODAY) == {}
        assert not statements
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
