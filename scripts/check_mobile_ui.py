"""Check the running Arctos demo in Chromium at phone, tablet, and desktop widths.

Start ``uv run python -m scripts.dev_demo`` and ``just frontend`` first, then:

    uv run python scripts/check_mobile_ui.py
    uv run python scripts/check_mobile_ui.py --base-url http://127.0.0.1:8443 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-ui

This explicitly invoked browser check is separate from backend pytest. It refuses
non-loopback URLs and requires the demo-only /_api/demo-info marker before login.
All data-changing requests are blocked; the only real POSTs allowed are demo
login and read-only Markdown rendering. Draft creation and a failed finalize
request are simulated in the browser, so fixtures remain unchanged. Match UUIDs
and competition IDs are discovered from the running demo instead of assuming database IDs.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import BrowserContext, Page, Route, async_playwright, expect

WIDTHS = (360, 390, 768, 1280)
TIMEOUT_MS = 20_000
DEMO_USER = "demo-organizer"
DEMO_PASSWORD = "arctos-demo-only"
MOCK_ERROR = "Smoke check: simulated connection failure. Your input is safe."


@dataclass(frozen=True)
class Screen:
    name: str
    path: str
    ready: str = "main h1, main h2, main h3"


PUBLIC_SCREENS = (
    Screen("home", "/", "#event-search"),
    Screen("login", "/login", "#username"),
    Screen("register-choice", "/register"),
    Screen("register-player", "/register/player", "#confirm_password"),
    Screen("register-team", "/register/team", "#confirm_password"),
    Screen("leagues", "/leagues"),
    Screen("players", "/players"),
    Screen("teams", "/teams"),
    Screen("event-live", "/demo-open"),
    Screen("event-upcoming", "/demo-summer-cup"),
    Screen("event-past", "/demo-spring-cup"),
    Screen("league", "/leagues/demo-coast-league"),
    Screen("league-event", "/demo-league-round-one"),
    Screen("schedule", "/demo-open/schedule", ".schedule-keyboard-focus"),
    Screen("results", "/demo-open/results"),
    Screen("past-results", "/demo-spring-cup/results"),
    Screen("league-results", "/leagues/demo-coast-league/results"),
    Screen("player-profile", "/players/demo-player"),
    Screen("team-profile", "/teams/demo-team"),
    Screen("stones", "/stones", ".stones-page"),
    Screen("about", "/about"),
    Screen("docs", "/docs.md"),
)

AUTH_SCREENS = (
    Screen("new-tournament", "/new-tournament", "#name"),
    Screen("new-league", "/leagues/new", "#league_name"),
    Screen("league-new-event", "/leagues/demo-coast-league/new-tournament", "#name"),
    Screen("bracket", "/demo-open/bracket", "#bracket-keyboard-focus"),
    Screen("profile-edit", "/players/demo-organizer/edit", "main form"),
    Screen("event-settings", "/demo-open/settings", "#tournament-settings-form"),
    Screen("event-manage", "/demo-open/manage", "main table"),
    Screen("event-footage", "/demo-open/manage-footage", ".event-tool-content h2"),
    Screen("event-register", "/demo-summer-cup/register", "main form"),
    Screen("sidecomp-new", "/demo-open/sidecomps/new", "main form"),
    Screen("schedule-editor", "/demo-open/schedule/edit", ".schedule-editor-split"),
    Screen("league-settings", "/leagues/demo-coast-league/settings", "main form"),
    Screen("league-manage", "/leagues/demo-coast-league/manage", "main table"),
    Screen("league-register", "/leagues/demo-coast-league/register", "main form"),
    Screen("developer-apps", "/developers/apps"),
)

# Only document overflow is a failure: schedules, tables, and canvases may have
# intentional scrolling/oversized descendants clipped by their own container.
LAYOUT_DIAGNOSTICS = """() => {
    const width = document.documentElement.clientWidth;
    const documentWidth = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
    const escaped = [...document.querySelectorAll('body *')].filter(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || (r.left >= -1 && r.right <= width + 1)) return false;
        let parent = el.parentElement;
        while (parent && parent !== document.body) {
            const style = getComputedStyle(parent);
            if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX)) return false;
            parent = parent.parentElement;
        }
        return getComputedStyle(el).position !== 'fixed';
    }).slice(0, 10).map(el => ({
        tag: el.tagName.toLowerCase(), id: el.id, class: String(el.className).slice(0, 120),
        left: Math.round(el.getBoundingClientRect().left),
        right: Math.round(el.getBoundingClientRect().right)
    }));
    return {width, documentWidth, escaped};
}"""


class SmokeCheck:
    def __init__(self, base_url: str, screenshots: Path | None):
        self.base_url = base_url
        self.origin = urlsplit(base_url).netloc
        self.screenshots = screenshots
        self.failures: list[str] = []
        self.page_errors: list[str] = []
        self.network_errors: list[str] = []
        self.blocked_writes: list[str] = []
        self.mock_gate: asyncio.Event | None = None
        self.mock_count = 0
        self.visited = 0
        self.workflows = 0
        self.authenticated = False

    async def guard_requests(self, route: Route) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.netloc != self.origin and parsed.path.startswith("/_api/"):
            self.network_errors.append(f"Refused API request outside the verified demo: {request.url}")
            await route.abort("blockedbyclient")
            return
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if parsed.netloc != self.origin:
                self.blocked_writes.append(f"Cross-origin {request.method} {request.url}")
                await route.abort("blockedbyclient")
                return
            if parsed.path == "/_api/tournaments/demo-open/finalize-match" and self.mock_gate is not None:
                self.mock_count += 1
                await self.mock_gate.wait()
                await route.fulfill(status=503, json={"error": MOCK_ERROR})
                return
            if parsed.path not in ("/_api/login", "/_api/render-markdown"):
                self.blocked_writes.append(f"Unexpected {request.method} {parsed.path}")
                await route.fulfill(status=409, json={"error": "Smoke check blocked a data-changing request."})
                return
        await route.continue_()

    def observe(self, page: Page) -> None:
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))

        def console_error(message) -> None:
            # HTTP errors are checked below, including expected anonymous 401s
            # and the deliberately mocked save failure.
            if message.type == "error" and not message.text.startswith("Failed to load resource:"):
                self.page_errors.append(message.text)

        page.on("console", console_error)

        def response_error(response) -> None:
            path = urlsplit(response.url).path
            if response.status < 400:
                return
            # Anonymous views probe optional personal registration and editor warnings.
            if response.status == 401 and (path == "/_api/me" or not self.authenticated):
                return
            if path == "/_api/tournaments/demo-open/finalize-match" and self.mock_gate is not None:
                return
            self.network_errors.append(f"HTTP {response.status} {response.url}")

        page.on("response", response_error)

    async def screenshot(self, page: Page, name: str, width: int) -> None:
        if self.screenshots:
            self.screenshots.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(self.screenshots / f"{width:04d}-{name}.png"), full_page=True, animations="disabled"
            )
            if name == "home" and width in (360, 1280):
                await page.screenshot(
                    path=str(self.screenshots / f"{width:04d}-home-viewport.png"), animations="disabled"
                )

    async def check_layout(self, page: Page, name: str, width: int) -> None:
        await page.evaluate("document.fonts.ready")
        await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        layout = await page.evaluate(LAYOUT_DIAGNOSTICS)
        if layout["documentWidth"] > layout["width"] + 1:
            raise AssertionError(f"document horizontal overflow: {json.dumps(layout)}")
        if await page.locator("#page-banner-dismiss").is_visible():
            collisions = await page.locator("#page-banner").evaluate("""banner => {
                const close = banner.querySelector('#page-banner-dismiss').getBoundingClientRect();
                const overlaps = r => Math.min(close.right, r.right) - Math.max(close.left, r.left) > 1
                    && Math.min(close.bottom, r.bottom) - Math.max(close.top, r.top) > 1;
                return [...banner.querySelectorAll('#page-banner-heading, .event-hero-badges > *')].flatMap(el => {
                    const range = document.createRange(); range.selectNodeContents(el);
                    const rects = el.id === 'page-banner-heading' ? [...range.getClientRects()] : [el.getBoundingClientRect()];
                    return rects.some(overlaps) ? [el.textContent.trim()] : [];
                });
            }""")
            assert not collisions, f"Banner close target overlaps heading or badges: {collisions}"
        if name == "run-match" and width < 576:
            await expect(page.locator("#points-table .winner-option").first).to_be_visible()
            points = await page.locator("#points-table").evaluate("""table => ({
                width: table.clientWidth, contentWidth: table.scrollWidth,
                winners: [...table.querySelectorAll('.winner-options-group')].map(el => el.querySelectorAll('.winner-option').length),
                inaccessible: [...table.querySelectorAll('.winner-option, .delete-point-btn, .set-step-btn')].flatMap(el => {
                    const r = el.getBoundingClientRect();
                    return r.width < 43.9 || r.height < 43.9 || r.left < -1 || r.right > innerWidth + 1
                        ? [{control: el.className, width: r.width, height: r.height, left: r.left, right: r.right}] : [];
                })
            })""")
            assert points["contentWidth"] <= points["width"] + 1, (
                f"Point controls require horizontal scrolling: {points}"
            )
            assert points["winners"] and all(count == 2 for count in points["winners"]), (
                f"Both teams must remain available on each point: {points}"
            )
            assert not points["inaccessible"], f"Phone point controls are clipped or smaller than 44px: {points}"
            live_layout = await page.evaluate("""() => {
                const score = document.querySelector('#score-stones-card').getBoundingClientRect();
                const point = document.querySelector('#points-table .point-entry').getBoundingClientRect();
                const bar = document.querySelector('.live-match-action-bar').getBoundingClientRect();
                const steppers = [...document.querySelectorAll('.set-number-controls')].map(el => ({
                    decrease: el.querySelector('[aria-label="Decrease set number"]').getBoundingClientRect().left,
                    value: el.querySelector('.set-number-display').getBoundingClientRect().left,
                    increase: el.querySelector('[aria-label="Increase set number"]').getBoundingClientRect().left
                }));
                const end = [...document.querySelectorAll('.live-match-action-bar button')]
                    .find(el => el.textContent.trim() === 'End point');
                return {
                    scoreTop: score.top, pointBottom: point.bottom, actionTop: bar.top, steppers,
                    running: Boolean(end), actionCount: document.querySelectorAll('.live-match-action-bar button').length,
                    endWidth: end?.getBoundingClientRect().width, barWidth: bar.width
                };
            }""")
            assert live_layout["scoreTop"] >= 0 and live_layout["pointBottom"] <= live_layout["actionTop"], (
                f"The score and newest point should fit above the phone action bar: {live_layout}"
            )
            assert all(step["decrease"] < step["value"] < step["increase"] for step in live_layout["steppers"]), (
                f"Set controls must read minus, value, plus from left to right: {live_layout}"
            )
            if live_layout["running"]:
                assert live_layout["actionCount"] == 1 and live_layout["endWidth"] >= live_layout["barWidth"] - 32, (
                    f"End point should span the action bar while a point is running: {live_layout}"
                )
        if name == "finalize-match" and width < 576:
            summary = await page.locator(".finalize-points-scroll").evaluate(
                "el => ({width: el.clientWidth, contentWidth: el.scrollWidth})"
            )
            assert summary["contentWidth"] <= summary["width"] + 1, (
                f"Point summary should fit the phone viewport: {summary}"
            )
        invalid_patterns = await page.evaluate("""() => [...document.querySelectorAll('input[pattern]')].flatMap(el => {
            try { new RegExp(el.pattern, 'v'); return []; }
            catch (error) { return [{id: el.id, pattern: el.pattern, error: String(error)}]; }
        })""")
        assert not invalid_patterns, f"Invalid HTML validation patterns: {invalid_patterns}"
        assert not self.page_errors, f"JavaScript errors: {self.page_errors}"
        assert not self.network_errors, f"Failed HTTP resources: {self.network_errors}"
        assert not self.blocked_writes, f"Data-changing requests prevented: {self.blocked_writes}"
        await self.screenshot(page, name, width)

    async def visit(self, page: Page, screen: Screen, width: int) -> None:
        self.page_errors.clear()
        self.network_errors.clear()
        try:
            response = await page.goto(self.base_url + screen.path, wait_until="domcontentloaded")
            assert response and response.ok, f"Page returned HTTP {response.status if response else 'no response'}"
            await page.locator(screen.ready).first.wait_for(state="visible")
            # Avoid arbitrary sleeps: wait for the public data or form shell and
            # for any visible loading placeholder to leave the content region.
            await page.wait_for_function("""() => {
                const root = document.querySelector('main') || document.querySelector('.sc-enter');
                if (!root) return false;
                return ![...root.querySelectorAll('p, div, span')].some(el =>
                    el.children.length === 0 && el.getBoundingClientRect().height > 0 &&
                    /^Loading(?:[ .…]|$)/i.test(el.textContent.trim()));
            }""")
            await self.check_layout(page, screen.name, width)
            self.visited += 1
            print(f"PASS {width:4d}px {screen.path}", flush=True)
        except Exception as error:
            diagnostics = f"JS={self.page_errors}; HTTP={self.network_errors}; blocked writes={self.blocked_writes}"
            self.failures.append(f"{width}px {screen.path}: {error}\n  {diagnostics}")
            print(f"FAIL {self.failures[-1]}", flush=True)
            await self.screenshot(page, f"FAIL-{screen.name}", width)

    async def workflow(self, page: Page, name: str, width: int, action) -> None:
        self.page_errors.clear()
        self.network_errors.clear()
        try:
            await action()
            await self.check_layout(page, name, width)
            self.workflows += 1
            print(f"PASS {width:4d}px workflow: {name}", flush=True)
        except Exception as error:
            diagnostics = f"JS={self.page_errors}; HTTP={self.network_errors}; blocked writes={self.blocked_writes}"
            self.failures.append(f"{width}px workflow {name}: {error}\n  {diagnostics}")
            print(f"FAIL {self.failures[-1]}", flush=True)
            await self.screenshot(page, f"FAIL-{name}", width)
        finally:
            if self.mock_gate is not None:
                self.mock_gate.set()
                self.mock_gate = None

    async def discover_events(self, page: Page) -> None:
        await page.goto(self.base_url, wait_until="domcontentloaded")
        await expect(page.locator('.arctos-event-card a[href="/demo-summer-cup"]').first).to_be_visible()
        await expect(page.locator('.arctos-event-card a[href="/demo-spring-cup"]')).to_have_count(0)
        await page.get_by_role("button", name=re.compile(r"^Past\b")).click()
        await expect(page.locator('.arctos-event-card a[href="/demo-spring-cup"]').first).to_be_visible()
        await page.locator("#event-search").fill("no-such-demo-event")
        await expect(page.get_by_role("heading", name="No matching events")).to_be_visible()
        await page.get_by_role("button", name="Clear search", exact=True).click()
        await expect(page.locator("#event-search")).to_have_value("")
        await page.get_by_role("button", name=re.compile(r"^Upcoming\b")).click()
        await page.locator("#event-search").fill("Summer Cup")
        await expect(page.locator(".arctos-event-card")).to_have_count(1)
        await expect(page.locator('.arctos-event-card a[href="/demo-summer-cup"]').first).to_be_visible()

    async def discovery_controls(self, page: Page) -> None:
        """Check Organize, date filters, and intercepted draft creation without writes."""
        context = page.context
        original_viewport = page.viewport_size
        payload = await api_json(context, self.base_url, "/_api/leagues")
        today = date.fromisoformat(
            await page.evaluate("""() => {
            const d = new Date(); return [d.getFullYear(), String(d.getMonth()+1).padStart(2,'0'),
                String(d.getDate()).padStart(2,'0')].join('-');
        }""")
        )
        cases = (
            ("completed", "Completed season", -8, -1, 2),
            ("today", "Today season", 0, 0, 1),
            ("future", "Future season", 7, 14, 2),
            ("mixed", "Mixed season", -14, 7, 3),
            ("undated", "Undated season", None, None, 0),
        )
        leagues, dates = [], {}
        for key, name, start, end, count in cases:
            key = "smoke-" + key
            leagues.append(
                {
                    **payload["leagues"][0],
                    "league_url": key,
                    "name": name,
                    "team_registration_open": name == "Completed season",
                    "player_registration_open": False,
                    "published": True,
                }
            )
            dates[key] = {
                "start_date": (today + timedelta(days=start)).isoformat() + "T12:00:00" if start is not None else None,
                "end_date": (today + timedelta(days=end)).isoformat() + "T12:00:00" if end is not None else None,
                "event_count": count,
            }
        fixture_payload = {
            **payload,
            "leagues": leagues,
            "event_dates": dates,
            "team_counts": {league["league_url"]: 1 for league in leagues},
            "user_reg_status": {},
        }

        async def fixture(route: Route) -> None:
            if route.request.method == "GET" and urlsplit(route.request.url).netloc == self.origin:
                await route.fulfill(json=fixture_payload)
            else:
                await route.fallback()

        async def toolbar() -> None:
            await page.evaluate("document.fonts.ready")
            controls = await page.locator(".arctos-discovery-actions .arctos-segment, #organize-trigger").evaluate_all(
                """els => els.map(el => {
                    const r = el.getBoundingClientRect(), walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
                    let node, wrapped = false;
                    while ((node = walker.nextNode())) if (node.textContent.trim()) {
                        const range = document.createRange(); range.selectNodeContents(node);
                        if (range.getClientRects().length > 1) wrapped = true;
                    }
                    return {text: el.textContent.trim(), x:r.x, y:r.y, right:r.right, width:r.width, height:r.height, wrapped};
                })"""
            )
            centers = [c["y"] + c["height"] / 2 for c in controls]
            assert len(controls) == 3 and max(centers) - min(centers) <= 1, controls
            assert all(
                c["width"] >= 44
                and c["height"] >= 44
                and not c["wrapped"]
                and c["x"] >= 0
                and c["right"] <= page.viewport_size["width"] + 1
                for c in controls
            ), controls

        await context.route("**/_api/leagues", fixture)
        try:
            for width in (320, 360):
                await page.set_viewport_size({"width": width, "height": 900})
                for path, label, destination, heading in (
                    ("/", "Create new Event", "/new-tournament", "Create an Event"),
                    ("/leagues", "Create a league", "/leagues/new", "Create a league"),
                ):
                    await page.goto(self.base_url + path, wait_until="domcontentloaded")
                    nav = page.get_by_role("navigation", name="Main navigation")
                    await expect(nav.get_by_role("link", name="Events", exact=True)).to_be_visible()
                    assert [text.strip() for text in await nav.get_by_role("link").all_text_contents()][:2] == [
                        "Events",
                        "Leagues",
                    ]
                    await expect(
                        page.locator(
                            '.app-header a[href="/create-event"], .app-header a[href="/new-tournament"], '
                            '.app-header a[href="/leagues/new"]'
                        )
                    ).to_have_count(0)
                    await expect(page.locator(".arctos-event-card").first).to_be_visible()
                    await toolbar()
                    trigger = page.get_by_role("button", name="Organize", exact=True)
                    options = page.locator("#organize-options")
                    await expect(options).to_be_hidden()
                    await expect(
                        page.get_by_role("link", name=re.compile(r"^(?:Create new Event|Create a league|Log past Event)$"))
                    ).to_have_count(0)
                    await trigger.focus()
                    await page.keyboard.press("Enter")
                    await expect(trigger).to_have_attribute("aria-expanded", "true")
                    await expect(trigger).to_be_focused()
                    if path == "/":
                        await expect(options.get_by_role("link", name="Create new Event", exact=True)).to_be_visible()
                        past = options.get_by_role("link", name="Log past Event", exact=True)
                        await expect(past).to_be_visible()
                        await expect(past).to_have_attribute("href", "/new-historical-tournament")
                        await expect(options.get_by_role("link", name="Create a league", exact=True)).to_have_count(0)
                    else:
                        await expect(options.get_by_role("link", name="Create a league", exact=True)).to_be_visible()
                        await expect(options.get_by_role("link", name="Create new Event", exact=True)).to_have_count(0)
                        await expect(options.get_by_role("link", name="Log past Event", exact=True)).to_have_count(0)
                    links = await options.get_by_role("link").evaluate_all("""els => els.map(el => {
                        const r=el.getBoundingClientRect(); return {width:r.width,height:r.height,left:r.left,right:r.right};
                    })""")
                    expected_links = 2 if path == "/" else 1
                    assert len(links) == expected_links and all(
                        link["width"] >= 44
                        and link["height"] >= 44
                        and link["left"] >= 0
                        and link["right"] <= width + 1
                        for link in links
                    ), links
                    await page.keyboard.press("Tab")
                    await expect(options.get_by_role("link").first).to_be_focused()
                    await page.keyboard.press("Escape")
                    await expect(options).to_be_hidden()
                    await expect(trigger).to_be_focused()
                    await trigger.click()
                    await page.get_by_role("button", name="Dismiss organize menu", exact=True).click(
                        position={"x": 2, "y": 200}
                    )
                    await expect(options).to_be_hidden()
                    await expect(trigger).to_be_focused()
                    await trigger.click()
                    create = options.get_by_role("link", name=label, exact=True)
                    await expect(create).to_have_attribute("href", destination)
                    await create.focus()
                    await page.keyboard.press("Enter")
                    await expect(page).to_have_url(self.base_url + destination)
                    await expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                    login = page.get_by_role("link", name="Log in to continue", exact=True)
                    href = urlsplit(await login.get_attribute("href"))
                    assert href.path == "/login" and parse_qs(href.query).get("next") == [destination], href
                    await page.go_back(wait_until="domcontentloaded")
                    await expect(trigger).to_be_visible()
                active = page.get_by_role("button", name=re.compile(r"^Active\s+4$"))
                past = page.get_by_role("button", name=re.compile(r"^Past\s+1$"))
                await expect(active).to_have_attribute("aria-pressed", "true")
                await expect(page.locator(".arctos-league-grid .arctos-event-card")).to_have_count(4)
                for name in ("Today season", "Future season", "Mixed season", "Undated season"):
                    await expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
                mixed = page.locator(".arctos-event-card").filter(has=page.get_by_role("heading", name="Mixed season"))
                for offset in (-14, 7):
                    day = today + timedelta(days=offset)
                    await expect(mixed).to_contain_text(f"{day:%b} {day.day}, {day.year}")
                await expect(mixed).to_contain_text("3 events")
                undated = page.locator(".arctos-event-card").filter(
                    has=page.get_by_role("heading", name="Undated season")
                )
                await expect(undated).to_contain_text("Dates to be announced")
                await page.locator("#league-search").fill("TODAY")
                await expect(page.locator(".arctos-league-grid .arctos-event-card")).to_have_count(1)
                await past.click()
                await expect(page.get_by_role("heading", name="No matching leagues")).to_be_visible()
                await expect(page.locator("#league-search")).to_have_value("TODAY")
                await active.click()
                await expect(page.locator(".arctos-league-grid .arctos-event-card")).to_have_count(1)
                await page.locator("#league-search").fill("no-such-season")
                await expect(page.get_by_role("heading", name="No matching leagues")).to_be_visible()
                await page.get_by_role("button", name="Clear search", exact=True).click()
                await expect(page.locator("#league-search")).to_have_value("")
                await past.click()
                await expect(past).to_have_attribute("aria-pressed", "true")
                await expect(page.locator(".arctos-league-grid .arctos-event-card")).to_have_count(1)
                await expect(page.get_by_role("heading", name="Completed season", exact=True)).to_be_visible()
                await expect(page.get_by_text("Past league", exact=True)).to_be_visible()
                await toolbar()
                await self.check_layout(page, "discovery-league-past", width)
                await active.click()
                await expect(active).to_have_attribute("aria-pressed", "true")
                await expect(page.locator(".arctos-league-grid .arctos-event-card")).to_have_count(4)
                await self.check_layout(page, "discovery-league-active", width)
            await self.draft_creation(page)
        finally:
            await context.unroute("**/_api/leagues", fixture)
            if original_viewport:
                await page.set_viewport_size(original_viewport)

    async def draft_creation(self, page: Page) -> None:
        """Simulate creation errors/success locally; never forward a creation POST."""
        attempts = []
        succeed = False
        responses = {
            "/_api/create-tournament": {"success": True, "url": "demo-open"},
            "/_api/create-league": {"success": True, "league_url": "demo-coast-league"},
        }
        error = "Smoke check: draft creation failed. Your input is safe."

        async def fixture(route: Route) -> None:
            request, parsed = route.request, urlsplit(route.request.url)
            if request.method == "POST" and parsed.netloc == self.origin and parsed.path in responses:
                attempts.append((parsed.path, parse_qs(request.post_data or "")))
                await route.fulfill(json=responses[parsed.path] if succeed else {"success": False, "error": error})
                return
            await route.fallback()

        await page.context.route("**/_api/**", fixture)
        try:
            await self.login(page)
            for path, name_field, slug_field, kind, target, ready in (
                ("/new-tournament", "name", "url", "tournament", "/demo-open/settings", "#tournament-settings-form"),
                (
                    "/leagues/new",
                    "league_name",
                    "league_url",
                    "league",
                    "/leagues/demo-coast-league/settings",
                    "main form",
                ),
            ):
                succeed = False
                await page.goto(self.base_url + path, wait_until="domcontentloaded")
                name, slug = f"Browser-only {kind}", f"smoke-uncreated-{kind}"
                await page.locator(f"#{name_field}").fill(name)
                await page.locator(f"#{slug_field}").fill(slug)
                await expect(page.locator("main")).to_contain_text("private draft")
                submit = page.locator('form button[type="submit"]')
                before = len(attempts)
                await submit.click()
                await expect(page.get_by_role("alert")).to_have_text(error)
                await expect(page.locator(f"#{name_field}")).to_have_value(name)
                await expect(page.locator(f"#{slug_field}")).to_have_value(slug)
                await expect(submit).to_be_enabled()
                assert len(attempts) == before + 1 and attempts[-1] == (
                    f"/_api/create-{kind}",
                    {name_field: [name], slug_field: [slug]},
                ), attempts
                succeed = True
                await submit.click()
                await expect(page).to_have_url(self.base_url + target)
                await expect(page.locator(ready).first).to_be_visible()
                assert len(attempts) == before + 2, attempts
        finally:
            await page.context.unroute("**/_api/**", fixture)
            await page.context.clear_cookies()
            self.authenticated = False
            await page.goto(self.base_url, wait_until="domcontentloaded")
            await expect(page.locator("#event-search")).to_be_visible()

    async def registration_and_schedule(self, page: Page) -> None:
        """Exercise navigation using GET-only draft/unregistered browser fixtures."""
        context = page.context
        event = await api_json(context, self.base_url, "/_api/tournaments/demo-league-round-one")
        league = event["tournament"]["league"]["league_url"]
        schedule = await api_json(context, self.base_url, "/_api/tournaments/demo-open/schedule-setup")
        match_id = schedule["matches"][0]["uuid"]
        competitions = await api_json(context, self.base_url, "/_api/demo-open/sidecomps")
        comp_id = competitions[0]["id"]
        league_path = f"/leagues/{league}"
        match_path = f"/demo-open/match/{match_id}"
        schedule_api = "/_api/tournaments/demo-open/schedule-setup"
        published, allowed = False, False
        paths = {
            "/_api/tournaments/demo-open",
            "/_api/tournaments/demo-open/match",
            schedule_api,
            "/_api/tournaments/demo-league-round-one",
            f"/_api{league_path}",
            f"/_api/sidecomps/{comp_id}",
        }

        async def fixture(route: Route) -> None:
            parsed = urlsplit(route.request.url)
            path = parsed.path
            if route.request.method != "GET" or parsed.netloc != self.origin or path not in paths:
                await route.fallback()
                return
            if path == schedule_api:
                if not allowed:
                    await route.fulfill(status=403, json={"error": "Schedule not published"})
                else:
                    await route.fallback()
                return
            response = await route.fetch()
            data = await response.json()
            if path.startswith("/_api/tournaments/demo-open"):
                data["can_view_schedule"] = allowed
                data.get("tournament", data)["schedule_published"] = published
            data.update(is_current_player_registered=False, is_current_team_registered=False)
            if path == f"/_api/sidecomps/{comp_id}":
                data.update(
                    viewer_can_register=True, viewer_is_registered_in_comp=False, viewer_registration_incomplete=False
                )
            await route.fulfill(response=response, json=data)

        await context.route("**/_api/**", fixture)
        try:
            for path in ("/demo-open", "/demo-open/results"):
                await page.goto(self.base_url + path, wait_until="domcontentloaded")
                unavailable_tab = page.locator("#event-tab-schedule")
                await expect(unavailable_tab).to_have_text("Schedule")
                await expect(unavailable_tab).to_be_disabled()
                assert await unavailable_tab.get_attribute("href") is None
                await expect(page.locator('.event-route-tabs a[href^="/demo-open/schedule"]')).to_have_count(0)
            # This separate page explicitly expects the denied read response; all mutations
            # still use the context-wide guard, and no backend publication state changes.
            denied_page = await context.new_page()
            try:
                async with denied_page.expect_response(
                    lambda response: urlsplit(response.url).path == schedule_api
                ) as denied:
                    await denied_page.goto(self.base_url + "/demo-open/schedule", wait_until="domcontentloaded")
                assert (await denied.value).status == 403
                await expect(
                    denied_page.get_by_text("The schedule has not been posted yet.", exact=True)
                ).to_be_visible()
                await expect(denied_page.locator("#event-tab-schedule")).to_be_disabled()
                await expect(denied_page.locator("#event-tab-schedule")).to_have_attribute("aria-current", "page")
                await expect(denied_page.locator(".schedule-keyboard-focus")).to_have_count(0)
                await denied_page.locator("#event-tab-info").click()
                await expect(denied_page).to_have_url(self.base_url + "/demo-open")
            finally:
                await denied_page.close()
            await page.goto(self.base_url + match_path, wait_until="domcontentloaded")
            crumb = page.get_by_role("navigation", name="breadcrumb")
            unavailable = crumb.get_by_text("Schedule not posted", exact=True)
            await expect(unavailable).to_be_visible()
            assert await unavailable.evaluate("el => el.tagName === 'SPAN' && !el.hasAttribute('href')")
            await expect(crumb.locator('a[href^="/demo-open/schedule"]')).to_have_count(0)
            published, allowed = True, True
            for path in ("/demo-open", match_path):
                await page.goto(self.base_url + path, wait_until="domcontentloaded")
                scope = (
                    page.locator(".event-route-tabs")
                    if path == "/demo-open"
                    else page.get_by_role("navigation", name="breadcrumb")
                )
                await scope.get_by_role("link", name="Schedule", exact=True).click()
                await expect(page.locator(".schedule-keyboard-focus")).to_be_visible()
                await expect(page.locator("#event-tab-schedule")).to_have_attribute("aria-current", "page")
            await self.login(page)
            published = False
            for path in ("/demo-open", match_path):
                await page.goto(self.base_url + path, wait_until="domcontentloaded")
                scope = (
                    page.locator(".event-route-tabs")
                    if path == "/demo-open"
                    else page.get_by_role("navigation", name="breadcrumb")
                )
                label = "Schedule" if path == "/demo-open" else "Preview schedule"
                await scope.get_by_role("link", name=label, exact=True).click()
                await expect(page.locator(".schedule-keyboard-focus")).to_be_visible()
                await expect(page.locator("#event-tab-schedule")).to_have_attribute("aria-current", "page")
            await page.goto(self.base_url + "/demo-open", wait_until="domcontentloaded")
            await page.locator("#organizer-tools-trigger").click()
            await page.get_by_role("link", name="Edit Schedule", exact=True).click()
            await expect(page.locator(".schedule-editor-split")).to_be_visible()
            for path, destination in (
                ("/demo-open", "/demo-open/register"),
                ("/demo-league-round-one", league_path + "/register"),
                (league_path, league_path + "/register"),
            ):
                await page.goto(self.base_url + path, wait_until="domcontentloaded")
                register = page.get_by_role("link", name="Register", exact=True)
                await expect(register).to_have_attribute("href", destination)
                await register.click()
                await expect(page).to_have_url(self.base_url + destination)
                await expect(page.get_by_role("button", name="Register", exact=True)).to_be_visible()
            # Observe even briefly mounted standalone forms during a direct league-event visit.
            await page.add_init_script("""window.eventRegistrationFormSeen = false;
                const direct = location.pathname === '/demo-league-round-one/register';
                const forms = '#player-register-form, #team-register-form';
                new MutationObserver(records => {
                    if (direct && records.some(r => [...r.addedNodes].some(n => n.nodeType === 1 &&
                        (n.matches(forms) || n.querySelector(forms)))))
                        window.eventRegistrationFormSeen = true;
                }).observe(document, {childList: true, subtree: true});""")
            await page.goto(self.base_url + "/about", wait_until="domcontentloaded")
            await page.goto(self.base_url + "/demo-league-round-one/register", wait_until="domcontentloaded")
            await expect(page).to_have_url(self.base_url + league_path + "/register")
            await expect(page.get_by_role("button", name="Register", exact=True)).to_be_visible()
            assert not await page.evaluate("window.eventRegistrationFormSeen"), (
                "Standalone form flashed before redirect"
            )
            await page.go_back(wait_until="domcontentloaded")
            await expect(page).to_have_url(self.base_url + "/about")
            await page.go_forward(wait_until="domcontentloaded")
            await expect(page).to_have_url(self.base_url + league_path + "/register")
            await page.goto(self.base_url + f"/demo-open/sidecomps/{comp_id}", wait_until="domcontentloaded")
            await page.get_by_role("button", name="Register", exact=True).click()
            dialog = page.get_by_role("dialog")
            await expect(dialog.get_by_role("button", name="Register", exact=True)).to_be_visible()
            await dialog.get_by_role("button", name="Close", exact=True).click()
        finally:
            await context.unroute("**/_api/**", fixture)

    async def introduction_visibility(self, page: Page) -> None:
        await page.goto(self.base_url, wait_until="domcontentloaded")
        dismiss = page.locator("#page-banner-dismiss")
        intro = page.locator("#page-banner")
        await expect(page.locator("#page-banner-heading")).to_have_text("Events")
        box = await dismiss.bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44, "Banner close target is too small"
        assert await intro.evaluate("el => getComputedStyle(el).textAlign") == "center"
        await expect(intro.get_by_role("link", name="jugger", exact=True)).to_have_attribute(
            "href", "https://en.wikipedia.org/wiki/Jugger"
        )
        await page.locator("#event-search").fill("Summer Cup")
        await expect(page.locator(".arctos-event-card")).to_have_count(1)
        before = await page.locator("#event-search").bounding_box()
        await dismiss.focus()
        await page.keyboard.press("Enter")
        await expect(intro).to_be_hidden()
        await expect(page.locator("#event-discovery")).to_be_focused()
        await expect(page.locator("#page-banner-heading")).to_be_hidden()
        await expect(page.locator("#event-search")).to_have_value("Summer Cup")
        await expect(page.locator(".arctos-event-card")).to_have_count(1)
        after = await page.locator("#event-search").bounding_box()
        assert before and after and after["y"] < before["y"], "Dismissing the banner did not reveal more events"
        await (
            page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Teams", exact=True).click()
        )
        await (
            page.get_by_role("navigation", name="Main navigation")
            .get_by_role("link", name="Events", exact=True)
            .click()
        )
        await expect(intro).to_be_hidden()
        await page.reload(wait_until="domcontentloaded")
        await expect(intro).to_be_hidden()
        await page.locator("#event-search").fill("Summer Cup")
        await page.locator("#utility-trigger").click()
        await page.get_by_role("button", name="Appearance", exact=True).click()
        setting = page.get_by_role("checkbox", name="Show page introduction", exact=True)
        await expect(setting).not_to_be_checked()
        await setting.check()
        await expect(intro).to_be_visible()
        await expect(page.locator("#appearance-options")).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.locator("#utility-trigger")).to_be_focused()
        await expect(page.locator("#event-search")).to_have_value("Summer Cup")
        await expect(page.locator(".arctos-event-card")).to_have_count(1)
        await page.reload(wait_until="domcontentloaded")
        await expect(intro).to_be_visible()

    async def registration_validation(self, page: Page) -> None:
        await page.goto(self.base_url + "/register/player", wait_until="domcontentloaded")
        await page.locator("#username").fill("demo-validation-only")
        await page.locator("#name").fill("Retain this display name")
        await page.locator("#password").fill("demo-first-password")
        await page.locator("#confirm_password").fill("demo-other-password")
        await page.get_by_role("button", name="Create account", exact=True).click()
        await expect(page.get_by_role("alert")).to_contain_text("Passwords do not match")
        await expect(page.locator("#username")).to_have_value("demo-validation-only")
        await expect(page.locator("#name")).to_have_value("Retain this display name")
        await expect(page.locator("#password")).to_have_value("demo-first-password")
        await expect(page.get_by_role("button", name="Create account", exact=True)).to_be_enabled()

    async def profile_rows_fit(self, page: Page, selector: str) -> None:
        """Histories keep table columns; overflow is accessible within the table region."""
        await page.evaluate("document.fonts.ready")
        for table in await page.locator(selector).all():
            if not await table.is_visible():
                continue
            layout = await table.evaluate("""table => {
                const region = table.closest('.profile-table-scroll');
                const rect = region?.getBoundingClientRect();
                return {
                    display: getComputedStyle(table).display,
                    rowDisplay: table.tBodies[0]?.rows[0] ? getComputedStyle(table.tBodies[0].rows[0]).display : null,
                    headingsVisible: !!table.tHead && getComputedStyle(table.tHead).display === 'table-header-group'
                        && [...table.tHead.querySelectorAll('th')].every(th => th.scope === 'col'
                            && th.textContent.trim() && th.getBoundingClientRect().height >= 20),
                    region: region && {role: region.getAttribute('role'), tabIndex: region.tabIndex,
                        label: region.getAttribute('aria-label'), overflow: getComputedStyle(region).overflowX,
                        left: rect.left, right: rect.right, width: region.clientWidth,
                        content: region.scrollWidth, scroll: region.scrollLeft},
                    viewport: document.documentElement.clientWidth,
                    document: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)
                };
            }""")
            assert layout["display"] == "table" and layout["rowDisplay"] in (None, "table-row"), layout
            assert layout["headingsVisible"], f"History headings are missing or inaccessible: {layout}"
            region = layout["region"]
            assert region and region["role"] == "region" and region["tabIndex"] == 0 and region["label"], layout
            assert region["overflow"] in ("auto", "scroll"), layout
            assert region["left"] >= -1 and region["right"] <= layout["viewport"] + 1, layout
            assert layout["document"] <= layout["viewport"] + 1, f"Table escaped its scroll region: {layout}"
            scroll = table.locator("..")
            if region["content"] > region["width"] + 1:
                await scroll.evaluate("el => el.scrollLeft = 0")
                await scroll.focus()
                await page.keyboard.press("ArrowRight")
                await page.wait_for_function("el => el.scrollLeft > 0", arg=await scroll.element_handle())
                await scroll.evaluate("el => el.scrollLeft = el.scrollWidth")
                last_header = table.locator("thead th").last
                assert await last_header.evaluate("""th => {
                    const cell = th.getBoundingClientRect(), region = th.closest('.profile-table-scroll').getBoundingClientRect();
                    return cell.left >= region.left - 1 && cell.right <= region.right + 1;
                }"""), "Last history column cannot be scrolled into view"
            controls = table.locator("input:not([type=checkbox]), button, a")
            if await controls.count():
                await controls.last.focus()
                assert await controls.last.evaluate("""el => {
                    const control = el.getBoundingClientRect(), region = el.closest('.profile-table-scroll').getBoundingClientRect();
                    return control.left >= region.left - 1 && control.right <= region.right + 1;
                }"""), "History action is unreachable with keyboard focus"
            await scroll.evaluate("(el, left) => el.scrollLeft = left", region["scroll"])

    async def profile_event_count(self, page: Page, account_type: str, account_id: str) -> None:
        path = f"/{account_type}s/{account_id}"
        profile = await api_json(page.context, self.base_url, "/_api" + path)
        count = profile["past_event_count"]
        assert isinstance(count, int) and count >= 0, "Invalid public past-event count"
        await page.goto(self.base_url + f"/{account_type}s", wait_until="domcontentloaded")
        await page.locator(f"#{account_type}-search").fill(account_id)
        await page.get_by_role("search").get_by_role("button", name="Search", exact=True).click()
        card = page.locator(f'.arctos-directory-card[href="{path}"]')
        await expect(card.locator(".arctos-directory-event-count strong")).to_have_text(str(count))
        await card.click()
        await expect(page.locator(".profile-event-stat dt")).to_have_text("Past events")
        await expect(page.locator(".profile-event-stat dd")).to_have_text(str(count))

    async def player_profile_tabs(self, page: Page) -> None:
        await self.login(page, "demo-player")
        await self.profile_event_count(page, "player", "demo-player")
        history = page.locator("#player-tournament-history")
        await expect(history).to_be_hidden()
        await page.locator("#player-tab-history").focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#player-tab-history")).to_have_attribute("aria-current", "page")
        await expect(history.locator("tbody tr").first).to_be_visible()
        await self.profile_rows_fit(page, "#player-tournament-history table")
        await page.locator("#player-tab-info").click()
        await expect(history).to_be_hidden()

        await page.goto(self.base_url + "/players/demo-player", wait_until="domcontentloaded")
        injuries = page.locator("#player-injury-history")
        await expect(injuries).to_be_hidden()
        await page.locator("#player-tab-injuries").click()
        description_count = await injuries.get_by_label("Injury description", exact=True).count()
        await injuries.get_by_role("button", name="New Injury", exact=True).click()
        description = injuries.get_by_label("Injury description", exact=True).last
        await description.fill("Unsaved injury description")
        active = injuries.get_by_label("Active", exact=True).last
        await active.uncheck()
        await active.check()
        await self.profile_rows_fit(page, "#player-injury-history table")
        await page.go_back()
        await expect(page.locator("#player-tab-info")).to_have_attribute("aria-current", "page")
        await page.go_forward()
        await expect(description).to_have_value("Unsaved injury description")
        await expect(active).to_be_checked()
        await page.locator("#player-tab-history").click()
        await expect(description).to_be_hidden()
        await page.locator("#player-tab-injuries").click()
        await expect(description).to_have_value("Unsaved injury description")
        await expect(active).to_be_checked()
        await injuries.get_by_role("button", name="Cancel", exact=True).last.click()
        await expect(injuries.get_by_label("Injury description", exact=True)).to_have_count(description_count)
        for tab in await page.locator(".event-route-tabs a").all():
            box = await tab.bounding_box()
            assert box and box["height"] >= 44 and box["width"] >= 44, "Profile navigation target is too small"

    async def team_profile_tabs(self, page: Page) -> None:
        await self.profile_event_count(page, "team", "demo-team")
        registrations = page.locator("#team-tournament-registrations")
        await expect(registrations).to_be_hidden()
        await page.locator("#team-tab-history").click()
        row = registrations.locator("tbody > tr").filter(has=page.locator('a[href="/demo-open"]')).first
        toggle = row.get_by_role("button")
        await toggle.focus()
        await page.keyboard.press("Enter")
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        players = registrations.locator(".profile-roster")
        await expect(players).to_be_visible()
        await expect(players.get_by_role("link").first).to_be_visible()
        await self.profile_rows_fit(page, "#team-tournament-registrations table")
        await page.go_back()
        await expect(page.locator("#team-tab-info")).to_have_attribute("aria-current", "page")
        await page.go_forward()
        await expect(players).to_be_visible()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await page.locator("#team-tab-info").click()
        await expect(players).to_be_hidden()
        await page.locator("#team-tab-history").click()
        await expect(players).to_be_visible()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(registrations.get_by_role("link", name="Manage Roster").first).to_be_visible()

    async def settings_disclosures(self, page: Page) -> None:
        for path in ("/demo-open/settings", "/leagues/demo-coast-league/settings"):
            await page.goto(self.base_url + path, wait_until="domcontentloaded")
            sections = page.locator("details.section-disclosure")
            await expect(sections).to_have_count(1)
            for section in await sections.all():
                await expect(section).to_have_js_property("open", True)
                summary = section.locator("summary")
                await summary.focus()
                await page.keyboard.press("Enter")
                await expect(section).to_have_js_property("open", False)
                await page.keyboard.press("Space")
                await expect(section).to_have_js_property("open", True)
            toggle = page.locator("#penalty-types-toggle")
            content = page.locator("#penalty-types-content")
            add = page.get_by_role("button", name="Add penalty type", exact=True)
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(content).to_be_hidden()
            await expect(add).to_be_visible()
            assert await add.evaluate("el => !!el.closest('.penalty-types-heading') && !el.closest('table')")
            await page.evaluate("document.fonts.ready")
            # Keyboard focus can still be scrolling the page. Sample both
            # controls in one frame so their viewport coordinates are comparable.
            heading_bounds, add_bounds = await page.locator(".penalty-types-heading").evaluate(
                """el => ['#penalty-types-toggle', '#penalty-types-add'].map(selector =>
                    el.querySelector(selector).getBoundingClientRect().toJSON())"""
            )
            for bounds in (heading_bounds, add_bounds):
                assert bounds["width"] >= 44 and bounds["height"] >= 44, bounds
            assert (
                abs(heading_bounds["y"] + heading_bounds["height"] / 2 - add_bounds["y"] - add_bounds["height"] / 2)
                <= 1
            ), (heading_bounds, add_bounds)
            await toggle.focus()
            await page.keyboard.press("Enter")
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await page.keyboard.press("Space")
            await expect(content).to_be_hidden()
            await add.click()
            name = page.get_by_role("textbox", name="Penalty type name", exact=True)
            await expect(name).to_be_focused()
            await expect(content).to_be_visible()
            await expect(add).to_be_disabled()
            name_bounds = await name.bounding_box()
            assert name_bounds and name_bounds["width"] >= 160, name_bounds
            await expect(content.get_by_label("Description (optional)", exact=True)).to_be_visible()
            await name.fill("Unsaved penalty type")
            await toggle.click()
            await expect(content).to_be_hidden()
            await toggle.click()
            await expect(name).to_have_value("Unsaved penalty type")
            await name.locator("xpath=ancestor::tr").get_by_role("button", name="Cancel", exact=True).click()
            await expect(name).to_have_count(0)
            await expect(add).to_be_enabled()
            if path == "/demo-open/settings":
                advanced = page.locator("#head-ref-options")
                await expect(advanced).to_have_js_property("open", False)
                await advanced.locator("summary").click()
                await expect(advanced.get_by_role("checkbox").first).to_be_visible()
                await advanced.locator("summary").click()
                await expect(advanced).to_have_js_property("open", False)

    async def login(self, page: Page, username: str = DEMO_USER) -> None:
        await page.goto(self.base_url + "/login", wait_until="domcontentloaded")
        await page.get_by_label("Username", exact=True).fill(username)
        await page.get_by_label("Password", exact=True).fill(DEMO_PASSWORD)
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await expect(page.locator("#event-search")).to_be_visible()
        result = await page.context.request.get(self.base_url + "/_api/me")
        assert result.ok and (await result.json())["id"] == username, f"Demo login failed: {username}"
        self.authenticated = True

    async def bracket_controls(self, page: Page) -> None:
        await page.goto(self.base_url + "/demo-open/bracket", wait_until="domcontentloaded")
        canvas = page.locator("#bracket-canvas-wrap")
        await expect(canvas).to_be_visible()
        assert await canvas.evaluate("el => getComputedStyle(el).touchAction") == "auto", (
            "Read-only bracket prevents normal phone scrolling"
        )
        await page.locator("#bracketEditMode").check()
        controls = page.get_by_role("group", name="Bracket canvas view", exact=True)
        await expect(controls).to_be_visible()
        await controls.get_by_role("button", name="Zoom in", exact=True).click()
        await expect(controls.locator("output")).to_have_text("120%")
        await controls.get_by_role("button", name="Move diagram right", exact=True).click()
        await controls.get_by_role("button", name="Reset view", exact=True).click()
        await expect(controls.locator("output")).to_have_text("100%")
        await page.locator("#bracketEditMode").uncheck()
        assert await canvas.evaluate("el => getComputedStyle(el).touchAction") == "auto"

    async def bracket_touch(self, page: Page) -> None:
        # Supply one movable node only in this browser response. Cancel the drag
        # instead of saving it, so the real demo bracket remains untouched.
        pattern = "**/_api/tournaments/demo-open/bracket"

        async def fixture(route: Route) -> None:
            if route.request.method != "GET":
                await self.guard_requests(route)
                return
            response = await route.fetch()
            data = await response.json()
            data["texts"] = [{"id": "touch-probe", "text": "Touch drag probe", "x_pos": 50, "y_pos": 80, "size": 18}]
            data["placements"] = []
            data["images"] = []
            data["labeled_teams"] = []
            await route.fulfill(response=response, json=data)

        await page.context.route(pattern, fixture)
        cdp = await page.context.new_cdp_session(page)
        touch_active = False
        try:
            await page.goto(self.base_url + "/demo-open/bracket", wait_until="domcontentloaded")
            await page.locator("#bracketEditMode").check()
            wrap = page.locator("#bracket-canvas-wrap")
            await wrap.scroll_into_view_if_needed()
            node = page.locator(".bracket-text")
            await expect(node).to_be_visible()
            box = await node.bounding_box()
            bounds = await wrap.bounding_box()
            assert box and bounds, "Touch bracket fixture is missing its node or canvas"
            original = await node.get_attribute("style")
            start = {"x": box["x"] + 40, "y": box["y"] + 10, "id": 1}
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [start]})
            touch_active = True
            await cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{**start, "x": start["x"] + 40, "y": start["y"] + 35}]},
            )
            await expect(node).not_to_have_attribute("style", original)
            moved = await node.get_attribute("style")
            await cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{**start, "x": start["x"] + 60, "y": max(20, bounds["y"] - 30)}]},
            )
            await expect(node).not_to_have_attribute("style", moved)
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
            touch_active = False
            await expect(node).to_have_attribute("style", original)
            assert not self.blocked_writes, f"Touch cancellation tried to save: {self.blocked_writes}"

            canvas = page.locator("#bracket-canvas")
            transform = await canvas.get_attribute("style")
            viewport = page.viewport_size
            assert viewport
            blank = {
                "x": bounds["x"] + bounds["width"] - 45,
                "y": min(bounds["y"] + 280, viewport["height"] - 60),
                "id": 2,
            }
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [blank]})
            touch_active = True
            await cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{**blank, "x": blank["x"] - 45, "y": blank["y"] - 55}]},
            )
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            touch_active = False
            await expect(canvas).not_to_have_attribute("style", transform)
            await node.dblclick()
            await expect(page.get_by_text("Edit Text", exact=True)).to_be_visible()
        finally:
            if touch_active:
                await cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
            await cdp.detach()
            await page.context.unroute(pattern, fixture)

    async def finalize_failure(self, page: Page, match_id: str) -> None:
        await page.goto(self.base_url + f"/demo-open/finalize-match/{match_id}", wait_until="domcontentloaded")
        await page.locator("#winner-team1").check()
        await page.locator("#final_notes").fill("Keep this note after a failed save.")
        for canvas_id in ("team1_signature_canvas", "team2_signature_canvas"):
            canvas = page.locator(f"#{canvas_id}")
            await canvas.scroll_into_view_if_needed()
            box = await canvas.bounding_box()
            assert box and box["width"] > 30, f"Signature canvas has no usable width: {canvas_id}"
            await page.mouse.move(box["x"] + 12, box["y"] + 25)
            await page.mouse.down()
            await page.mouse.move(box["x"] + min(90, box["width"] - 12), box["y"] + 60, steps=5)
            await page.mouse.up()
            await expect(canvas).to_have_attribute("data-signed", "true")
        self.mock_count = 0
        self.mock_gate = asyncio.Event()
        button = page.get_by_role("button", name="Confirm and finalize match", exact=True)
        await button.click()
        await expect(page.get_by_role("button", name="Finalizing match…", exact=True)).to_be_disabled()
        # A second form submission while pending must not send another request.
        await page.locator("form").dispatch_event("submit")
        self.mock_gate.set()
        await expect(page.get_by_role("alert")).to_contain_text(MOCK_ERROR)
        await expect(button).to_be_enabled()
        assert self.mock_count == 1, f"Expected one save request; got {self.mock_count}"
        await expect(page.locator("#final_notes")).to_have_value("Keep this note after a failed save.")
        await expect(page.locator("#winner-team1")).to_be_checked()
        for canvas_id in ("team1_signature_canvas", "team2_signature_canvas"):
            await expect(page.locator(f"#{canvas_id}")).to_have_attribute("data-signed", "true")


async def api_json(context: BrowserContext, base_url: str, path: str):
    response = await context.request.get(base_url + path)
    if not response.ok:
        raise RuntimeError(f"Demo discovery {path} returned HTTP {response.status}: {(await response.text())[:250]}")
    return await response.json()


def arguments(description: str | None = __doc__, *, workflows: bool = False) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8080", help="Loopback Dioxus/proxy origin serving the demo"
    )
    parser.add_argument(
        "--browser-executable", type=Path, help="Installed Chromium executable, e.g. /usr/bin/google-chrome"
    )
    parser.add_argument("--screenshots", type=Path, help="Optional output directory for browser screenshots")
    if workflows:
        parser.add_argument(
            "--workflow",
            choices=("registration-and-schedule", "discovery-controls"),
            help="Run one workflow (discovery covers 320/360px; other workflows 360px)",
        )
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        parser.error("--base-url must be a loopback http(s) origin, without credentials, path, query or fragment")
    args.base_url = args.base_url.rstrip("/")
    if args.browser_executable and not args.browser_executable.is_file():
        parser.error(f"Browser executable does not exist: {args.browser_executable}")
    return args


async def run(args: argparse.Namespace) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        preflight = await playwright.request.new_context()
        try:
            response = await preflight.get(args.base_url + "/_api/demo-info", timeout=TIMEOUT_MS)
            try:
                marker = await response.json() if response.ok else {}
            except ValueError:
                marker = {}
            if marker.get("demo") is not True or marker.get("organizer") != DEMO_USER:
                raise RuntimeError("Refusing to run: backend is missing the scripts.dev_demo demo-only marker.")
        finally:
            await preflight.dispose()
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        try:
            widths = (360,) if args.workflow else WIDTHS
            for width in widths:
                check.authenticated = False
                context = await browser.new_context(
                    viewport={"width": width, "height": 900}, has_touch=width < 768, is_mobile=width < 768
                )
                context.set_default_timeout(TIMEOUT_MS)
                await context.route("**/*", check.guard_requests)
                page = await context.new_page()
                check.observe(page)
                try:
                    if args.workflow:
                        action = {
                            "registration-and-schedule": check.registration_and_schedule,
                            "discovery-controls": check.discovery_controls,
                        }[args.workflow]
                        await check.workflow(page, args.workflow, width, lambda: action(page))
                        continue
                    if width == 360:
                        await check.workflow(
                            page, "registration-and-schedule", width, lambda: check.registration_and_schedule(page)
                        )
                        await context.clear_cookies()
                        check.authenticated = False
                        await check.workflow(page, "discovery-controls", width, lambda: check.discovery_controls(page))
                    for screen in PUBLIC_SCREENS:
                        await check.visit(page, screen, width)
                    await check.workflow(page, "event-search-and-dates", width, lambda: check.discover_events(page))
                    await check.workflow(
                        page, "banner-dismissal-and-restore", width, lambda: check.introduction_visibility(page)
                    )
                    await check.workflow(
                        page, "registration-validation", width, lambda: check.registration_validation(page)
                    )
                    await check.workflow(page, "organizer-login", width, lambda: check.login(page))
                    # Stop this width if authentication failed; avoid misleading permission failures.
                    me = await api_json(context, args.base_url, "/_api/me")
                    assert me["id"] == DEMO_USER
                    schedule = await api_json(context, args.base_url, "/_api/tournaments/demo-open/schedule-setup")
                    matches = {match["name"]: match["uuid"] for match in schedule["matches"]}
                    if not {"Field A live match", "Next on Field B"} <= matches.keys():
                        raise RuntimeError("Demo matches missing; use an unmodified demo fixture for this check.")
                    live_id = matches["Field A live match"]
                    next_id = matches["Next on Field B"]
                    sidecomps = await api_json(context, args.base_url, "/_api/demo-open/sidecomps")
                    competition = next(comp for comp in sidecomps if comp["name"] == "Sideline Dueling — Demo")
                    comp_id = competition["id"]
                    dynamic = (
                        Screen("match", f"/demo-open/match/{live_id}", ".match-overview"),
                        Screen("start-match", f"/demo-open/start-match/{next_id}", "#match_notes"),
                        Screen("run-match", f"/demo-open/run-match/{live_id}", "#score-stones-card"),
                        Screen("finalize-match", f"/demo-open/finalize-match/{live_id}", "#final_notes"),
                        Screen("sidecomp", f"/demo-open/sidecomps/{comp_id}", ".event-tool-content h2"),
                        Screen("sidecomp-edit", f"/demo-open/sidecomps/{comp_id}/edit", "#sidecomp-name"),
                        Screen(
                            "sidecomp-quick-register",
                            f"/demo-open/sidecomps/{comp_id}/register-player-as-to",
                            "main .list-group",
                        ),
                        Screen("sidecomp-results", f"/demo-open/sidecomps/{comp_id}/results", ".event-tool-content h2"),
                        Screen(
                            "sidecomp-manage",
                            f"/demo-open/sidecomps/{comp_id}/manage-results",
                            ".event-tool-content h2",
                        ),
                        Screen("sidecomp-scoring", f"/demo-open/sidecomps/{comp_id}/enter-results", ".sc-numpad"),
                    )
                    for screen in (*AUTH_SCREENS, *dynamic):
                        await check.visit(page, screen, width)
                    await check.workflow(page, "settings-disclosures", width, lambda: check.settings_disclosures(page))
                    await check.workflow(page, "bracket-view-controls", width, lambda: check.bracket_controls(page))
                    if width < 768:
                        await check.workflow(page, "bracket-touch-and-cancel", width, lambda: check.bracket_touch(page))
                    await check.workflow(
                        page, "finalize-save-failure", width, lambda: check.finalize_failure(page, live_id)
                    )
                    await check.workflow(page, "team-login", width, lambda: check.login(page, "demo-team"))
                    for screen in (
                        Screen("event-invitations", "/demo-open/invitations", ".event-tool-content h2"),
                        Screen(
                            "league-invitations", "/leagues/demo-coast-league/invitations", ".event-tool-content h2"
                        ),
                        Screen("team-profile-edit", "/teams/demo-team/edit", "main form"),
                    ):
                        await check.visit(page, screen, width)
                    await check.workflow(page, "team-profile-tabs", width, lambda: check.team_profile_tabs(page))
                    await check.workflow(page, "player-profile-tabs", width, lambda: check.player_profile_tabs(page))
                finally:
                    if check.mock_gate:
                        check.mock_gate.set()
                    await context.close()
        finally:
            await browser.close()
    print(
        f"\n{check.visited} screen checks and {check.workflows} workflows passed; "
        f"{len(check.failures)} failures across widths {widths}."
    )
    if check.failures:
        print("\n".join(f"- {failure}" for failure in check.failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(arguments(workflows=True))))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"Smoke check stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error
