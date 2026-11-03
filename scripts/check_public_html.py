"""Measure public HTML and application entry from an external, local browser.

    uv run python scripts/check_public_html.py --public-only --runs 1 \
        --profile unthrottled --browser-executable /usr/bin/google-chrome

This standalone tool injects performance observers into its own Playwright
pages and writes local JSON reports. It visits anonymous pages, submits GET
searches, and follows reading and navigation links. Run it against a seeded
demo served by Flask. Remote staging requires both an explicit --base-url and
--allow-remote. Screenshots are opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import statistics
import sys
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, CDPSession, Page, async_playwright


@dataclass(frozen=True)
class Target:
    label: str
    path: str
    selectors: tuple[str, ...]


DIRECTORY_SELECTORS = (
    'main[data-public-page="directory"] h1',
    'main[data-public-page="directory"] form input[name="q"]',
    'main[data-public-page="directory"] .event-card',
)
EVENT_SELECTORS = (
    'main[data-public-page="event"] h1',
    'main[data-public-page="event"] time',
    "[data-event-location]",
    "[data-event-about]",
)
LEAGUES_SELECTORS = (
    'main[data-public-page="leagues"] h1',
    'main[data-public-page="leagues"] input[name="q"]',
    'main[data-public-page="leagues"] .league-card',
)
LEAGUE_SELECTORS = (
    'main[data-public-page="league"] h1',
    'main[data-public-page="league"] [data-event-about]',
    'main[data-public-page="league"] .event-registration-card',
)
APP_SELECTORS = ("#username", "#password", 'form:has(#username) button[type="submit"]')
PROFILES = {
    "unthrottled": {"latency_ms": 0, "download_bytes_s": -1, "upload_bytes_s": -1, "cpu_rate": 1},
    "fast-mobile": {
        "latency_ms": 40,
        "download_bytes_s": 10_000_000 / 8,
        "upload_bytes_s": 5_000_000 / 8,
        "cpu_rate": 2,
    },
    "mobile-4g": {
        "latency_ms": 150,
        "download_bytes_s": 1_600_000 / 8,
        "upload_bytes_s": 750_000 / 8,
        "cpu_rate": 4,
    },
    "constrained-mobile": {
        "latency_ms": 300,
        "download_bytes_s": 750_000 / 8,
        "upload_bytes_s": 250_000 / 8,
        "cpu_rate": 6,
    },
}


def validate_base_url(value: str, allow_remote: bool = False) -> str:
    """Reject credentials/ambiguous origins; localhost is the safe default."""
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("Use an HTTP(S) origin without credentials.")
    if url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("The base URL must be an origin; put deployment prefixes in each route path.")
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = url.hostname.lower() == "localhost"
    if not loopback and not allow_remote:
        raise ValueError("Non-loopback targets require an explicit --base-url and --allow-remote.")
    # Accessing port also validates malformed/non-numeric ports.
    _ = url.port
    return value.rstrip("/")


def validate_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("Route paths must be same-origin absolute paths.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Route paths must not contain control characters.")
    if urlsplit(value).fragment:
        raise ValueError("Use real document routes, not a hash-routed design prototype.")
    return value


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5006")
    parser.add_argument("--allow-remote", action="store_true", help="Explicitly allow an anonymous staging origin.")
    parser.add_argument("--directory-path", default="/")
    parser.add_argument("--event-path", default="/demo-open")
    parser.add_argument("--leagues-path", default="/leagues")
    parser.add_argument("--league-path", help="Include the Leagues directory and this published league Info page.")
    parser.add_argument("--app-path", default="/login?next=/demo-open/register")
    parser.add_argument(
        "--directory-ready-selector", action="append", help="Replace defaults; repeat for ALL requirements."
    )
    parser.add_argument(
        "--event-ready-selector", action="append", help="Replace defaults; repeat for ALL requirements."
    )
    parser.add_argument(
        "--app-ready-selector", action="append", help="Replace defaults; require usable workflow controls."
    )
    parser.add_argument("--runs", type=int, default=5, help="Cold/warm pairs per page (default: 5).")
    parser.add_argument("--profile", choices=PROFILES, default="mobile-4g")
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--timeout-ms", type=int, default=45_000)
    parser.add_argument(
        "--settle-ms", type=int, default=1_000, help="Observation window after readable/usable criteria."
    )
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/tmp/arctos-public-html"))
    parser.add_argument(
        "--screenshots", action="store_true", help="Save page images; these can contain displayed data."
    )
    parser.add_argument(
        "--public-only", action="store_true", help="Skip app readiness/failure checks before a Wasm build."
    )
    parser.add_argument("--skip-resilience", action="store_true", help="Measure only; report resilience as untested.")
    args = parser.parse_args(argv)
    try:
        args.base_url = validate_base_url(args.base_url, args.allow_remote)
        for field in ("directory_path", "event_path", "app_path", "leagues_path"):
            setattr(args, field, validate_path(getattr(args, field)))
        if args.league_path:
            args.league_path = validate_path(args.league_path)
        if not 1 <= args.runs <= 100:
            raise ValueError("--runs must be between 1 and 100.")
        if min(args.width, args.height) < 240 or not 1 <= args.timeout_ms < 60_000 or not 0 <= args.settle_ms < 60_000:
            raise ValueError("Use a viewport of at least 240px and timeout/settle windows below 60 seconds.")
    except ValueError as error:
        parser.error(str(error))
    return args


def targets(args: argparse.Namespace) -> list[Target]:
    result = [
        Target("directory", args.directory_path, tuple(args.directory_ready_selector or DIRECTORY_SELECTORS)),
        Target("event", args.event_path, tuple(args.event_ready_selector or EVENT_SELECTORS)),
    ]
    if args.league_path:
        result.extend(
            [
                Target("leagues", args.leagues_path, LEAGUES_SELECTORS),
                Target("league", args.league_path, LEAGUE_SELECTORS),
            ]
        )
    if not args.public_only:
        result.append(Target("app", args.app_path, tuple(args.app_ready_selector or APP_SELECTORS)))
    return result


def probe_script(selectors: tuple[str, ...], *, require_boot_hidden: bool = False) -> str:
    """Only Playwright injects this; no script or endpoint ships to users."""
    return """(() => {
        const required = SELECTORS;
        const requireBootHidden = REQUIRE_BOOT_HIDDEN;
        const audit = window.__arctosLocalAudit = {
            ready_ms: null, lcp_ms: null, fcp_ms: null, background_router_ready_ms: null
        };
        const routerObserver = new MutationObserver(() => {
            if (document.documentElement?.dataset.arctosRouterReady === 'true') {
                audit.background_router_ready_ms = performance.now();
                routerObserver.disconnect();
            }
        });
        routerObserver.observe(document, {subtree: true, attributes: true, attributeFilter: ['data-arctos-router-ready']});
        const visible = (element) => {
            if (!element || element.closest('[hidden], [inert]') || element.matches(':disabled')
                || element.getAttribute('aria-disabled') === 'true') return false;
            const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                && (element.matches('input,select,textarea') || element.textContent.trim().length > 0);
        };
        try { new PerformanceObserver(list => {
            for (const entry of list.getEntries()) audit.lcp_ms = entry.startTime;
        }).observe({type: 'largest-contentful-paint', buffered: true}); } catch (_) {}
        try { new PerformanceObserver(list => {
            for (const entry of list.getEntries())
                if (entry.name === 'first-contentful-paint') audit.fcp_ms = entry.startTime;
        }).observe({type: 'paint', buffered: true}); } catch (_) {}
        const isReady = () => {
            const boot = document.getElementById('app-boot');
            return required.every(selector => visible(document.querySelector(selector)))
                && (!requireBootHidden || !boot || boot.hidden);
        };
        const check = () => {
            if (isReady()) {
                // Two more frame opportunities avoid calling pre-paint DOM
                // availability "readable" before the browser's first paint.
                requestAnimationFrame(() => requestAnimationFrame(() => {
                    if (isReady())
                        audit.ready_ms = performance.now();
                    else requestAnimationFrame(check);
                }));
            } else requestAnimationFrame(check);
        };
        requestAnimationFrame(check);
    })();""".replace("SELECTORS", json.dumps(selectors)).replace("REQUIRE_BOOT_HIDDEN", json.dumps(require_boot_hidden))


METRICS_SCRIPT = """() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const wasmStarts = performance.getEntriesByType('resource')
        .filter(entry => new URL(entry.name).pathname.endsWith('.wasm')).map(entry => entry.startTime);
    return {
        ready_ms: window.__arctosLocalAudit?.ready_ms != null
            ? Math.max(window.__arctosLocalAudit.ready_ms, window.__arctosLocalAudit.fcp_ms ?? 0) : null,
        content_layout_ready_ms: window.__arctosLocalAudit?.ready_ms ?? null,
        fcp_ms: window.__arctosLocalAudit?.fcp_ms ?? null,
        lcp_observed_ms: window.__arctosLocalAudit?.lcp_ms ?? null,
        public_background_enabled: document.documentElement.dataset.publicBackground === 'true',
        background_router_ready_ms: window.__arctosLocalAudit?.background_router_ready_ms ?? null,
        first_wasm_resource_start_ms: wasmStarts.length ? Math.min(...wasmStarts) : null,
        observation_end_ms: performance.now(),
        navigation: nav ? {
            ttfb_ms: nav.responseStart - nav.startTime,
            response_download_ms: nav.responseEnd - nav.responseStart,
            dom_content_loaded_ms: nav.domContentLoadedEventEnd || null,
            load_ms: nav.loadEventEnd || null,
            transfer_bytes: nav.transferSize,
            encoded_body_bytes: nav.encodedBodySize,
            decoded_body_bytes: nav.decodedBodySize,
        } : null,
        horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        viewport: {layout_width: document.documentElement.clientWidth, document_width: document.documentElement.scrollWidth},
    };
}"""


class NetworkObservation:
    """Collect aggregate transport evidence without storing URLs or bodies."""

    def __init__(self, *, allow_anonymous_account_probe: bool = False) -> None:
        self.allow_anonymous_account_probe = allow_anonymous_account_probe
        self.requests: dict[str, dict] = {}
        self.js_errors = 0
        self.navigation_start: float | None = None

    def request(self, event: dict) -> None:
        request = event["request"]
        path = urlsplit(request["url"]).path.lower()
        self.requests[event["requestId"]] = {
            "kind": "wasm" if path.endswith(".wasm") else event.get("type", "Other").lower(),
            "method": request["method"],
            # This existing API renders supplied text; it persists no data.
            "read_only_markdown": request["method"] == "POST" and path.endswith("/_api/render-markdown"),
            "timestamp": event.get("timestamp"),
            "leagues_list": request["method"] == "GET" and path.rstrip("/").endswith("/_api/leagues"),
            "account_probe": request["method"] == "GET" and path.endswith("/_api/me"),
            "status": None,
            "encoded_bytes": 0,
            "failed": False,
            "canceled": False,
        }

    def response(self, event: dict) -> None:
        row = self.requests.get(event["requestId"])
        if row is None:
            return
        response = event["response"]
        headers = {key.lower(): value for key, value in response.get("headers", {}).items()}
        encoding = headers.get("content-encoding", "identity").lower()
        mime = response.get("mimeType", "").lower()
        if mime == "application/wasm":
            row["kind"] = "wasm"
        row.update(
            status=response["status"],
            disk_cache=bool(response.get("fromDiskCache")),
            service_worker=bool(response.get("fromServiceWorker")),
            encoding=encoding if encoding in ("gzip", "br", "deflate", "zstd", "identity") else "other",
            # Keep numeric timing only, never response headers or a resource URL.
            response_headers_ms=response.get("timing", {}).get("receiveHeadersEnd"),
        )

    def finished(self, event: dict) -> None:
        if row := self.requests.get(event["requestId"]):
            row["encoded_bytes"] = event.get("encodedDataLength", 0)

    def failed(self, event: dict) -> None:
        if row := self.requests.get(event["requestId"]):
            row["failed"] = True
            row["canceled"] = bool(event.get("canceled"))

    def summary(self) -> dict:
        def expected_account_probe(row: dict) -> bool:
            return self.allow_anonymous_account_probe and row["account_probe"] and row["status"] == 401

        def read_request(row: dict) -> bool:
            return row["method"] in ("GET", "HEAD", "OPTIONS") or row["read_only_markdown"]

        wasm_starts = [
            (row["timestamp"] - self.navigation_start) * 1000
            for row in self.requests.values()
            if row["kind"] == "wasm" and row["timestamp"] is not None and self.navigation_start is not None
        ]
        groups: dict[str, dict] = defaultdict(
            lambda: {"requests": 0, "encoded_bytes": 0, "failed": 0, "http_errors": 0, "disk_cache": 0, "encodings": {}}
        )
        for request in self.requests.values():
            group = groups[request["kind"]]
            group["requests"] += 1
            group["encoded_bytes"] += request["encoded_bytes"]
            group["failed"] += int(request["failed"])
            group["http_errors"] += int((request["status"] or 0) >= 400)
            group["disk_cache"] += int(request.get("disk_cache", False))
            encoding = request.get("encoding")
            if encoding:
                group["encodings"][encoding] = group["encodings"].get(encoding, 0) + 1
        return {
            "encoded_bytes": sum(row["encoded_bytes"] for row in self.requests.values()),
            "requests": len(self.requests),
            "failed_requests": sum(row["failed"] for row in self.requests.values()),
            "canceled_read_requests": sum(row["canceled"] and read_request(row) for row in self.requests.values()),
            "canceled_markdown_requests": sum(
                row["canceled"] and row["read_only_markdown"] for row in self.requests.values()
            ),
            "unexpected_failed_requests": sum(
                row["failed"]
                and not expected_account_probe(row)
                and not (row["canceled"] and row["read_only_markdown"])
                for row in self.requests.values()
            ),
            "http_errors": sum((row["status"] or 0) >= 400 for row in self.requests.values()),
            "expected_anonymous_401": sum(expected_account_probe(row) for row in self.requests.values()),
            "unexpected_http_errors": sum(
                (row["status"] or 0) >= 400 and not expected_account_probe(row) for row in self.requests.values()
            ),
            "non_read_requests": sum(not read_request(row) for row in self.requests.values()),
            "read_only_markdown_requests": sum(row["read_only_markdown"] for row in self.requests.values()),
            "wasm_requests": sum(row["kind"] == "wasm" for row in self.requests.values()),
            "first_wasm_request_ms": min(wasm_starts) if wasm_starts else None,
            "leagues_list_requests": sum(row["leagues_list"] for row in self.requests.values()),
            "javascript_errors": self.js_errors,
            "by_kind": dict(groups),
        }


async def new_context(browser: Browser, args: argparse.Namespace, *, javascript: bool = True) -> BrowserContext:
    return await browser.new_context(
        viewport={"width": args.width, "height": args.height},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
        java_script_enabled=javascript,
        reduced_motion="reduce",
        service_workers="block",
        locale="en-US",
        timezone_id="America/Los_Angeles",
    )


async def cdp_observer(
    context: BrowserContext, page: Page, args: argparse.Namespace, target: Target
) -> tuple[NetworkObservation, CDPSession]:
    cdp = await context.new_cdp_session(page)
    observation = NetworkObservation(allow_anonymous_account_probe=target.label == "app")
    await cdp.send("Network.enable")
    await cdp.send("Performance.enable")
    profile = PROFILES[args.profile]
    await cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": profile["latency_ms"],
            "downloadThroughput": profile["download_bytes_s"],
            "uploadThroughput": profile["upload_bytes_s"],
        },
    )
    await cdp.send("Emulation.setCPUThrottlingRate", {"rate": profile["cpu_rate"]})
    cdp.on("Network.requestWillBeSent", observation.request)
    cdp.on("Network.responseReceived", observation.response)
    cdp.on("Network.loadingFinished", observation.finished)
    cdp.on("Network.loadingFailed", observation.failed)
    page.on("pageerror", lambda _error: setattr(observation, "js_errors", observation.js_errors + 1))
    return observation, cdp


def background_start_check(sample: dict) -> dict:
    """Compare two browser clocks with the same CDP navigation origin.

    The content probe deliberately waits extra animation frames. Comparing a
    request with that probe would incorrectly fail a valid post-paint startup.
    FCP is the scheduling guard; blocked/delayed tests prove useful HTML does
    not depend on Wasm. Paint Timing rounds a non-isolated document's
    presentation timestamp up to the next 4ms boundary. Keep that uncertainty
    distinct from a request observed after the reported paint timestamp.
    https://www.w3.org/TR/paint-timing/#mark-paint-timing
    """
    if not sample.get("public_background_enabled"):
        return {"status": "not-enabled", "passed": sample["network"]["wasm_requests"] == 0}
    started = sample["network"].get("first_wasm_request_ms")
    painted = sample.get("fcp_ms")
    if started is None:
        requested = sample["network"]["wasm_requests"] > 0
        return {"status": "timing-unavailable" if requested else "not-observed", "passed": not requested}
    if painted is None:
        return {"status": "paint-unavailable", "passed": False}
    delta = started - painted
    status = "after-paint" if delta >= 0 else "within-paint-precision" if delta >= -4 else "before-paint"
    return {"status": status, "delta_ms": delta, "passed": delta >= -4}


async def screenshot(page: Page, args: argparse.Namespace, filename: str) -> None:
    if args.screenshots:
        await page.screenshot(path=str(args.output / f"{filename}.png"), full_page=True)


async def measure_pair(browser: Browser, args: argparse.Namespace, target: Target, run: int) -> list[dict]:
    context = await new_context(browser, args)
    context.set_default_timeout(args.timeout_ms)
    await context.add_init_script(script=probe_script(target.selectors, require_boot_hidden=target.label == "app"))
    samples = []
    try:
        # Do not add request routing here: Playwright routing disables HTTP
        # cache and would turn the purported warm sample into a cold sample.
        for cache in ("cold", "warm"):
            page = await context.new_page()
            network, cdp = await cdp_observer(context, page, args, target)
            sample = {"page": target.label, "run": run, "cache": cache, "passed": False}
            try:
                response = await page.goto(args.base_url + target.path, wait_until="domcontentloaded")
                sample["document_status"] = response.status if response else None
                await page.wait_for_function("window.__arctosLocalAudit?.ready_ms != null")
                await page.wait_for_timeout(args.settle_ms)
                sample.update(await page.evaluate(METRICS_SCRIPT))
                metrics = await cdp.send("Performance.getMetrics")
                network.navigation_start = next(
                    (metric["value"] for metric in metrics["metrics"] if metric["name"] == "NavigationStart"), None
                )
                network.allow_anonymous_account_probe = target.label == "app" or sample["public_background_enabled"]
                sample["network"] = network.summary()
                if target.label != "app":
                    sample["background_start"] = background_start_check(sample)
                sample["passed"] = (
                    sample["document_status"] == 200
                    and not sample["horizontal_overflow"]
                    and (target.label == "app" or sample["background_start"]["passed"])
                    and sample["network"]["unexpected_failed_requests"] == 0
                    and sample["network"]["unexpected_http_errors"] == 0
                    and sample["network"]["javascript_errors"] == 0
                    and sample["network"]["non_read_requests"] == 0
                )
                await screenshot(page, args, f"{target.label}-{cache}-{run}")
            except Exception as error:
                # Playwright exceptions include URL/page content; report only
                # the error class and stage in the shareable JSON artifact.
                sample["failure"] = type(error).__name__
                sample["network"] = network.summary()
                await screenshot(page, args, f"{target.label}-{cache}-{run}-failed")
            finally:
                await page.close()
            samples.append(sample)
    finally:
        await context.close()
    return samples


async def require_content(page: Page, target: Target) -> None:
    for selector in target.selectors:
        await page.locator(selector).first.wait_for(state="visible")
        element = page.locator(selector).first
        if not await element.evaluate("el => el.matches('input, select, textarea')"):
            if not (await element.inner_text()).strip():
                raise AssertionError("Required content is empty")


async def require_leagues(page: Page) -> None:
    await page.locator("#main #league-search").wait_for(state="visible")
    # A loading message or error is not a successful league directory. A
    # genuine empty result is useful and must also work against staging.
    await page.locator("#main .arctos-league-grid, #main .arctos-empty-state:has(h3)").first.wait_for(state="visible")


async def retained_application_check(page: Page, network: NetworkObservation) -> dict:
    """Use real links; a marker set only in this document catches reloads."""
    await page.evaluate("window.__arctosLocalDocumentIdentity = {}")
    before = network.summary()
    started = await page.evaluate("performance.now()")
    await (
        page.get_by_role("navigation", name="Main navigation", exact=True)
        .get_by_role("link", name="Leagues", exact=True)
        .click()
    )
    await require_leagues(page)
    first_ready_ms = await page.evaluate("performance.now()") - started
    first = network.summary()
    if first["leagues_list_requests"] != 1:
        raise AssertionError("Initial league directory must issue one list request")
    return_ready_ms = []
    for _ in range(2):
        navigation = page.get_by_role("navigation", name="Main navigation", exact=True)
        await navigation.get_by_role("link", name="Teams", exact=True).click()
        await page.locator("#main #team-search").wait_for(state="visible")
        await page.locator("#main .arctos-results-count").wait_for(state="visible")
        started = await page.evaluate("performance.now()")
        await navigation.get_by_role("link", name="Leagues", exact=True).click()
        await require_leagues(page)
        return_ready_ms.append(await page.evaluate("performance.now()") - started)
        # Give effects/fetches a scheduling opportunity after the cached render.
        await page.wait_for_timeout(150)
        after = network.summary()
        if not await page.evaluate("Boolean(window.__arctosLocalDocumentIdentity)"):
            raise AssertionError("Interactive navigation replaced the document")
        if after["by_kind"].get("document", {}).get("requests", 0) != before["by_kind"].get("document", {}).get(
            "requests", 0
        ):
            raise AssertionError("Interactive navigation requested another document")
        if after["wasm_requests"] != before["wasm_requests"]:
            raise AssertionError("Interactive navigation requested Wasm again")
        if after["leagues_list_requests"] != first["leagues_list_requests"]:
            raise AssertionError("Returning to leagues bypassed its current cached result")
        if after["non_read_requests"]:
            raise AssertionError("Read-only journey unexpectedly attempted a write")
    return {
        "same_document": True,
        "return_visits": 2,
        "first_leagues_ready_ms": first_ready_ms,
        "return_leagues_ready_ms": return_ready_ms,
        "wasm_requests": after["wasm_requests"],
        "leagues_list_requests": after["leagues_list_requests"],
        "document_requests": after["by_kind"].get("document", {}).get("requests", 0),
    }


async def resilience(browser: Browser, args: argparse.Namespace, selected: list[Target]) -> list[dict]:
    checks = []
    for mode in ("no-javascript", "blocked-wasm", "delayed-wasm"):
        for target in selected:
            print(f"Checking {target.label}: {mode} resilience", flush=True)
            context = await new_context(browser, args, javascript=mode != "no-javascript")
            context.set_default_timeout(args.timeout_ms)
            intercepted = []
            wasm_requested = asyncio.Event()
            release = asyncio.Event()

            async def block_wasm(route):
                path = urlsplit(route.request.url).path.lower()
                if path.endswith(".wasm"):
                    intercepted.append(True)
                    wasm_requested.set()
                    if mode == "blocked-wasm":
                        await route.abort("failed")
                    else:
                        await release.wait()
                        await route.continue_()
                else:
                    await route.continue_()

            if mode in ("blocked-wasm", "delayed-wasm"):
                await context.route("**/*", block_wasm)
            page = await context.new_page()
            # Fault checks run without synthetic network/CPU throttling; only
            # the intentionally intercepted Wasm is blocked or delayed.
            network = NetworkObservation(allow_anonymous_account_probe=True)
            cdp = await context.new_cdp_session(page)
            await cdp.send("Network.enable")
            cdp.on("Network.requestWillBeSent", network.request)
            cdp.on("Network.responseReceived", network.response)
            cdp.on("Network.loadingFinished", network.finished)
            cdp.on("Network.loadingFailed", network.failed)
            row = {"check": mode, "page": target.label, "passed": False}
            stage = "document"
            try:
                response = await page.goto(args.base_url + target.path, wait_until="commit")
                if response is None or response.status != 200:
                    raise AssertionError("Expected a successful document")
                if target.label != "app":
                    stage = "public-content"
                    await require_content(page, target)
                    background = await page.locator("html").get_attribute("data-public-background") == "true"
                    row["public_background_enabled"] = background
                    if mode != "no-javascript" and background:
                        stage = "intercept-background-wasm"
                        await asyncio.wait_for(wasm_requested.wait(), args.timeout_ms / 1000)
                        # Prove actual failure/delay, not a snapshot taken before
                        # the browser has attempted the background app request.
                        await page.wait_for_timeout(200)
                        await require_content(page, target)
                        if await page.locator("#main #app-layout").is_visible():
                            raise AssertionError("Background startup replaced readable public content")
                        row["readable_while_wasm_unavailable"] = True
                        if mode == "delayed-wasm" and not args.public_only:
                            stage = "background-router-ready"
                            release.set()
                            await page.wait_for_function(
                                "document.documentElement.dataset.arctosRouterReady === 'true'"
                            )
                            await require_content(page, target)
                            stage = "retained-application-navigation"
                            row["retained_application"] = await retained_application_check(page, network)
                    elif intercepted:
                        raise AssertionError("Public page loaded Wasm without opting into background startup")
                    if mode == "no-javascript" and target.label in {"directory", "leagues"}:
                        stage = "native-public-navigation"
                        search = page.locator('main form input[name="q"]')
                        await search.fill("A&B")
                        await page.locator('main form button[type="submit"]').click()
                        await page.locator(f'main[data-public-page="{target.label}"]').wait_for(state="visible")
                        if await search.input_value() != "A&B":
                            raise AssertionError("Native search lost the literal ampersand")
                        await page.go_back(wait_until="domcontentloaded")
                        await require_content(page, target)
                        card = "event" if target.label == "directory" else "league"
                        await page.locator(f".{card}-card h2 a").first.click()
                        await page.locator(f'main[data-public-page="{card}"] h1').wait_for(state="visible")
                        await page.go_back(wait_until="domcontentloaded")
                        await require_content(page, target)
                else:
                    stage = "app-recovery"
                    await page.locator("#app-boot").wait_for(state="visible")
                    await page.locator("#app-boot a[data-public-return]").first.wait_for(state="visible")
                    if mode == "no-javascript":
                        await page.locator("#app-boot noscript").wait_for(state="visible")
                    elif mode == "blocked-wasm":
                        await page.locator('#app-boot[data-boot-state="failed"]').wait_for(state="visible")
                        if not intercepted:
                            raise AssertionError("Failure test did not actually block a Wasm request")
                        retry = page.locator("#app-boot button, #app-boot a").filter(has_text="Retry").first
                        await retry.wait_for(state="visible")
                        if not await retry.is_enabled():
                            raise AssertionError("Retry is disabled")
                        await context.unroute("**/*", block_wasm)
                        await retry.click()
                        await require_content(page, target)
                        await page.locator("#app-boot").wait_for(state="hidden")
                    else:
                        await page.locator('#app-boot[data-boot-state="slow"]').wait_for(state="visible")
                        if not intercepted:
                            raise AssertionError("Slow test did not actually delay a Wasm request")
                        release.set()
                        await require_content(page, target)
                        await page.locator("#app-boot").wait_for(state="hidden")
                row["passed"] = True
                await screenshot(page, args, f"{target.label}-{mode}")
            except Exception as error:
                row["failure"] = type(error).__name__
                row["failure_stage"] = stage
                await screenshot(page, args, f"{target.label}-{mode}-failed")
            finally:
                release.set()
                await context.unroute_all(behavior="wait")
                await page.close()
                await context.close()
            checks.append(row)
    return checks


def summarize(samples: list[dict]) -> list[dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for sample in samples:
        groups[(sample["page"], sample["cache"])].append(sample)
    summary = []
    for (page, cache), rows in sorted(groups.items()):
        row = {"page": page, "cache": cache, "samples": len(rows), "passed": sum(item["passed"] for item in rows)}
        for metric in ("ready_ms", "fcp_ms", "lcp_observed_ms"):
            values = [item[metric] for item in rows if item.get(metric) is not None and item["passed"]]
            row[metric] = (
                {"median": statistics.median(values), "min": min(values), "max": max(values), "samples": len(values)}
                if values
                else None
            )
        summary.append(row)
    return summary


async def run(args: argparse.Namespace) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    selected = targets(args)
    report = {
        "schema_version": 2,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "anonymous synthetic lab checks; no field telemetry",
        "profile": {"name": args.profile, **PROFILES[args.profile]},
        "viewport": {"width": args.width, "height": args.height, "device_scale_factor": 2},
        "observation_after_ready_ms": args.settle_ms,
        "samples": [],
        "checks": [],
        "resilience_tested": not args.skip_resilience,
        "app_tested": not args.public_only,
        "screenshots_contain_displayed_content": args.screenshots,
    }
    async with async_playwright() as playwright:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        report["browser_version"] = browser.version
        try:
            for target in selected:
                for run_number in range(1, args.runs + 1):
                    print(f"Checking {target.label}: cold/warm pair {run_number}/{args.runs}", flush=True)
                    report["samples"].extend(await measure_pair(browser, args, target, run_number))
            if not args.skip_resilience:
                report["checks"] = await resilience(browser, args, selected)
        finally:
            await browser.close()
    report["summary"] = summarize(report["samples"])
    report["passed"] = all(row["passed"] for row in report["samples"] + report["checks"])
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
    print(f"{'PASS' if report['passed'] else 'FAIL'}: local report at {args.output / 'report.json'}")
    return int(not report["passed"])


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments())))
