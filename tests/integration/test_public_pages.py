"""Public documents work without executing application JavaScript."""

from datetime import datetime
from html.parser import HTMLParser
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.public_page_service import PublicPageService
from app.services.public_league_service import PublicLeagueService
from models import TO, db
from tests.unit.test_public_page_service import make_event, make_league
from tests.utils import login_as


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def all(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


@pytest.fixture
def public_client(client, app, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "PUBLIC_HTML_ENABLED", True)
    monkeypatch.setitem(app.config, "PUBLIC_CANONICAL_ORIGIN", "https://arctos.example")
    monkeypatch.setitem(app.config, "PUBLIC_HTML_PAGE_SIZE", 2)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets/app-dxh123456.js").write_text("/* Test entry: not executable application. */")
    (dist / "arctos-assets.json").write_text(json.dumps({"version": 1, "entry": "/assets/app-dxh123456.js"}))
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(dist))
    return client


def test_home_contains_useful_html_before_background_app_startup(public_client, tournament):
    response = public_client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    tags = Elements(html)
    assert tournament.name in html
    assert tournament.location in html
    assert "<title>Events | Arctos</title>" in html
    assert any(form.get("method", "get").lower() == "get" for form in tags.all("form"))
    assert any(field.get("name") == "q" for field in tags.all("input"))
    assert any(link.get("href") == f"/{tournament.url}" for link in tags.all("a"))
    assert "boot.js" not in html
    assert 'data-public-background="true"' in html
    assert 'data-entry="/assets/app-dxh123456.js"' in html
    assert 'id="public-document"' in html
    assert 'id="main" aria-hidden="true" inert' in html
    assert "background.js" in html
    assert not any(script.get("type") == "module" for script in tags.all("script"))
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
    assert "Cookie" in response.headers["Vary"]


def test_public_html_remains_usable_without_a_prepared_application(
    public_client, app, monkeypatch, tournament, tmp_path
):
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path / "missing"))
    response = public_client.get("/")
    assert response.status_code == 200
    assert tournament.name in response.get_data(as_text=True)
    assert "background.js" not in response.get_data(as_text=True)


def test_directory_html_and_api_read_current_data(public_client, tournament):
    assert tournament.name in public_client.get("/").get_data(as_text=True)
    tournament.name = "Updated event name"
    db.session.commit()
    assert tournament.name in public_client.get("/").get_data(as_text=True)
    cards = public_client.get("/_api/public/events").get_json()["cards"]
    assert next(card for card in cards if card["url"] == f"/{tournament.url}")["name"] == tournament.name


def test_interactive_directory_shares_html_search_pagination_and_privacy(public_client):
    for index in range(3):
        make_event(f"shared-{index}", name=f"A & B Cup {index}", start=datetime(2020, 1, index + 1))
    make_event("private-shared", name="A & B hidden", published=False)
    db.session.commit()
    first = public_client.get("/_api/public/events?q=A%20%26%20B&view=past")
    assert first.status_code == 200
    data = first.get_json()
    assert data["query"] == "A & B"
    assert data["page"] == 1 and data["page_size"] == 2 and data["total"] == 3
    assert len(data["cards"]) == 2
    assert data["next_url"] == "/?q=A+%26+B&view=past&page=2"
    assert "private-shared" not in first.get_data(as_text=True)
    assert all(card["account_status"] is None for card in data["cards"])
    html = public_client.get("/?q=A%20%26%20B&view=past").get_data(as_text=True)
    for card in data["cards"]:
        assert card["name"].replace("&", "&amp;") in html
    second = public_client.get("/_api/public/events?q=A%20%26%20B&view=past&page=2").get_json()
    assert [card["name"] for card in second["cards"]] == ["A & B Cup 0"]
    assert first.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in first.vary


def test_interactive_directory_database_failure_is_json_503(public_client, monkeypatch):
    def unavailable(**kwargs):
        raise SQLAlchemyError("test unavailable")

    monkeypatch.setattr(PublicPageService, "directory", unavailable)
    response = public_client.get("/_api/public/events")
    assert response.status_code == 503
    assert response.is_json
    assert response.headers["Cache-Control"] == "private, no-store"


