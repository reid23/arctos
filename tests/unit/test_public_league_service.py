"""League publication, season boundaries and bounded public projections."""

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event as sqlalchemy_event

from app.error_values import Err, Ok
from app.services.public_league_service import PublicLeagueService
from models import TO, PlayerRegistration, TeamRegistration, db
from tests.unit.test_public_page_service import make_event, make_league


def league_page(slug, user=None, prefix=""):
    result = PublicLeagueService.league(slug, user=user, prefix=prefix)
    assert isinstance(result, Ok)
    return result.unwrap()


def test_directory_keeps_unfinished_seasons_active_without_disclosing_draft_dates(test_db, player):
    league = make_league()
    make_event("visible-past", league=league, start=datetime(2030, 1, 2), end=datetime(2030, 1, 4))
    make_event("private-next", league=league, start=datetime(2030, 7, 1), published=False)
    db.session.commit()
    anonymous = PublicLeagueService.directory(today=date(2030, 5, 1))
    assert anonymous.active_count == 1 and anonymous.past_count == 0
    assert anonymous.cards[0].event_count == 1
    assert anonymous.cards[0].date_start == "2030-01-02"
    assert anonymous.cards[0].date_end == "2030-01-04"
    assert "2030-07" not in str(asdict(anonymous))
    db.session.add(TO(league_id=league.url, user_id=player.id, user_type="player"))
    db.session.commit()
    organizer = PublicLeagueService.directory(today=date(2030, 5, 1), user=player)
    assert organizer.cards[0].event_count == 2
    assert organizer.cards[0].date_end == "2030-07-01"
    assert PublicLeagueService.directory(today=date(2030, 7, 1)).active_count == 1
    assert PublicLeagueService.directory(today=date(2030, 7, 2), view="past").past_count == 1


def test_directory_search_pagination_sort_and_empty_seasons(test_db):
    for slug, name, start in (
        ("zulu", "Côte 100%", datetime(2030, 6, 1, 8)),
        ("alpha", "Côte 100%", datetime(2030, 6, 1, 16)),
        ("beta", "Côte Other", datetime(2030, 7, 1)),
        ("empty", "Côte Empty", None),
    ):
        league = make_league(slug)
        league.name = name
        if start:
            make_event("round-" + slug, league=league, start=start)
    make_league("hidden", published=False)
    db.session.commit()
    first = PublicLeagueService.directory(query=" CÔTE ", page_size=2, today=date(2030, 1, 1), prefix="/arctos")
    assert first.active_count == first.total == 4
    assert [card.url for card in first.cards] == ["/arctos/leagues/alpha", "/arctos/leagues/zulu"]
    assert first.next_url == "/arctos/leagues?q=C%C3%94TE&page=2"
    last = PublicLeagueService.directory(query="CÔTE", page_size=2, page=999999, today=date(2030, 1, 1))
    assert last.page == 2 and last.next_url is None
    assert [card.url for card in last.cards] == ["/leagues/beta", "/leagues/empty"]
    assert last.cards[-1].date_start is None and last.cards[-1].event_count == 0
    literal = PublicLeagueService.directory(query="%", today=date(2030, 1, 1))
    assert literal.total == 2
    past = PublicLeagueService.directory(view="past", today=date(2031, 1, 1))
    assert [card.url for card in past.cards] == ["/leagues/beta", "/leagues/alpha", "/leagues/zulu"]
    assert past.active_count == 1


def test_public_league_visibility_requires_publication_or_typed_organizer(test_db, player, team):
    private = make_league("private", published=False)
    db.session.add(TO(league_id=private.url, user_id=player.id, user_type="team"))
    db.session.commit()
    assert PublicLeagueService.directory(user=player).total == 0
    assert isinstance(PublicLeagueService.league(private.url, user=player), Err)
    assert isinstance(PublicLeagueService.league("missing", user=team), Err)
    db.session.add(TO(league_id=private.url, user_id=player.id, user_type="player"))
    db.session.commit()
    draft = league_page(private.url, player)
    assert not draft.card.published
    assert draft.organizer_links
    assert draft.indexable_app_sections == ()
    assert PublicLeagueService.directory(user=player).total == 1
    assert PublicLeagueService.directory(user=team).total == 0


