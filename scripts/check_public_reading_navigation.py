"""Verify public reading navigation and application handoff on a local release.

    uv run python scripts/check_public_reading_navigation.py --base-url http://127.0.0.1:5016

The target must enable public HTML and include a public event plus a public league.
Requests are read-only; writes are blocked except the read-only Markdown renderer.
Delay/failure injection makes this a functional check, separate from timing runs.
Reports contain assertion outcomes and failure stages rather than page content.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright
from scripts.check_public_html import validate_base_url, validate_path

STEP = "starting"
SAMPLER = """(() => {
  window.__readingMaxHeaders = 0;
  const tick = () => {
    const count = [...document.querySelectorAll('.app-header')].filter(el => {
      const s = getComputedStyle(el), r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width && r.height;
    }).length;
    window.__readingMaxHeaders = Math.max(window.__readingMaxHeaders, count);
    requestAnimationFrame(tick);
  }; requestAnimationFrame(tick);
})();"""


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5016")
    parser.add_argument("--event-path", default="/demo-open")
    parser.add_argument("--league-path", default="/leagues/demo-coast-league")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        args.base_url = validate_base_url(args.base_url)
        args.event_path = validate_path(args.event_path)
        args.league_path = validate_path(args.league_path)
    except ValueError as error:
        parser.error(str(error))
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = Path(__file__).resolve().parents[1] / "preview" / f"public-reading-{stamp}.json"
    return args


@asynccontextmanager
async def session(browser, *, wasm="held"):
    context = await browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    await context.add_init_script(SAMPLER)
    page = await context.new_page()
    page.set_default_timeout(10_000)
    observed = {"documents": 0, "wasm": 0, "writes": 0, "errors": 0}
    gate, requested = asyncio.Event(), asyncio.Event()

    def request(req):
        observed["documents"] += req.resource_type == "document"
        observed["wasm"] += urlsplit(req.url).path.endswith(".wasm")

    async def request_guard(route):
        req = route.request
        if req.method in {"GET", "HEAD", "OPTIONS"} or (
            req.method == "POST" and urlsplit(req.url).path == "/_api/render-markdown"
        ):
            await route.continue_()
        else:
            observed["writes"] += 1
            await route.abort()

    async def wasm_route(route):
        requested.set()
        if wasm == "failed":
            await route.abort()
            return
        if wasm == "held":
            await gate.wait()
        await route.continue_()

    page.on("request", request)
    page.on("pageerror", lambda _error: observed.__setitem__("errors", observed["errors"] + 1))
    await context.route("**/*", request_guard)
    await page.route("**/*.wasm", wasm_route)
    try:
        yield page, observed, requested, gate
    finally:
        gate.set()
        await context.close()


async def enter(page, args, path="/"):
    response = await page.goto(args.base_url + path, wait_until="domcontentloaded")
    assert response.status == 200
    await page.locator("#public-document main[data-public-page]").wait_for(state="visible")
    await page.evaluate("window.__readingSentinel = true")


async def reading_nav(page, label):
    await (
        page.locator('#public-document nav[aria-label="Main navigation"]')
        .get_by_role("link", name=label, exact=True)
        .click()
    )


async def retained(page, observed):
    assert await page.evaluate("window.__readingSentinel === true")
    assert observed["documents"] == 1
    assert observed["wasm"] == 1
    assert observed["writes"] == 0
    assert await page.evaluate("window.__readingMaxHeaders") <= 1


async def metadata_matches(page, args, path):
    response = await page.request.get(args.base_url + path)
    assert response.status == 200
    expected = await page.evaluate(
        """html => {
          const doc = new DOMParser().parseFromString(html, 'text/html');
          return [doc.title, ...['meta[name="description"]', 'meta[name="robots"]',
            'link[rel="canonical"]', 'meta[property="og:title"]', 'meta[property="og:url"]']
            .map(s => doc.querySelector(s)?.getAttribute(s.startsWith('link') ? 'href' : 'content') || null)];
        }""",
        await response.text(),
    )
    actual = await page.evaluate(
        """() => [document.title, ...['meta[name="description"]', 'meta[name="robots"]',
          'link[rel="canonical"]', 'meta[property="og:title"]', 'meta[property="og:url"]']
          .map(s => document.querySelector(s)?.getAttribute(s.startsWith('link') ? 'href' : 'content') || null)]"""
    )
    assert actual == expected


async def held_reading(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, gate):
        STEP = "initial-public-reading"
        await enter(page, args)
        await asyncio.wait_for(requested.wait(), 10)
        await page.evaluate("window.scrollTo(0, Math.min(180, document.documentElement.scrollHeight-innerHeight))")
        initial_scroll = await page.evaluate("scrollY")
        assert initial_scroll > 0
        STEP = "held-wasm-leagues"
        await reading_nav(page, "Leagues")
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        await metadata_matches(page, args, "/leagues")
        assert await page.evaluate("Array.isArray(history.state) && history.state.length === 2")
        STEP = "appearance-rebound-after-swap"
        await page.locator('#public-document summary[aria-label="More options"]').click()
        await page.locator("#public-document [data-public-open-appearance]").click()
        await page.locator('#public-document input[data-public-preference="mode"][value="dark"]').check()
        assert await page.locator("#public-document").get_attribute("data-bs-theme") == "dark"
        await page.locator("#public-document [data-public-appearance-close]").click()
        STEP = "held-wasm-league-info"
        league = await page.locator(
            f'#public-document a[data-public-reading][href="{args.league_path}"]'
        ).first.element_handle()
        await league.evaluate("el => el.href += '#about-heading'")
        await league.click()
        await page.locator('main[data-public-page="league"]').wait_for(state="visible")
        assert urlsplit(page.url).fragment == "about-heading"
        await metadata_matches(page, args, args.league_path)
        assert await page.locator("#public-document").get_attribute("data-bs-theme") == "dark"
        STEP = "public-back-forward"
        await page.go_back()
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        await page.go_back()
        await page.locator('main[data-public-page="directory"]').wait_for(state="visible")
        await page.wait_for_function("y => Math.abs(scrollY-y) <= 2", arg=initial_scroll)
        await page.go_forward()
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        STEP = "native-get-encoded-query"
        await page.locator("#league-search").fill("A&B + café")
        await page.locator("form[data-public-reading]").get_by_role("button", name="Search", exact=True).click()
        await page.wait_for_function("new URLSearchParams(location.search).get('q') === 'A&B + café'")
        assert await page.locator("#league-search").input_value() == "A&B + café"
        await metadata_matches(page, args, urlsplit(page.url).path + "?" + urlsplit(page.url).query)
        await retained(page, observed)
        assert observed["errors"] == 0
        return {"document_requests": 1, "wasm_requests": 1, "metadata_and_history": True, "appearance_rebound": True}


async def failed_reading(browser, args):
    global STEP
    async with session(browser, wasm="failed") as (page, observed, requested, _gate):
        STEP = "failed-runtime-entry"
        await enter(page, args)
        await asyncio.wait_for(requested.wait(), 10)
        await page.wait_for_timeout(150)
        STEP = "failed-runtime-reading"
        await reading_nav(page, "Leagues")
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        await page.locator(f'#public-document a[data-public-reading][href="{args.league_path}"]').first.click()
        await page.locator('main[data-public-page="league"]').wait_for(state="visible")
        await reading_nav(page, "Events")
        await page.locator('main[data-public-page="directory"]').wait_for(state="visible")
        await retained(page, observed)
        return {"reading_survives_runtime_failure": True, "document_requests": 1, "wasm_requests": 1}


async def latest_reading(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, _gate):
        first_started, release = asyncio.Event(), asyncio.Event()

        async def delayed_leagues(route):
            if route.request.resource_type == "document":
                await route.continue_()
                return
            response = await route.fetch()
            first_started.set()
            await release.wait()
            try:
                await route.fulfill(response=response)
            except Exception:
                pass  # An aborted superseded fetch is the expected path.

        await page.route(args.base_url + "/leagues", delayed_leagues)
        try:
            STEP = "latest-intent-entry"
            await enter(page, args, args.event_path)
            await asyncio.wait_for(requested.wait(), 10)
            await reading_nav(page, "Leagues")
            await asyncio.wait_for(first_started.wait(), 5)
            STEP = "supersede-delayed-reading"
            await reading_nav(page, "Events")
            await page.locator('main[data-public-page="directory"]').wait_for(state="visible")
            release.set()
            await page.wait_for_timeout(200)
            assert urlsplit(page.url).path == "/"
            assert await page.locator('main[data-public-page="leagues"]').count() == 0
            await retained(page, observed)
            assert observed["errors"] == 0
            return {"superseded_reading_ignored": True}
        finally:
            release.set()


async def css_handoff(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, gate):
        css_started, css_release = asyncio.Event(), asyncio.Event()

        async def delayed_css(route):
            css_started.set()
            await css_release.wait()
            await route.continue_()

        await page.route("**/assets/**.css", delayed_css)
        try:
            STEP = "workflow-css-entry"
            await enter(page, args, args.event_path)
            await asyncio.wait_for(requested.wait(), 10)
            await reading_nav(page, "Leagues")
            await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
            await page.locator(f'#public-document a[data-public-reading][href="{args.league_path}"]').first.click()
            await page.locator('main[data-public-page="league"]').wait_for(state="visible")
            gate.set()
            await page.wait_for_function("document.documentElement.dataset.arctosRouterReady === 'true'")
            STEP = "workflow-css-held"
            await page.locator("#public-document .app-login").click()
            await asyncio.wait_for(css_started.wait(), 10)
            assert await page.locator("#public-document").is_visible()
            assert not await page.locator("#main").is_visible()
            assert await page.locator('#public-document main[data-public-page="league"]').is_visible()
            assert await page.locator('#public-document [id="page-content"]').count() == 0
            STEP = "appearance-during-stylesheet-handoff"
            initial_mode = await page.locator("#main #app-layout").get_attribute("data-bs-theme")
            selected_mode = "dark" if initial_mode == "light" else "light"
            await page.locator('#public-document summary[aria-label="More options"]').click()
            await page.locator("#public-document [data-public-open-appearance]").click()
            await page.locator(
                f'#public-document input[data-public-preference="mode"][value="{selected_mode}"]'
            ).check()
            assert await page.locator("#public-document").get_attribute("data-bs-theme") == selected_mode
            css_release.set()
            STEP = "workflow-handoff"
            await page.locator("#main #username").wait_for(state="visible")
            await page.locator("#main #password").wait_for(state="visible")
            assert (
                await page.locator("#public-document").count() == 0
                or not await page.locator("#public-document").is_visible()
            )
            await page.wait_for_function("document.activeElement?.id === 'page-content'")
            assert await page.locator("#main #app-layout").get_attribute("data-bs-theme") == selected_mode
            assert await page.locator("head [data-public-meta]").count() == 0
            STEP = "activated-back-forward"
            await page.go_back()
            await page.locator("#main #league-section-info").wait_for(state="visible")
            assert urlsplit(page.url).path == args.league_path
            await page.go_back()
            await page.locator("#main #league-search").wait_for(state="visible")
            await page.go_forward()
            await page.locator("#main #league-section-info").wait_for(state="visible")
            await page.go_forward()
            await page.locator("#main #username").wait_for(state="visible")
            await retained(page, observed)
            assert observed["errors"] == 0
            return {
                "public_reading_during_css_delay": True,
                "post_activation_history": True,
                "appearance_preserved": True,
            }
        finally:
            css_release.set()


async def workflow_entry_focus(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, gate):
        STEP = "direct-workflow-entry"
        response = await page.goto(args.base_url + "/login", wait_until="domcontentloaded")
        assert response.status == 200
        await page.locator("#app-boot").wait_for(state="visible")
        await page.evaluate("window.__readingSentinel = true")
        await asyncio.wait_for(requested.wait(), 10)
        control = page.locator('#app-startup summary[aria-label="More options"]')
        await control.focus()
        assert await control.evaluate("el => document.activeElement === el")
        STEP = "direct-workflow-focus-handoff"
        gate.set()
        await page.locator("#main #username").wait_for(state="visible")
        await page.wait_for_function("document.activeElement?.id === 'page-content'")
        assert not await page.locator("#app-startup").is_visible()
        await retained(page, observed)
        assert observed["errors"] == 0
        return {"keyboard_focus_restored": True, "document_requests": 1, "wasm_requests": 1}


async def incompatible_reading(browser, args, *, mismatch):
    global STEP
    async with session(browser) as (page, observed, requested, _gate):

        async def reject_fetch(route):
            if route.request.resource_type == "document":
                await route.continue_()
            elif mismatch:
                response = await route.fetch()
                body = await response.text()
                # Keep the actual document while simulating a new deployment's entry.
                body = body.replace('data-entry="', 'data-entry="/different-release')
                await route.fulfill(response=response, body=body)
            else:
                await route.fulfill(status=503, content_type="text/html", body="Service unavailable")

        await page.route(args.base_url + "/leagues", reject_fetch)
        STEP = "incompatible-reading-entry"
        await enter(page, args)
        await asyncio.wait_for(requested.wait(), 10)
        STEP = "incompatible-reading-native-recovery"
        await reading_nav(page, "Leagues")
        if not mismatch:
            recovery = page.locator("#public-app-status").get_by_role("link", name="Open page directly")
            await recovery.wait_for(state="visible")
            assert urlsplit(page.url).path == "/"
            assert await page.locator('main[data-public-page="directory"]').is_visible()
            await retained(page, observed)
            await recovery.click()
        await page.wait_for_function("location.pathname === '/leagues' && window.__readingSentinel !== true")
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        assert observed["documents"] == 2
        assert observed["writes"] == 0
        return {"native_recovery": True, "incompatible_manifest": mismatch}


async def stylesheet_failure(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, gate):
        STEP = "stylesheet-failure-entry"
        await enter(page, args, args.event_path)
        await asyncio.wait_for(requested.wait(), 10)
        original_title = await page.title()
        gate.set()
        await page.wait_for_function("document.documentElement.dataset.arctosRouterReady === 'true'")

        async def failed_css(route):
            await route.abort()

        await page.route("**/assets/**.css", failed_css)
        STEP = "stylesheet-failure-restores-public-page"
        login = page.locator("#public-document .app-login")
        target = await login.get_attribute("href")
        await login.click()
        await page.locator("#main").wait_for(state="detached")
        recovery = page.locator("#public-app-status").get_by_role("link", name="Open page directly")
        await recovery.wait_for(state="visible")
        assert await recovery.get_attribute("href") == args.base_url + target
        assert urlsplit(page.url).path == args.event_path
        assert await page.title() == original_title
        assert await page.locator('#public-document #page-content[data-public-page="event"]').is_visible()
        assert await page.evaluate("document.querySelectorAll('#page-content').length") == 1
        STEP = "stylesheet-failure-reading-remains-usable"
        await reading_nav(page, "Leagues")
        await page.locator('main[data-public-page="leagues"]').wait_for(state="visible")
        await metadata_matches(page, args, "/leagues")
        await retained(page, observed)
        assert observed["errors"] == 0
        return {"failed_application_detached": True, "public_ids_restored": True, "reading_recovers": True}


async def native_links(browser, args):
    global STEP
    async with session(browser) as (page, observed, requested, _gate):
        STEP = "native-link-entry"
        await enter(page, args)
        await asyncio.wait_for(requested.wait(), 10)
        link = page.locator('#public-document nav[aria-label="Main navigation"]').get_by_role(
            "link", name="Leagues", exact=True
        )
        STEP = "modified-link-new-tab"
        async with page.context.expect_page() as opened:
            await link.click(modifiers=["Control"])
        new_page = await opened.value
        await new_page.wait_for_url(args.base_url + "/leagues")
        await new_page.close()
        assert urlsplit(page.url).path == "/"
        await retained(page, observed)
        STEP = "explicit-native-link"
        await link.evaluate("el => el.setAttribute('data-native-navigation', '')")
        await link.click()
        await page.wait_for_function("location.pathname === '/leagues' && window.__readingSentinel !== true")
        assert observed["documents"] == 2
        assert observed["writes"] == 0
        return {"modified_click_preserved": True, "native_opt_out_preserved": True}


async def main(args):
    if args.output.exists():
        raise ValueError("Choose a new report path; prior evidence is preserved.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cases = [
        ("held runtime reading/history/appearance", held_reading),
        ("failed runtime reading", failed_reading),
        ("latest reading intent", latest_reading),
        ("workflow stylesheet handoff", css_handoff),
        ("direct workflow keyboard focus", workflow_entry_focus),
        ("stylesheet failure preserves reading", stylesheet_failure),
        ("HTTP failure native recovery", lambda browser, args: incompatible_reading(browser, args, mismatch=False)),
        ("manifest mismatch native recovery", lambda browser, args: incompatible_reading(browser, args, mismatch=True)),
        ("modified and native links", native_links),
    ]
    rows = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=str(args.browser_executable) if args.browser_executable else None, headless=True
        )
        try:
            for name, case in cases:
                try:
                    result = {"passed": True, **await case(browser, args)}
                except Exception as error:
                    result = {"passed": False, "failure": type(error).__name__, "step": STEP}
                rows.append({"case": name, **result})
                args.output.write_text(
                    json.dumps(
                        {
                            "complete": len(rows) == len(cases),
                            "passed": len(rows) == len(cases) and all(row["passed"] for row in rows),
                            "scope": "Loopback-only functional reading/handoff checks; request delay/failure injection; writes blocked.",
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
