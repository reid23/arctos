"""League browsing uses public round dates without changing registration data."""

from datetime import datetime

import pytest
from sqlalchemy import event

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


def listing(client):
    response = client.get("/_api/leagues")
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
    assert len(round_queries) == (1 if league_count else 0)
    assert all(summary["event_count"] == 1 for summary in payload["event_dates"].values())


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
    add_league("other-private-season", published=False)
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
    if viewer == "assigned_to":
        # Organizer visibility does not leak unpublished round metadata into
        # the listing range, which must match the public event calendar.
        assert payload["event_dates"][private.url] == {
            "start_date": "2025-06-01T00:00:00",
            "end_date": "2025-06-01T00:00:00",
            "event_count": 1,
        }
