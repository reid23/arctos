"""Check Google Form requirements against an isolated local Arctos demo.

    uv run python scripts/check_registration_forms_ui.py \
        --base-url http://127.0.0.1:8451 \
        --browser-executable /usr/bin/google-chrome \
        --screenshots /tmp/arctos-registration-forms

Uses the shared demo marker and mutation guard. Form configuration and player
registration requests receive browser-only responses. Google links are checked
without opening them; no response spreadsheet or external form is requested.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import json
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, Route, async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu
from check_mobile_ui import SmokeCheck, TIMEOUT_MS, api_json, arguments

FORM_URL = "https://docs.google.com/forms/d/e/browser-fixture/viewform?usp=pp_url&entry.123=ARCTOS_CODE"
SHEET_URL = "https://docs.google.com/spreadsheets/d/browser-fixture/edit#gid=0"
SERVICE_ACCOUNT = "registration-response-reader@arctos-browser-check.iam.gserviceaccount.com"
SETTINGS_ERROR = "The registration form could not be checked. Please try again shortly or contact an organizer."
MISSING_ERROR = "No form response was found. Open your personal registration form, submit it, then try again."
SCOPES = (
    (
        "event",
        "/demo-open",
        "/_api/tournaments/demo-open",
        "/_api/demo-open/update-settings",
        "/_api/demo-open/register-player",
    ),
    (
        "league",
        "/leagues/demo-coast-league",
        "/_api/leagues/demo-coast-league",
        "/_api/leagues/demo-coast-league/settings",
        "/_api/leagues/demo-coast-league/register-player",
    ),
)


def posted_values(request) -> dict:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return request.post_data_json
    if content_type.startswith("multipart/form-data"):
        body = request.post_data_buffer or b""
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        values = {
            part.get_param("name", header="content-disposition"): part.get_content()
            for part in message.iter_parts()
            if part.get_content_disposition() == "form-data" and part.get_filename() is None
        }
        return json.loads(values["settings"]) if "settings" in values else values
    return {key: values[-1] for key, values in parse_qs(request.post_data or "", keep_blank_values=True).items()}


class FormFixtures:
    def __init__(self, check, payloads):
        self.check = check
        self.payloads = payloads
        self.required = True
        self.available = True
        self.registration_status = None
        self.status = 503
        self.message = SETTINGS_ERROR
        self.writes = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def delay(self, status, message):
        self.status, self.message = status, message
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(self, route: Route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != self.check.origin:
            await route.abort("blockedbyclient")
            return
        if request.method == "GET" and url.path in self.payloads:
            payload = deepcopy(self.payloads[url.path])
            config = payload.get("tournament", payload.get("league"))
            config.update(
                registration_form_required=self.required,
                registration_form_url=FORM_URL,
                registration_form_sheet_url=SHEET_URL,
                registration_form_code_column="Arctos registration code",
                registration_form_service_account_email=SERVICE_ACCOUNT,
                registration_form_available=self.available,
                player_registration_open=True,
                waiver_required=False,
            )
            payload.update(is_current_player_registered=self.registration_status is not None)
            await route.fulfill(json=payload)
            return
        registration_paths = {scope[2] + "/registrations/player/me" for scope in SCOPES}
        if request.method == "GET" and url.path in registration_paths and self.registration_status is not None:
            await route.fulfill(
                json={
                    "registration": {
                        "id": 910001,
                        "jersey_name": "Pending player",
                        "jersey_number": "12",
                        "team": "demo-team",
                        "status": self.registration_status,
                    },
                    "current_team": {"id": "demo-team", "pseudonym": "Demo team"},
                    "waiver_required": False,
                    "waiver_signature_valid": True,
                }
            )
            return
        write_paths = {path for scope in SCOPES for path in scope[3:]}
        if request.method == "PUT" and url.path in registration_paths:
            self.writes.append((url.path, posted_values(request)))
            await route.fulfill(status=503, json={"error": SETTINGS_ERROR})
            return
        if request.method == "POST" and url.path in write_paths:
            self.writes.append((url.path, posted_values(request)))
            self.started.set()
            await self.release.wait()
            await route.fulfill(
                status=self.status,
                json={"success": True} if self.status == 200 else {"success": False, "error": self.message},
            )
            return
        await route.fallback()


async def assert_labeled_controls(page: Page):
    for label, field in (
        ("Prefilled Google Form link", "registration_form_url"),
        ("Response spreadsheet link", "registration_form_sheet_url"),
        ("Registration code column heading", "registration_form_code_column"),
    ):
        control = page.get_by_label(label, exact=True)
        await expect(control).to_have_attribute("id", field)
        await expect(control).to_have_js_property("required", True)
        hint = await control.get_attribute("aria-describedby")
        assert hint and await page.locator(f"#{hint}").text_content()
        bounds = await control.bounding_box()
        assert bounds and bounds["height"] >= 44, (label, bounds)


async def settings_flow(check: SmokeCheck, page: Page, fixtures: FormFixtures, scope, width):
    kind, root, _, save_path, _ = scope
    fixtures.required = False
    await page.goto(check.base_url + root + "/settings", wait_until="domcontentloaded")
    enabled = page.get_by_label("Require a Google Form response for player registration", exact=True)
    await expect(enabled).not_to_be_checked()
    await expect(page.locator("#registration_form_url")).to_have_count(0)
    await enabled.check()
    await assert_labeled_controls(page)
    await page.get_by_label("Prefilled Google Form link", exact=True).fill(FORM_URL)
    await page.get_by_label("Response spreadsheet link", exact=True).fill(SHEET_URL)
    await page.get_by_label("Registration code column heading", exact=True).fill("Arctos registration code")
    await page.locator("#about").fill("Retain this organizer draft.")
    await expect(page.get_by_text(SERVICE_ACCOUNT, exact=True)).to_be_visible()
    await expect(page.get_by_text("as a Viewer. Keep general access Restricted.", exact=False)).to_be_visible()
    await enabled.uncheck()
    await expect(page.locator("#registration_form_url")).to_have_count(0)
    await enabled.check()
    await expect(page.get_by_label("Registration code column heading", exact=True)).to_have_value(
        "Arctos registration code"
    )
    await check.check_layout(page, f"forms-{kind}-settings", width)
    await check.screenshot(page, f"forms-{kind}-settings", width)
    if check.screenshots:
        await page.get_by_role("group", name="Registration form", exact=True).screenshot(
            path=str(check.screenshots / f"{width:04d}-forms-{kind}-settings-section.png"),
            animations="disabled",
            # Keep the sticky navigation outside this isolated component crop.
            style=".app-header { visibility: hidden; }",
        )

    count = len(fixtures.writes)
    fixtures.delay(503, SETTINGS_ERROR)
    save = page.get_by_role("button", name="Save settings", exact=True)
    await save.click()
    await asyncio.wait_for(fixtures.started.wait(), timeout=10)
    await expect(page.locator("#registration_form_url")).to_be_disabled()
    await expect(page.get_by_role("button", name="Saving…", exact=True)).to_be_disabled()
    await page.locator("#registration_form_required").evaluate(
        "el => el.form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))"
    )
    assert len(fixtures.writes) == count + 1
    fixtures.release.set()
    await expect(page.locator("#settings-save-error")).to_contain_text(SETTINGS_ERROR)
    await expect(page.locator("#about")).to_have_value("Retain this organizer draft.")
    await expect(page.get_by_label("Registration code column heading", exact=True)).to_have_value(
        "Arctos registration code"
    )
    path, values = fixtures.writes[-1]
    assert path == save_path
    assert values["registration_form_required"] in ("true", True)
    assert values["registration_form_url"] == FORM_URL
    assert values["registration_form_sheet_url"] == SHEET_URL
    assert values["registration_form_code_column"] == "Arctos registration code"
    await enabled.uncheck()
    fixtures.status = 200
    await save.click()
    await expect(page).not_to_have_url(check.base_url + root + "/settings")
    assert fixtures.writes[-1][1]["registration_form_required"] in ("false", False)
    check.workflows += 1


async def registration_flow(check: SmokeCheck, page: Page, fixtures: FormFixtures, scope, width):
    kind, root, _, _, register_path = scope
    fixtures.required = True
    await page.goto(check.base_url + root + "/register", wait_until="domcontentloaded")
    notice = page.locator(".alert").filter(
        has=page.get_by_role("heading", name="Registration form required", exact=True)
    )
    await expect(notice).to_contain_text("Your registration code is filled in automatically")
    link = notice.get_by_role("link", name="Open my registration form (opens in a new tab)", exact=True)
    await expect(link).to_have_attribute("href", check.base_url + scope[2] + "/registration-form")
    await expect(link).to_have_attribute("target", "_blank")
    assert {"noopener", "noreferrer"}.issubset(set((await link.get_attribute("rel")).split()))
    assert "Response spreadsheet" not in await page.locator("main").inner_text()
    assert SERVICE_ACCOUNT not in await page.locator("main").inner_text()
    await page.locator("#jersey_name").fill("Keep my jersey")
    await page.locator("#jersey_number").fill("81")
    options = await page.locator("#team option").evaluate_all("els => els.map(el => el.value).filter(Boolean)")
    assert options, "Demo must provide a team choice"
    await page.locator("#team").select_option(options[0])
    await link.focus()
    await expect(link).to_be_focused()
    await check.check_layout(page, f"forms-{kind}-register", width)
    await check.screenshot(page, f"forms-{kind}-register", width)
    if check.screenshots:
        await notice.screenshot(
            path=str(check.screenshots / f"{width:04d}-forms-{kind}-notice.png"), animations="disabled"
        )

    for status, message in ((503, SETTINGS_ERROR), (400, MISSING_ERROR)):
        count = len(fixtures.writes)
        fixtures.delay(status, message)
        await page.get_by_role("button", name="Register", exact=True).click()
        await asyncio.wait_for(fixtures.started.wait(), timeout=10)
        await expect(page.get_by_role("button", name="Registering…", exact=True)).to_be_disabled()
        await page.locator("#jersey_name").evaluate(
            "el => el.form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))"
        )
        assert len(fixtures.writes) == count + 1
        fixtures.release.set()
        await expect(page.locator("#registration-error")).to_contain_text(message)
        await expect(page.locator("#registration-error")).to_be_focused()
        await expect(page.locator("#jersey_name")).to_have_value("Keep my jersey")
        await expect(page.locator("#jersey_number")).to_have_value("81")
        await expect(page.locator("#team")).to_have_value(options[0])
        path, values = fixtures.writes[-1]
        assert path == register_path
        assert values["jersey_name"] == "Keep my jersey" and values["team"] == options[0]
    await check.screenshot(page, f"forms-{kind}-registration-error", width)
    fixtures.status = 200
    await page.get_by_role("button", name="Register", exact=True).click()
    await expect(page).not_to_have_url(check.base_url + root + "/register")
    check.workflows += 1


async def pending_editor_flow(check: SmokeCheck, page: Page, fixtures: FormFixtures, scope, width):
    kind, root, _, _, _ = scope
    fixtures.registration_status = "PENDING_TEAM_APPROVAL"
    fixtures.required = True
    await page.goto(check.base_url + root, wait_until="domcontentloaded")
    await page.get_by_role("button", name="Edit registration", exact=True).click()
    dialog = page.get_by_role("dialog", name="Edit player registration", exact=True)
    await expect(dialog.get_by_role("heading", name="Registration form required", exact=True)).to_be_visible()
    await expect(dialog).to_contain_text("Your registration code is filled in automatically")
    await expect(
        dialog.get_by_role("link", name="Open my registration form (opens in a new tab)", exact=True)
    ).to_have_attribute("href", check.base_url + scope[2] + "/registration-form")
    await dialog.locator("#edit-jersey-name").fill("Retain pending draft")
    await dialog.locator("#edit-registration-team").select_option("")
    await dialog.get_by_role("button", name="Save changes", exact=True).click()
    await expect(dialog.get_by_role("alert")).to_contain_text(SETTINGS_ERROR)
    await expect(dialog.locator("#edit-jersey-name")).to_have_value("Retain pending draft")
    await expect(dialog.locator("#edit-registration-team")).to_have_value("")
    await check.check_layout(page, f"forms-{kind}-pending-editor", width)
    await check.screenshot(page, f"forms-{kind}-pending-editor", width)
    await dialog.get_by_role("button", name="Cancel", exact=True).click()
    await expect(dialog).to_have_count(0)
    fixtures.registration_status = "CONFIRMED"
    await page.goto(check.base_url + root, wait_until="domcontentloaded")
    await page.get_by_role("button", name="Edit registration", exact=True).click()
    await expect(page.locator("#edit-jersey-name")).to_be_visible()
    await expect(page.get_by_role("heading", name="Registration form required", exact=True)).to_have_count(0)
    await page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
    fixtures.registration_status = None
    check.workflows += 1


async def palette_matrix(check: SmokeCheck, page: Page, fixtures: FormFixtures):
    fixtures.required = True
    for view in ("settings", "register"):
        await page.goto(check.base_url + "/demo-open/" + view, wait_until="domcontentloaded")
        ready = "#registration_form_required" if view == "settings" else "#jersey_name"
        await expect(page.locator(ready)).to_be_visible()
        for palette in PALETTES:
            for mode in ("light", "dark"):
                await choose(page, palette, mode)
                await close_menu(page)
                selector = (
                    "fieldset:has(> legend) label, fieldset:has(> legend) .form-text, fieldset:has(> legend) input[type=url], fieldset:has(> legend) input[type=text]"
                    if view == "settings"
                    else ".alert-info h3, .alert-info p, .alert-info a"
                )
                await check_contrast(page, selector)
                await check.check_layout(page, f"forms-{view}-{palette}-{mode}", 390)
                if check.screenshots:
                    panel = (
                        page.get_by_role("group", name="Registration form", exact=True)
                        if view == "settings"
                        else page.locator(".alert").filter(
                            has=page.get_by_role("heading", name="Registration form required", exact=True)
                        )
                    )
                    await panel.screenshot(
                        path=str(check.screenshots / f"0390-forms-{view}-{palette}-{mode}.png"), animations="disabled"
                    )
    check.workflows += 1


async def run(args):
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        probe = await playwright.request.new_context()
        marker = await probe.get(args.base_url + "/_api/demo-info")
        assert marker.ok and (await marker.json()).get("demo") is True, "Refusing to run without a local demo marker"
        await probe.dispose()
        options = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            options["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**options)
        try:
            for width in (320, 390, 1280):
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
                page.on("pageerror", lambda error: check.page_errors.append(str(error)))
                try:
                    await check.login(page)
                    payloads = {scope[2]: await api_json(context, args.base_url, scope[2]) for scope in SCOPES}
                    fixtures = FormFixtures(check, payloads)
                    await context.route("**/*", fixtures.handle)
                    for scope in SCOPES:
                        await settings_flow(check, page, fixtures, scope, width)
                        await registration_flow(check, page, fixtures, scope, width)
                        await pending_editor_flow(check, page, fixtures, scope, width)
                    if width == 390:
                        await palette_matrix(check, page, fixtures)
                    check.visited += 4
                finally:
                    await context.close()
        finally:
            await browser.close()
    assert not check.page_errors, check.page_errors
    assert not check.blocked_writes, check.blocked_writes
    print(
        f"Registration form checks passed: {check.visited} routes at 320/390/1280px; {check.workflows} workflows; all palettes in light/dark."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(arguments())))
    except (AssertionError, RuntimeError) as error:
        print(f"Registration form check failed: {error}", file=sys.stderr)
        sys.exit(1)
