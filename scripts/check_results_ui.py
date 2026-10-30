"""Check result statistics, sorting, and match details against the local demo.

Reads actual results and uses the shared loopback/demo mutation guard. Start
scripts.dev_demo first: it seeds a completed demo-open match and unplayed summer
and league events. Practice data from scripts.dev_practice is not required.
    uv run python scripts/check_results_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome
"""

from __future__ import annotations

import asyncio
from fractions import Fraction
import sys
from urllib.parse import parse_qs, quote, urlsplit

from playwright.async_api import Page, async_playwright, expect

from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments

SUMMARY_ROWS = ".results-summary-table > tbody > tr:has(> td.table-identity)"


async def rows(page: Page) -> list[dict]:
    return await page.locator(SUMMARY_ROWS).evaluate_all("""rows => rows.map(row => {
        const cells = row.querySelectorAll(':scope > td');
        const link = row.querySelector('td.table-identity a');
        return {
            id: decodeURIComponent(new URL(link.href).pathname.split('/').pop()),
            name: link.lastElementChild.textContent.trim(),
            matches: cells[2].textContent.trim(), points: cells[3].textContent.trim()
        };
    })""")


async def verify_stats(page: Page, teams: list[dict]) -> None:
    await expect(page.locator(SUMMARY_ROWS)).to_have_count(len(teams))
    for team in teams:
        row = page.locator(SUMMARY_ROWS).filter(has=page.locator(f'a[href="/teams/{quote(team["id"], safe="")}"]'))
        await expect(row.locator(":scope > td").nth(2)).to_have_text(f"{team['matches_won']}–{team['matches_lost']}")
        await expect(row.locator(":scope > td").nth(3)).to_have_text(f"{team['points_won']}–{team['points_lost']}")
    assert {row["id"]: row["name"] for row in await rows(page)} == {team["id"]: team["pseudonym"] for team in teams}


async def verify_match_links(page: Page, matches: list[dict], include_ribbon: bool) -> None:
    visible = [match for match in matches if include_ribbon or not match["ribbon"]]
    links = page.locator(".results-games-table td.match a")
    await expect(links).to_have_count(len(visible))
    actual = await links.evaluate_all("links => links.map(link => new URL(link.href).pathname.split('/').pop())")
    assert sorted(actual) == sorted(match["uuid"] for match in visible), (actual, visible)


async def set_ribbon(page: Page, endpoint: str, enabled: bool) -> None:
    async with page.expect_response(
        lambda response: (
            urlsplit(response.url).path == endpoint
            and parse_qs(urlsplit(response.url).query).get("include_ribbon") == [str(enabled).lower()]
        )
    ) as response:
        await page.locator("#results-include-ribbon").set_checked(enabled)
    assert (await response.value).ok, "Ribbon filter could not load results"


async def verify_empty(page: Page) -> None:
    await expect(page.get_by_role("heading", name="No results yet", exact=True)).to_be_visible()
    await expect(page.locator(".results-summary-table")).to_have_count(0)


async def check_widths(check: SmokeCheck, page: Page, path: str) -> None:
    for width in (320, 360, 768, 1280):
        await page.set_viewport_size({"width": width, "height": 900})
        await check.check_layout(page, "results-" + path.strip("/").replace("/", "-"), width)
        check.visited += 1


