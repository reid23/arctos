"""Publication, scope, calendar, and bounded-query contracts for public HTML."""

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event as sqlalchemy_event

from app.error_values import Err, Ok
from app.services.public_page_service import PublicPageService, safe_terms_url
from models import League, PlayerRegistration, TeamRegistration, TO, Tournament, db
from tests.utils import make_registrable_config


def make_event(slug, *, start=None, end=None, published=True, league=None, **kwargs):
    row = Tournament(
        url=slug,
        name=kwargs.pop("name", slug),
        start_date=start or datetime(2030, 6, 15),
        end_date=end,
        location=kwargs.pop("location", ""),
        published=published,
        league_id=league.url if league else None,
        registrable_config_id=None if league else make_registrable_config().id,
        **kwargs,
    )
    db.session.add(row)
    db.session.flush()
    return row


def make_league(slug="league", *, published=True, **config):
    row = League(
        url=slug, name="Pacific League", published=published, registrable_config_id=make_registrable_config(**config).id
    )
    db.session.add(row)
    db.session.flush()
    return row


def event_page(slug, user=None, prefix=""):
    result = PublicPageService.event(slug, user=user, prefix=prefix)
    assert isinstance(result, Ok)
    return result.unwrap()


@pytest.mark.unit
def test_public_directory_search_visibility_literal_wildcards_and_pages(test_db):
    league = make_league()
    make_event("one", name="Beach & Field", location="Harbor")
    make_event("two", name="Second", league=league)
    make_event("three", name="100% Effort")
    make_event("hidden", name="Secret", published=False)
    db.session.commit()
    default = PublicPageService.directory(today=date(2030, 1, 1), page_size=2)
    assert default.total == 3
    assert len(default.cards) == 2
    assert default.next_url == "/?page=2"
    second = PublicPageService.directory(today=date(2030, 1, 1), page_size=2, page=999999)
    assert second.page == 2
    assert len(second.cards) == 1
    assert second.previous_url == "/"
    for query, expected in (("harBOR", "one"), ("pacific", "two"), ("%", "three"), ("Beach &", "one")):
        page = PublicPageService.directory(query=query, today=date(2030, 1, 1))
        assert [card.url for card in page.cards] == [f"/{expected}"]
    assert PublicPageService.directory(query="Secret", today=date(2030, 1, 1)).total == 0


@pytest.mark.unit
def test_private_league_title_is_not_searchable_but_published_event_remains_visible(test_db, team):
    league = make_league(published=False)
    make_event("public-round", league=league)
    db.session.commit()
    page = PublicPageService.directory(today=date(2030, 1, 1))
    assert page.total == 1
    assert page.cards[0].league_name is None
    assert page.cards[0].league_url is None
    assert PublicPageService.directory(query="Pacific", today=date(2030, 1, 1)).total == 0
    detail = event_page("public-round")
    assert detail.registration_action is None
    assert detail.roster_action is None
    assert not detail.card.registration_open
    assert "/leagues/league" not in str(asdict(detail))
    db.session.add(
        TeamRegistration(league_id=league.url, team=team.id, pseudonym="Team", status="CONFIRMED", paid=True)
    )
    db.session.commit()
    participant = event_page("public-round", team)
    assert participant.account_status.status == "CONFIRMED"
    assert participant.registration_action is None
    assert participant.roster_action is None
    assert "/leagues/league" not in str(asdict(participant))


@pytest.mark.unit
def test_directory_calendar_days_and_deterministic_order(test_db):
    make_event("z", start=datetime(2030, 6, 15), end=datetime(2030, 6, 17))
    make_event("a", start=datetime(2030, 6, 15))
    make_event("past", start=datetime(2030, 6, 14, 23, 59))
    db.session.commit()
    page = PublicPageService.directory(today=date(2030, 6, 15))
    assert [card.url for card in page.cards] == ["/a", "/z"]
    assert page.past_count == 1
    assert page.cards[0].date_start == "2030-06-15"
    assert page.cards[0].date_label == "Jun 15, 2030"
    assert page.cards[1].date_label == "Jun 15, 2030 – Jun 17, 2030"
    assert PublicPageService.directory(view="past", today=date(2030, 6, 18)).cards[0].url == "/z"


