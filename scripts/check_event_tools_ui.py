"""Check shared event headers and flat editing tools on the local demo.

    uv run python scripts/check_event_tools_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-event-tools

Requires the demo marker and shared mutation guard. Only demo login and read-only
Markdown requests reach the backend; settings and schedule changes are not saved.
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import Page, async_playwright, expect

from check_event_ui import selected_route
from check_mobile_ui import Screen, SmokeCheck, TIMEOUT_MS, api_json, arguments


async def composition(page: Page, tab: str, prefix: str = "event") -> None:
    await selected_route(page, prefix, tab)
    await expect(page.locator("main h1")).to_have_count(1)
    await expect(page.locator("#page-banner")).to_have_count(1)
    await expect(page.locator("#page-banner-dismiss")).to_have_count(0)
    geometry = await page.locator(".event-detail-hero").evaluate("""hero => {
        const rect = selector => hero.querySelector(selector).getBoundingClientRect().toJSON();
        return {cover:rect('.event-banner-image'),name:rect('h1'),main:rect('.event-hero-main'),meta:rect('.event-hero-meta'),
            badges:rect('.event-hero-badges'),actions:rect('.event-hero-actions'),
            alignment:getComputedStyle(hero.querySelector('.event-hero-inner')).textAlign};
    }""")
    assert abs(geometry["cover"]["width"] / geometry["cover"]["height"] - 3) < 0.02, geometry
    assert geometry["alignment"] in ("start", "left"), geometry
    assert geometry["cover"]["bottom"] <= geometry["name"]["top"], geometry
    assert geometry["name"]["bottom"] <= geometry["meta"]["top"], geometry
    assert geometry["meta"]["bottom"] <= geometry["badges"]["top"], geometry
    if geometry["actions"]["height"]:
        if page.viewport_size["width"] >= 768:
            assert geometry["actions"]["left"] >= geometry["main"]["right"] - 1, geometry
        else:
            assert geometry["badges"]["bottom"] <= geometry["actions"]["top"], geometry
    assert abs(geometry["name"]["left"] - geometry["meta"]["left"]) <= 1, geometry


async def settings_state(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url + "/demo-open/settings", wait_until="domcontentloaded")
    await expect(page.locator("#name")).to_be_visible()
    await page.locator("#name").fill("Unsaved settings draft")
    await page.locator("#about").fill("Keep this local note.")
    published = page.locator("#published")
    original = await published.is_checked()
    await published.set_checked(not original)
    # Opening shared menus rerenders the parent shell, not the editing draft.
    trigger = page.locator("#organizer-tools-trigger")
    await trigger.click()
    await expect(page.locator("#organizer-tools-options")).to_be_visible()
    await trigger.press("Escape")
    await expect(trigger).to_be_focused()
    await expect(page.locator("#organizer-tools-options")).to_be_hidden()
    await page.locator("#utility-trigger").click()
    await page.locator("#appearance-menu-item").click()
    await page.get_by_role("radio", name="Dark mode", exact=True).check()
    await page.get_by_role("button", name="Close appearance settings", exact=True).click()
    await expect(page.locator("#name")).to_have_value("Unsaved settings draft")
    await expect(page.locator("#about")).to_have_value("Keep this local note.")
    assert await published.is_checked() is not original
    await page.locator("#head-ref-options > summary").click()
    await expect(page.locator("#head_refs_allowed_list")).to_be_visible()
    for section in await page.locator("details.section-disclosure").all():
        initial = await section.evaluate("el => el.open")
        summary = section.locator("summary")
        await summary.focus()
        await page.keyboard.press("Enter")
        await expect(section).to_have_js_property("open", not initial)
        await page.keyboard.press("Space")
        await expect(section).to_have_js_property("open", initial)
    await check.settings_disclosures(page)
    await penalty_edit_state(check, page)


async def penalty_edit_state(check: SmokeCheck, page: Page) -> None:
    """Existing penalty edits stay intact while their section is closed."""
    for kind, path, endpoint in (
        ("event", "/demo-open/settings", "/_api/tournaments/demo-open"),
        ("league", "/leagues/demo-coast-league/settings", "/_api/leagues/demo-coast-league"),
    ):
        payload = await api_json(page.context, check.base_url, endpoint)
        payload["penalty_types"] = [
            {"id": 987601, "name": "Browser-only first penalty", "color": "808080", "desc": "Local fixture"},
            {"id": 987602, "name": "Browser-only second penalty", "color": "808080", "desc": None},
        ]

        async def fixture(route) -> None:
            if route.request.method == "GET":
                await route.fulfill(json=payload)
            else:
                await route.fallback()

        url = check.base_url + endpoint
        await page.context.route(url, fixture)
        try:
            await page.goto(check.base_url + path, wait_until="domcontentloaded")
            toggle = page.locator("#penalty-types-toggle")
            await toggle.click()
            content = page.locator("#penalty-types-content")
            await content.get_by_role("button", name="Edit", exact=True).first.click()
            name = content.get_by_placeholder("Name (max 50)", exact=True)
            await name.fill("Unsaved existing penalty")
            bounds = await name.bounding_box()
            assert bounds and bounds["width"] >= 160, bounds
            description = content.get_by_label("Description (optional)", exact=True)
            await description.fill("Keep this penalty description.")
            color = content.get_by_role("button", name="Choose penalty color", exact=True)
            await color.click()
            palette = content.locator(".penalty-color-options")
            await expect(palette).to_be_visible()
            choices = await palette.get_by_role("button", name="Use color #", exact=False).evaluate_all(
                """els => els.map(el => {
                    const r = el.getBoundingClientRect();
                    return {width:r.width,height:r.height,left:r.left,right:r.right};
                })"""
            )
            assert len(choices) == 14 and all(
                choice["width"] >= 44
                and choice["height"] >= 44
                and choice["left"] >= 0
                and choice["right"] <= page.viewport_size["width"] + 1
                for choice in choices
            ), choices
            await palette.get_by_role("button", name="Use color #1E90FF", exact=True).click()
            await expect(palette.get_by_role("textbox", name="Hex color", exact=True)).to_have_value("1E90FF")
            await palette.get_by_role("button", name="Apply", exact=True).click()
            await expect(palette).to_have_count(0)
            await expect(color).to_have_css("background-color", "rgb(30, 144, 255)")
            await expect(page.get_by_role("button", name="Add penalty type", exact=True)).to_be_disabled()
            await expect(content.get_by_role("button", name="Edit", exact=True)).to_be_disabled()
            await toggle.click()
            await expect(content).to_be_hidden()
            await toggle.click()
            await expect(name).to_have_value("Unsaved existing penalty")
            await expect(description).to_have_value("Keep this penalty description.")
            await check.check_layout(page, f"{kind}-penalty-edit", page.viewport_size["width"])
            if check.screenshots:
                await page.locator(".penalty-types-section").screenshot(
                    path=str(check.screenshots / f"{page.viewport_size['width']:04d}-{kind}-penalty-section.png"),
                    animations="disabled",
                )
            await content.get_by_role("button", name="Cancel", exact=True).click()
            await expect(name).to_have_count(0)
            await expect(content.get_by_text("Browser-only first penalty", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name="Add penalty type", exact=True)).to_be_enabled()
        finally:
            await page.context.unroute(url, fixture)


async def sidecomp_menus(check: SmokeCheck, page: Page, comp_id: int) -> None:
    await page.goto(check.base_url + f"/demo-open/sidecomps/{comp_id}", wait_until="domcontentloaded")
    for prefix in ("organizer-tools", "sidecomp-organizer-tools"):
        trigger = page.locator(f"#{prefix}-trigger")
        await expect(trigger).to_have_count(1)
        await trigger.click()
        dropdown = page.locator(f"#{prefix}-options")
        await expect(dropdown).to_be_visible()
        bounds = await dropdown.bounding_box()
        assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= page.viewport_size["width"] + 1, bounds
        await trigger.press("Escape")
        await expect(dropdown).to_be_hidden()
        await expect(trigger).to_be_focused()


async def schedule_panels(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url + "/demo-open/schedule/edit", wait_until="domcontentloaded")
    await expect(page.locator(".schedule-editor-split")).to_be_visible()
    await composition(page, "schedule-edit")
    assert await page.evaluate("window.scrollY") <= 1
    await page.get_by_role("button", name="Fields", exact=True).click()
    await expect(page.locator(".modal-title")).to_have_text("Manage Fields")
    await expect(page.locator(".modal-body .card")).to_have_count(0)
    await page.locator(".modal-header").get_by_role("button", name="Close", exact=True).click()
    await page.get_by_role("button", name="Table", exact=True).click()
    row = page.locator("tbody tr").filter(has_text="Next on Field B")
    await row.get_by_role("button", name="✎", exact=True).click()
    panel = page.locator(".schedule-editor-card")
    await expect(panel).to_be_visible()
    assert "card" not in (await panel.get_attribute("class")).split()
    await panel.get_by_role("button", name="Close", exact=True).click()


async def late_account_tabs(check: SmokeCheck, page: Page) -> None:
    """A late account response may add organizer-only tabs before the active one."""
    gate = asyncio.Event()

    async def account(route) -> None:
        await gate.wait()
        await route.fallback()

    await page.context.route("**/_api/me", account)
    try:
        await page.goto(check.base_url + "/demo-open/settings", wait_until="domcontentloaded")
        await selected_route(page, "event", "settings")
        await expect(page.locator("#event-tab-bracket")).to_have_count(0)
        gate.set()
        await expect(page.locator("#event-tab-bracket")).to_be_visible()
        await expect(page.locator("#organizer-tools-trigger")).to_be_visible()
        await selected_route(page, "event", "settings")
    finally:
        gate.set()
        await page.context.unroute("**/_api/me", account)


async def menu_history(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url + "/demo-open/settings", wait_until="domcontentloaded")
    await page.locator("#organizer-tools-trigger").click()
    await (
        page.locator("#organizer-tools-options").get_by_role("link", name="Registration Management", exact=True).click()
    )
    await expect(page).to_have_url(check.base_url + "/demo-open/manage")
    await page.locator("#organizer-tools-trigger").click()
    await expect(page.locator("#organizer-tools-options")).to_be_visible()
    await page.go_back(wait_until="domcontentloaded")
    await expect(page).to_have_url(check.base_url + "/demo-open/settings")
    await expect(page.locator("#name")).to_be_visible()
    await expect(page.locator("#organizer-tools-options")).to_have_count(0)
    await expect(page.locator(".organizer-tools-dismiss")).to_have_count(0)
    await page.go_forward(wait_until="domcontentloaded")
    await expect(page).to_have_url(check.base_url + "/demo-open/manage")
    await expect(page.locator("#organizer-tools-options")).to_have_count(0)


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        probe = await playwright.request.new_context()
        marker = await probe.get(args.base_url + "/_api/demo-info")
        assert marker.ok and (await marker.json()).get("demo") is True, "Refusing to run without the local demo marker"
        await probe.dispose()
        options = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            options["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**options)
        try:
            for width in (320, 360, 1280):
                check.authenticated = False
                context = await browser.new_context(
                    viewport={"width": width, "height": 900},
                    reduced_motion="reduce",
                    has_touch=width < 768,
                    is_mobile=width < 768,
                )
                context.set_default_timeout(TIMEOUT_MS)
                await context.route("**/*", check.guard_requests)
                page = await context.new_page()
                check.observe(page)
                try:
                    await check.login(page)
                    competitions = await api_json(context, args.base_url, "/_api/demo-open/sidecomps")
                    comp_id = next(comp["id"] for comp in competitions if comp["name"] == "Sideline Dueling — Demo")
                    entries = (
                        ("event-settings", "/demo-open/settings", "#name", "settings", "event"),
                        ("event-register", "/demo-summer-cup/register", "main form", "registration", "event"),
                        ("event-manage", "/demo-open/manage", ".management-table", "manage", "event"),
                        ("event-footage", "/demo-open/manage-footage", ".event-tool-content", "footage", "event"),
                        (
                            "schedule-edit",
                            "/demo-open/schedule/edit",
                            ".schedule-editor-split",
                            "schedule-edit",
                            "event",
                        ),
                        (
                            "legacy-bracket",
                            "/demo-open/legacy-bracket",
                            ".event-tool-content",
                            "legacy-bracket",
                            "event",
                        ),
                        ("sidecomp-new", "/demo-open/sidecomps/new", "#sidecomp-name", "sidecomps", "event"),
                        (
                            "sidecomp-detail",
                            f"/demo-open/sidecomps/{comp_id}",
                            ".sidecomp-heading",
                            "sidecomps",
                            "event",
                        ),
                        (
                            "sidecomp-edit",
                            f"/demo-open/sidecomps/{comp_id}/edit",
                            "#sidecomp-name",
                            "sidecomps",
                            "event",
                        ),
                        (
                            "sidecomp-register",
                            f"/demo-open/sidecomps/{comp_id}/register-player-as-to",
                            "main .list-group",
                            "sidecomps",
                            "event",
                        ),
                        (
                            "sidecomp-results",
                            f"/demo-open/sidecomps/{comp_id}/results",
                            ".event-tool-content",
                            "sidecomps",
                            "event",
                        ),
                        (
                            "sidecomp-manage",
                            f"/demo-open/sidecomps/{comp_id}/manage-results",
                            ".event-tool-content",
                            "sidecomps",
                            "event",
                        ),
                        ("league-settings", "/leagues/demo-coast-league/settings", "#about", "settings", "league"),
                        (
                            "league-register",
                            "/leagues/demo-coast-league/register",
                            "main form",
                            "registration",
                            "league",
                        ),
                        ("league-manage", "/leagues/demo-coast-league/manage", ".management-table", "manage", "league"),
                        (
                            "league-new-event",
                            "/leagues/demo-coast-league/new-tournament",
                            "#name",
                            "new-event",
                            "league",
                        ),
                    )
                    for name, path, ready, tab, prefix in entries:
                        before = len(check.failures)
                        await check.visit(page, Screen(name, path, ready), width)
                        if len(check.failures) == before:
                            try:
                                await composition(page, tab, prefix)
                                framed = await page.locator("main form").evaluate_all(
                                    "forms => forms.filter(el => el.closest('.card')).map(el => el.id)"
                                )
                                assert not framed, f"Editing forms retain card frames: {framed}"
                            except Exception as error:
                                check.failures.append(f"{width}px {path}: {error}")
                    await check.workflow(page, "settings-parent-state", width, lambda: settings_state(check, page))
                    await check.workflow(
                        page, "separate-organizer-menus", width, lambda: sidecomp_menus(check, page, comp_id)
                    )
                    await check.workflow(page, "flat-schedule-panels", width, lambda: schedule_panels(check, page))
                    if width == 360:
                        await check.workflow(
                            page, "late-account-tab-reveal", width, lambda: late_account_tabs(check, page)
                        )
                        await check.workflow(page, "menu-browser-history", width, lambda: menu_history(check, page))
                    await check.login(page, "demo-team")
                    for path, prefix in (
                        ("/demo-open/invitations", "event"),
                        ("/leagues/demo-coast-league/invitations", "league"),
                    ):
                        await check.visit(page, Screen(prefix + "-roster-tool", path, "main table"), width)
                        await composition(page, "roster", prefix)
                finally:
                    await context.close()
        finally:
            await browser.close()
    print(f"{check.visited} event-tool screens; {check.workflows} workflows passed; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(arguments(__doc__))))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"Event tool check stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error