async def verify(check: SmokeCheck, page: Page, path: str, endpoint: str, coverage: set[str]) -> None:
    payload = await api_json(page.context, check.base_url, endpoint + "?include_ribbon=false")
    teams = payload["teams"]
    ribbon_payload = await api_json(page.context, check.base_url, endpoint + "?include_ribbon=true")
    await page.goto(check.base_url + path)
    if not teams:
        await verify_empty(page)
        await set_ribbon(page, endpoint, True)
        if ribbon_payload["teams"]:
            await verify_stats(page, ribbon_payload["teams"])
        else:
            await verify_empty(page)
        await set_ribbon(page, endpoint, False)
        await verify_empty(page)
        await check_widths(check, page, path)
        coverage.add("empty")
        return
    await verify_stats(page, teams)
    expected_names = sorted(team["pseudonym"].lower() for team in teams)
    assert [row["name"].lower() for row in await rows(page)] == expected_names
    await expect(page.get_by_role("columnheader", name="Team", exact=False)).to_have_attribute("aria-sort", "ascending")

    # Native header buttons support keyboard sorting and announce direction.
    await page.get_by_role("button", name="Team", exact=False).focus()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("columnheader", name="Team", exact=False)).to_have_attribute(
        "aria-sort", "descending"
    )
    assert [row["name"].lower() for row in await rows(page)] == list(reversed(expected_names))
    for label, field in (("Matches (W–L)", "matches"), ("Points (W–L)", "points")):
        button = page.get_by_role("button", name=label, exact=False)
        for direction in ("descending", "ascending"):
            await button.click()
            await expect(page.get_by_role("columnheader", name=label, exact=False)).to_have_attribute(
                "aria-sort", direction
            )
            values = [
                Fraction(int(value.split("–")[0]), max(1, sum(map(int, value.split("–")))))
                for value in (row[field] for row in await rows(page))
            ]
            assert values == sorted(values, reverse=direction == "descending"), (field, direction, values)
    await page.get_by_role("button", name="Team", exact=False).click()

    # Prefer a team with ribbon games, so inclusion is checked against both the
    # summary and its real match list. No responses or match data are replaced.
    details = await asyncio.gather(
        *(api_json(page.context, check.base_url, endpoint + "/team/" + quote(team["id"], safe="")) for team in teams)
    )
    team, detail = max(
        zip(teams, details, strict=True),
        key=lambda pair: (any(match["ribbon"] for match in pair[1]["matches"]), len(pair[1]["matches"])),
    )
    assert detail["matches"], f"The demo needs per-team matches to check {path}"
    row = page.locator(SUMMARY_ROWS).filter(has=page.locator(f'a[href="/teams/{quote(team["id"], safe="")}"]'))
    toggle = row.get_by_role("button")
    await toggle.focus()
    await page.keyboard.press("Enter")
    await expect(toggle).to_have_attribute("aria-expanded", "true")
    await verify_match_links(page, detail["matches"], False)
    await set_ribbon(page, endpoint, True)
    await verify_stats(page, ribbon_payload["teams"])
    await verify_match_links(page, detail["matches"], True)
    await set_ribbon(page, endpoint, False)
    await verify_stats(page, teams)
    await verify_match_links(page, detail["matches"], False)
    await check_widths(check, page, path)
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    await expect(page.locator(".results-games-table")).to_have_count(0)
    coverage.add("populated")


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    coverage: set[str] = set()
    async with async_playwright() as playwright:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        context = await browser.new_context(viewport={"width": 360, "height": 900}, reduced_motion="reduce")
        context.set_default_timeout(TIMEOUT_MS)
        try:
            marker = await api_json(context, check.base_url, "/_api/demo-info")
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER
            await context.route("**/*", check.guard_requests)
            page = await context.new_page()
            check.observe(page)
            for path, endpoint in (
                ("/demo-open/results", "/_api/tournaments/demo-open/results"),
                ("/demo-summer-cup/results", "/_api/tournaments/demo-summer-cup/results"),
                ("/leagues/demo-coast-league/results", "/_api/leagues/demo-coast-league/results"),
            ):
                await check.workflow(
                    page, path.strip("/").replace("/", "-"), 360, lambda: verify(check, page, path, endpoint, coverage)
                )
        finally:
            await context.close()
            await browser.close()
    missing = {"populated", "empty"} - coverage
    if missing:
        check.failures.append(
            "Results checks did not cover "
            + ", ".join(sorted(missing))
            + " results. Use a separate scripts.dev_demo database with its completed demo-open match "
            "and an unplayed demo-summer-cup or demo-coast-league; existing demo edits are not reset."
        )
    print(f"{check.visited} results screens; {check.workflows} workflows; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures))


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments(__doc__))))