def test_waiver_lookup_uses_configured_isolated_static_directory(app, tmp_path, monkeypatch):
    from app.routes.waivers import _waiver_disk_path

    waiver = tmp_path / "uploads" / "waivers" / "isolated-cup" / "waiver.pdf"
    waiver.parent.mkdir(parents=True)
    waiver.write_bytes(b"local fixture")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    with app.app_context():
        assert _waiver_disk_path("isolated-cup") == str(waiver)


def test_header_sign_in_preserves_directory_search_and_page(public_client):
    path = "/?q=Rock%26Roll&view=past&page=2"
    tags = Elements(public_client.get(path).get_data(as_text=True))
    sign_in = next(link["href"] for link in tags.all("a") if link.get("href", "").startswith("/login?"))
    assert parse_qs(urlsplit(sign_in).query)["next"] == [path]


def test_event_details_are_escaped_and_sanitized_in_first_response(public_client, tournament):
    tournament.name = '<img src=x onerror="alert(1)">'
    tournament.about = "## Find us\n\nBring water. <script>alert(2)</script>"
    db.session.commit()
    response = public_client.get(f"/{tournament.url}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "&lt;img" in html
    assert '<img src=x onerror="alert(1)">' not in html
    assert "Bring water." in html
    assert "<script>alert(2)</script>" not in html
    assert "Free" in html
    assert "Log in to register" in html
    assert 'datetime="' in html
    assert "boot.js" not in html


@pytest.mark.parametrize("query", ["?tab=teams", "?tab=sidecomps", "?tab=app", "?app=1", "?tab=future-panel"])
def test_event_query_panels_are_application_entries(public_client, tournament, query):
    response = public_client.get(f"/{tournament.url}{query}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="app-boot"' in html
    assert 'data-entry="/assets/app-dxh123456.js"' in html
    assert tournament.name in html
    assert_robots(response, noindex=query not in {"?tab=teams", "?tab=sidecomps"})


def test_info_query_stays_public_and_registration_has_contextual_shell(public_client, tournament):
    assert "boot.js" not in public_client.get(f"/{tournament.url}?tab=info").get_data(as_text=True)
    response = public_client.get(f"/{tournament.url}/register")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert tournament.name in html
    assert f'href="/{tournament.url}"' in html
    assert 'data-workflow="registration"' in html.lower()
    assert "boot.js" in html


def test_private_event_and_unknown_page_have_same_real_404(public_client, tournament):
    tournament.published = False
    db.session.commit()
    private = public_client.get(f"/{tournament.url}")
    missing = public_client.get("/missing-event")
    assert private.status_code == missing.status_code == 404
    for response in (private, missing):
        assert "<h1>Page not found</h1>" in response.get_data(as_text=True)
        assert "This page may have moved or may not be public." in response.get_data(as_text=True)
    assert tournament.name.encode() not in private.get_data()
    assert private.headers["X-Robots-Tag"] == "noindex"
    assert "boot.js" not in private.get_data(as_text=True)
    assert public_client.get(f"/{tournament.url}/register").status_code == 404


def test_organizer_can_find_draft_and_it_opens_the_application(public_client, tournament, player):
    tournament.published = False
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    login_as(public_client, player)
    directory = public_client.get("/").get_data(as_text=True)
    assert tournament.name in directory
    assert "Draft" in directory
    draft = public_client.get(f"/{tournament.url}")
    assert draft.status_code == 200
    assert "boot.js" in draft.get_data(as_text=True)
    assert draft.headers["X-Robots-Tag"] == "noindex"


def test_native_search_and_pagination_preserve_query_and_view(public_client):
    for index in range(3):
        make_event(f"cup-{index}", name=f"A & B Cup {index}", start=datetime(2020, 1, index + 1))
    make_event("other", name="Other", start=datetime(2020, 2, 1))
    db.session.commit()
    response = public_client.get("/?q=A+%26+B&view=past")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    links = [tag.get("href") for tag in Elements(html).all("a")]
    assert "/?q=A+%26+B&view=past&page=2" in links
    assert "Other" not in html
    second = public_client.get("/?q=A+%26+B&view=past&page=2").get_data(as_text=True)
    assert "A &amp; B Cup 0" in second
    assert "A &amp; B Cup 1" not in second
    assert response.headers["X-Robots-Tag"] == "noindex"


@pytest.mark.parametrize("value", ["-4", "1e100", "9999999999999999999999999", "hello"])
def test_invalid_page_input_remains_a_bounded_valid_page(public_client, tournament, value):
    response = public_client.get("/", query_string={"page": value})
    assert response.status_code == 200
    assert tournament.name in response.get_data(as_text=True)


def test_canonical_uses_configured_origin_not_request_host(public_client, tournament, app, monkeypatch):
    html = public_client.get(f"/{tournament.url}", headers={"Host": "untrusted.example"}).get_data(as_text=True)
    assert f'href="https://arctos.example/{tournament.url}"' in html
    assert "untrusted.example" not in html
    monkeypatch.setitem(app.config, "PUBLIC_CANONICAL_ORIGIN", "")
    html = public_client.get(f"/{tournament.url}").get_data(as_text=True)
    assert 'rel="canonical"' not in html


def test_subpath_urls_and_canonical(public_client, tournament):
    response = public_client.get(f"/{tournament.url}", environ_overrides={"SCRIPT_NAME": "/arctos"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'href="https://arctos.example/arctos/{tournament.url}"' in html
    assert f'href="/arctos/{tournament.url}?tab=teams"' in html
    assert "/arctos/login?next=%2Farctos%2F" in html


def test_missing_api_and_static_requests_never_receive_public_or_app_document(public_client):
    for path in ("/_api/no-such-endpoint", "/api/no-such-endpoint"):
        response = public_client.get(path)
        assert response.status_code == 404
        assert response.is_json
        assert response.json == {"error": "Not found"}
    for path in ("/static/no-such-file.css", "/assets/no-such-file.wasm"):
        response = public_client.get(path)
        assert response.status_code == 404
        assert "app-boot" not in response.get_data(as_text=True)
        assert "public-content" not in response.get_data(as_text=True)
    assert public_client.get("/_api/me").status_code == 401
    assert public_client.get("/_api/tournaments").is_json


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/players",
        "/teams",
        "/stones",
        "/create-event",
        "/docs.md",
        "/developers/apps",
        "/oauth/authorize",
    ],
)
def test_existing_application_paths_keep_application_ownership(public_client, path):
    response = public_client.get(path)
    assert response.status_code == 200
    assert "boot.js" in response.get_data(as_text=True)
    assert_robots(response, noindex=path not in {"/players", "/teams", "/docs.md"})


def test_flag_off_returns_application_shell_at_root_and_event(public_client, tournament, app, monkeypatch):
    monkeypatch.setitem(app.config, "PUBLIC_HTML_ENABLED", False)
    for path in ("/", f"/{tournament.url}"):
        response = public_client.get(path)
        assert response.status_code == 200
        assert "boot.js" in response.get_data(as_text=True)
        assert 'data-public-html="false"' in response.get_data(as_text=True)
        assert_robots(response, noindex=False)


def test_missing_application_build_is_503_but_public_reading_still_works(
    public_client, tournament, app, monkeypatch, tmp_path
):
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path / "missing"))
    assert public_client.get(f"/{tournament.url}").status_code == 200
    workflow = public_client.get(f"/{tournament.url}/register")
    assert workflow.status_code == 503
    assert "temporarily unavailable" in workflow.get_data(as_text=True)
    assert "Retry" in workflow.get_data(as_text=True)