@pytest.mark.unit
def test_event_and_league_organizer_scopes_match_permission_service(test_db, player):
    league = make_league(published=False)
    make_event("league-draft", published=False, league=league)
    standalone = make_event("standalone-draft", published=False)
    make_event("unrelated-draft", published=False)
    db.session.add_all(
        [
            TO(user_id=player.id, user_type="player", league_id=league.url),
            TO(user_id=player.id, user_type="player", event=standalone.url),
        ]
    )
    db.session.commit()
    anonymous = PublicPageService.directory(today=date(2030, 1, 1))
    assert anonymous.total == 0
    private = PublicPageService.directory(user=player, today=date(2030, 1, 1))
    assert {card.url for card in private.cards} == {"/league-draft", "/standalone-draft"}
    assert isinstance(PublicPageService.event("league-draft"), Err)
    assert isinstance(PublicPageService.event("missing"), Err)
    assert event_page("league-draft", player).organizer_links


@pytest.mark.unit
def test_league_count_and_own_registration_are_scoped_and_batched(test_db, team, player):
    league = make_league(team_registration_open=True, player_registration_open=True, team_reg_fee=Decimal("12.50"))
    make_event("round-one", league=league)
    make_event("round-two", league=league)
    db.session.add(
        TeamRegistration(
            league_id=league.url,
            team=team.id,
            pseudonym="Private nickname",
            status="CONFIRMED",
            paid=True,
            amount_paid=Decimal("12.50"),
        )
    )
    db.session.add(
        PlayerRegistration(league_id=league.url, player=player.id, status="PENDING_TEAM_APPROVAL", paid=False)
    )
    db.session.commit()
    page = event_page("round-one", team)
    assert page.card.team_count == 1
    assert page.registration_action.url == "/leagues/league/register"
    assert page.registration_action.label == "Edit registration"
    assert page.roster_action.url == "/leagues/league/invitations"
    assert page.account_status.paid is True
    assert page.facts[2].value == "$12.50"
    assert page.league_registration
    assert event_page("round-two", player).registration_action.label == "Edit registration"
    anonymous = event_page("round-one")
    assert anonymous.account_status is None
    assert "Private nickname" not in str(asdict(anonymous))
    assert anonymous.registration_action.url == "/login?next=%2Fleagues%2Fleague%2Fregister"


@pytest.mark.unit
def test_player_waiver_state_does_not_expose_signature_or_amount(test_db, tournament, player):
    cfg = tournament.registrable_config
    cfg.waiver_filepath = "uploads/waiver.pdf"
    cfg.waiver_sha256 = "current"
    reg = PlayerRegistration(
        event=tournament.url,
        player=player.id,
        status="CONFIRMED",
        paid=True,
        amount_paid=Decimal("42.00"),
        waiver_legal_name_signature="Confidential legal name",
        waiver_legal_name_signature_sha256="old",
    )
    db.session.add(reg)
    db.session.commit()
    page = event_page(tournament.url, player)
    assert page.account_status.waiver_status == "Needs updating"
    assert "Confidential legal name" not in str(asdict(page))
    assert "42.00" not in str(asdict(page))
    reg.waiver_legal_name_signature_sha256 = "current"
    db.session.commit()
    assert event_page(tournament.url, player).account_status.waiver_status == "Signed"
    reg.status = "CANCELLED"
    db.session.commit()
    assert event_page(tournament.url, player).account_status is None
    assert event_page(tournament.url, player).registration_action.label == "Register"


@pytest.mark.unit
def test_event_html_sanitization_calendar_dates_and_user_supplied_links(test_db, tournament):
    tournament.about = '**Useful text**<script>alert(1)</script><img src="x" onerror="alert(2)">'
    tournament.registrable_config.terms_link = "javascript:alert(3)"
    tournament.banner_photo = "https://untrusted.example/image.jpg"
    db.session.commit()
    page = event_page(tournament.url)
    assert "<strong>Useful text</strong>" in page.description_html
    assert "<script" not in page.description_html
    assert "onerror" not in page.description_html
    assert page.terms_url is None
    assert page.banner_url is None
    assert "UTC" not in page.card.date_label
    assert page.facts[2].value == "Free"


