"""Check standings, ties, sorting, and ribbon filtering in the local demo.

Uses GET response fixtures and the shared loopback/demo mutation guard.
    uv run python scripts/check_results_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome
"""

from __future__ import annotations

import asyncio
import copy
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, Route, async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu
from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments


def team(name, wins, losses, points_won, points_lost):
    return dict(
        id=name.lower(),
        pseudonym=name,
        profile_photo=None,
        matches_won=wins,
        matches_lost=losses,
        points_won=points_won,
        points_lost=points_lost,
    )


TEAMS = [
    team("Unplayed", 0, 0, 99, 0),
    team("Delta", 6, 2, 8, 12),
    team("Alpha", 3, 1, 4, 6),
    team("Zeta", 1, 2, 9, 1),
    team("Gamma", 2, 0, 4, 4),
    team("Beta", 1, 0, 6, 4),
]
EXPECTED = {
    "Beta": "Gold, rank 1",
    "Gamma": "Silver, rank 2",
    "Alpha": "Bronze, rank 3",
    "Delta": "Bronze, rank 3",
    "Zeta": "Rank 5",
    "Unplayed": "—Unranked",
}


async def standings(page: Page) -> dict[str, str]:
    return await page.locator("tr:has(.results-rank)").evaluate_all("""rows => Object.fromEntries(rows.map(row => [
        row.querySelector('a > span:last-child').textContent.trim(), row.querySelector('.results-rank').textContent.trim()
    ]))""")


async def verify(check: SmokeCheck, page: Page, path: str, endpoint: str, payload: dict) -> None:
    mode = "normal"

    async def fixture(route: Route):
        if route.request.method != "GET":
            await route.fallback()
            return
        data = copy.deepcopy(payload)
        data["teams"] = copy.deepcopy(TEAMS) if mode == "normal" else []
        if mode == "unplayed":
            data["teams"] = [TEAMS[0]]
        if mode == "normal" and parse_qs(urlsplit(route.request.url).query).get("include_ribbon") == ["true"]:
            data["teams"][-1]["matches_lost"] = 3  # Beta falls from first to fifth.
        await route.fulfill(json=data)

    async def detail(route: Route):
        await route.fulfill(
            json={
                "matches": [
                    dict(
                        uuid="standings-played",
                        name="Pool match",
                        team1_name="Beta",
                        team2_name="Gamma",
                        match_winner="TEAM1",
                        your_side="TEAM1",
                        sets=[dict(set_number=1, team1_points=6, team2_points=4)],
                        ribbon=False,
                        event="demo-open",
                    ),
                    dict(
                        uuid="standings-ribbon",
                        name="Exhibition match",
                        team1_name="Beta",
                        team2_name="Gamma",
                        match_winner="TEAM2",
                        your_side="TEAM1",
                        sets=[dict(set_number=1, team1_points=1, team2_points=3)],
                        ribbon=True,
                        event="demo-open",
                    ),
                ]
            }
        )

    await page.route(f"**{endpoint}?*", fixture)
    await page.route(f"**{endpoint}/team/*", detail)
    try:
        await page.goto(check.base_url + path)
        await expect(page.locator(".results-rank")).to_have_count(6)
        assert await standings(page) == EXPECTED, await standings(page)
        assert list((await standings(page))) == ["Beta", "Gamma", "Alpha", "Delta", "Zeta", "Unplayed"]
        await expect(page.locator(".results-rank .fa-trophy")).to_have_count(4)
        # Keyboard sorting changes display order without changing placement.
        await page.get_by_role("button", name="Team", exact=True).focus()
        await page.keyboard.press("Enter")
        assert list((await standings(page))) == ["Alpha", "Beta", "Delta", "Gamma", "Unplayed", "Zeta"]
        assert await standings(page) == EXPECTED, await standings(page)
        await expect(page.get_by_role("columnheader", name="Team")).to_have_attribute("aria-sort", "ascending")
        await page.get_by_role("button", name="Rank", exact=True).click()
        await page.get_by_role("button", name="Rank", exact=True).click()
        assert list((await standings(page))) == ["Zeta", "Alpha", "Delta", "Gamma", "Beta", "Unplayed"]
        assert await standings(page) == EXPECTED, await standings(page)
        # Expand remains usable with the extra column, and the same ribbon filter applies to detail.
        beta = page.locator("tr:has(.results-rank)").filter(has=page.locator('a[href="/teams/beta"]'))
        await beta.get_by_role("button", name="Expand", exact=True).click()
        await expect(page.locator("td[colspan='5']").get_by_text("Pool match", exact=True)).to_be_visible()
        await expect(page.get_by_text("Exhibition match", exact=True)).to_have_count(0)
        await page.locator("#results-include-ribbon").check()
        await expect(beta.locator(".results-rank")).to_have_text("Rank 5")
        await expect(page.get_by_text("Exhibition match", exact=True)).to_be_visible()
        await page.locator("#results-include-ribbon").uncheck()
        await expect(beta.locator(".results-rank")).to_have_text("Gold, rank 1")
        await expect(page.get_by_text("Exhibition match", exact=True)).to_have_count(0)
        await beta.get_by_role("button", name="Collapse", exact=True).click()
        await page.get_by_role("button", name="Rank", exact=True).click()
        for width in (320, 360, 768, 1280):
            await page.set_viewport_size({"width": width, "height": 900})
            await check.check_layout(page, "standings-" + path.split("/")[1], width)
            check.visited += 1
        await page.set_viewport_size({"width": 360, "height": 900})
        for palette in PALETTES:
            for theme in ("light", "dark"):
                await choose(page, palette, theme)
                await close_menu(page)
                await check_contrast(page, ".results-rank .fa-trophy", minimum=3)
                await check_contrast(page, ".results-rank")
                await check.check_layout(page, f"standings-{palette}-{theme}", 360)
                check.visited += 1
        mode = "unplayed"
        await page.reload()
        await expect(page.locator(".results-rank")).to_have_count(1)
        await expect(page.locator(".results-rank")).to_have_text("—Unranked")
        await expect(page.locator(".results-rank .fa-trophy")).to_have_count(0)
        mode = "empty"
        await page.reload()
        await expect(page.get_by_role("heading", name="No results yet")).to_be_visible()
        await expect(page.locator(".results-rank")).to_have_count(0)
    finally:
        await page.unroute(f"**{endpoint}?*", fixture)
        await page.unroute(f"**{endpoint}/team/*", detail)


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
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER
            await context.route("**/*", check.guard_requests)
            page = await context.new_page()
            check.observe(page)
            for path, endpoint in (
                ("/demo-open/results", "/_api/tournaments/demo-open/results"),
                ("/leagues/demo-coast-league/results", "/_api/leagues/demo-coast-league/results"),
            ):
                payload = await api_json(context, check.base_url, endpoint)
                await check.workflow(
                    page, path.strip("/").replace("/", "-"), 360, lambda: verify(check, page, path, endpoint, payload)
                )
        finally:
            await context.close()
            await browser.close()
    print(f"{check.visited} standings screens; {check.workflows} workflows; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures))


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments(__doc__))))