def test_database_failure_returns_retryable_document_not_empty_200(public_client, monkeypatch):
    def unavailable(**_kwargs):
        raise SQLAlchemyError("Internal failure that must not be shown")

    monkeypatch.setattr(PublicPageService, "directory", unavailable)
    response = public_client.get("/")
    assert response.status_code == 503
    html = response.get_data(as_text=True)
    assert "Try again" in html
    assert "Internal failure" not in html
    assert "boot.js" not in html


def test_timezone_cookie_is_validated_and_calendar_dates_remain_unchanged(public_client, tournament):
    public_client.set_cookie("arctos_timezone", "America%2FLos_Angeles")
    response = public_client.get("/")
    assert response.status_code == 200
    public_client.set_cookie("arctos_timezone", "../not-a-zone")
    assert public_client.get("/").status_code == 200
    html = public_client.get(f"/{tournament.url}").get_data(as_text=True)
    assert tournament.start_date.date().isoformat() in html


def test_waiver_routes_remain_download_routes_with_visibility_gate(public_client, tournament, monkeypatch, tmp_path):
    from app.routes import waivers

    waiver = tmp_path / "waiver.pdf"
    waiver.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(waivers, "_waiver_disk_path", lambda _scope: str(waiver))
    response = public_client.get(f"/{tournament.url}/waiver")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert "no-store" in response.headers["Cache-Control"]
    tournament.published = False
    db.session.commit()
    assert public_client.get(f"/{tournament.url}/waiver").status_code == 404
    league = make_league(published=False)
    db.session.commit()
    assert public_client.get(f"/leagues/{league.url}/waiver").status_code == 404


