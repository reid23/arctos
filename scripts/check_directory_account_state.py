"""Check retained directory privacy and retry states against a local release.

    uv run python scripts/check_directory_account_state.py --base-url http://127.0.0.1:5016

Playwright supplies synthetic account/directory responses and intercepts logout.
Unexpected API requests are blocked: this check never changes server records or
sessions. Reports contain assertions and failure stages, not page contents.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright
from scripts.check_public_html import validate_base_url

STEP = "starting"


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5016")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        args.base_url = validate_base_url(args.base_url)
    except ValueError as error:
        parser.error(str(error))
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = Path(__file__).resolve().parents[1] / "preview" / f"directory-account-{stamp}.json"
    return args


async def block_unmocked_api(page):
    unexpected = []

    async def reject(route):
        unexpected.append("unmocked-api-request")
        await route.abort()

    # Known mocks are registered after this guard and take priority.
    await page.route("**/_api/**", reject)
    return unexpected


def directory(private):
    card = {
        "url": "/audit-event",
        "name": "Synthetic private draft" if private else "Synthetic public event",
        "date_start": "2026-10-28",
        "date_end": None,
        "date_label": "Oct 28, 2026",
        "location": "Synthetic field",
        "league_name": None,
        "league_url": None,
        "team_count": 0,
        "max_teams": None,
        "registration_open": False,
        "registration_label": "Registration closed",
        "published": not private,
        "account_status": {
            "account_type": "player",
            "status": "CONFIRMED",
            "status_label": "Synthetic personal registration",
            "paid": False,
            "waiver_status": None,
        }
        if private
        else None,
    }
    return {
        "query": "",
        "view": "upcoming",
        "page": 1,
        "page_size": 24,
        "total": 1,
        "upcoming_count": 1,
        "past_count": 0,
        "cards": [card],
        "previous_url": None,
        "next_url": None,
        "upcoming_url": "/",
        "past_url": "/?view=past",
        "clear_url": "/",
    }


async def logout_case(browser, origin, delayed_old):
    global STEP
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    page.set_default_timeout(8000)
    logged_in = True
    old_started, old_release = asyncio.Event(), asyncio.Event()
    anonymous_started, anonymous_release = asyncio.Event(), asyncio.Event()
    requests = []
    errors = []
    page.on("pageerror", lambda _error: errors.append("javascript-error"))
    page.on(
        "request",
        lambda request: requests.append((request.resource_type, urlsplit(request.url).path.endswith(".wasm"))),
    )

    async def account(route):
        if logged_in:
            await route.fulfill(json={"id": "synthetic-account", "type": "player", "name": "Synthetic account"})
        else:
            await route.fulfill(status=401, json={"error": "Unauthenticated"})

    async def logout(route):
        nonlocal logged_in
        logged_in = False
        await route.fulfill(json={"success": True})

    async def events(route):
        private = logged_in
        if private:
            old_started.set()
            if delayed_old:
                await old_release.wait()
        else:
            anonymous_started.set()
            await anonymous_release.wait()
        try:
            await route.fulfill(json=directory(private))
        except Exception:
            # Cancellation of a superseded request is expected; no real API is called.
            if not (private and delayed_old):
                raise

    unexpected_api = await block_unmocked_api(page)
    await page.route("**/_api/me", account)
    await page.route("**/_api/logout", logout)
    await page.route("**/_api/public/events?*", events)
    try:
        STEP = "entry"
        await page.goto(origin + "/?app=1", wait_until="domcontentloaded")
        STEP = "initial-directory-request"
        await asyncio.wait_for(old_started.wait(), 8)
        STEP = "account-control"
        await page.locator("#main #account-trigger").wait_for(state="visible")
        await page.evaluate("window.__auditDocument = true")
        if not delayed_old:
            STEP = "initial-private-content"
            await page.get_by_text("Synthetic private draft", exact=True).wait_for(state="visible")
            await page.get_by_text("Synthetic personal registration", exact=True).wait_for(state="visible")
        STEP = "logout-control"
        await page.locator("#main #account-trigger").click()
        await page.get_by_role("button", name="Log out", exact=True).click()
        STEP = "anonymous-account"
        await page.locator("#main").get_by_role("link", name="Log in", exact=True).wait_for(state="visible")
        STEP = "anonymous-directory-request"
        await asyncio.wait_for(anonymous_started.wait(), 3)
        assert await page.get_by_text("Synthetic private draft", exact=True).count() == 0
        assert await page.get_by_text("Synthetic personal registration", exact=True).count() == 0
        if delayed_old:
            STEP = "ignore-prior-account-response"
            old_release.set()
            await page.wait_for_timeout(150)
            assert await page.get_by_text("Synthetic private draft", exact=True).count() == 0
            assert await page.get_by_text("Synthetic personal registration", exact=True).count() == 0
        anonymous_release.set()
        STEP = "anonymous-directory-content"
        await page.get_by_text("Synthetic public event", exact=True).wait_for(state="visible")
        await page.wait_for_timeout(100)
        assert await page.get_by_text("Synthetic private draft", exact=True).count() == 0
        assert await page.evaluate("Boolean(window.__auditDocument)")
        assert sum(kind == "document" for kind, _ in requests) == 1
        assert sum(wasm for _, wasm in requests) == 1
        assert not errors
        assert not unexpected_api
        return {
            "passed": True,
            "old_response_delayed": delayed_old,
            "document_requests": 1,
            "wasm_requests": 1,
            "private_content_removed_while_pending": True,
        }
    finally:
        old_release.set()
        anonymous_release.set()
        await context.close()


async def league_retry(browser, origin):
    global STEP
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    page.set_default_timeout(8000)
    count = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def account(route):
        await route.fulfill(status=503, json={"error": "Unavailable"})

    async def leagues(route):
        nonlocal count
        count += 1
        if count == 1:
            await route.fulfill(status=503, json={"error": "Synthetic temporary error"})
        else:
            started.set()
            await release.wait()
            await route.fulfill(json={"leagues": []})

    unexpected_api = await block_unmocked_api(page)
    await page.route("**/_api/me", account)
    await page.route("**/_api/leagues?*", leagues)
    try:
        STEP = "league-entry"
        await page.goto(origin + "/leagues?app=1", wait_until="domcontentloaded")
        STEP = "initial-league-error"
        await page.get_by_role("heading", name="Leagues could not be loaded", exact=True).wait_for(state="visible")
        await page.get_by_role("button", name="Try again", exact=True).click()
        STEP = "retry-request"
        await asyncio.wait_for(started.wait(), 3)
        STEP = "retry-pending-ui"
        await page.get_by_text("Loading leagues…", exact=True).wait_for(state="visible")
        assert await page.get_by_role("heading", name="Leagues could not be loaded", exact=True).count() == 0
        release.set()
        STEP = "retry-completes"
        await page.get_by_role("heading", name="No active leagues", exact=True).wait_for(state="visible")
        assert not unexpected_api
        return {"passed": True, "list_requests": count, "retry_replaces_old_error_with_pending": True}
    finally:
        release.set()
        await context.close()


async def league_pagination(browser, origin):
    """A large synthetic list keeps its page and literal query across workflows."""
    global STEP
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    page.set_default_timeout(8000)
    list_requests = 0
    unexpected_api = await block_unmocked_api(page)

    async def leagues(route):
        nonlocal list_requests
        list_requests += 1
        await route.fulfill(
            json={
                "leagues": [
                    {"league_url": f"synthetic-{index:02}", "name": f"A&B League {index:02}", "published": True}
                    for index in range(30)
                ]
                + [{"league_url": "unrelated", "name": "Unrelated league", "published": True}]
            }
        )

    async def anonymous(route):
        await route.fulfill(status=401, json={"error": "Unauthenticated"})

    await page.route("**/_api/me", anonymous)
    await page.route("**/_api/leagues?*", leagues)
    try:
        STEP = "league-page-two-entry"
        await page.goto(origin + "/leagues?q=A%26B&page=2&app=1", wait_until="domcontentloaded")
        await page.locator("#main .arctos-league-grid").wait_for(state="visible")
        await page.evaluate("window.__auditDocument = true")

        async def assert_second_page():
            await page.locator("#main .arctos-page-position").filter(has_text="Page 2").wait_for(state="visible")
            assert await page.locator("#main .arctos-card-title").all_text_contents() == [
                f"A&B League {index:02}" for index in range(24, 30)
            ]
            assert await page.locator("#main #league-search").input_value() == "A&B"
            assert await page.locator("#main .arctos-segment.is-active .arctos-count").inner_text() == "30"

        await assert_second_page()
        STEP = "league-page-two-workflow-and-back"
        await page.locator("#main").get_by_role("link", name="Log in", exact=True).click()
        await page.locator("#main #username").wait_for(state="visible")
        await page.go_back()
        await assert_second_page()
        STEP = "league-page-one-and-forward"
        await page.get_by_role("button", name="Previous", exact=True).click()
        await page.locator("#main .arctos-page-position").filter(has_text="Page 1").wait_for(state="visible")
        assert await page.locator("#main .arctos-card-title").count() == 24
        await page.go_back()
        await assert_second_page()
        assert await page.evaluate("Boolean(window.__auditDocument)")
        assert list_requests == 1
        assert not unexpected_api
        return {"passed": True, "list_requests": 1, "page_two_cards": 6, "query_and_history_preserved": True}
    finally:
        await context.close()


async def main(args):
    if args.output.exists():
        raise ValueError("Choose a new report path; prior regression evidence will not be overwritten.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=str(args.browser_executable) if args.browser_executable else None, headless=True
        )
        try:
            for name, callback in [
                ("same-route logout", lambda: logout_case(browser, args.base_url, False)),
                ("delayed prior account response", lambda: logout_case(browser, args.base_url, True)),
                ("retry pending state", lambda: league_retry(browser, args.base_url)),
                ("league pagination and history", lambda: league_pagination(browser, args.base_url)),
            ]:
                try:
                    result = await callback()
                except Exception as error:
                    result = {"passed": False, "failure": type(error).__name__, "step": STEP}
                rows.append({"case": name, **result})
                args.output.write_text(
                    json.dumps(
                        {
                            "complete": len(rows) == 4,
                            "passed": len(rows) == 4 and all(row["passed"] for row in rows),
                            "scope": "Synthetic account and directory responses; all unmatched API requests blocked; no server/session mutations or production instrumentation.",
                            "cases": rows,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                print(name, result, flush=True)
        finally:
            await browser.close()
    return int(not all(row["passed"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(arguments())))
