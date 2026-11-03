"""Check Arctos appearance settings against the explicitly started local demo.

    uv run python scripts/check_appearance_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-appearance

Checks both themes in light/dark at phone width, actual surface/label
contrast and distinct backgrounds, nested menu keyboard/focus behavior, guest,
player and team access, unsaved form retention, existing preferences, persistence,
live System changes, explicit overrides, cross-tab changes/removal, blocked
browser storage, personal registration statuses, representative custom surfaces,
and overlay isolation.
Shares the demo marker, loopback restriction, layout checks, and mutation guard
with check_mobile_ui.py. No server data is changed except signing into the demo.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sys

from playwright.async_api import Page, Route, async_playwright, expect

from check_mobile_ui import DEMO_USER, TIMEOUT_MS, Screen, SmokeCheck, api_json, arguments

STORAGE_KEY = "arctos.appearance.v1"
WIDTH = 360
PALETTES = {
    "california": "California Jugger",
    "neutral": "Neutral",
    "monokai": "Monokai",
}
LEGACY_PALETTES = {
    "field": "california",
    "field-chalk": "california",
    "forest": "california",
    "ocean": "california",
    "plum": "california",
    "ember": "california",
}
CONTEXT_OPTIONS = {
    "viewport": {"width": WIDTH, "height": 900},
    "color_scheme": "light",
    "has_touch": True,
    "is_mobile": True,
    "reduced_motion": "reduce",
}


async def preference(page: Page, palette: str, resolved_mode: str) -> None:
    app = page.locator(".arctos-app")
    await expect(app).to_have_attribute("data-palette", palette)
    await expect(app).to_have_attribute("data-bs-theme", resolved_mode)


async def open_menu(page: Page) -> None:
    """Open the app-wide More menu, regardless of account status."""
    if not await page.locator("#appearance-options").is_visible():
        if not await page.locator("#appearance-menu-item").is_visible():
            await page.locator("#utility-trigger").click()
        await page.get_by_role("button", name="Appearance", exact=True).click()
    await expect(page.locator("#appearance-options")).to_be_visible()


async def choose(page: Page, palette: str, mode: str) -> None:
    await open_menu(page)
    radio = page.get_by_role("radio", name=f"{PALETTES[palette]} theme", exact=True)
    await radio.check()
    await page.get_by_role("radio", name=f"{mode.title()} mode", exact=True).check()
    await page.mouse.move(0, 0)


async def closed_menu(page: Page) -> None:
    await expect(page.locator("#appearance-options")).to_be_hidden()
    await expect(page.locator("#utility-options")).to_be_hidden()
    await expect(page.locator("#utility-trigger")).to_be_focused()
    await expect(page.locator("#utility-trigger")).to_have_attribute("aria-expanded", "false")


async def close_menu(page: Page) -> None:
    await page.keyboard.press("Escape")
    await closed_menu(page)


async def check_contrast(page: Page, selector: str, minimum: float = 4.5) -> None:
    """Check actual solid-surface text against its composited ancestor backgrounds."""
    await expect(page.locator(selector).first).to_be_visible()
    failures = await page.locator(selector).evaluate_all(
        """(elements, minimum) => {
        const rgba = value => {
            const values = value.match(/[\\d.]+/g)?.map(Number);
            return values && values.length >= 3 ? [...values.slice(0, 3), values[3] ?? 1] : [0, 0, 0, 0];
        };
        const luminance = rgb => rgb.slice(0, 3).map(v => {
            v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
        }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        return elements.filter(el => el.getBoundingClientRect().height > 0).flatMap(el => {
            let bg = [255, 255, 255], ancestors = [], current = el;
            while (current) { ancestors.unshift(current); current = current.parentElement; }
            for (const ancestor of ancestors) {
                const color = rgba(getComputedStyle(ancestor).backgroundColor);
                bg = bg.map((v, i) => color[i] * color[3] + v * (1 - color[3]));
            }
            const color = rgba(getComputedStyle(el).color);
            const fg = bg.map((v, i) => color[i] * color[3] + v * (1 - color[3]));
            const a = luminance(fg), b = luminance(bg);
            const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
            return ratio + .01 >= minimum ? [] : [{selector: el.className, fg, bg, ratio}];
        });
    }""",
        minimum,
    )
    assert not failures, f"Text contrast below {minimum}: {failures}"


async def palette_matrix(check: SmokeCheck, page: Page) -> None:
    surfaces: dict[str, dict[str, dict[str, str]]] = {"light": {}, "dark": {}}
    for mode in ("light", "dark"):
        for palette in PALETTES:
            await page.goto(check.base_url, wait_until="domcontentloaded")
            await expect(page.locator(".arctos-event-card").first).to_be_visible()
            await choose(page, palette, mode)
            await preference(page, palette, mode)
            await check.check_layout(page, f"appearance-menu-{palette}-{mode}", WIDTH)
            panel = await page.locator("#appearance-options").bounding_box()
            assert panel and panel["y"] >= 0 and panel["y"] + panel["height"] <= 901, (
                f"Theme panel escapes the phone viewport: {panel}"
            )
            await check_contrast(
                page,
                ".appearance-panel h2, .appearance-panel legend, .appearance-mode-option, "
                ".appearance-theme-option, .appearance-back, .appearance-page-setting",
            )
            await close_menu(page)
            await check.check_layout(page, f"appearance-home-{palette}-{mode}", WIDTH)
            await check_contrast(
                page,
                ".arctos-app, .arctos-event-card, #page-banner p, "
                "#organize-trigger, .arctos-segment.is-active, .arctos-event-card .btn, .arctos-card-title a, "
                ".arctos-meta-list li, .arctos-league-link",
            )
            surfaces[mode][palette] = await page.evaluate("""() => {
                const app = getComputedStyle(document.querySelector('.arctos-app'));
                const card = getComputedStyle(document.querySelector('.arctos-event-card'));
                const filter = getComputedStyle(document.querySelector('.arctos-segment.is-active'));
                return {page: app.backgroundColor, card: card.backgroundColor, accent: filter.color,
                    text: app.color, border: card.borderTopColor};
            }""")
            check.visited += 1
    # Check rendered surfaces, not merely the selected ID or CSS variable text.
    # Both themes intentionally have their own page and card backgrounds.
    for mode, themes in surfaces.items():
        for surface in ("page", "card", "accent"):
            colors = {name: values[surface] for name, values in themes.items()}
            assert len(set(colors.values())) == len(PALETTES), (
                f"{mode} themes did not render distinct {surface} colors: {colors}"
            )
    print(f"Rendered theme colors: {json.dumps(surfaces, sort_keys=True)}", flush=True)


async def keyboard_and_persistence(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url, wait_until="domcontentloaded")
    await choose(page, "california", "light")
    await close_menu(page)
    trigger = page.locator("#utility-trigger")
    await expect(trigger).to_have_attribute("aria-label", "More options")
    await trigger.click()
    appearance = page.get_by_role("button", name="Appearance", exact=True)
    await expect(trigger).to_be_focused()
    assert not await appearance.evaluate("el => el.matches(':focus, :focus-visible')")
    await page.keyboard.press("Escape")
    await trigger.focus()
    await page.keyboard.press("Enter")
    await expect(trigger).to_be_focused()
    await page.keyboard.press("Tab")
    await expect(appearance).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("radio", name="Light mode", exact=True)).to_be_focused()
    await expect(page.locator("#appearance-options legend").first).to_have_text("Display mode")
    setting = page.get_by_role("checkbox", name="Show page introduction", exact=True)
    await expect(setting).to_be_checked()
    await setting.focus()
    await page.keyboard.press("Space")
    await expect(setting).not_to_be_checked()
    await expect(page.locator("#page-banner")).to_be_hidden()
    await expect(setting).to_be_focused()
    await page.keyboard.press("Space")
    await expect(page.locator("#page-banner")).to_be_visible()
    await page.get_by_role("radio", name="Light mode", exact=True).focus()
    await page.keyboard.press("ArrowRight")
    await expect(page.get_by_role("radio", name="Dark mode", exact=True)).to_be_checked()
    await preference(page, "california", "dark")
    await page.get_by_role("button", name="Back to menu", exact=True).click()
    await expect(page.locator("#appearance-options")).to_be_hidden()
    await expect(trigger).to_be_focused()
    await page.keyboard.press("Tab")
    await expect(appearance).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("radio", name="Dark mode", exact=True)).to_be_focused()
    await page.get_by_role("button", name="Close appearance settings", exact=True).click()
    await closed_menu(page)
    await open_menu(page)
    # A genuine outside pointer click should dismiss the panel and restore focus.
    await page.mouse.click(2, 300)
    await closed_menu(page)
    await open_menu(page)
    await close_menu(page)
    # Clicking noninteractive panel content must keep Escape within the app.
    await open_menu(page)
    await page.locator("#appearance-heading").click()
    await close_menu(page)
    await trigger.click()
    await page.locator("#utility-options").click(position={"x": 2, "y": 2})
    await page.keyboard.press("Escape")
    await closed_menu(page)
    await choose(page, "neutral", "dark")
    await close_menu(page)
    saved = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", STORAGE_KEY)
    assert saved == {"palette": "neutral", "mode": "dark"}, f"Unexpected stored preference: {saved}"
    await page.reload(wait_until="domcontentloaded")
    await preference(page, "neutral", "dark")
    await page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Teams", exact=True).click()
    await expect(page).to_have_url(check.base_url + "/teams")
    await preference(page, "neutral", "dark")


async def registration_statuses(check: SmokeCheck, browser) -> None:
    """Check real pending/payment highlights, then GET-only fixtures for rarer states."""
    selector = ".arctos-registration-summary .arctos-registration-badge"
    async with isolated_page(check, browser) as page:
        await check.login(page, "demo-player")
        pending = page.locator(selector).filter(has_text="Awaiting Team Approval").first
        unpaid = page.locator(selector).filter(has_text="Unpaid").first
        await expect(pending).to_have_text("Awaiting Team Approval")
        await expect(unpaid).to_have_text("Unpaid")
        for mode in ("light", "dark"):
            for palette in PALETTES:
                await choose(page, palette, mode)
                await close_menu(page)
                await preference(page, palette, mode)
                await expect(page.locator(".arctos-registration-summary").filter(has_text="Player:")).to_have_count(0)
                await check_contrast(page, selector + " > span")
                styles = []
                for badge in (pending, unpaid):
                    await expect(badge.locator("i")).to_have_attribute("aria-hidden", "true")
                    styles.append(
                        await badge.evaluate("""el => {
                        const s = getComputedStyle(el), icon = el.querySelector('i');
                        const r = icon.getBoundingClientRect();
                        return {background: s.backgroundColor, border: s.borderTopColor,
                            width: parseFloat(s.borderTopWidth), icon: getComputedStyle(icon, '::before').content,
                            visible: r.width > 0 && r.height > 0};
                    }""")
                    )
                assert all(s["width"] >= 1 and s["visible"] and s["icon"] not in ("none", '""') for s in styles), styles
                assert all(styles[0][key] != styles[1][key] for key in ("background", "border", "icon")), styles
                clipped = await page.locator(selector).evaluate_all("""els => els.filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.left < -1 || r.right > innerWidth + 1 || el.scrollWidth > el.clientWidth + 1;
                }).map(el => el.textContent)""")
                assert not clipped, f"Registration highlights are clipped: {clipped}"
                await check.check_layout(page, f"registration-statuses-{palette}-{mode}", WIDTH)
                check.visited += 1

        # Payment flags remain authoritative, including paid zero-fee and unpaid
        # partial-payment records. These responses exist only in this browser.
        cases = (
            ("confirmed", "player", "CONFIRMED", True, 0, True, "VALID", ["Confirmed", "Paid", "Waiver valid"]),
            (
                "pending",
                "player",
                "PENDING_TEAM_APPROVAL",
                False,
                5,
                True,
                "OUT_OF_DATE",
                ["Awaiting Team Approval", "Unpaid", "Waiver needs re-signing"],
            ),
            (
                "declined",
                "player",
                "REJECTED",
                False,
                0,
                True,
                "NOT_SIGNED",
                ["Declined", "Unpaid", "Waiver not signed"],
            ),
            ("cancelled", "player", "CANCELLED", True, 0, False, "VALID", ["Cancelled", "Paid"]),
            (
                "unknown",
                "player",
                "FUTURE_STATUS",
                False,
                0,
                True,
                "FUTURE_WAIVER",
                ["Status unavailable", "Unpaid", "Waiver status unknown"],
            ),
            ("team", "team", "CONFIRMED", True, 0, False, "NOT_SIGNED", ["Confirmed", "Paid"]),
        )
        for resource, path, id_key in (("tournaments", "/", "url"), ("leagues", "/leagues", "league_url")):
            payload = await api_json(page.context, check.base_url, "/_api/" + resource)
            template = payload[resource][0]
            payload[resource], payload["user_reg_status"], payload["team_counts"] = [], {}, {}
            for key, kind, status, paid, amount, required, waiver, _labels in cases:
                fixture_id = "badge-fixture-" + key
                payload[resource].append(
                    {
                        **template,
                        id_key: fixture_id,
                        "name": fixture_id,
                        "start_date": "2099-01-01",
                        "end_date": "2099-01-02",
                    }
                )
                payload["user_reg_status"][fixture_id] = {
                    "type": kind,
                    "status": status,
                    "paid": paid,
                    "amount_paid": amount,
                    "waiver_required": required,
                    "waiver_status": waiver,
                }

            async def fixture(route: Route) -> None:
                if route.request.method != "GET":
                    await check.guard_requests(route)
                    return
                await route.fulfill(status=200, json=payload)

            pattern = check.base_url + "/_api/" + resource
            await page.context.route(pattern, fixture)
            try:
                await page.goto(check.base_url + path, wait_until="domcontentloaded")
                for key, *_values, labels in cases:
                    card = page.locator(".arctos-event-card").filter(
                        has=page.get_by_role("heading", name="badge-fixture-" + key, exact=True)
                    )
                    await expect(card.locator(".arctos-registration-badge")).to_have_text(labels)
                    await expect(
                        card.locator(".arctos-registration-summary a, .arctos-registration-summary button")
                    ).to_have_count(0)
                await check_contrast(page, selector + " > span")
                await check.check_layout(page, f"registration-fixtures-{resource}", WIDTH)
                check.visited += 1
            finally:
                await page.context.unroute(pattern, fixture)


async def retain_registration(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url + "/register/player", wait_until="domcontentloaded")
    values = {"username": "demo-unsaved-only", "name": "Keep my unsaved entry", "password": "not-submitted"}
    for field, value in values.items():
        await page.locator(f"#{field}").fill(value)
    await choose(page, "neutral", "light")
    await close_menu(page)
    await preference(page, "neutral", "light")
    await expect(page).to_have_url(check.base_url + "/register/player")
    for field, value in values.items():
        await expect(page.locator(f"#{field}")).to_have_value(value)
    await check_contrast(page, ".auth-card, .auth-card .form-label, .auth-card .form-control, .auth-card .form-text")


async def system_changes(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url, wait_until="domcontentloaded")
    await page.emulate_media(color_scheme="light")
    await choose(page, "neutral", "system")
    await close_menu(page)
    await preference(page, "neutral", "light")
    await page.emulate_media(color_scheme="dark")
    await preference(page, "neutral", "dark")
    await page.emulate_media(color_scheme="light")
    await preference(page, "neutral", "light")
    await choose(page, "neutral", "dark")
    await close_menu(page)
    await preference(page, "neutral", "dark")
    await page.emulate_media(color_scheme="dark")
    await page.emulate_media(color_scheme="light")
    await preference(page, "neutral", "dark")
    await choose(page, "neutral", "light")
    await close_menu(page)
    await page.emulate_media(color_scheme="dark")
    await preference(page, "neutral", "light")


async def cross_tab(check: SmokeCheck, page: Page) -> None:
    other = await page.context.new_page()
    check.observe(other)
    try:
        await other.goto(check.base_url, wait_until="domcontentloaded")
        await choose(other, "neutral", "dark")
        await preference(page, "neutral", "dark")
        await other.evaluate("key => localStorage.removeItem(key)", STORAGE_KEY)
        await preference(page, "california", "dark")  # System follows page's dark media setting.
        await page.emulate_media(color_scheme="light")
        await preference(page, "california", "light")
    finally:
        await other.close()


async def custom_surfaces(check: SmokeCheck, page: Page) -> None:
    await check.login(page)
    sidecomps = await api_json(page.context, check.base_url, "/_api/demo-open/sidecomps")
    # Discover a usable existing competition without depending on its editable name.
    comp_id = next((comp["id"] for comp in sidecomps if comp["type"] == "DUELING"), None)
    assert comp_id is not None, "Demo needs an existing dueling side competition for keypad checks"
    for palette in PALETTES:
        for mode in ("light", "dark"):
            await page.goto(check.base_url, wait_until="domcontentloaded")
            await choose(page, palette, mode)
            await close_menu(page)
            for name, path, ready, contrast in (
                (
                    "schedule",
                    "/demo-open/schedule?view=field",
                    ".schedule-timeline-event",
                    ".schedule-timeline-event-name, .schedule-timeline-status-tag--corner",
                ),
                (
                    "bracket",
                    "/demo-open/bracket",
                    "#bracket-canvas-wrap",
                    ".bracket-canvas-wrap, .bracket-empty-hint, .bracket-match-name, .bracket-text",
                ),
                (
                    "scoring",
                    f"/demo-open/sidecomps/{comp_id}/enter-results",
                    ".sc-numpad",
                    ".sc-num-btn, .sc-search-display, .sc-display-name, .sc-meta",
                ),
            ):
                await check.visit(page, Screen(f"appearance-{name}-{palette}-{mode}", path, ready), WIDTH)
                await preference(page, palette, mode)
                if name == "schedule":
                    # Demo matches may be above the current-time scroll position
                    # when the fixture has been running for a while.
                    await page.locator(".schedule-timeline-event").first.scroll_into_view_if_needed()
                    await check.check_layout(page, f"appearance-{name}-{palette}-{mode}", WIDTH)
                await check_contrast(page, contrast)
            await page.goto(check.base_url, wait_until="domcontentloaded")
            await expect(page.locator("#event-search")).to_be_visible()


async def overlay_isolation(check: SmokeCheck, page: Page) -> None:
    for path, ready in (
        ("/demo-open/scoreboard?field=Field%20A", ".scoreboard-container"),
        ("/scoreboard-render", None),
    ):
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        if ready:
            await expect(page.locator(ready)).to_be_visible()
        else:
            await page.wait_for_function("() => typeof window.arctosRenderScoreboard === 'function'")
        await expect(page.locator(".arctos-app, #utility-trigger, [data-palette], [data-bs-theme]")).to_have_count(0)
    # Return to the regular shell for shared layout/error checks.
    await page.goto(check.base_url, wait_until="domcontentloaded")
    await expect(page.locator("#event-search")).to_be_visible()


@asynccontextmanager
async def isolated_page(check: SmokeCheck, browser, init_script: str | None = None):
    """Keep anonymous, legacy-preference, and account checks independent."""
    context = await browser.new_context(**CONTEXT_OPTIONS)
    context.set_default_timeout(TIMEOUT_MS)
    if init_script:
        await context.add_init_script(init_script)
    await context.route("**/*", check.guard_requests)
    page = await context.new_page()
    was_authenticated = check.authenticated
    check.authenticated = False
    check.observe(page)
    try:
        yield page
    finally:
        await context.close()
        check.authenticated = was_authenticated


async def legacy_preferences(check: SmokeCheck, browser) -> None:
    for old_palette, expected in LEGACY_PALETTES.items():
        stored = json.dumps({"palette": old_palette, "mode": "dark"}, separators=(",", ":"))
        async with isolated_page(check, browser) as page:
            await page.goto(check.base_url + "/_api/demo-info", wait_until="domcontentloaded")
            await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [STORAGE_KEY, stored])
            await page.goto(check.base_url, wait_until="domcontentloaded")
            await preference(page, expected, "dark")
            await open_menu(page)
            await expect(page.get_by_role("radio", name=f"{PALETTES[expected]} theme", exact=True)).to_be_checked()
            await expect(page.get_by_role("radio", name="Dark mode", exact=True)).to_be_checked()
            await expect(page.locator('input[name="arctos-palette"]')).to_have_count(len(PALETTES))
            await close_menu(page)
            await page.reload(wait_until="domcontentloaded")
            await preference(page, expected, "dark")
            # Changing a preference writes a supported, canonical theme ID.
            await choose(page, expected, "light")
            actual = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", STORAGE_KEY)
            assert actual == {"palette": expected, "mode": "light"}, f"Migration did not save: {actual}"
            await close_menu(page)
            await check.check_layout(page, f"appearance-migrated-{old_palette}", WIDTH)


async def account_access(check: SmokeCheck, browser) -> None:
    for username, account_type in (("demo-player", "player"), ("demo-team", "team")):
        async with isolated_page(check, browser) as page:
            await check.login(page, username)
            if account_type == "team":
                await expect(page.locator(".arctos-registration-summary").first).to_be_visible()
                await expect(page.locator(".arctos-registration-summary").filter(has_text="Team:")).to_have_count(0)
            await expect(page.locator("#account-trigger")).to_have_attribute(
                "aria-label",
                f"Account: {(await api_json(page.context, check.base_url, '/_api/me'))['name']} ({account_type})",
            )
            await page.locator("#account-trigger").click()
            account = page.locator("#account-options")
            await expect(account).to_be_visible()
            await expect(account.locator("#account-profile-link")).to_be_focused()
            await expect(account.get_by_role("link", name="Developer apps", exact=True)).to_be_visible()
            await expect(account.get_by_role("button", name="Log out", exact=True)).to_be_visible()
            await expect(account).not_to_contain_text("Appearance")
            await expect(account).not_to_contain_text("Help")
            await expect(account).not_to_contain_text("About")
            await account.locator(".account-identity").click()
            await page.keyboard.press("Escape")
            await expect(account).to_be_hidden()
            await expect(page.locator("#account-trigger")).to_be_focused()
            await page.locator("#utility-trigger").click()
            utility = page.locator("#utility-options")
            await expect(utility.get_by_role("button", name="Appearance", exact=True)).to_be_visible()
            await expect(utility.get_by_role("link", name="Help & documentation", exact=True)).to_be_visible()
            await expect(utility.get_by_role("link", name="About Arctos", exact=True)).to_be_visible()
            await expect(utility).not_to_contain_text("Log out")
            await expect(account).to_be_hidden()
            await choose(page, "neutral", "dark")
            await preference(page, "neutral", "dark")
            await check.check_layout(page, f"appearance-{account_type}-menu", WIDTH)
            await close_menu(page)
            await page.reload(wait_until="domcontentloaded")
            await preference(page, "neutral", "dark")
            await open_menu(page)
            await expect(page.get_by_role("radio", name="Dark mode", exact=True)).to_be_focused()
            await close_menu(page)
            # Account navigation still works after changing app-wide settings.
            await page.locator("#account-trigger").click()
            await page.locator("#account-profile-link").click()
            prefix = "players" if account_type == "player" else "teams"
            await expect(page).to_have_url(f"{check.base_url}/{prefix}/{username}")
            await check.check_layout(page, f"appearance-{account_type}-access", WIDTH)


async def storage_disabled(check: SmokeCheck, browser) -> None:
    script = """Object.defineProperty(window, 'localStorage', {
        configurable: true, get() { throw new DOMException('Storage blocked by smoke check', 'SecurityError'); }
    });"""
    async with isolated_page(check, browser, script) as page:
        await page.goto(check.base_url, wait_until="domcontentloaded")
        await preference(page, "california", "light")
        await choose(page, "neutral", "dark")
        await preference(page, "neutral", "dark")
        await close_menu(page)
        await check.check_layout(page, "appearance-storage-unavailable", WIDTH)
        await page.locator("#page-banner-dismiss").click()
        await expect(page.locator("#page-banner")).to_be_hidden()
        await open_menu(page)
        setting = page.get_by_role("checkbox", name="Show page introduction", exact=True)
        await expect(setting).not_to_be_checked()
        await setting.check()
        await expect(page.locator("#page-banner")).to_be_visible()
        await expect(page.locator("#appearance-options")).to_be_visible()
        await close_menu(page)
        await page.reload(wait_until="domcontentloaded")
        await preference(page, "california", "light")


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        preflight = await playwright.request.new_context()
        try:
            response = await preflight.get(args.base_url + "/_api/demo-info", timeout=TIMEOUT_MS)
            marker = await response.json() if response.ok else {}
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER, "Verified local demo required"
        finally:
            await preflight.dispose()
        launch = {"headless": True}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        context.set_default_timeout(TIMEOUT_MS)
        await context.route("**/*", check.guard_requests)
        page = await context.new_page()
        check.observe(page)
        try:
            await page.goto(args.base_url, wait_until="domcontentloaded")
            await preference(page, "california", "light")
            await open_menu(page)
            await expect(page.get_by_role("radio", name="System mode", exact=True)).to_be_checked()
            await expect(page.get_by_role("radio", name="California Jugger theme", exact=True)).to_be_checked()
            await expect(page.locator('input[name="arctos-palette"]')).to_have_count(len(PALETTES))
            await close_menu(page)
            for name, action in (
                ("palette-mode-matrix", lambda: palette_matrix(check, page)),
                ("registration-statuses", lambda: registration_statuses(check, browser)),
                ("nested-menu-and-persistence", lambda: keyboard_and_persistence(check, page)),
                ("unsaved-registration", lambda: retain_registration(check, page)),
                ("system-and-overrides", lambda: system_changes(check, page)),
                ("cross-tab-and-removal", lambda: cross_tab(check, page)),
                ("legacy-preferences", lambda: legacy_preferences(check, browser)),
                ("player-and-team-access", lambda: account_access(check, browser)),
                ("custom-surfaces", lambda: custom_surfaces(check, page)),
                ("overlay-isolation", lambda: overlay_isolation(check, page)),
                ("storage-unavailable", lambda: storage_disabled(check, browser)),
            ):
                await check.workflow(page, f"appearance-{name}", WIDTH, action)
        finally:
            await context.close()
            await browser.close()
    print(
        f"\n{check.visited} appearance screen checks; {check.workflows} workflows passed; {len(check.failures)} failures."
    )
    if check.failures:
        print(json.dumps(check.failures, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(arguments(__doc__))))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"Appearance check stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error