@pytest.mark.parametrize("organizer", [False, True])
def test_event_waiver_alias_does_not_disclose_a_private_league(public_client, player, organizer):
    league = make_league("private-league", published=False)
    make_event("public-round", league=league)
    db.session.add(TO(league_id=league.url, user_id=player.id, user_type="player"))
    db.session.commit()
    if organizer:
        login_as(public_client, player)
    response = public_client.get("/public-round/waiver", environ_overrides={"SCRIPT_NAME": "/arctos"})
    if organizer:
        assert response.status_code == 302
        assert response.headers["Location"] == "/arctos/leagues/private-league/waiver"
    else:
        assert response.status_code == 404
        assert "Location" not in response.headers
        assert "private-league" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.vary


@pytest.mark.parametrize("path, expected_status", [("/", 503), ("/login", 200)])
def test_account_lookup_failure_preserves_anonymous_recovery_document(
    public_client, app, monkeypatch, player, path, expected_status
):
    login_as(public_client, player)

    def unavailable(_user_id):
        raise SQLAlchemyError("Account database unavailable")

    monkeypatch.setattr(app.login_manager, "_user_callback", unavailable)
    response = public_client.get(path)
    assert response.status_code == expected_status
    assert "Log in" in response.get_data(as_text=True)
    assert player.name not in response.get_data(as_text=True)
    assert "Account database unavailable" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "private, no-store"


def assert_robots(response, *, noindex):
    directives = [
        tag.get("content", "")
        for tag in Elements(response.get_data(as_text=True)).all("meta")
        if tag.get("name") == "robots"
    ]
    if noindex:
        assert response.headers.get("X-Robots-Tag") == "noindex"
        assert any("noindex" in directive for directive in directives)
    else:
        assert "X-Robots-Tag" not in response.headers
        assert not directives
    assert "no-store" in response.headers["Cache-Control"]


def test_public_profiles_keep_indexing_but_profile_workflows_and_sensitive_tabs_do_not(public_client, player, team):
    for base in (f"/players/{player.id}", f"/teams/{team.id}"):
        for suffix in ("", "?tab=info", "?tab=history"):
            response = public_client.get(base + suffix)
            assert response.status_code == 200
            assert_robots(response, noindex=False)
        for suffix in ("/edit", "?app=1"):
            assert_robots(public_client.get(base + suffix), noindex=True)
    for path in (
        f"/players/{player.id}?tab=injuries",
        f"/players/{player.id}/injuries/new",
        f"/players/{player.id}?tab=infractions",
        f"/teams/{team.id}?tab=notes",
        "/players/missing",
    ):
        assert_robots(public_client.get(path), noindex=True)