def test_league_details_expose_only_own_status_and_public_registration_facts(test_db, team, player):
    league = make_league(
        team_registration_open=True,
        player_registration_open=False,
        team_reg_fee=Decimal("12.50"),
        n_max_teams=8,
        waiver_filepath="uploads/private-legal.pdf",
        waiver_sha256="current-hash",
    )
    league.about = "## Season\n\nBring water. <script>unsafe()</script>"
    league.banner_photo = "uploads/league-banners/season.jpg"
    db.session.add_all(
        [
            TeamRegistration(
                league_id=league.url, team=team.id, pseudonym="Private nickname", status="CONFIRMED", paid=True
            ),
            PlayerRegistration(
                league_id=league.url,
                player=player.id,
                status="CONFIRMED",
                paid=False,
                waiver_legal_name_signature="Private Signature",
                waiver_legal_name_signature_sha256="old-hash",
            ),
        ]
    )
    db.session.commit()
    anonymous = league_page(league.url, prefix="/arctos")
    assert anonymous.card.team_count == 1 and anonymous.card.max_teams == 8
    assert anonymous.registration_action.label == "Log in to register"
    assert anonymous.registration_action.url == "/arctos/login?next=%2Farctos%2Fleagues%2Fleague%2Fregister"
    assert anonymous.facts[2].value == "$12.50"
    assert anonymous.waiver_required
    assert anonymous.account_status is None
    assert "Bring water." in anonymous.description_html and "<script>" not in anonymous.description_html
    assert anonymous.banner_url == "/arctos/static/uploads/league-banners/season.jpg"
    assert "Private nickname" not in str(asdict(anonymous))
    assert "private-legal.pdf" not in str(asdict(anonymous))
    owner = league_page(league.url, player)
    assert owner.account_status.waiver_status == "Needs updating"
    assert owner.registration_action.label == "Edit registration"
    assert "Private Signature" not in str(asdict(owner))
    assert "old-hash" not in str(asdict(owner))
    team_page = league_page(league.url, team)
    assert team_page.account_status.paid
    assert team_page.roster_action.url == "/leagues/league/invitations"


def test_closed_registration_and_unsafe_organizer_links(test_db, player):
    league = make_league(team_registration_open=True, player_registration_open=False, terms_link="javascript:unsafe()")
    league.banner_photo = "../private.png"
    db.session.commit()
    page = league_page(league.url, player)
    assert page.registration_action is None
    assert page.registration_message == "Player registration closed"
    assert page.terms_url is None and page.banner_url is None


def test_cancelled_league_registration_remains_visible_without_editing_action(test_db, player):
    league = make_league(player_registration_open=True)
    db.session.add(PlayerRegistration(league_id=league.url, player=player.id, status="CANCELLED"))
    db.session.commit()
    page = league_page(league.url, player)
    assert page.account_status.status_label == "Cancelled"
    assert page.registration_action.label == "Register"
    assert page.roster_action is None
    assert PublicLeagueService.directory(user=player).cards[0].account_status.status == "CANCELLED"


def test_directory_queries_remain_bounded_when_more_leagues_are_added(test_db, player):
    def measure():
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sqlalchemy_event.listen(db.engine, "before_cursor_execute", capture)
        try:
            page = PublicLeagueService.directory(user=player, page_size=2)
        finally:
            sqlalchemy_event.remove(db.engine, "before_cursor_execute", capture)
        return page, statements

    make_league("one")
    make_league("two")
    db.session.commit()
    _ = player.id
    first, first_queries = measure()
    for number in range(10):
        league = make_league(f"extra-{number}")
        db.session.add(PlayerRegistration(league_id=league.url, player=player.id, status="CONFIRMED"))
    db.session.commit()
    _ = player.id
    larger, larger_queries = measure()
    assert len(first.cards) == len(larger.cards) == 2
    assert larger.total == 12
    assert len(larger_queries) == len(first_queries)
    assert any("LIMIT" in sql.upper() for sql in larger_queries)
