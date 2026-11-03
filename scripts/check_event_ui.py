"""Check event navigation and profile statistics against the explicitly started demo.

    uv run python scripts/check_event_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-events

Uses the shared loopback/demo marker and mutation guard. Profile count fixtures
replace GET responses only; no demo data is changed. Chromium audio is muted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import sys
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from playwright.async_api import Locator, Page, Request, Route, TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu, preference
from check_mobile_ui import DEMO_USER, LAYOUT_DIAGNOSTICS, TIMEOUT_MS, SmokeCheck, api_json, arguments


@dataclass(frozen=True)
class EventPage:
    name: str
    path: str
    api_path: str
    prefix: str


EVENTS = (
    EventPage("tournament", "/demo-open", "/_api/tournaments/demo-open", "event"),
    EventPage("league", "/leagues/demo-coast-league", "/_api/leagues/demo-coast-league", "league"),
    EventPage(
        "league-event",
        "/demo-league-round-one",
        "/_api/tournaments/demo-league-round-one",
        "event",
    ),
)


async def selected_route(page: Page, prefix: str, tab: str) -> None:
    await expect(page.locator(f"a#{prefix}-tab-{tab}")).to_have_attribute("aria-current", "page")
    await expect(page.locator('.event-route-tabs [aria-current="page"]')).to_have_count(1)
    await expect(page.locator(".event-detail-hero")).to_be_visible()
    # Font loading can widen a link after the first route render.
    await page.evaluate("document.fonts.ready")
    try:
        await page.wait_for_function("""() => {
            const link = document.querySelector('.event-route-tabs [aria-current="page"]');
            if (!link) return false;
            const item = link.getBoundingClientRect(), nav = link.parentElement.getBoundingClientRect();
            return item.left >= nav.left - 1 && item.right <= nav.right + 1;
        }""")
    except PlaywrightTimeoutError as error:
        bounds = await page.locator(f"#{prefix}-tab-{tab}").evaluate("""el => ({
            link: el.getBoundingClientRect().toJSON(), nav: el.parentElement.getBoundingClientRect().toJSON(),
            scrollLeft: el.parentElement.scrollLeft, scrollWidth: el.parentElement.scrollWidth
        })""")
        raise AssertionError(f"Selected route clipped at {page.url}: {bounds}") from error


async def selected_panel(page: Page, event: EventPage, tab: str) -> None:
    await selected_route(page, event.prefix, tab)
    await expect(page.locator(".event-section:visible")).to_have_count(1)
    panel = page.locator(f"#{event.prefix}-section-{tab}")
    await expect(panel).to_be_visible()
    location = urlsplit(page.url)
    assert location.path == event.path, location
    assert (parse_qs(location.query).get("tab", ["info"])[0] or "info") == tab, location


async def phone_targets(page: Page) -> None:
    """Check actual visible controls, including roster and event navigation links."""
    failures = await page.locator(
        ".event-hero-actions .btn, .event-detail-page .btn, .event-route-tab, "
        ".event-section .roster-name, .event-roster-toggle, .event-roster-player-link, "
        ".event-section .event-series-list a, .event-side-list > li > a"
    ).evaluate_all("""els => els.flatMap(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) return [];
        // Route links may intentionally scroll horizontally inside their nav.
        const inNav = el.closest('.event-route-tabs');
        return r.width < 43.9 || r.height < 43.9 || (!inNav && (r.left < -1 || r.right > innerWidth + 1))
            ? [{text: el.textContent.trim(), width: r.width, height: r.height, left: r.left, right: r.right}] : [];
    })""")
    assert not failures, f"Small or clipped event controls: {failures}"
    layout = await page.evaluate(LAYOUT_DIAGNOSTICS)
    assert layout["documentWidth"] <= layout["width"] + 1, f"Horizontal overflow: {layout}"
    nav = await page.locator(".event-route-tabs").evaluate("""el => {
        const r = el.getBoundingClientRect();
        return {left: r.left, right: r.right, width: el.clientWidth, content: el.scrollWidth,
            overflow: getComputedStyle(el).overflowX,
            rows: [...el.children].map(child => Math.round(child.getBoundingClientRect().top))};
    }""")
    assert nav["left"] >= -1 and nav["right"] <= layout["width"] + 1, nav
    assert len(set(nav["rows"])) == 1, f"Route navigation wrapped to multiple rows: {nav}"
    assert nav["content"] <= nav["width"] + 1 or nav["overflow"] in ("auto", "scroll"), nav


def roster_endpoint(event: EventPage, payload: dict, team: dict) -> str:
    if event.prefix == "league":
        scope = "league:" + payload["league"]["league_url"]
    else:
        tournament = payload["tournament"]
        league = tournament.get("league")
        scope = "league:" + league["league_url"] if league else tournament["url"]
    return f"/_api/teams/{quote(team['team_id'], safe='')}/players?{urlencode({'event': scope})}"


async def roster_players(region: Locator, players: list) -> None:
    await expect(region).to_be_visible()
    await expect(region.locator(".event-roster-player-link")).to_have_count(len(players))
    await expect(region.locator(".card")).to_have_count(0)
    if not players:
        await expect(region.get_by_text("No players registered yet.", exact=True)).to_be_visible()
    for item in players:
        registration = item["registration"]
        player = item.get("player") or {}
        link = region.locator(f'a.event-roster-player-link[href="/players/{registration["player"]}"]')
        await expect(link).to_be_visible()
        await expect(link).to_contain_text(player["name"] if player else registration["player"])
        if (registration.get("jersey_name") or "").strip():
            await expect(link).to_contain_text(registration["jersey_name"])
        if (registration.get("jersey_number") or "").strip():
            await expect(link).to_contain_text("#" + registration["jersey_number"])


async def event_rosters(check: SmokeCheck, page: Page, event: EventPage, payload: dict) -> None:
    """Read real public rosters, including league membership on league-event pages."""
    requests: list[str] = []

    def record_request(request: Request) -> None:
        if request.method == "GET" and re.fullmatch(r"/_api/teams/[^/]+/players", urlsplit(request.url).path):
            requests.append(request.url)

    page.on("request", record_request)
    try:
        await page.goto(check.base_url + event.path + "?tab=teams", wait_until="domcontentloaded")
        await selected_panel(page, event, "teams")
        panel = page.locator(f"#{event.prefix}-section-teams")
        teams = payload["teams_with_counts"]
        assert teams, f"{event.name} demo needs a team to exercise roster disclosure"
        await expect(panel.locator(".event-roster-toggle")).to_have_count(len(teams))
        assert not requests, f"Collapsed rosters fetched players: {requests}"
        for index, team in enumerate(teams):
            endpoint = roster_endpoint(event, payload, team)
            players = await api_json(page.context, check.base_url, endpoint)
            assert len(players) == team["player_count"], f"{event.name} roster count disagrees for {team['team_id']}"
            name = team["pseudonym"] if team.get("pseudonym") is not None else team["team_name"]
            row = panel.locator(".event-team-summary").filter(has=page.locator(f'a[href="/teams/{team["team_id"]}"]'))
            toggle = row.get_by_role("button", name=f"View players for {name}", exact=True)
            await expect(toggle).to_have_attribute("type", "button")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(toggle).to_contain_text("View players")
            region_id = await toggle.get_attribute("aria-controls")
            assert region_id, "Roster disclosure must identify its controlled region"
            region = panel.locator(f'.event-team-players[id="{region_id}"]')
            await expect(region).not_to_be_visible()
            await toggle.focus()
            await page.keyboard.press("Enter")
            toggle = row.locator(".event-roster-toggle")
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await expect(toggle).to_have_accessible_name(f"Hide players for {name}")
            await expect(toggle).to_contain_text("Hide players")
            await roster_players(region, players)
            await expect(panel.locator('.event-roster-toggle[aria-expanded="true"]')).to_have_count(index + 1)
            if index == 0:
                await toggle.focus()
                await page.keyboard.press("Space")
                await expect(toggle).to_have_attribute("aria-expanded", "false")
                await expect(region).not_to_be_visible()
                await page.keyboard.press("Enter")
                await roster_players(region, players)
                await expect(toggle).to_have_attribute("aria-expanded", "true")
            await page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            matches = [url for url in requests if urlsplit(url).path == urlsplit(endpoint).path]
            assert len(matches) == 1, f"Roster should load once across close/reopen: {matches}"
            assert parse_qs(urlsplit(matches[0]).query) == parse_qs(urlsplit(endpoint).query), matches[0]
        await phone_targets(page)
    finally:
        page.remove_listener("request", record_request)


async def roster_recovery(check: SmokeCheck, page: Page, event: EventPage, payload: dict) -> None:
    team = payload["teams_with_counts"][0]
    endpoint = roster_endpoint(event, payload, team)
    players = await api_json(page.context, check.base_url, endpoint)
    url = check.base_url + endpoint
    pattern = re.compile(re.escape(url) + "$", re.IGNORECASE)
    requested = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def fail_once(route: Route) -> None:
        nonlocal attempts
        if route.request.method != "GET":
            await route.fallback()
            return
        attempts += 1
        if attempts == 1:
            requested.set()
            await release.wait()
            await route.fulfill(status=503, json={"error": "Roster temporarily unavailable"})
        else:
            await route.fallback()

    await page.route(pattern, fail_once)
    try:
        await page.goto(check.base_url + event.path + "?tab=teams", wait_until="domcontentloaded")
        await selected_panel(page, event, "teams")
        row = page.locator(".event-team-summary").filter(has=page.locator(f'a[href="/teams/{team["team_id"]}"]'))
        toggle = row.locator(".event-roster-toggle")
        await toggle.click()
        region_id = await toggle.get_attribute("aria-controls")
        region = page.locator(f'.event-team-players[id="{region_id}"]')
        await expect(region.get_by_text("Loading players…", exact=True)).to_be_visible()
        await asyncio.wait_for(requested.wait(), TIMEOUT_MS / 1000)
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(region).not_to_be_visible()
        await toggle.click()
        await expect(region.get_by_text("Loading players…", exact=True)).to_be_visible()
        assert attempts == 1, "Reopening a pending roster duplicated its request"
        async with page.expect_response(lambda response: response.url == url and response.status == 503):
            release.set()
        await expect(region.get_by_text("Players could not be loaded.", exact=True)).to_be_visible()
        # Exclude only this explicitly asserted failure from the shared HTTP-error check.
        check.network_errors.remove(f"HTTP 503 {url}")
        name = team["pseudonym"] if team.get("pseudonym") is not None else team["team_name"]
        retry = region.get_by_role("button", name=f"Retry loading players for {name}", exact=True)
        await expect(retry).to_have_text("Retry")
        await retry.focus()
        await page.keyboard.press("Enter")
        await expect(toggle).to_be_focused()
        await roster_players(region, players)
        await expect(retry).not_to_be_visible()
        await expect(toggle).to_be_focused()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        assert attempts == 2, f"Retry should issue one fresh roster read, got {attempts}"
    finally:
        release.set()
        await page.unroute(pattern, fail_once)


async def event_content(page: Page, event: EventPage, payload: dict, side_events: list) -> None:
    await page.locator(f"#{event.prefix}-tab-teams").click()
    await selected_panel(page, event, "teams")
    panel = page.locator(f"#{event.prefix}-section-teams")
    for team in payload["teams_with_counts"]:
        link = panel.locator(f'a[href="/teams/{team["team_id"]}"]')
        await expect(link).to_be_visible()
        row = panel.locator("tr").filter(has=page.locator(f'a[href="/teams/{team["team_id"]}"]'))
        await expect(row.locator(".roster-count")).to_have_text(str(team["player_count"]))
        assert (await row.locator('td[data-label="Registered"]').inner_text()).strip(), "Registration date lost"
    for player in payload.get("unattached_players", []):
        await expect(panel.locator(f'a[href="/players/{player["player_id"]}"]')).to_be_visible()
    other = "events" if event.prefix == "league" else "sidecomps"
    await page.locator(f"#{event.prefix}-tab-{other}").click()
    await selected_panel(page, event, other)
    panel = page.locator(f"#{event.prefix}-section-{other}")
    records = payload["events"] if other == "events" else side_events
    for record in records:
        path = f"/{record['url']}" if other == "events" else f"{event.path}/sidecomps/{record['id']}"
        await expect(panel.locator(f'a[href="{path}"]')).to_be_visible()
    if not records:
        await expect(panel.get_by_text(re.compile("No (?:events|side competitions)"))).to_be_visible()


async def event_navigation(check: SmokeCheck, page: Page, event: EventPage, payload: dict, sides: list) -> None:
    await page.goto(check.base_url + event.path, wait_until="domcontentloaded")
    await selected_panel(page, event, "info")
    nav = page.locator(".event-route-tabs")
    links = nav.get_by_role("link")
    await links.first.focus()
    for index in range(1, await links.count()):
        await page.keyboard.press("Tab")
        await expect(links.nth(index)).to_be_focused()
    last = await links.last.bounding_box()
    nav_box = await nav.bounding_box()
    assert (
        last
        and nav_box
        and last["x"] >= nav_box["x"] - 1
        and last["x"] + last["width"] <= nav_box["x"] + nav_box["width"] + 1
    ), f"Keyboard focus did not reveal the scrolled navigation link: link={last}, nav={nav_box}"
    await page.locator(f"#{event.prefix}-tab-teams").focus()
    await page.keyboard.press("Enter")
    await selected_panel(page, event, "teams")
    await event_content(page, event, payload, sides)
    other = "events" if event.prefix == "league" else "sidecomps"
    await page.go_back()
    await selected_panel(page, event, "teams")
    await page.go_forward()
    await selected_panel(page, event, other)
    # The action strip remains available while viewing any section.
    login = page.locator(".event-hero-actions").get_by_role("link", name="Log in to register", exact=True)
    await expect(login).to_be_visible()
    href = urlsplit(await login.get_attribute("href"))
    registration_path = event.path + "/register"
    if event.name == "league-event":
        registration_path = f"/leagues/{payload['tournament']['league']['league_url']}/register"
    assert href.path == "/login" and parse_qs(href.query).get("next") == [registration_path], href
    await expect(nav.get_by_role("link", name="Results", exact=True)).to_have_attribute("href", event.path + "/results")
    if event.prefix == "event":
        if payload["can_view_schedule"]:
            schedule = nav.get_by_role("link", name="Schedule", exact=True)
            await expect(schedule).to_be_visible()
            assert urlsplit(await schedule.get_attribute("href")).path == event.path + "/schedule"
        else:
            schedule = nav.get_by_role("button", name="Schedule", exact=True)
            await expect(schedule).to_be_disabled()
            assert await schedule.get_attribute("href") is None
        if payload["tournament"]["bracket_published"]:
            await expect(nav.get_by_role("link", name="Bracket", exact=True)).to_have_attribute(
                "href", event.path + "/bracket"
            )
    await phone_targets(page)
    await page.locator(f"#{event.prefix}-tab-results").click()
    await selected_route(page, event.prefix, "results")
    assert urlsplit(page.url).path == event.path + "/results"
    if event.prefix == "event" and payload["can_view_schedule"]:
        await page.locator("#event-tab-schedule").click()
        await selected_route(page, "event", "schedule")
        await expect(page.locator(".schedule-keyboard-focus")).to_be_visible()
    await page.locator(f"#{event.prefix}-tab-info").click()
    await selected_panel(page, event, "info")
    if event.name == "tournament":
        await page.goto(check.base_url + event.path + "?tab=sidecomps", wait_until="domcontentloaded")
        await selected_panel(page, event, "sidecomps")


async def event_themes(check: SmokeCheck, page: Page) -> None:
    for width in (320, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        for event in EVENTS:
            await page.goto(check.base_url + event.path, wait_until="domcontentloaded")
            await selected_panel(page, event, "info")
            for mode in ("light", "dark"):
                for palette in PALETTES:
                    await choose(page, palette, mode)
                    await close_menu(page)
                    await preference(page, palette, mode)
                    # Inspect every panel, then capture the compact team rows.
                    for tab in ("info", "events" if event.prefix == "league" else "sidecomps", "teams"):
                        await page.locator(f"#{event.prefix}-tab-{tab}").click()
                        await selected_panel(page, event, tab)
                        if tab == "teams":
                            toggle = page.locator(".event-roster-toggle").first
                            if await toggle.count() and await toggle.get_attribute("aria-expanded") != "true":
                                await toggle.click()
                            if await toggle.count():
                                region_id = await toggle.get_attribute("aria-controls")
                                region = page.locator(f'.event-team-players[id="{region_id}"]')
                                await expect(region).to_be_visible()
                                await expect(region.get_by_text("Loading players…", exact=True)).not_to_be_visible()
                                await expect(
                                    region.get_by_text("Players could not be loaded.", exact=True)
                                ).not_to_be_visible()
                        await page.mouse.move(0, 0)
                        await phone_targets(page)
                        await check_contrast(
                            page,
                            ".event-route-tab, .event-hero-actions .btn, .event-detail-hero h1, "
                            ".event-hero-meta, .event-kind-badge, .event-registration-badge, "
                            ".event-section:visible h2, .event-section:visible h5, "
                            ".event-section:visible dt, .event-section:visible dd, "
                            ".event-section:visible p, .event-section:visible strong, "
                            ".event-roster-toggle, .event-roster-player-link, .event-roster-jersey, "
                            ".event-section:visible .text-muted, .event-section:visible .badge",
                        )
                    await check.check_layout(page, f"{event.name}-{palette}-{mode}", width)
                    check.visited += 1


async def profile_counts(check: SmokeCheck, page: Page) -> None:
    for kind in ("player", "team"):
        path = f"/{kind}s"
        owner = f"demo-{kind}"
        listing = await api_json(page.context, check.base_url, "/_api" + path + "?search=" + owner)
        profile = await api_json(page.context, check.base_url, f"/_api{path}/{owner}")
        item = next(item for item in listing[kind + "s"] if item["id"] == owner)
        assert item["past_event_count"] == profile["past_event_count"], "Directory/profile totals disagree"

        async def rendered_count(count: int, label: str, width: int) -> None:
            for suffix, selector in (
                ("", f'.arctos-directory-card[href="{path}/{owner}"] .arctos-directory-event-count strong'),
                (f"/{owner}", ".profile-event-stat dd"),
            ):
                await page.goto(check.base_url + path + suffix, wait_until="domcontentloaded")
                if not suffix:
                    await page.locator(f"#{kind}-search").fill(owner)
                    await page.get_by_role("search").get_by_role("button", name="Search", exact=True).click()
                await expect(page.locator(selector)).to_have_text(str(count))
                await check_contrast(page, selector)
                await check.check_layout(page, f"{kind}-{'profile' if suffix else 'directory'}-{label}", width)
                check.visited += 1

        for width, mode in ((320, "light"), (360, "dark")):
            await page.set_viewport_size({"width": width, "height": 900})
            await page.goto(check.base_url + path, wait_until="domcontentloaded")
            await choose(page, "california", mode)
            await close_menu(page)
            await rendered_count(item["past_event_count"], mode, width)
        # Ensure both zero and nonzero render correctly even in a fresh demo.
        fixture_count = 7 if item["past_event_count"] == 0 else 0
        item["past_event_count"] = profile["past_event_count"] = fixture_count

        async def fixture(route: Route) -> None:
            if route.request.method != "GET":
                await route.fallback()
                return
            payload = profile if urlsplit(route.request.url).path.endswith("/" + owner) else listing
            await route.fulfill(status=200, json=payload)

        patterns = (check.base_url + "/_api" + path + "?*", check.base_url + f"/_api{path}/{owner}")
        # Team listings omit empty query parameters; cover that exact URL too.
        patterns += (check.base_url + "/_api" + path,)
        for pattern in patterns:
            await page.context.route(pattern, fixture)
        try:
            await page.set_viewport_size({"width": 320, "height": 900})
            await rendered_count(fixture_count, f"fixture-{fixture_count}", 320)
        finally:
            for pattern in patterns:
                await page.context.unroute(pattern, fixture)


async def desktop(check: SmokeCheck, page: Page) -> None:
    await page.set_viewport_size({"width": 1280, "height": 900})
    for event in EVENTS[:2]:
        await page.goto(check.base_url + event.path, wait_until="domcontentloaded")
        await selected_panel(page, event, "info")
        await choose(page, "california", "light")
        await close_menu(page)
        await check.check_layout(page, f"{event.name}-desktop-info", 1280)
        check.visited += 1


async def routed_views(check: SmokeCheck, page: Page) -> None:
    # Organizer access also exercises the unpublished bracket preview route.
    await check.login(page)
    schedule = await api_json(page.context, check.base_url, "/_api/tournaments/demo-open/schedule-setup")
    field_id = str(schedule["fields"][0]["id"])
    schedule_path = f"/demo-open/schedule?view=field&team=demo-team&field={field_id}"
    await page.goto(check.base_url + schedule_path, wait_until="domcontentloaded")
    await selected_route(page, "event", "schedule")
    await expect(page.locator(".schedule-keyboard-focus")).to_be_visible()
    await page.locator("#event-tab-results").click()
    await selected_route(page, "event", "results")
    await page.go_back()
    await selected_route(page, "event", "schedule")
    await page.wait_for_function("() => new URL(location.href).searchParams.get('view') === 'field'")
    query = parse_qs(urlsplit(page.url).query)
    assert query.get("team") == ["demo-team"] and query.get("field") == [field_id], query
    field = page.locator("select").filter(has=page.locator(f'option[value="{field_id}"]')).first
    await expect(field).to_have_value(field_id)
    await page.go_forward()
    await selected_route(page, "event", "results")
    routes = (
        ("schedule", schedule_path, "event", ".schedule-keyboard-focus"),
        ("results", "/demo-open/results", "event", "#results-include-ribbon"),
        ("bracket", "/demo-open/bracket", "event", "#bracket-keyboard-focus"),
        ("results", "/leagues/demo-coast-league/results", "league", "#results-include-ribbon"),
    )
    for width in (320, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        for mode in ("light", "dark"):
            await choose(page, "california", mode)
            await close_menu(page)
            for tab, path, prefix, ready in routes:
                await page.goto(check.base_url + path, wait_until="domcontentloaded")
                await selected_route(page, prefix, tab)
                await expect(page.locator(ready)).to_be_visible()
                scroll_y = await page.evaluate("window.scrollY")
                assert scroll_y <= 1, f"Opening {path} scrolled {scroll_y}px past the event banner"
                await expect(page.locator(".event-hero-actions .btn").first).to_be_visible()
                await phone_targets(page)
                await check_contrast(page, ".event-route-tab, .event-hero-actions .btn")
                await check.check_layout(page, f"{prefix}-{tab}-route-{mode}", width)
                check.visited += 1


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        context = await browser.new_context(viewport={"width": 360, "height": 900}, reduced_motion="reduce")
        context.set_default_timeout(TIMEOUT_MS)
        try:
            marker = await api_json(context, check.base_url, "/_api/demo-info")
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER, "Verified demo required"
            await context.route("**/*", check.guard_requests)
            page = await context.new_page()
            check.observe(page)
            for event in EVENTS:
                payload = await api_json(context, check.base_url, event.api_path)
                sides = (
                    await api_json(context, check.base_url, f"/_api{event.path}/sidecomps")
                    if event.prefix == "event"
                    else []
                )
                await check.workflow(
                    page, f"{event.name}-navigation", 360, lambda: event_navigation(check, page, event, payload, sides)
                )
                await check.workflow(
                    page, f"{event.name}-rosters", 360, lambda: event_rosters(check, page, event, payload)
                )
                if event.name == "tournament":
                    await check.workflow(
                        page, "roster-recovery", 360, lambda: roster_recovery(check, page, event, payload)
                    )
            await check.workflow(page, "event-themes", 360, lambda: event_themes(check, page))
            await check.workflow(page, "profile-counts", 320, lambda: profile_counts(check, page))
            await check.workflow(page, "event-desktop", 1280, lambda: desktop(check, page))
            await check.workflow(page, "event-routed-views", 360, lambda: routed_views(check, page))
        finally:
            await context.close()
            await browser.close()
    print(
        f"{check.visited} event/profile screen checks; {check.workflows} workflows passed; {len(check.failures)} failures."
    )
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures))


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments(__doc__))))