def test_league_indexing_requires_publication_even_for_organizers(public_client, player):
    league = make_league()
    db.session.add(TO(league_id=league.url, user_id=player.id, user_type="player"))
    db.session.commit()
    for suffix in ("", "?tab=events", "?tab=teams", "/results"):
        assert_robots(public_client.get(f"/leagues/{league.url}{suffix}"), noindex=False)
    assert_robots(public_client.get(f"/leagues/{league.url}/register"), noindex=True)
    assert_robots(public_client.get(f"/leagues/{league.url}/settings"), noindex=True)
    league.published = False
    db.session.commit()
    login_as(public_client, player)
    for suffix in ("", "?tab=events", "?tab=teams", "/results"):
        assert_robots(public_client.get(f"/leagues/{league.url}{suffix}"), noindex=True)


def test_league_directory_contains_live_content_native_search_and_pagination(public_client):
    for number in range(3):
        league = make_league(f"season-{number}")
        league.name = f"A & B League {number}"
        make_event(f"round-{number}", league=league, start=datetime(2030, 1, number + 1))
    private = make_league("hidden-season", published=False)
    private.name = "A & B Secret"
    db.session.commit()
    response = public_client.get("/leagues?q=A+%26+B")
    html = response.get_data(as_text=True)
    tags = Elements(html)
    assert response.status_code == 200
    assert "A &amp; B League 0" in html and "A &amp; B League 1" in html
    assert "A &amp; B League 2" not in html and "Secret" not in html
    assert any(form.get("method", "get").lower() == "get" for form in tags.all("form"))
    assert any(field.get("name") == "q" for field in tags.all("input"))
    assert any(link.get("href") == "/leagues?q=A+%26+B&page=2" for link in tags.all("a"))
    assert "A &amp; B League 2" in public_client.get("/leagues?q=A+%26+B&page=2").get_data(as_text=True)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.vary
    assert_robots(response, noindex=True)
    for path in ("/leagues", "/leagues/"):
        document = public_client.get(path)
        assert_robots(document, noindex=False)
        assert 'data-public-page="leagues"' in document.get_data(as_text=True)
    league.name = "League name updated in the database"
    db.session.commit()
    assert league.name in public_client.get("/leagues?q=updated").get_data(as_text=True)


def test_league_info_is_sanitized_readable_and_has_workflow_links_without_javascript(public_client):
    league = make_league(team_registration_open=True, team_reg_fee=12.50, n_max_teams=12)
    league.name = '<img src=x onerror="unsafe()">'
    league.about = "## Season information\n\nBring water. <script>unsafe()</script>"
    db.session.commit()
    response = public_client.get(f"/leagues/{league.url}")
    html = response.get_data(as_text=True)
    tags = Elements(html)
    assert response.status_code == 200
    assert "&lt;img" in html and '<img src=x onerror="unsafe()">' not in html
    assert "Bring water." in html and "<script>unsafe()</script>" not in html
    assert "$12.50" in html and "Log in to register" in html
    assert "boot.js" not in html
    assert any(link.get("href") == f"/leagues/{league.url}?tab=events" for link in tags.all("a"))
    assert any(
        link.get("rel") == "canonical" and link.get("href") == f"https://arctos.example/leagues/{league.url}"
        for link in tags.all("link")
    )
    for suffix in ("", "?tab=info"):
        assert 'data-public-page="league"' in public_client.get(f"/leagues/{league.url}{suffix}").get_data(as_text=True)
    for suffix in ("?tab=events", "?tab=teams", "?tab=unknown", "?app=1", "/register", "/results", "/settings"):
        workflow = public_client.get(f"/leagues/{league.url}{suffix}")
        assert workflow.status_code == 200
        assert "boot.js" in workflow.get_data(as_text=True)


def test_league_missing_or_private_documents_return_real_404(public_client):
    league = make_league("private-league", published=False)
    db.session.commit()
    for suffix in ("", "?tab=events", "/register", "/results", "/settings"):
        missing = public_client.get("/leagues/missing" + suffix)
        private = public_client.get("/leagues/private-league" + suffix)
        assert missing.status_code == private.status_code == 404
        # Sign-in destinations retain the requested path in both responses.
        assert missing.get_data(as_text=True) == private.get_data(as_text=True).replace("private-league", "missing")


