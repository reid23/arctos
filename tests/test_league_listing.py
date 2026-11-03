"""League lifecycle includes every round while visible metadata respects access."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from models import League, Player, PlayerRegistration, Team, TeamRegistration, TO, Tournament, db
from tests.utils import login_as, make_registrable_config

pytestmark = pytest.mark.integration


def add_league(slug, *, published=True, **config):
    league = League(
        url=slug,
        name=slug,
        published=published,
        registrable_config_id=make_registrable_config(**config).id,
    )
    db.session.add(league)
    db.session.flush()
    return league


def add_round(league, slug, start, end=None, *, published=True):
    db.session.add(
        Tournament(
            url=slug,
            name=slug,
            league_id=league.url,
            start_date=datetime.fromisoformat(start),
            end_date=datetime.fromisoformat(end) if end else None,
            published=published,
        )
    )


def listing(client, today="2026-10-26"):
    response = client.get("/_api/leagues", query_string={"today": today})
    assert response.status_code == 200
    return response.get_json()


def test_listing_ranges_cover_all_public_rounds_with_missing_end_fallback(client):
    league = add_league("mixed-season", team_registration_open=False)
    add_round(league, "past-round", "2025-01-02T09:00:00", "2025-01-04T18:00:00")
    add_round(league, "future-round", "2027-08-03T09:00:00", "2027-08-05T18:00:00")
    add_round(league, "last-round-no-end", "2027-09-06T12:30:00")
    # Hidden rounds must neither extend the range nor increase the count.
    add_round(league, "hidden-earlier", "2024-01-01", published=False)
    add_round(league, "hidden-later", "2028-12-31", published=False)
    db.session.commit()

    payload = listing(client)
    assert payload["event_dates"] == {
        league.url: {
            "start_date": "2025-01-02T09:00:00",
            "end_date": "2027-09-06T12:30:00",
            "event_count": 3,
        }
    }
    assert payload["leagues"][0]["registration_open"] is False
    assert payload["past_leagues"] == []


def test_listing_uses_latest_end_instead_of_latest_start(client):
    league = add_league("overlapping-rounds")
    add_round(league, "long-round", "2025-06-01", "2025-06-05T18:00:00")
    add_round(league, "short-round", "2025-06-03")
    db.session.commit()

    assert listing(client)["event_dates"][league.url] == {
        "start_date": "2025-06-01T00:00:00",
        "end_date": "2025-06-05T18:00:00",
        "event_count": 2,
    }


def test_listing_empty_and_hidden_rounds_have_unknown_dates_and_private_leagues_are_absent(client):
    empty = add_league("empty-season", player_registration_open=True)
    hidden_rounds = add_league("draft-rounds")
    private = add_league("private-season", published=False)
    add_round(hidden_rounds, "draft", "2025-01-01", published=False)
    add_round(private, "private-season-public-round", "2025-01-01")
    db.session.commit()

    payload = listing(client)
    assert {league["league_url"] for league in payload["leagues"]} == {empty.url, hidden_rounds.url}
    assert payload["event_dates"] == {
        slug: {"start_date": None, "end_date": None, "event_count": 0} for slug in (empty.url, hidden_rounds.url)
    }
    assert private.url not in payload["team_counts"]
    assert private.url not in payload["user_reg_status"]
    assert payload["past_leagues"] == [hidden_rounds.url]


@pytest.mark.parametrize("published", [True, False])
@pytest.mark.parametrize(
    ("end", "today", "past"),
    [
        ("2026-10-26T18:00:00", "2026-10-25", False),
        ("2026-10-26T18:00:00", "2026-10-26", False),
        ("2026-10-26T18:00:00", "2026-10-27", True),
        (None, "2026-10-24", False),
        (None, "2026-10-25", False),
        (None, "2026-10-26", True),
    ],
)
def test_season_becomes_past_only_after_its_last_round_day(client, published, end, today, past):
    league = add_league("season")
    add_round(league, "first-round", "2026-04-01")
    add_round(league, "final-round", "2026-10-25T09:00:00", end, published=published)
    db.session.commit()

    payload = listing(client, today)
    assert payload["past_leagues"] == ([league.url] if past else [])
    if not published:
        assert payload["event_dates"][league.url] == {
            "start_date": "2026-04-01T00:00:00",
            "end_date": "2026-04-01T00:00:00",
            "event_count": 1,
        }


@pytest.mark.parametrize("has_finished_round", [False, True])
def test_unknown_round_dates_keep_a_season_active(client, has_finished_round):
    league = add_league("undated-season")
    add_league("empty-season")
    if has_finished_round:
        add_round(league, "finished-round", "2025-04-01")
    add_round(league, "undated-draft", "2025-05-01", published=False)
    db.session.flush()
    # Legacy SQLite rows can contain an empty date even though model-created
    # rounds require a datetime. Missing draft dates cannot prove a season ended.
    db.session.execute(text("UPDATE tournaments SET start_date = '' WHERE url = 'undated-draft'"))
    db.session.commit()

    payload = listing(client)
    assert payload["past_leagues"] == []
    assert payload["event_dates"][league.url]["event_count"] == int(has_finished_round)


def test_listing_without_today_uses_the_current_utc_calendar_day(client):
    today = datetime.now(timezone.utc).date()
    ended = add_league("ended-yesterday")
    ongoing = add_league("ends-today")
    add_round(ended, "yesterday", (today - timedelta(days=1)).isoformat())
    add_round(ongoing, "today", today.isoformat())
    db.session.commit()

    response = client.get("/_api/leagues")
    assert response.status_code == 200
    assert response.get_json()["past_leagues"] == [ended.url]


@pytest.mark.parametrize(
    "today",
    [
        "",
        "20261026",
        "2026-1-1",
        "2026-02-29",
        "2026-13-01",
        "2026-10-26T00:00:00",
        "2026-W44-1",
        " 2026-10-26",
        "0000-01-01",
    ],
)
def test_listing_rejects_invalid_calendar_day(client, today):
    response = client.get("/_api/leagues", query_string={"today": today})
    assert response.status_code == 400
    assert response.get_json() == {"error": "today must be a valid calendar date in YYYY-MM-DD format."}


def test_listing_accepts_a_valid_leap_day(client):
    assert listing(client, "2028-02-29")["past_leagues"] == []


@pytest.mark.parametrize("owner_kind", ["player", "team"])
@pytest.mark.parametrize("viewer", ["guest", "unrelated", "league_organizer", "event_organizer", "same_id_other_type"])
def test_league_season_dates_match_the_rounds_visible_on_its_page(
    app, client, player, team, monkeypatch, owner_kind, viewer
):
    owner = player if owner_kind == "player" else team
    unrelated = team if owner_kind == "player" else player
    league = add_league("season-with-draft")
    add_round(league, "past-public-round", "2025-06-01")
    add_round(league, "future-draft-round", "2028-12-30", "2028-12-31", published=False)
    db.session.flush()
    if viewer in {"league_organizer", "same_id_other_type"}:
        db.session.add(TO(league_id=league.url, user_id=owner.id, user_type=owner_kind))
    elif viewer == "event_organizer":
        # League events inherit league organizers, not event-specific TO rows.
        db.session.add(TO(event="future-draft-round", user_id=owner.id, user_type=owner_kind))
    if viewer == "same_id_other_type":
        other_model = Team if owner_kind == "player" else Player
        colliding = other_model(id=owner.id, name="Same username, other account type", pw_hash="unused")
        db.session.add(colliding)
    db.session.commit()
    if viewer != "guest":
        login_as(client, unrelated if viewer == "unrelated" else owner)
    if viewer == "same_id_other_type":
        # Exercise the account-type boundary independently of the ID-only
        # session loader; ordinary registration prevents these collisions.
        monkeypatch.setattr(app.login_manager, "_user_callback", lambda _user_id: colliding)

    payload = listing(client)
    detail = client.get(f"/_api/leagues/{league.url}")
    assert detail.status_code == 200
    events = detail.get_json()["events"]
    can_see_drafts = viewer == "league_organizer"
    assert [event["url"] for event in events] == (
        ["past-public-round", "future-draft-round"] if can_see_drafts else ["past-public-round"]
    )
    assert payload["event_dates"][league.url] == {
        "start_date": "2025-06-01T00:00:00",
        "end_date": "2028-12-31T00:00:00" if can_see_drafts else "2025-06-01T00:00:00",
        "event_count": len(events),
    }
    assert payload["past_leagues"] == []
    # The lifecycle is identical for visitors and organizers; only event
    # metadata differs. Once the unpublished final round ends, both see Past.
    assert listing(client, "2029-01-01")["past_leagues"] == [league.url]


@pytest.mark.parametrize("league_count", [0, 1, 5])
def test_listing_batches_round_aggregation_for_the_directory(client, league_count):
    for index in range(league_count):
        league = add_league(f"season-{index}")
        add_round(league, f"round-{index}", "2025-06-01")
    db.session.commit()
    round_queries = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if "from tournaments" in statement.lower():
            round_queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        payload = listing(client)
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert len(payload["event_dates"]) == league_count
    assert len(round_queries) == (2 if league_count else 0)
    assert all(summary["event_count"] == 1 for summary in payload["event_dates"].values())
    assert set(payload["past_leagues"]) == {f"season-{index}" for index in range(league_count)}


@pytest.mark.parametrize("kind", ["player", "team"])
def test_listing_retains_user_registration_payment_waiver_and_team_counts(client, player, team, kind):
    league = add_league("registered-season", player_registration_open=True, waiver_filepath="waiver.pdf")
    db.session.add(TeamRegistration(league_id=league.url, team=team.id, pseudonym="Team", status="CONFIRMED"))
    if kind == "player":
        db.session.add(
            PlayerRegistration(league_id=league.url, player=player.id, status="PENDING_TEAM_APPROVAL", amount_paid=12.5)
        )
    db.session.commit()
    login_as(client, player if kind == "player" else team)

    payload = listing(client)
    assert payload["team_counts"] == {league.url: 1}
    assert payload["user_reg_status"][league.url] == {
        "type": kind,
        "status": "PENDING_TEAM_APPROVAL" if kind == "player" else "CONFIRMED",
        "paid": False,
        "amount_paid": 12.5 if kind == "player" else 0.0,
        "waiver_required": kind == "player",
        "waiver_status": "NOT_SIGNED" if kind == "player" else None,
    }
    assert payload["event_dates"][league.url]["event_count"] == 0
    assert payload["past_leagues"] == []


@pytest.mark.parametrize("kind", ["player", "team"])
def test_new_private_league_remains_discoverable_to_its_creator(client, player, team, kind):
    owner = player if kind == "player" else team
    login_as(client, owner)
    response = client.post(
        "/_api/create-league", data={"league_name": "New season", "league_url": "new-private-season"}
    )
    assert response.status_code == 200
    slug = response.get_json()["league_url"]
    assert TO.query.filter_by(league_id=slug, user_id=owner.id, user_type=kind).one()

    payload = listing(client)
    assert [(league["league_url"], league["published"]) for league in payload["leagues"]] == [(slug, False)]
    assert payload["event_dates"][slug] == {"start_date": None, "end_date": None, "event_count": 0}
    assert payload["team_counts"][slug] == 0
    assert payload["past_leagues"] == []


@pytest.mark.parametrize("owner_kind", ["player", "team"])
@pytest.mark.parametrize("viewer", ["guest", "same_id_other_type", "assigned_to", "unrelated"])
def test_private_league_discovery_requires_typed_organizer_membership(
    app, client, player, team, monkeypatch, owner_kind, viewer
):
    owner = player if owner_kind == "player" else team
    other_model = Team if owner_kind == "player" else Player
    colliding = other_model(id=owner.id, name="Same username, other account type", pw_hash="unused")
    assigned = other_model(id="assigned-organizer", name="Assigned organizer", pw_hash="unused")
    unrelated = other_model(id="unrelated", name="Unrelated account", pw_hash="unused")
    db.session.add_all([colliding, assigned, unrelated])
    public = add_league("public-season")
    private = add_league("private-season", published=False)
    other_private = add_league("other-private-season", published=False)
    add_round(other_private, "finished-private-round", "2025-01-01", published=False)
    db.session.add_all(
        [
            TO(league_id=private.url, user_id=owner.id, user_type=owner_kind),
            TO(
                league_id=private.url,
                user_id=assigned.id,
                user_type="team" if owner_kind == "player" else "player",
            ),
        ]
    )
    add_round(private, "public-round-in-draft-league", "2025-06-01")
    add_round(private, "draft-round", "2028-12-31", published=False)
    db.session.commit()
    if viewer != "guest":
        login_as(client, {"same_id_other_type": colliding, "assigned_to": assigned, "unrelated": unrelated}[viewer])
    if viewer == "same_id_other_type":
        # Normal registration prevents cross-type duplicate usernames. Inject
        # the authenticated principal here to exercise the authorization type
        # boundary independently of the legacy ID-only session user loader.
        monkeypatch.setattr(app.login_manager, "_user_callback", lambda _user_id: colliding)

    payload = listing(client)
    expected = {public.url, private.url} if viewer == "assigned_to" else {public.url}
    assert {league["league_url"] for league in payload["leagues"]} == expected
    assert set(payload["event_dates"]) == set(payload["team_counts"]) == expected
    assert set(payload["user_reg_status"]) <= expected
    assert payload["past_leagues"] == []
    assert listing(client, "2029-01-01")["past_leagues"] == ([private.url] if viewer == "assigned_to" else [])
    detail = client.get(f"/_api/leagues/{private.url}")
    if viewer == "assigned_to":
        assert detail.status_code == 200
        assert [event["url"] for event in detail.get_json()["events"]] == [
            "public-round-in-draft-league",
            "draft-round",
        ]
        assert payload["event_dates"][private.url] == {
            "start_date": "2025-06-01T00:00:00",
            "end_date": "2028-12-31T00:00:00",
            "event_count": 2,
        }
    else:
        assert detail.status_code == 403
        assert "events" not in detail.get_json()
