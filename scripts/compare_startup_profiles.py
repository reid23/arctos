"""Compare startup variants using a standalone browser measurement tool.

    uv run python scripts/compare_startup_profiles.py \
        --browser-executable /usr/bin/google-chrome --runs 3

Run against frozen local servers with identical snapshots, backend APIs, and
asset/compression policy, without concurrent builds or browser tests. Default
origins: original=5017 and public-html=5016. The tool follows
anonymous directory links and records numeric measurements in local reports.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright

from scripts.check_public_html import (
    METRICS_SCRIPT,
    PROFILES,
    Target,
    background_start_check,
    cdp_observer,
    new_context,
    probe_script,
    validate_base_url,
    validate_path,
)

DEFAULT_ORIGINS = {
    "original": "http://127.0.0.1:5017",
    "public-html": "http://127.0.0.1:5016",
}
EVENTS_SELECTORS = ("#event-search", ".arctos-event-card .arctos-card-title", ".arctos-meta-list")
LEAGUES_SELECTORS = ("#league-search", ".arctos-league-grid .arctos-card-title", ".arctos-meta-list")
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Variant:
    name: str
    origin: str


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", action="append", metavar="NAME=ORIGIN", help="Repeat to select/override variants.")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--profile", action="append", choices=PROFILES, help="Repeat; default is all four profiles.")
    parser.add_argument("--runs", type=int, default=3, help="Independent cold/warm pairs per variant/profile.")
    parser.add_argument("--events-path", default="/")
    parser.add_argument("--leagues-path", default="/leagues")
    routes = parser.add_mutually_exclusive_group()
    routes.add_argument("--include-direct-leagues", action="store_true", help="Additional direct-load pairs; slower.")
    routes.add_argument(
        "--direct-leagues-only", action="store_true", help="Only direct Leagues pairs, without Events or journeys."
    )
    parser.add_argument("--skip-journey", action="store_true", help="Explicitly omit first/return navigation checks.")
    parser.add_argument("--continue-on-failure", action="store_true", help="Default stops at the first failed sample.")
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--timeout-ms", type=int, default=55_000)
    parser.add_argument("--settle-ms", type=int, default=250)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        raw_variants = args.variant or [f"{name}={origin}" for name, origin in DEFAULT_ORIGINS.items()]
        args.variants = []
        for item in raw_variants:
            name, separator, origin = item.partition("=")
            if not separator or name not in DEFAULT_ORIGINS:
                raise ValueError("Variant must be original=ORIGIN or public-html=ORIGIN.")
            args.variants.append(Variant(name, validate_base_url(origin, args.allow_remote)))
        if len({variant.name for variant in args.variants}) != len(args.variants):
            raise ValueError("Variant names must be unique.")
        if len({variant.origin for variant in args.variants}) != len(args.variants):
            raise ValueError("Distinct variants must use distinct origins.")
        args.profiles = list(dict.fromkeys(args.profile or PROFILES))
        args.targets = (
            ["leagues"]
            if args.direct_leagues_only
            else (["events", "leagues"] if args.include_direct_leagues else ["events"])
        )
        args.events_path = validate_path(args.events_path)
        args.leagues_path = validate_path(args.leagues_path)
        if any(urlsplit(path).query for path in (args.events_path, args.leagues_path)):
            raise ValueError("Use canonical Events and Leagues paths without query parameters for header navigation.")
        if not 1 <= args.runs <= 20:
            raise ValueError("--runs must be between 1 and 20.")
        if min(args.width, args.height) < 240 or not 1 <= args.timeout_ms < 60_000 or not 0 <= args.settle_ms < 60_000:
            raise ValueError("Use a viewport of at least 240px and timeout/settle windows below 60 seconds.")
    except ValueError as error:
        parser.error(str(error))
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = ROOT / "preview" / f"startup-profiles-{stamp}"
    return args


def variant_order(variants: list[Variant], run: int) -> list[Variant]:
    """Rotate the first measured variant to reduce fixed-order bias."""
    offset = (run - 1) % len(variants)
    return variants[offset:] + variants[:offset]


def startup_probe(selectors: tuple[str, ...]) -> str:
    return (
        probe_script(selectors, require_boot_hidden=True)
        + """
    (() => {
        const audit = window.__arctosStartupAudit = {
            feedback_ms: null, feedback_kind: null, layout_mounted_ms: null,
            loading_shell_seen: false, public_content_seen: false
        };
        const visible = element => {
            if (!element || element.closest('[hidden],[inert]')) return false;
            const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
                && style.display !== 'none' && element.textContent.trim().length > 0;
        };
        const inspect = () => {
            if (document.getElementById('app-boot')) audit.loading_shell_seen = true;
            if (document.querySelector('main[data-public-page="directory"], main[data-public-page="leagues"]')) audit.public_content_seen = true;
            if (audit.layout_mounted_ms === null && document.querySelector('#main #app-layout'))
                audit.layout_mounted_ms = performance.now();
        };
        const observer = new MutationObserver(inspect);
        observer.observe(document, {childList: true, subtree: true});
        const checkFeedback = () => {
            inspect();
            const loading = document.querySelector('#app-boot [data-boot-message]');
            const heading = document.getElementById('page-banner-heading');
            const element = visible(loading) ? loading : heading;
            if (visible(element)) requestAnimationFrame(() => requestAnimationFrame(() => {
                if (visible(element)) {
                    audit.feedback_ms = performance.now();
                    audit.feedback_kind = element === loading ? 'loading-status' : 'page-heading';
                } else requestAnimationFrame(checkFeedback);
            }));
            else requestAnimationFrame(checkFeedback);
        };
        requestAnimationFrame(checkFeedback);
    })();"""
    )


STARTUP_METRICS = """() => {
    const audit = window.__arctosStartupAudit;
    const bridge = window.__arctosLocalAudit?.background_router_ready_ms;
    return {
        visible_feedback_ms: audit?.feedback_ms == null ? null
            : Math.max(audit.feedback_ms, window.__arctosLocalAudit?.fcp_ms ?? 0),
        feedback_kind: audit?.feedback_kind ?? null,
        loading_shell_seen: audit?.loading_shell_seen ?? false,
        public_content_seen: audit?.public_content_seen ?? false,
        runtime_warm_ms: bridge ?? audit?.layout_mounted_ms ?? null,
        runtime_evidence: bridge != null ? 'navigation-bridge' :
            audit?.layout_mounted_ms != null ? 'mounted-app-layout' : null
    };
}"""


def healthy_network(network: dict) -> bool:
    return all(
        network[field] == 0
        for field in ("unexpected_failed_requests", "unexpected_http_errors", "javascript_errors", "non_read_requests")
    )


def startup_passes(row: dict, variant: str, target: str) -> bool:
    network = row["network_after_startup"]
    usable = all(
        row.get(metric) is not None for metric in ("ready_ms", "runtime_warm_ms", "visible_feedback_ms", "fcp_ms")
    )
    if variant == "public-html":
        valid_variant = (
            row["public_background_enabled"]
            and row["public_content_seen"]
            and not row["loading_shell_seen"]
            and row["background_start"]["passed"]
        )
    else:
        valid_variant = not row["public_background_enabled"] and not row["loading_shell_seen"]
    return bool(
        usable
        and valid_variant
        and row["document_status"] == 200
        and not row["horizontal_overflow"]
        and network["wasm_requests"] == 1
        and network["by_kind"].get("document", {}).get("requests") == 1
        and (target != "leagues" or network["leagues_list_requests"] == (0 if variant == "public-html" else 1))
        and healthy_network(network)
    )


async def measure(page, context, args, variant: Variant, target: str):
    path, selectors = (
        (args.events_path, EVENTS_SELECTORS) if target == "events" else (args.leagues_path, LEAGUES_SELECTORS)
    )
    await page.add_init_script(startup_probe(selectors))
    network, cdp = await cdp_observer(context, page, args, Target("app", path, selectors))
    response = await page.goto(variant.origin + path, wait_until="domcontentloaded")
    await page.wait_for_function("window.__arctosLocalAudit?.ready_ms != null")
    metrics = await cdp.send("Performance.getMetrics")
    network.navigation_start = next(
        (metric["value"] for metric in metrics["metrics"] if metric["name"] == "NavigationStart"), None
    )
    row = await page.evaluate(METRICS_SCRIPT)
    row["network_at_useful_readiness"] = network.summary()
    if row["public_background_enabled"]:
        await page.wait_for_function("document.documentElement.dataset.arctosRouterReady === 'true'")
    await page.wait_for_function(
        "window.__arctosStartupAudit?.feedback_ms != null && "
        "(window.__arctosLocalAudit?.background_router_ready_ms != null || window.__arctosStartupAudit?.layout_mounted_ms != null)"
    )
    # Prime the next HTTP-warm sample after the initial runtime/resources finish;
    # this wait never changes the useful-readiness timestamp captured above.
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(args.settle_ms)
    row.update(await page.evaluate(METRICS_SCRIPT))
    row.update(await page.evaluate(STARTUP_METRICS))
    row["document_status"] = response.status if response else None
    row["network_after_startup"] = network.summary()
    if variant.name == "public-html":
        row["background_start"] = background_start_check({**row, "network": row["network_after_startup"]})
    row["passed"] = startup_passes(row, variant.name, target)
    return row, network


INTERACTION_PROBE = """({path, selectors}) => {
    const audit = window.__arctosInteractionAudit = {start_ms: null, ready_ms: null};
    const visible = selector => {
        const element = document.querySelector(selector);
        if (!element || element.closest('[hidden],[inert]')) return false;
        const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
            && style.display !== 'none' && (element.matches('input') || element.textContent.trim());
    };
    const ready = () => location.pathname === path && selectors.every(visible);
    const check = () => {
        if (ready()) requestAnimationFrame(() => requestAnimationFrame(() => {
            if (ready()) audit.ready_ms = performance.now(); else requestAnimationFrame(check);
        }));
        else requestAnimationFrame(check);
    };
    const clicked = event => {
        const anchor = event.target.closest?.('a[href]');
        if (!anchor || new URL(anchor.href).pathname !== path) return;
        audit.start_ms = performance.now();
        document.removeEventListener('click', clicked, true);
        requestAnimationFrame(check);
    };
    document.addEventListener('click', clicked, true);
}"""


async def navigate_ready(page, name: str, path: str, selectors: tuple[str, ...]) -> float:
    await page.evaluate(INTERACTION_PROBE, {"path": urlsplit(path).path, "selectors": selectors})
    await (
        page.get_by_role("navigation", name="Main navigation", exact=True)
        .get_by_role("link", name=name, exact=True)
        .click()
    )
    if not await page.evaluate("Boolean(window.__arctosComparisonDocument)"):
        raise AssertionError("Navigation replaced the document")
    await page.wait_for_function("window.__arctosInteractionAudit?.ready_ms != null")
    return await page.evaluate("window.__arctosInteractionAudit.ready_ms - window.__arctosInteractionAudit.start_ms")


def journey_passes(variant: str, identity_retained: bool, before: dict, first: dict, after: dict) -> bool:
    return bool(
        identity_retained
        and before["wasm_requests"] == after["wasm_requests"] == 1
        and before["by_kind"].get("document", {}).get("requests")
        == after["by_kind"].get("document", {}).get("requests")
        == 1
        and before["leagues_list_requests"] == 0
        and first["leagues_list_requests"] == 1
        and after["leagues_list_requests"] == (2 if variant == "original" else 1)
        and healthy_network(after)
    )


async def journey(page, args, variant: Variant, network) -> dict:
    await page.evaluate("window.__arctosComparisonDocument = {}")
    before = network.summary()
    first_ms = await navigate_ready(page, "Leagues", args.leagues_path, LEAGUES_SELECTORS)
    first = network.summary()
    await navigate_ready(page, "Events", args.events_path, EVENTS_SELECTORS)
    return_ms = await navigate_ready(page, "Leagues", args.leagues_path, LEAGUES_SELECTORS)
    await page.wait_for_timeout(args.settle_ms)
    after = network.summary()
    retained = await page.evaluate("Boolean(window.__arctosComparisonDocument)")
    return {
        "passed": journey_passes(variant.name, retained, before, first, after),
        "first_leagues_ready_ms": first_ms,
        "return_leagues_ready_ms": return_ms,
        "return_visits": 1,
        "document_identity_retained": retained,
        "leagues_cache_reused": first["leagues_list_requests"] == after["leagues_list_requests"] == 1,
        "network": after,
    }


METRICS = (
    "fcp_ms",
    "visible_feedback_ms",
    "ready_ms",
    "runtime_warm_ms",
    "first_leagues_ready_ms",
    "return_leagues_ready_ms",
)


def summarize(samples: list[dict]) -> list[dict]:
    summary = []
    groups = sorted({(row["profile"], row["variant"], row["target"], row["cache"]) for row in samples})
    for key in groups:
        rows = [row for row in samples if (row["profile"], row["variant"], row["target"], row["cache"]) == key]
        result = dict(zip(("profile", "variant", "target", "cache"), key))
        result.update(samples=len(rows), passed=sum(row["passed"] for row in rows))
        for metric in METRICS:
            values = [row.get(metric, row.get("journey", {}).get(metric)) for row in rows if row["passed"]]
            values = [value for value in values if value is not None]
            result[metric] = (
                {"median": statistics.median(values), "min": min(values), "max": max(values), "samples": len(values)}
                if values
                else None
            )
        summary.append(result)
    return summary


def markdown_summary(report: dict) -> str:
    def cell(metric):
        if metric is None:
            return "—"
        return f"{metric['median'] / 1000:.3f} [{metric['min'] / 1000:.3f}–{metric['max'] / 1000:.3f}]"

    lines = [
        "# Startup profile comparison",
        "",
        f"Status: {'PASS' if report['passed'] else 'INCOMPLETE / FAILED'}.",
        "",
        "Times are shown in seconds as median [minimum–maximum] for the recorded laboratory samples.",
        "",
        "FCP measures first contentful paint. Feedback records a visible loading status or page heading. Useful content requires search plus loaded event or league titles and metadata. Runtime readiness records the navigation bridge or, for the baseline application, the mounted layout. Each column measures a separate stage of startup.",
        "",
        "Journeys measure the click to painted league content within a running application, including one return through Events. Cold and warm samples each start a new document; warm samples reuse the browser's HTTP cache. Results contain numeric observations collected by the local test browser.",
        "",
    ]
    for profile in report["profiles"]:
        lines.extend(
            [
                f"## {profile}",
                "",
                "| Variant / target / cache | Pass | FCP | Feedback | Useful | Runtime | First Leagues | Return Leagues |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report["summary"]:
            if row["profile"] == profile:
                lines.append(
                    f"| {row['variant']} / {row['target']} / {row['cache']} | {row['passed']}/{row['samples']} | "
                    + " | ".join(cell(row[key]) for key in METRICS)
                    + " |"
                )
        lines.append("")
    lines.extend(
        [
            "## Collection scope",
            "",
            report["collection_scope"],
            "",
            "The network gate accepts the anonymous account check's expected 401 response and canceled read-only Markdown rendering. It reports all request counts and fails on other transport errors, HTTP errors, JavaScript errors or mutation requests.",
            "",
            "Detailed numeric results: [report.json](report.json).",
            "",
        ]
    )
    return "\n".join(lines)


def report_metadata(args) -> dict:
    journeys = "enabled for Events samples" if "events" in args.targets and not args.skip_journey else "disabled"
    scope = " ".join(
        [
            "Anonymous laboratory measurements collected by an external browser.",
            f"Directory targets: {', '.join(args.targets)}. Leagues navigation journeys: {journeys}.",
            "The operator runs each server with the same dataset, backend and asset compression policy.",
            "Variant order rotates each run, and profiles run sequentially with the browser's normal HTTP cache.",
            "Every profile uses the configured mobile viewport. The unthrottled profile uses the host's available network and CPU.",
        ]
    )
    return {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "variants": [variant.name for variant in args.variants],
        "profiles": {name: PROFILES[name] for name in args.profiles},
        "viewport": {"width": args.width, "height": args.height, "device_scale_factor": 2},
        "runs_per_cold_warm_pair": args.runs,
        "collection_scope": scope,
        "targets": args.targets,
        "runtime_evidence": {"original": "mounted-app-layout", "public-html": "navigation-bridge"},
        "expected_samples": len(args.variants) * len(args.profiles) * args.runs * 2 * len(args.targets),
        "complete": False,
        "passed": False,
        "samples": [],
        "summary": [],
    }


def save_report(report: dict, output: Path) -> None:
    report["summary"] = summarize(report["samples"])
    report["passed"] = (
        report["complete"]
        and len(report["samples"]) == report["expected_samples"]
        and all(row["passed"] for row in report["samples"])
    )
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "summary.md").write_text(markdown_summary(report))


async def run(args) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists() or (args.output / "summary.md").exists():
        raise ValueError("Choose a new output directory; existing measurements will not be overwritten.")
    report = report_metadata(args)
    save_report(report, args.output)
    async with async_playwright() as playwright:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        report["browser_version"] = browser.version
        try:
            for profile in args.profiles:
                args.profile = profile
                for iteration in range(1, args.runs + 1):
                    for target in args.targets:
                        for variant in variant_order(args.variants, iteration):
                            context = await new_context(browser, args)
                            context.set_default_timeout(args.timeout_ms)
                            try:
                                for cache in ("cold", "warm"):
                                    print(
                                        f"{profile}: {iteration}/{args.runs} {variant.name} {target} {cache}",
                                        flush=True,
                                    )
                                    page = await context.new_page()
                                    row = {
                                        "profile": profile,
                                        "variant": variant.name,
                                        "target": target,
                                        "run": iteration,
                                        "cache": cache,
                                        "passed": False,
                                    }
                                    stage = "startup"
                                    try:
                                        result, network = await measure(page, context, args, variant, target)
                                        row.update(result)
                                        if row["passed"] and target == "events" and not args.skip_journey:
                                            stage = "retained-navigation"
                                            row["journey"] = await journey(page, args, variant, network)
                                            row["passed"] = row["passed"] and row["journey"]["passed"]
                                    except Exception as error:
                                        row.update(passed=False, failure=type(error).__name__, failure_stage=stage)
                                    finally:
                                        await page.close()
                                    report["samples"].append(row)
                                    save_report(report, args.output)
                                    if not row["passed"] and not args.continue_on_failure:
                                        print(f"Stopped at failed {stage} check; report: {args.output / 'report.json'}")
                                        return 1
                            finally:
                                await context.close()
        finally:
            await browser.close()
    report["complete"] = True
    save_report(report, args.output)
    print(f"{'PASS' if report['passed'] else 'FAIL'}: {args.output / 'summary.md'}")
    return int(not report["passed"])


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments())))