@pytest.mark.unit
def test_schedule_and_bracket_publication_links_use_existing_permissions(test_db, tournament, player):
    tournament.schedule_published = False
    tournament.bracket_published = False
    db.session.commit()
    page = event_page(tournament.url)
    assert "Schedule" not in {link.label for link in page.nav_links}
    assert "Bracket" not in {link.label for link in page.nav_links}
    db.session.add(TO(user_id=player.id, user_type="player", event=tournament.url))
    db.session.commit()
    organizer = event_page(tournament.url, player)
    assert {"Schedule", "Bracket"} <= {link.label for link in organizer.nav_links}
    assert organizer.organizer_links[-1].url.endswith("?tab=app")


@pytest.mark.unit
def test_account_specific_registration_closed_and_prefix_links(test_db, tournament, player, team):
    tournament.registrable_config.player_registration_open = False
    tournament.registrable_config.team_registration_open = True
    db.session.commit()
    page = event_page(tournament.url, player, "/arctos")
    assert page.registration_action is None
    assert page.registration_message == "Player registration closed"
    team_page = event_page(tournament.url, team, "/arctos")
    assert team_page.registration_action.url == f"/arctos/{tournament.url}/register"
    assert all(link.url.startswith("/arctos/") for link in team_page.nav_links)


@pytest.mark.unit
def test_directory_query_count_is_independent_of_number_of_cards(test_db, team):
    for index in range(26):
        make_event(f"event-{index:02d}")
    db.session.commit()
    db.session.expire_all()
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sqlalchemy_event.listen(db.engine, "before_cursor_execute", record)
    try:
        page = PublicPageService.directory(user=team, today=date(2030, 1, 1), page_size=24)
    finally:
        sqlalchemy_event.remove(db.engine, "before_cursor_execute", record)
    assert len(page.cards) == 24
    assert page.total == 26
    assert len(statements) <= 7, statements
    assert all(
        "tournaments.about" not in statement and "tournaments.bracket AS" not in statement for statement in statements
    )


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "//evil.example/a",
        "https://user:pass@evil.example/",  # pragma: allowlist secret -- synthetic URL credentials
        "https://good.example/\n",
        "/\\evil.example",
        "data:text/html,test",
    ],
)
def test_terms_reject_unsafe_urls(value):
    assert safe_terms_url(value) is None


@pytest.mark.unit
def test_general_head_ref_can_see_unpublished_schedule_link(test_db, tournament, head_ref_player):
    tournament.schedule_published = False
    tournament.bracket_published = False
    db.session.commit()
    page = event_page(tournament.url, head_ref_player)
    labels = {link.label for link in page.nav_links}
    assert "Schedule" in labels
    assert "Bracket" not in labels
    assert not page.organizer_links


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/path",
        "https://example.test/?",
        "https://example.test/#",
        "javascript:bad",
        "https://user:pass@example.test",  # pragma: allowlist secret -- synthetic URL credentials
        "https://example.test:0",
        "https://example.test:invalid",
        "https://exam\x00ple.test",
    ],
)
def test_canonical_origin_configuration_rejects_non_origins(origin):
    from app import create_app

    with pytest.raises(ValueError):
        create_app({"PUBLIC_CANONICAL_ORIGIN": origin})


@pytest.mark.unit
@pytest.mark.parametrize("query, expected", [("åLAND", "/aland"), ("mÜNCHEN", "/munich"), ("öRESUND", "/round")])
def test_public_search_preserves_unicode_lowercase_matching(test_db, query, expected):
    league = make_league()
    league.name = "Öresund League"
    make_event("aland", name="Åland Cup")
    make_event("munich", location="München")
    make_event("round", league=league)
    db.session.commit()
    page = PublicPageService.directory(query=query, today=date(2030, 1, 1), page_size=1)
    assert page.total == 1
    assert [card.url for card in page.cards] == [expected]
