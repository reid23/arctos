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
            assert choices and all(
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
    dialog = page.get_by_role("dialog", name="Manage Fields", exact=True)
    await expect(dialog.get_by_label("Field name", exact=True)).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(dialog).to_be_hidden()
    await expect(page.get_by_role("button", name="Fields", exact=True)).to_be_focused()
    await page.get_by_role("button", name="Table", exact=True).click()
    row = page.locator("tbody tr").filter(has_text="Next on Field B")
    await row.get_by_role("button", name="Edit Next on Field B", exact=True).click()
    panel = page.locator(".schedule-editor-card")
    await expect(panel).to_be_visible()
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


async def settings_and_penalty_recovery(check: SmokeCheck, page: Page) -> None:
    """Browser-only failure fixtures; every mutation is fulfilled or aborted locally.

    These cases were authored with the persistence fix. Execute separately from
    backend tests; compilation or Flask tests do not establish browser coverage.
    """
    probe = await page.context.new_page()
    probe.on("pageerror", lambda error: check.page_errors.append(str(error)))
    try:
        for kind, path, detail, save_path, penalties in (
            (
                "event",
                "/demo-open/settings",
                "/_api/tournaments/demo-open",
                "/_api/demo-open/update-settings",
                "/_api/demo-open/penalty-types",
            ),
            (
                "league",
                "/leagues/demo-coast-league/settings",
                "/_api/leagues/demo-coast-league",
                "/_api/leagues/demo-coast-league/settings",
                "/_api/leagues/demo-coast-league/penalty-types",
            ),
        ):
            payload = await api_json(page.context, check.base_url, detail)
            payload["penalty_types"] = [
                {"id": 987601, "name": "Fixture penalty", "color": "808080", "desc": "Old description"}
            ]
            details_url, save_url, penalty_url = (check.base_url + value for value in (detail, save_path, penalties))
            started, release = asyncio.Event(), asyncio.Event()
            requests, writes = [], []
            save_succeeds = False
            mode = "update-failure"

            async def details_fixture(route):
                if route.request.method == "GET":
                    await route.fulfill(json=payload)
                else:
                    await route.fallback()

            async def settings_fixture(route):
                if route.request.method != "POST":
                    await route.fallback()
                    return
                requests.append(route.request)
                started.set()
                await release.wait()
                await route.fulfill(
                    status=200 if save_succeeds else 500,
                    json={"success": True} if save_succeeds else {"error": "Fixture settings failure"},
                )

            async def penalty_fixture(route):
                request = route.request
                if request.method == "GET":
                    await route.fulfill(json={"penalty_types": payload["penalty_types"]})
                    return
                writes.append((request.method, request.post_data_json if request.post_data else None))
                if request.method == "PATCH":
                    if mode == "update-failure":
                        await route.fulfill(status=400, json={"error": "Fixture update failure"})
                    else:
                        payload["penalty_types"][0].update(request.post_data_json)
                        await route.fulfill(json={"success": True})
                elif request.method == "POST":
                    if mode == "create-not-saved":
                        await route.fulfill(status=500, json={"error": "Fixture create failure"})
                    else:
                        item = {"id": 987602, **request.post_data_json}
                        payload["penalty_types"].append(item)
                        if mode == "create-response-lost":
                            await route.abort("failed")  # Commit happened, response did not arrive.
                        else:
                            await route.fulfill(json={"success": True, "penalty_type": item})
                elif request.method == "DELETE":
                    await route.fulfill(status=409, json={"error": "Cannot delete penalty type that is in use."})
                else:
                    await route.fallback()

            await page.context.route(details_url, details_fixture)
            await page.context.route(save_url, settings_fixture)
            await page.context.route(penalty_url + "**", penalty_fixture)
            try:
                await probe.goto(check.base_url + path, wait_until="domcontentloaded")
                await probe.locator("#about").fill("Retain this settings draft.")
                await probe.locator("#require_waiver_signature").check()
                await probe.locator('input[type="file"][accept="*/*"]').set_input_files(
                    {"name": "draft-waiver.txt", "mimeType": "text/plain", "buffer": b"retained waiver"}
                )
                await expect(probe.get_by_text("Selected waiver: draft-waiver.txt", exact=True)).to_be_visible()
                save = probe.get_by_role("button", name="Save settings", exact=False)
                await save.click()
                await asyncio.wait_for(started.wait(), timeout=5)
                await expect(probe.locator("#about")).to_be_disabled()
                await expect(probe.get_by_role("button", name="Saving…", exact=True)).to_be_disabled()
                await probe.locator('button[type="submit"]').dispatch_event("click")
                assert len(requests) == 1
                release.set()
                await expect(probe.get_by_role("alert").filter(has_text="Fixture settings failure")).to_be_visible()
                await expect(probe.locator("#about")).to_have_value("Retain this settings draft.")
                await expect(probe.get_by_text("Selected waiver: draft-waiver.txt", exact=True)).to_be_visible()
                assert "multipart/form-data" in requests[0].headers["content-type"]
                assert "retained waiver" in requests[0].post_data
                save_succeeds = True
                await save.click()
                await expect(probe).not_to_have_url(check.base_url + path)
                assert len(requests) == 2

                await probe.goto(check.base_url + path, wait_until="domcontentloaded")
                await probe.locator("#penalty-types-toggle").click()
                section = probe.locator("#penalty-types-content")
                await section.get_by_role("button", name="Edit", exact=True).click()
                await probe.locator("#penalty-type-edit-name").fill("Retained penalty draft")
                await probe.locator("#penalty-type-edit-description").fill("")
                await probe.locator("#penalty-type-save").click()
                await expect(probe.locator("#penalty-save-error")).to_contain_text("Fixture update failure")
                await expect(probe.locator("#penalty-type-edit-name")).to_have_value("Retained penalty draft")
                await expect(probe.locator("#penalty-type-edit-description")).to_have_value("")
                assert writes[-1][1]["desc"] == ""
                mode = "update-success"
                await probe.locator("#penalty-type-save").click()
                await expect(probe.locator("#penalty-type-edit-name")).to_have_count(0)
                await section.get_by_role("button", name="Delete", exact=True).click()
                await expect(probe.locator("#penalty-save-error")).to_contain_text("in use")
                await expect(section.get_by_text("Retained penalty draft", exact=True)).to_be_visible()

                for create_mode in ("create-not-saved", "create-response-lost"):
                    mode = create_mode
                    await probe.get_by_role("button", name="Add penalty type", exact=True).click()
                    await probe.locator("#penalty-type-new-name").fill("New fixture " + create_mode)
                    await probe.locator("#penalty-type-save").click()
                    await expect(probe.locator("#penalty-type-save")).to_have_text("Check save status")
                    await expect(probe.locator("#penalty-type-new-name")).to_be_disabled()
                    post_count = sum(method == "POST" for method, _ in writes)
                    await probe.locator("#penalty-type-save").click()
                    if create_mode == "create-not-saved":
                        await expect(probe.locator("#penalty-type-save")).to_have_text("Save")
                        await expect(probe.locator("#penalty-type-new-name")).to_have_value(
                            "New fixture " + create_mode
                        )
                        await section.get_by_role("button", name="Cancel", exact=True).click()
                    else:
                        await expect(probe.locator("#penalty-type-new-name")).to_have_count(0)
                        await expect(section.get_by_text("New fixture " + create_mode, exact=True)).to_be_visible()
                    assert sum(method == "POST" for method, _ in writes) == post_count
            finally:
                release.set()
                await page.context.unroute(details_url, details_fixture)
                await page.context.unroute(save_url, settings_fixture)
                await page.context.unroute(penalty_url + "**", penalty_fixture)
    finally:
        await probe.close()


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
                            except Exception as error:
                                check.failures.append(f"{width}px {path}: {error}")
                    await check.workflow(page, "settings-parent-state", width, lambda: settings_state(check, page))
                    await check.workflow(
                        page, "separate-organizer-menus", width, lambda: sidecomp_menus(check, page, comp_id)
                    )
                    await check.workflow(page, "flat-schedule-panels", width, lambda: schedule_panels(check, page))
                    if width == 360:
                        await check.workflow(
                            page,
                            "settings-and-penalty-recovery",
                            width,
                            lambda: settings_and_penalty_recovery(check, page),
                        )
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
