"""Check registration actions against the explicitly started local demo.

    uv run python scripts/check_registration_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-registration

Uses real demo response shapes with GET-only account/registration fixtures. The
shared mutation guard remains active; no registration or login is submitted.
Checks scope, existing registrations, closed gates, side-event eligibility, and
action placement/contrast at phone and desktop widths across all themes.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import re
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, Route, async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu
from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments


SCOPES = (
    ("tournament", "/demo-open", "/demo-open/register"),
    ("league", "/leagues/demo-coast-league", "/leagues/demo-coast-league/register"),
    ("league-event", "/demo-league-round-one", "/leagues/demo-coast-league/register"),
)


@dataclass
class Viewer:
    role: str | None = "player"
    registered: bool = False
    player_open: bool = True
    team_open: bool = True
    organizer: bool = False
    side_state: str = "eligible"


class RegistrationFixtures:
    def __init__(self, check: SmokeCheck, payloads: dict, side: dict):
        self.check, self.payloads, self.side = check, payloads, side
        self.viewer = Viewer()
        self.side_path = f"/demo-open/sidecomps/{side['id']}"

    async def handle(self, route: Route) -> None:
        parsed = urlsplit(route.request.url)
        if route.request.method != "GET" or parsed.netloc != self.check.origin:
            await route.fallback()
            return
        path, v = parsed.path, self.viewer
        if path == "/_api/me":
            payload = {"id": "demo-" + (v.role or "player"), "name": "Demo viewer", "type": v.role}
            await route.fulfill(status=200 if v.role else 401, json=payload if v.role else {"error": "Not signed in"})
            return
        if path in self.payloads:
            payload = deepcopy(self.payloads[path])
            config = payload.get("tournament", payload.get("league"))
            configs = [config] + ([config["league"]] if config.get("league") else [])
            for config in configs:
                config.update(player_registration_open=v.player_open, team_registration_open=v.team_open)
                config["registration_open"] = v.player_open or v.team_open
            payload.update(is_current_player_registered=v.registered, is_current_team_registered=v.registered)
            payload["to_entries"] = (
                [{"id": 1, "user_id": "demo-" + v.role, "user_type": v.role, "user_name": "Demo viewer"}]
                if v.organizer and v.role
                else []
            )
        elif path == f"/_api/sidecomps/{self.side['id']}":
            payload = deepcopy(self.side)
            payload.update(
                description="Choose your role to join this side competition. " * 8,
                allowed_weapons=["LONG", "QTIP"],
                active=True,
                results_page_enabled=True,
                registration_open=v.side_state not in ("closed", "incomplete", "registered"),
                viewer_is_to=v.organizer,
                viewer_can_register=v.role == "player" and v.side_state == "eligible",
                viewer_is_registered_in_comp=v.side_state in ("incomplete", "registered"),
                viewer_registration_incomplete=v.side_state == "incomplete",
                viewer_weapon="LONG" if v.side_state == "registered" else None,
            )
        elif path.endswith("/registrations/player/me"):
            payload = {
                "registration": {
                    "id": 1,
                    "jersey_name": "Demo",
                    "jersey_number": "7",
                    "team": None,
                    "status": "CONFIRMED",
                },
                "current_team": None,
                "waiver_required": False,
                "waiver_signature_valid": True,
            }
        elif path.endswith("/registrations/team/me"):
            payload = {"registration": {"id": 1, "pseudonym": "Demo team", "shortname": "DEMO", "status": "CONFIRMED"}}
        else:
            await route.fallback()
            return
        await route.fulfill(status=200, json=payload)


async def main_action(page: Page):
    actions = page.locator(".event-participant-actions")
    await expect(actions).to_be_visible()
    return actions


async def no_parent_action(page: Page) -> None:
    await expect(page.locator(".event-detail-hero .event-participant-actions:visible")).to_have_count(0)


async def event_states(check: SmokeCheck, page: Page, fixtures: RegistrationFixtures) -> None:
    for name, path, registration_path in SCOPES:
        for role in (None, "player", "team"):
            fixtures.viewer = Viewer(role=role)
            await page.goto(check.base_url + path, wait_until="domcontentloaded")
            actions = await main_action(page)
            primary = actions.locator(".event-registration-action")
            await expect(primary).to_have_count(1)
            await expect(primary).to_have_text("Register" if role else "Log in to register")
            destination = urlsplit(await primary.get_attribute("href"))
            if role:
                assert destination.path == registration_path, (name, role, destination)
                await primary.click()
                await expect(page).to_have_url(check.base_url + registration_path)
                await expect(page.get_by_role("button", name="Register", exact=True)).to_be_visible()
                await no_parent_action(page)
            else:
                assert destination.path == "/login" and parse_qs(destination.query).get("next") == [registration_path]
            fixtures.viewer = Viewer(role=role, player_open=False, team_open=False)
            await page.goto(check.base_url + path, wait_until="domcontentloaded")
            actions = await main_action(page)
            await expect(actions.get_by_role("button", name=re.compile("registration closed", re.I))).to_be_disabled()
            await expect(actions.get_by_role("link")).to_have_count(0)
            if role:
                fixtures.viewer.registered = True
                await page.reload(wait_until="domcontentloaded")
                actions = await main_action(page)
                edit = actions.get_by_role("button", name=re.compile("^Edit registration", re.I))
                await expect(edit).to_be_enabled()
                await expect(actions.get_by_role("link", name="Register", exact=True)).to_have_count(0)
                if role == "team":
                    roster = actions.get_by_role("link", name=re.compile("^Manage roster$", re.I))
                    target = (
                        "/demo-open/invitations" if name == "tournament" else "/leagues/demo-coast-league/invitations"
                    )
                    await expect(roster).to_have_attribute("href", target)
                await edit.click()
                dialog = page.get_by_role("dialog")
                await expect(dialog).to_be_visible()
                await dialog.get_by_role("button", name="Close", exact=True).click()
        # Per-role flags remain authoritative when only the other role is open.
        fixtures.viewer = Viewer(role="player", player_open=False, team_open=True)
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        await expect(
            (await main_action(page)).get_by_role("button", name="Player registration closed")
        ).to_be_disabled()
    fixtures.viewer = Viewer()
    await page.goto(check.base_url + "/demo-league-round-one/register", wait_until="domcontentloaded")
    await expect(page).to_have_url(check.base_url + "/leagues/demo-coast-league/register")
    await expect(page.get_by_role("button", name="Register", exact=True)).to_be_visible()
    await expect(page.locator("#player-register-form, #team-register-form")).to_have_count(0)
    await no_parent_action(page)


async def side_states(check: SmokeCheck, page: Page, fixtures: RegistrationFixtures) -> None:
    for state, label, submit in (
        ("eligible", "Register", "Register"),
        ("incomplete", "Complete registration", "Register"),
        ("registered", "Edit registration", "Save"),
    ):
        fixtures.viewer = Viewer(side_state=state, registered=True)
        await page.goto(check.base_url + fixtures.side_path, wait_until="domcontentloaded")
        action = page.locator(".sidecomp-actions").get_by_role("button", name=label, exact=True)
        await expect(action).to_be_visible()
        await no_parent_action(page)
        results = page.locator(".sidecomp-actions").get_by_role("link", name="Enter results", exact=True)
        await expect(results).to_have_class(
            re.compile("btn-primary" if state == "registered" else "btn-outline-secondary")
        )
        await action.focus()
        await page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        await expect(dialog.get_by_role("button", name=submit, exact=True)).to_be_enabled()
        await expect(dialog.get_by_role("button", name="Deregister", exact=True)).to_have_count(
            int(state == "registered")
        )
        await dialog.get_by_role("button", name="Close", exact=True).click()
    for role, state in ((None, "eligible"), ("team", "eligible"), ("player", "ineligible"), ("player", "closed")):
        fixtures.viewer = Viewer(role=role, side_state=state)
        await page.goto(check.base_url + fixtures.side_path, wait_until="domcontentloaded")
        await expect(page.locator(".sidecomp-heading")).to_be_visible()
        await no_parent_action(page)
        await expect(page.locator(".sidecomp-actions").get_by_role("link", name="Results", exact=True)).to_be_visible()
        await expect(
            page.locator(".sidecomp-actions").get_by_role("button", name="Register", exact=True)
        ).to_have_count(0)
        if role is None:
            login = page.locator(".sidecomp-actions").get_by_role("link", name="Log in to register", exact=True)
            await expect(login).to_be_visible()
            destination = urlsplit(await login.get_attribute("href"))
            assert parse_qs(destination.query).get("next") == [fixtures.side_path]
        elif state == "closed":
            await expect(
                page.locator(".sidecomp-actions").get_by_role("button", name="Registration closed", exact=True)
            ).to_be_disabled()
        elif state == "ineligible":
            prerequisite = page.locator(".sidecomp-actions").get_by_role("link", name="Register for event", exact=True)
            await expect(prerequisite).to_have_attribute("href", "/demo-open/register")
            await expect(page.get_by_text("Event registration is required first.", exact=True)).to_be_visible()
            await prerequisite.click()
            await expect(page.get_by_role("button", name="Register", exact=True)).to_be_visible()
            await no_parent_action(page)
        else:
            await expect(page.locator(".sidecomp-actions .event-participant-actions a")).to_have_count(0)
    # An organizer can still register players while self-registration is closed.
    fixtures.viewer = Viewer(role="team", organizer=True, side_state="closed")
    await page.goto(check.base_url + fixtures.side_path, wait_until="domcontentloaded")
    await page.locator("#sidecomp-organizer-tools-trigger").click()
    await expect(page.get_by_role("link", name="Quick Register players", exact=True)).to_be_visible()
    await page.keyboard.press("Escape")


async def action_geometry(page: Page, side: bool, width: int) -> None:
    await page.evaluate("document.fonts.ready")
    selector = ".sidecomp-actions" if side else ".event-participant-actions"
    primary = page.locator(selector + " .event-registration-action")
    await expect(primary).to_have_count(1)
    failures = await page.locator(selector + " a, " + selector + " button").evaluate_all("""els => els.flatMap(el => {
        const r = el.getBoundingClientRect();
        return r.width && r.height && (r.width < 43.9 || r.height < 43.9 || r.left < -1 || r.right > innerWidth + 1)
            ? [{text: el.textContent, box: r.toJSON()}] : [];
    })""")
    assert not failures, f"Small or clipped participant controls: {failures}"
    await check_contrast(page, selector + " .btn")
    box = await primary.bounding_box()
    group = await page.locator(selector).bounding_box()
    if width < 768:
        assert box and group and box["width"] >= group["width"] - 2, (box, group)
    elif not side:
        identity = await page.locator(".event-hero-main").bounding_box()
        assert box and identity and box["x"] >= identity["x"] + identity["width"] - 1, (box, identity)
    colors = await primary.evaluate("""el => ({button: getComputedStyle(el).backgroundColor,
        surface: getComputedStyle(el.closest('.event-detail-page')).backgroundColor})""")
    assert colors["button"] != colors["surface"], f"Principal registration action lost its emphasis: {colors}"


async def appearance_matrix(check: SmokeCheck, page: Page, fixtures: RegistrationFixtures) -> None:
    fixtures.viewer = Viewer(organizer=True)
    for width in (320, 360, 1280):
        await page.set_viewport_size({"width": width, "height": 900})
        for mode in ("light", "dark"):
            for palette in PALETTES:
                for side in (False, True):
                    await page.goto(
                        check.base_url + (fixtures.side_path if side else "/demo-open"), wait_until="domcontentloaded"
                    )
                    await choose(page, palette, mode)
                    await close_menu(page)
                    await action_geometry(page, side, width)
                    if side:
                        await no_parent_action(page)
                    name = f"registration-{'side-event' if side else 'event'}-{palette}-{mode}"
                    await page.mouse.move(0, 0)
                    await check.check_layout(page, name, width)
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
            paths = (
                "/_api/tournaments/demo-open",
                "/_api/leagues/demo-coast-league",
                "/_api/tournaments/demo-league-round-one",
            )
            payloads = {path: await api_json(context, check.base_url, path) for path in paths}
            sides = await api_json(context, check.base_url, "/_api/demo-open/sidecomps")
            side = await api_json(context, check.base_url, f"/_api/sidecomps/{sides[0]['id']}")
            fixtures = RegistrationFixtures(check, payloads, side)
            await context.route("**/*", check.guard_requests)
            await context.route("**/_api/**", fixtures.handle)
            page = await context.new_page()
            check.observe(page)
            for label, action in (
                ("registration-scopes", event_states),
                ("side-event-registration", side_states),
                ("registration-appearance", appearance_matrix),
            ):
                await check.workflow(page, label, 360, lambda action=action: action(check, page, fixtures))
        finally:
            await context.close()
            await browser.close()
    print(f"{check.visited} registration screens; {check.workflows} workflows passed; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures or check.page_errors or check.network_errors or check.blocked_writes))


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments(__doc__))))