def test_league_draft_requires_signed_in_organizer(public_client, player):
    league = make_league("private-league", published=False)
    db.session.commit()
    login_as(public_client, player)
    assert public_client.get("/leagues/private-league").status_code == 404
    db.session.add(TO(league_id=league.url, user_id=player.id, user_type="player"))
    db.session.commit()
    draft = public_client.get("/leagues/private-league")
    assert draft.status_code == 200
    assert "boot.js" in draft.get_data(as_text=True)
    assert_robots(draft, noindex=True)


def test_league_documents_preserve_deployment_prefixes(public_client):
    league = make_league(team_registration_open=True)
    db.session.commit()
    response = public_client.get(f"/leagues/{league.url}", environ_overrides={"SCRIPT_NAME": "/arctos"})
    tags = Elements(response.get_data(as_text=True))
    links = tags.all("a")
    assert any(link.get("href") == "/arctos/leagues/league?tab=events" for link in links)
    assert any(link.get("href") == "/arctos/login?next=%2Farctos%2Fleagues%2Fleague%2Fregister" for link in links)
    assert any(
        link.get("rel") == "canonical" and link.get("href") == "https://arctos.example/arctos/leagues/league"
        for link in tags.all("link")
    )


@pytest.mark.parametrize("path,method", [("/leagues", "directory"), ("/leagues/season", "league")])
def test_league_database_failure_returns_recoverable_503(public_client, monkeypatch, path, method):
    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("Sensitive database failure")

    monkeypatch.setattr(PublicLeagueService, method, unavailable)
    response = public_client.get(path)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.headers["Cache-Control"] == "private, no-store"
    html = response.get_data(as_text=True)
    assert "Leagues are temporarily unavailable" in html
    assert "Try again" in html and "Sensitive" not in html


def test_league_public_reading_works_without_application_build(public_client, app, monkeypatch, tmp_path):
    league = make_league()
    league.about = "Season information before the application starts."
    db.session.commit()
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path / "missing"))
    for path in ("/leagues", "/leagues/league"):
        response = public_client.get(path)
        assert response.status_code == 200
        assert league.name in response.get_data(as_text=True)
        assert "background.js" not in response.get_data(as_text=True)
    assert league.about in public_client.get("/leagues/league").get_data(as_text=True)
    assert public_client.get("/leagues/league/register").status_code == 503


def test_event_section_indexing_distinguishes_publication_from_preview_permission(public_client, tournament, player):
    tournament.schedule_published = True
    tournament.bracket_published = True
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    for suffix in ("/schedule", "/results", "/bracket", "?tab=teams", "?tab=sidecomps"):
        assert_robots(public_client.get(f"/{tournament.url}{suffix}"), noindex=False)
    for suffix in ("/register", "/settings", "/schedule/edit", "?tab=app", "?app=1"):
        assert_robots(public_client.get(f"/{tournament.url}{suffix}"), noindex=True)
    tournament.schedule_published = False
    tournament.bracket_published = False
    db.session.commit()
    login_as(public_client, player)
    for suffix in ("/schedule", "/bracket"):
        assert_robots(public_client.get(f"/{tournament.url}{suffix}"), noindex=True)
    tournament.published = False
    db.session.commit()
    assert_robots(public_client.get(f"/{tournament.url}"), noindex=True)


def test_readonly_document_application_entries_preserve_prior_indexing(public_client):
    for path in ("/about", "/thanks", "/docs.md", "/privacy-policy.md", "/terms.md", "/developers.md"):
        response = public_client.get(path)
        assert response.status_code == 200
        assert_robots(response, noindex=False)


def test_missing_build_stays_noindex_for_otherwise_public_application_route(public_client, app, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "ARCTOS_FRONTEND_DIST", str(tmp_path / "missing"))
    response = public_client.get("/players")
    assert response.status_code == 503
    assert_robots(response, noindex=True)
