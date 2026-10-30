"""Check public leaders, personal results, organizer standings, and live refresh.

Run against an isolated local demo. Standings responses are browser fixtures;
the shared request guard blocks data changes. Backend tests separately verify
ranking, filtering, and publication settings.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, Route, async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu, preference
from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments


WEAPONS = {
    "CHAIN": "Chain",
    "LONG": "Long",
    "QTIP": "Q-Tip",
    "STAFF": "Staff",
    "BOARD": "Board",
    "FLORENTINE": "Florentine",
    "SKULL": "Skull",
    "UNARMED": "Unarmed",
}
LONG_NAME = "Alexandria Montgomery-Worthington"
UNBROKEN_NAME = "AlexandriaMontgomeryWorthington"
CHAIN_NAMES = ["Morgan Reed", "Alex Rivera", "Jamie Park", "Taylor Chen", "Casey Singh"]
STAFF_NAMES = ["Jordan Ellis", "Avery Brooks", "Riley Patel"]
OWN_NAME = "Cameron Blake"
OWN_ID = "cameron-blake"
FLORENTINE_BY_WINS = ["Sam Bennett", "Quinn Harper", "Drew Morgan", OWN_NAME]
WEAPON_NAMES = dict(
    zip(
        WEAPONS,
        [
            LONG_NAME,
            UNBROKEN_NAME,
            "Robin Santos",
            "Emerson Cole",
            "Parker Lin",
            "Harper Gray",
            "Sage Morris",
            "Finley Ross",
        ],
        strict=True,
    )
)
BEAR_CLASS_NAMES = [
    "Noah Patel",
    "Riley Chen",
    "Taylor Nguyen",
    "Casey Rivera",
    "Quinn Harper",
    "Jordan Blake",
    "Avery Singh",
]


def standing(entry_number: int, name: str, weapon: str, wins: int, losses: int | None) -> dict:
    return {
        "registration_id": entry_number,
        "entry_number": entry_number,
        "weapon": weapon,
        "player_id": name.lower().replace(" ", "-"),
        "display_name": name,
        "profile_photo": None,
        "jersey_name": name,
        "jersey_number": str(entry_number),
        "team_shortname": "DEMO",
        "team_profile_photo": None,
        "wins": wins,
        "losses": losses,
        "win_percentage": 100 * wins / (wins + losses) if losses is not None and wins + losses else None,
        "rank": 0,
    }


class StandingsFixture:
    def __init__(self, comp_id: int):
        self.comp_id = comp_id
        self.public_limit: int | None = 3
        self.viewer_player_id: str | None = None
        self.organizer = False
        self.hidden = False
        self.empty = False
        self.generic = False
        self.bear = False
        self.track_losses = True
        self.large_numbers = False
        self.no_scores = False
        self.unplayed_weapons: set[str] = set()
        self.removed_weapon: str | None = None
        self.denied_status: int | None = None
        self.fail_metadata = False
        self.fail_weapon: str | None = None
        self.delay_weapon: str | None = None
        self.delay_started = asyncio.Event()
        self.release_delay = asyncio.Event()
        self.held_policies: dict[
            tuple[bool, int | None, str, bool], tuple[asyncio.Event, asyncio.Event, asyncio.Event]
        ] = {}
        self.chains = [standing(20 + index, name, "CHAIN", 9 - index, None) for index, name in enumerate(CHAIN_NAMES)]
        self.staff = [standing(3 + index, name, "STAFF", 9 - index, 0) for index, name in enumerate(STAFF_NAMES)]
        self.florentine = [
            standing(6, FLORENTINE_BY_WINS[0], "FLORENTINE", 6, 6),
            standing(7, FLORENTINE_BY_WINS[1], "FLORENTINE", 5, 0),
            standing(8, FLORENTINE_BY_WINS[2], "FLORENTINE", 4, 0),
            standing(9, OWN_NAME, "FLORENTINE", 3, 0),
        ]
        self.all_weapons = [
            standing(10 + index, WEAPON_NAMES[weapon], weapon, 12 - index, 0) for index, weapon in enumerate(WEAPONS)
        ]
        self.all_weapons[-1].update(wins=0, losses=3, win_percentage=0)
        self.all_weapons[0].update(
            entry_number=12345, wins=123, jersey_name="MontgomeryWorthington", team_shortname="NORTHWESTJUGGER"
        )
        self.bear_rows = (
            [standing(30 + index, name, "CHAIN", 30 - index, 0) for index, name in enumerate(CHAIN_NAMES[:3])]
            + [
                standing(33 + index, name, weapon, 20 - index, 0)
                for index, (weapon, name) in enumerate(zip(list(WEAPONS)[1:], BEAR_CLASS_NAMES, strict=True))
            ]
            + [standing(40, OWN_NAME, "FLORENTINE", 3, 2)]
        )

    def table(self, table_id: str, weapon: str | None, sort: str | None) -> dict:
        track_losses = table_id != "chains" and self.track_losses
        effective_sort = (sort or "win_percentage") if track_losses else "wins"
        if self.bear:
            source = self.bear_rows
        elif self.generic:
            source = self.all_weapons
        elif table_id == "chains":
            source = self.chains
        else:
            source = self.staff + self.florentine
        rows = deepcopy(source)
        if self.removed_weapon is not None:
            rows = [row for row in rows if row["weapon"] != self.removed_weapon]
        for row in rows:
            if self.no_scores or row["weapon"] in self.unplayed_weapons:
                row.update(wins=0, losses=0 if track_losses else None, win_percentage=None)
        if self.empty:
            rows = []
        available_weapons = [
            value
            for value in WEAPONS
            if any(row["weapon"] == value and row["wins"] + (row["losses"] or 0) > 0 for row in rows)
        ]
        rows.sort(
            key=lambda row: (row[effective_sort] if row[effective_sort] is not None else -1, row["wins"]), reverse=True
        )
        if self.empty or self.hidden:
            rows = []
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            if not track_losses:
                row.update(losses=None, win_percentage=None)
        personal_row = next((deepcopy(row) for row in rows if row["player_id"] == self.viewer_player_id), None)
        if weapon is not None:
            rows = [row for row in rows if row["weapon"] == weapon] if weapon in available_weapons else []
            for rank, row in enumerate(rows, start=1):
                row["rank"] = rank
        if not self.organizer and self.public_limit is not None and self.public_limit > 0:
            rows = rows[: 1 if weapon is not None else self.public_limit]
        if self.large_numbers:
            for row in rows:
                row.update(
                    rank=2_147_483_600 + row["rank"], wins=2_147_483_647, losses=2_147_483_647, win_percentage=50
                )
        return {
            "id": table_id,
            "title": {"all": "Standings", "chains": "Chains", "breaks": "Breaks"}[table_id],
            "track_losses": track_losses,
            "available_weapons": available_weapons,
            "weapon": weapon,
            "sort": effective_sort,
            "personal_row": personal_row,
            "rows": rows,
        }

    async def handle(self, route: Route) -> None:
        query = parse_qs(urlsplit(route.request.url).query)
        assert "view" not in query, "The standings view is determined by server permissions"
        weapon = query.get("weapon", [None])[0]
        sort = query.get("sort", [None])[0]
        table_ids = query.get("table", ["all"] if self.generic or self.bear else ["chains", "breaks"])
        competition_type = "DUELING" if self.bear else "OTHER" if self.generic else "CHAIN_BREAKING"
        payload = {
            "id": self.comp_id,
            "name": "Bear pit" if self.bear else "Weapon standings" if self.generic else "Chain pit",
            "type": competition_type,
            "finalized": False,
            "only_show_top_n_results": 0 if self.hidden else self.public_limit,
            "showing_all_results": not self.hidden and (self.organizer or self.public_limit is None),
            "results_page_enabled": not self.hidden,
            "tables": [self.table(table_id, weapon, sort) for table_id in table_ids],
        }
        held_policy = (
            self.held_policies.get((self.organizer, self.public_limit, competition_type, self.track_losses))
            if "table" in query
            else None
        )
        if held_policy is not None:
            started, release, delivered = held_policy
            started.set()
            await release.wait()
        if weapon is not None and weapon == self.delay_weapon:
            self.delay_weapon = None
            self.delay_started.set()
            await self.release_delay.wait()
        if self.denied_status is not None:
            await route.fulfill(status=self.denied_status, json={"error": "Side competition not found"})
        elif (weapon is not None and weapon == self.fail_weapon) or ("table" not in query and self.fail_metadata):
            await route.fulfill(status=503, json={"error": "Standings temporarily unavailable"})
        else:
            await route.fulfill(json=payload)
        if held_policy is not None:
            delivered.set()

    async def account(self, route: Route) -> None:
        if self.viewer_player_id is None:
            await route.fulfill(status=401, json={"error": "Not signed in"})
        else:
            await route.fulfill(
                json={
                    "id": self.viewer_player_id,
                    "name": "Demo Organizer" if self.organizer else OWN_NAME,
                    "type": "player",
                }
            )

    async def logout(self, route: Route) -> None:
        """Change only the browser fixture; no logout request reaches the server."""
        self.viewer_player_id = None
        self.organizer = False
        await route.fulfill(json={})

    def hold_policy(
        self,
        organizer: bool,
        public_limit: int | None,
        competition_type: str | None = None,
        track_losses: bool | None = None,
    ) -> tuple[asyncio.Event, asyncio.Event, asyncio.Event]:
        events = (asyncio.Event(), asyncio.Event(), asyncio.Event())
        if competition_type is None:
            competition_type = "DUELING" if self.bear else "OTHER" if self.generic else "CHAIN_BREAKING"
        self.held_policies[
            organizer, public_limit, competition_type, self.track_losses if track_losses is None else track_losses
        ] = events
        return events

    def release_policies(self) -> None:
        for _, release, _ in self.held_policies.values():
            release.set()
        self.held_policies.clear()

    def delay_next(self, weapon: str) -> None:
        self.delay_weapon = weapon
        self.delay_started.clear()
        self.release_delay.clear()


async def assert_players(
    section, names: list[str], own_name: str | None = None, ranks: list[int] | None = None
) -> None:
    labels = [
        re.compile(r"^\s*" + re.escape(name) + (r"\s*You" if name == own_name else "") + r"\s*$") for name in names
    ]
    await expect(section.locator("tbody tr td:nth-child(2) .fw-semibold")).to_have_text(labels)
    await expect(section.locator("tbody tr td:first-child")).to_have_text(
        [str(rank) for rank in (ranks if ranks is not None else range(1, len(names) + 1))]
    )


async def check_table_layout(page: Page, allow_horizontal_scroll: bool = False) -> None:
    """Check column alignment, complete cell text, and usable controls."""
    diagnostics = await page.locator(".sidecomp-standings-table").evaluate_all(
        """(tables, allowScroll) => tables.map(table => {
        const wrapper = table.parentElement;
        const cells = [...table.querySelectorAll('th, td')];
        return {
            title: table.caption?.textContent,
            width: wrapper.clientWidth,
            contentWidth: wrapper.scrollWidth,
            overflowingCells: cells.filter(cell => cell.scrollWidth > cell.clientWidth + 1)
                .map(cell => cell.textContent.trim()),
            missingScopes: [...table.querySelectorAll('thead th')].filter(cell => cell.scope !== 'col').length
                + [...table.querySelectorAll('tbody th')].filter(cell => cell.scope !== 'row').length,
            unalignedNumbers: [...table.querySelectorAll('.table-number')]
                .filter(cell => getComputedStyle(cell).textAlign !== 'right').length,
            smallControls: [...table.closest('section').querySelectorAll('select, .results-sort-button')]
                .flatMap(control => {
                    const rect = control.getBoundingClientRect();
                    const outside = !allowScroll && (rect.left < -1 || rect.right > innerWidth + 1);
                    return rect.width < 43.9 || rect.height < 43.9 || outside
                        ? [{text: control.textContent.trim(), width: rect.width, height: rect.height}] : [];
                }),
            columnCounts: [...table.rows].map(row => row.cells.length)
        };
    })""",
        allow_horizontal_scroll,
    )
    assert len(diagnostics) == 1, f"Expected one standings table: {diagnostics}"
    for table in diagnostics:
        assert table["title"], table
        assert "Rankings are highest first" in table["title"], table
        if not allow_horizontal_scroll:
            assert table["contentWidth"] <= table["width"] + 1, f"Scores require horizontal scrolling: {table}"
        assert not table["overflowingCells"], f"Standings text overflows a cell: {table}"
        assert not table["missingScopes"] and not table["unalignedNumbers"], table
        assert not table["smallControls"], f"Standings controls are clipped or smaller than 44px: {table}"
        assert len(set(table["columnCounts"])) == 1, f"Headers and rows have different columns: {table}"
    small_tabs = (
        await page.get_by_role("tablist", name="Results", exact=True)
        .get_by_role("tab")
        .evaluate_all("""tabs => tabs.flatMap(tab => {
        const rect = tab.getBoundingClientRect();
        return rect.width < 43.9 || rect.height < 43.9 || rect.left < -1 || rect.right > innerWidth + 1
            ? [{text: tab.textContent, width: rect.width, height: rect.height}] : [];
    })""")
    )
    assert not small_tabs, f"Results tabs are clipped or smaller than 44px: {small_tabs}"


async def check_keyboard_focus(control) -> None:
    await control.focus()
    await expect(control).to_be_focused()
    focus = await control.evaluate("""control => {
        const style = getComputedStyle(control);
        return {visible: control.matches(':focus-visible'), outline: style.outlineStyle,
            width: parseFloat(style.outlineWidth), shadow: style.boxShadow};
    }""")
    assert focus["visible"] and ((focus["outline"] != "none" and focus["width"] >= 2) or focus["shadow"] != "none"), (
        f"Keyboard focus is not visible: {focus}"
    )


async def capture_state(check: SmokeCheck, page: Page, name: str) -> None:
    for width in (320, 390, 1280):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.evaluate("window.scrollTo(0, 0); document.activeElement?.blur()")
        await check_table_layout(page)
        await check.check_layout(page, name, width)
        if check.screenshots:
            content = page.get_by_role("heading", name=re.compile(r" - Results \(")).locator("..")
            bounds = await content.bounding_box()
            assert bounds is not None
            # Capture document coordinates without scrolling sticky navigation over rows.
            await page.screenshot(
                path=str(check.screenshots / f"{width:04d}-{name}-results.png"),
                clip=bounds,
                full_page=True,
                animations="disabled",
            )
        check.visited += 1
    await page.set_viewport_size({"width": 390, "height": 900})


async def show_breaks(page: Page):
    await page.get_by_role("tab", name="Breaks", exact=True).click()
    section = page.get_by_role("region", name="Breaks", exact=True)
    await expect(section).to_be_visible()
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
    return section


async def check_results_tabs(page: Page, comp_id: int, active: str) -> None:
    tabs = page.get_by_role("tablist", name="Results", exact=True)
    await expect(tabs.get_by_role("tab")).to_have_count(2)
    await expect(page.get_by_role("tabpanel", include_hidden=True)).to_have_count(2)
    for title in ("Chains", "Breaks"):
        tab = tabs.get_by_role("tab", name=title, exact=True)
        tab_id = f"sidecomp-{comp_id}-tab-{title.lower()}"
        panel_id = f"sidecomp-{comp_id}-panel-{title.lower()}"
        panel = page.locator(f'[id="{panel_id}"]')
        selected = title == active
        await expect(tab).to_have_attribute("id", tab_id)
        await expect(tab).to_have_attribute("aria-controls", panel_id)
        await expect(tab).to_have_attribute("aria-selected", str(selected).lower())
        await expect(tab).to_have_attribute("tabindex", "0" if selected else "-1")
        await expect(panel).to_have_attribute("role", "tabpanel")
        await expect(panel).to_have_attribute("aria-labelledby", tab_id)
        if selected:
            await expect(page.get_by_role("tabpanel", name=title, exact=True)).to_be_visible()
            await expect(panel).to_have_attribute("tabindex", "0")
            await expect(panel.locator(".sidecomp-standings-table")).to_have_count(1)
        else:
            await expect(panel).to_be_hidden()
            await expect(panel.locator(".sidecomp-standings-table")).to_have_count(0)


async def verify(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    tabs = page.get_by_role("tablist", name="Results", exact=True)
    await check_results_tabs(page, fixture.comp_id, "Chains")
    chains = page.get_by_role("region", name="Chains", exact=True)
    await assert_players(chains, CHAIN_NAMES[:3])
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
    await expect(page.get_by_role("heading", name="Your result", exact=True)).to_have_count(0)
    chain_weapon = chains.get_by_role("combobox", name="Weapon", exact=True)
    await expect(chain_weapon.locator("option")).to_have_text(["All weapons", "Chain"])
    await chain_weapon.select_option("CHAIN")
    await assert_players(chains, CHAIN_NAMES[:1])
    await expect(chains.locator("thead th")).to_have_text(["Rank", "Player", "Wins↓"])
    # Tabs use one keyboard stop; arrow keys activate preloaded scoring groups.
    await page.keyboard.press("Tab")
    await check_keyboard_focus(tabs.get_by_role("tab", name="Chains", exact=True))
    for key, title in (
        ("ArrowRight", "Breaks"),
        ("ArrowRight", "Chains"),
        ("ArrowLeft", "Breaks"),
        ("Home", "Chains"),
        ("End", "Breaks"),
        ("Space", "Breaks"),
        ("Enter", "Breaks"),
    ):
        await page.keyboard.press(key)
        await expect(tabs.get_by_role("tab", name=title, exact=True)).to_be_focused()
        await check_results_tabs(page, fixture.comp_id, title)
    await page.keyboard.press("Tab")
    await expect(page.get_by_role("tabpanel", name="Breaks", exact=True)).to_be_focused()
    await page.keyboard.press("Tab")
    await expect(page.get_by_role("combobox", name="Weapon", exact=True)).to_be_focused()
    breaks = await show_breaks(page)
    await assert_players(breaks, STAFF_NAMES)
    await capture_state(check, page, "sidecomp-chain-breaks")
    weapon = breaks.get_by_role("combobox", name="Weapon", exact=True)
    await weapon.select_option("FLORENTINE")
    await assert_players(breaks, [FLORENTINE_BY_WINS[1]])
    await breaks.get_by_role("button", name="Wins", exact=True).click()
    await assert_players(breaks, FLORENTINE_BY_WINS[:1])
    await weapon.select_option("STAFF")
    await assert_players(breaks, STAFF_NAMES[:1])

    # Delayed selections cannot replace newer choices or a different results group.
    fixture.delay_next("FLORENTINE")
    await weapon.select_option("FLORENTINE")
    await asyncio.wait_for(fixture.delay_started.wait(), TIMEOUT_MS / 1000)
    await expect(breaks.get_by_text("Loading standings…")).to_be_visible()
    await weapon.select_option("STAFF")
    await assert_players(breaks, STAFF_NAMES[:1])
    fixture.release_delay.set()
    fixture.delay_next("FLORENTINE")
    await weapon.select_option("FLORENTINE")
    await asyncio.wait_for(fixture.delay_started.wait(), TIMEOUT_MS / 1000)
    await tabs.get_by_role("tab", name="Chains", exact=True).click()
    await check_results_tabs(page, fixture.comp_id, "Chains")
    await expect(chains).to_be_visible()
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
    fixture.release_delay.set()
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await expect(page.get_by_role("region", name="Breaks", exact=True)).to_have_count(0)
    breaks = await show_breaks(page)
    weapon = breaks.get_by_role("combobox", name="Weapon", exact=True)
    await weapon.select_option("STAFF")
    await assert_players(breaks, STAFF_NAMES[:1])

    fixture.delay_next("FLORENTINE")
    await weapon.select_option("FLORENTINE")
    await asyncio.wait_for(fixture.delay_started.wait(), TIMEOUT_MS / 1000)
    await asyncio.sleep(1.2)
    await expect(breaks.get_by_text("Loading standings…")).to_be_visible()
    fixture.release_delay.set()
    await expect(breaks.locator("tbody tr")).to_have_count(1)
    fixture.fail_weapon = "STAFF"
    await weapon.select_option("STAFF")
    await expect(breaks.get_by_role("alert")).to_contain_text("Could not load standings")
    await breaks.get_by_role("button", name="Retry standings", exact=True).focus()
    fixture.fail_weapon = None
    await page.keyboard.press("Enter")
    await assert_players(breaks, STAFF_NAMES[:1])
    fixture.fail_metadata = True
    await expect(page.get_by_role("alert")).to_contain_text("Could not refresh results")
    await assert_players(breaks, STAFF_NAMES[:1])
    await expect(weapon).to_have_value("STAFF")
    await page.get_by_role("button", name="Retry results", exact=True).focus()
    fixture.fail_metadata = False
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("alert")).to_have_count(0)
    check.network_errors = [
        error for error in check.network_errors if "HTTP 503" not in error or "/standings" not in error
    ]
    fixture.public_limit = None
    await weapon.select_option("FLORENTINE")
    await breaks.get_by_role("button", name="Wins", exact=True).click()
    await assert_players(breaks, FLORENTINE_BY_WINS)
    fixture.hidden = True
    await expect(page.get_by_text("Results are not being shown for this side competition.", exact=True)).to_be_visible()
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)


async def verify_personal_rank(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.release_policies()
    fixture.hidden = False
    fixture.public_limit = 3
    fixture.viewer_player_id = OWN_ID
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    personal = page.get_by_role("region", name="Your result", exact=True)
    await expect(personal.get_by_text("Breaks · Florentine", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Rank: 6", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Wins: 3", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Losses: 0", exact=True)).to_be_visible()
    # The personal result remains available even when the other scoring group is selected.
    await expect(page.get_by_role("tab", name="Chains", exact=True)).to_have_attribute("aria-selected", "true")
    breaks = await show_breaks(page)
    weapon = breaks.get_by_role("combobox", name="Weapon", exact=True)
    await assert_players(breaks, STAFF_NAMES)
    await weapon.select_option("FLORENTINE")
    await assert_players(breaks, [FLORENTINE_BY_WINS[1]])
    await breaks.get_by_role("button", name="Wins", exact=True).click()
    await assert_players(breaks, FLORENTINE_BY_WINS[:1])
    await expect(personal.get_by_text("Rank: 6", exact=True)).to_be_visible()
    await weapon.select_option("STAFF")
    await assert_players(breaks, STAFF_NAMES[:1])
    await expect(personal.get_by_text("Rank: 6", exact=True)).to_be_visible()
    fixture.public_limit = None
    await weapon.select_option("FLORENTINE")
    await assert_players(breaks, FLORENTINE_BY_WINS, own_name=OWN_NAME)
    await expect(breaks.locator("tbody").get_by_text("You", exact=True)).to_have_count(1)
    fixture.hidden = True
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)
    await expect(personal).to_have_count(0)
    fixture.hidden = False


async def verify_organizer(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.viewer_player_id = DEMO_USER
    fixture.organizer = True
    fixture.public_limit = 3
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    await assert_players(page.get_by_role("region", name="Chains", exact=True), CHAIN_NAMES)
    breaks = await show_breaks(page)
    await assert_players(breaks, STAFF_NAMES + FLORENTINE_BY_WINS[1:] + FLORENTINE_BY_WINS[:1])
    weapon = breaks.get_by_role("combobox", name="Weapon", exact=True)
    await weapon.select_option("FLORENTINE")
    await breaks.get_by_role("button", name="Wins", exact=True).click()
    await assert_players(breaks, FLORENTINE_BY_WINS)
    await capture_state(check, page, "sidecomp-organizer-weapon")
    old_started, old_release, old_delivered = fixture.hold_policy(True, 3)
    await asyncio.wait_for(old_started.wait(), TIMEOUT_MS / 1000)
    new_started, new_release, _ = fixture.hold_policy(False, 3)
    fixture.organizer = False
    await asyncio.wait_for(new_started.wait(), TIMEOUT_MS / 1000)
    await expect(breaks.locator("tbody tr")).to_have_count(0)
    old_release.set()
    await asyncio.wait_for(old_delivered.wait(), TIMEOUT_MS / 1000)
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await expect(breaks.locator("tbody tr")).to_have_count(0)
    new_release.set()
    await assert_players(breaks, FLORENTINE_BY_WINS[:1])
    await expect(weapon).to_have_value("FLORENTINE")
    fixture.release_policies()
    # All weapons uses the configured cap; the nondefault Wins ranking requires a request.
    await weapon.select_option("")
    await assert_players(breaks, STAFF_NAMES)
    old_started, old_release, old_delivered = fixture.hold_policy(False, 3)
    await asyncio.wait_for(old_started.wait(), TIMEOUT_MS / 1000)
    new_started, new_release, _ = fixture.hold_policy(False, 2)
    fixture.public_limit = 2
    await asyncio.wait_for(new_started.wait(), TIMEOUT_MS / 1000)
    await expect(breaks.locator("tbody tr")).to_have_count(0)
    old_release.set()
    await asyncio.wait_for(old_delivered.wait(), TIMEOUT_MS / 1000)
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await expect(breaks.locator("tbody tr")).to_have_count(0)
    new_release.set()
    await assert_players(breaks, STAFF_NAMES[:2])
    await expect(weapon).to_have_value("")
    await expect(breaks.locator('th[aria-sort="descending"]')).to_have_text("Wins↓")
    fixture.release_policies()


async def verify_access_revocation(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.release_policies()
    fixture.hidden = False
    fixture.public_limit = 3
    fixture.viewer_player_id = OWN_ID
    fixture.organizer = False
    for status, filtered in ((403, False), (404, True)):
        await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
        await expect(page.get_by_text("Rank: 6", exact=True)).to_be_visible()
        breaks = await show_breaks(page)
        if filtered:
            await breaks.get_by_role("combobox", name="Weapon", exact=True).select_option("STAFF")
            await assert_players(breaks, STAFF_NAMES[:1])
        fixture.denied_status = status
        await expect(page.get_by_role("alert").filter(has_text="Could not refresh results")).to_be_visible()
        await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)
        await expect(page.get_by_role("heading", name="Chain pit - Results", exact=False)).to_have_count(0)
        await expect(page.get_by_role("heading", name="Your result", exact=True)).to_have_count(0)
        fixture.denied_status = None
        await expect(page.get_by_role("alert")).to_have_count(0)
        await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
        await expect(page.get_by_text("Rank: 6", exact=True)).to_be_visible()
    check.network_errors = [
        error
        for error in check.network_errors
        if "/standings" not in error or not any(f"HTTP {status}" in error for status in (403, 404))
    ]


async def verify_bear(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.bear = True
    fixture.viewer_player_id = None
    fixture.public_limit = 3
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    section = page.get_by_role("region", name="Standings", exact=True)
    weapon = section.get_by_role("combobox", name="Weapon", exact=True)
    await assert_players(section, CHAIN_NAMES[:3])
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
    await expect(page.get_by_role("region", name="Weapon class leaders", exact=True)).to_have_count(0)
    await expect(page.get_by_role("tablist", name="Results", exact=True)).to_have_count(0)
    await capture_state(check, page, "sidecomp-bear-overall")
    await weapon.select_option("FLORENTINE")
    await assert_players(section, [BEAR_CLASS_NAMES[4]])
    fixture.viewer_player_id = OWN_ID
    await page.reload()
    personal = page.get_by_role("region", name="Your result", exact=True)
    await expect(personal.get_by_text("Overall · Florentine", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Rank: 11", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Wins: 3", exact=True)).to_be_visible()
    await expect(personal.get_by_text("Losses: 2", exact=True)).to_be_visible()
    await expect(personal.get_by_text(re.compile(r"^Win percentage: 60(?:\.0)?%$"))).to_be_visible()
    await weapon.select_option("FLORENTINE")
    await assert_players(section, [BEAR_CLASS_NAMES[4]])
    await capture_state(check, page, "sidecomp-bear-personal-weapon")
    await weapon.select_option("STAFF")
    await assert_players(section, [BEAR_CLASS_NAMES[2]])
    await expect(personal.get_by_text("Rank: 11", exact=True)).to_be_visible()
    fixture.viewer_player_id = BEAR_CLASS_NAMES[4].lower().replace(" ", "-")
    await page.reload()
    await weapon.select_option("FLORENTINE")
    await assert_players(section, [BEAR_CLASS_NAMES[4]], own_name=BEAR_CLASS_NAMES[4])
    await expect(section.locator("tbody").get_by_text("You", exact=True)).to_have_count(1)
    fixture.hidden = True
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)
    await expect(personal).to_have_count(0)
    fixture.hidden = False
    fixture.bear = False


async def verify_account_change(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.viewer_player_id = OWN_ID
    fixture.organizer = True
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    breaks = await show_breaks(page)
    await expect(page.get_by_text("Rank: 6", exact=True)).to_be_visible()
    await expect(breaks.locator("tbody tr")).to_have_count(7)
    fixture.delay_next("FLORENTINE")
    await breaks.get_by_role("combobox", name="Weapon", exact=True).select_option("FLORENTINE")
    await asyncio.wait_for(fixture.delay_started.wait(), TIMEOUT_MS / 1000)
    fixture.fail_metadata = True
    await page.locator("#account-trigger").click()
    await page.get_by_role("button", name="Log out", exact=True).click()
    await expect(page).to_have_url(check.base_url + "/")
    await page.go_back()
    await expect(page.get_by_role("alert").filter(has_text="Could not refresh results")).to_be_visible()
    await expect(page.get_by_role("heading", name="Your result", exact=True)).to_have_count(0)
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)
    fixture.release_delay.set()
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(0)
    fixture.fail_metadata = False
    await expect(page.locator(".sidecomp-standings-table")).to_have_count(1)
    await expect(page.locator(".sidecomp-standings-table tbody tr")).to_have_count(3)
    await expect(page.get_by_role("heading", name="Your result", exact=True)).to_have_count(0)
    await expect(page.locator(".sidecomp-standing-self")).to_have_count(0)
    check.network_errors = [
        error for error in check.network_errors if "HTTP 503" not in error or "/standings" not in error
    ]


async def verify_loss_tracking(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    """A delayed five-column result cannot populate a three-column table."""
    fixture.release_policies()
    fixture.generic = True
    fixture.bear = False
    fixture.hidden = False
    fixture.track_losses = True
    fixture.viewer_player_id = None
    fixture.organizer = False
    fixture.public_limit = 3
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    section = page.get_by_role("region", name="Standings", exact=True)
    weapon = section.get_by_role("combobox", name="Weapon", exact=True)
    await weapon.select_option("CHAIN")
    await section.get_by_role("button", name="Wins", exact=True).click()
    await assert_players(section, [LONG_NAME])
    await expect(section.locator("thead th")).to_have_count(5)
    old_started, old_release, old_delivered = fixture.hold_policy(False, 3, track_losses=True)
    await asyncio.wait_for(old_started.wait(), TIMEOUT_MS / 1000)
    new_started, new_release, _ = fixture.hold_policy(False, 3, track_losses=False)
    fixture.track_losses = False
    await asyncio.wait_for(new_started.wait(), TIMEOUT_MS / 1000)
    await expect(section.locator("thead th")).to_have_count(3)
    await expect(section.locator("tbody tr")).to_have_count(0)
    old_release.set()
    await asyncio.wait_for(old_delivered.wait(), TIMEOUT_MS / 1000)
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await expect(section.locator("tbody tr")).to_have_count(0)
    new_release.set()
    await assert_players(section, [LONG_NAME])
    await expect(section.locator("tbody tr td")).to_have_count(3)
    await expect(weapon).to_have_value("CHAIN")
    fixture.release_policies()
    fixture.track_losses = True


async def verify_weapon_choices(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    fixture.release_policies()
    fixture.hidden = False
    fixture.generic = True
    fixture.bear = False
    fixture.track_losses = True
    fixture.organizer = False
    fixture.viewer_player_id = None
    fixture.public_limit = 8
    await page.goto(f"{check.base_url}/demo-open/sidecomps/{fixture.comp_id}/results")
    section = page.get_by_role("region", name="Standings", exact=True)
    weapon = section.get_by_role("combobox", name="Weapon", exact=True)
    await expect(weapon.locator("option")).to_have_text(["All weapons", *WEAPONS.values()])
    await expect(section.locator("tbody tr")).to_have_count(8)
    await expect(section.get_by_role("button", name="Win %", exact=True)).to_be_visible()
    await expect(section.locator('th[aria-sort="descending"]')).to_have_text("Win %↓")
    await expect(section.locator("thead th")).to_have_count(5)

    for value, label in WEAPONS.items():
        await weapon.select_option(value)
        await assert_players(section, [WEAPON_NAMES[value]])
        await expect(section.locator(".sidecomp-standing-weapon")).to_have_text(label)
    # A loss-only entrant still competed and remains selectable.
    await expect(section.locator("tbody td:last-child")).to_have_text("0%")

    # Native select keyboard behavior keeps all options reachable without a pointer.
    await page.keyboard.press("Tab")
    await check_keyboard_focus(weapon)
    await page.keyboard.press("Home")
    await expect(weapon).to_have_value("")
    await expect(section.locator("tbody tr")).to_have_count(8)
    await page.keyboard.press("End")
    await expect(weapon).to_have_value("UNARMED")
    await assert_players(section, [WEAPON_NAMES["UNARMED"]])
    await page.keyboard.press("Home")
    await page.keyboard.press("ArrowDown")
    await expect(weapon).to_have_value("CHAIN")
    await assert_players(section, [LONG_NAME])
    await page.keyboard.press("Tab")
    # The standings scroll region is intentionally a keyboard tab stop.
    await expect(section.get_by_role("region", name="Standings standings", exact=True)).to_be_focused()
    await page.keyboard.press("Tab")
    wins = section.get_by_role("button", name="Wins", exact=True)
    await expect(wins).to_be_focused()
    await check_keyboard_focus(wins)
    await page.keyboard.press("Enter")
    await expect(section.locator('th[aria-sort="descending"]')).to_have_text("Wins↓")
    await weapon.select_option("")
    await expect(section.locator("tbody tr")).to_have_count(8)
    for width in (320, 390, 1280):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.evaluate("window.scrollTo(0, 0); document.activeElement?.blur()")
        await check_table_layout(page)
        await check.check_layout(page, "sidecomp-all-weapons", width)
        check.visited += 1

    # Polling removes an unavailable option and restores the complete table.
    await weapon.select_option("SKULL")
    await assert_players(section, [WEAPON_NAMES["SKULL"]])
    fixture.removed_weapon = "SKULL"
    await expect(weapon.locator('option[value="SKULL"]')).to_have_count(0)
    await expect(weapon).to_have_value("")
    await expect(section.locator("tbody tr")).to_have_count(7)
    fixture.removed_weapon = None
    await expect(weapon.locator("option")).to_have_count(9)
    await expect(section.locator("tbody tr")).to_have_count(8)

    # Registered entries without valid scores do not create selectable classes.
    await weapon.select_option("BOARD")
    await assert_players(section, [WEAPON_NAMES["BOARD"]])
    fixture.unplayed_weapons.add("BOARD")
    await expect(weapon.locator('option[value="BOARD"]')).to_have_count(0)
    await expect(weapon).to_have_value("")
    await expect(section.locator("tbody tr")).to_have_count(8)
    fixture.unplayed_weapons.clear()
    await expect(weapon.locator("option")).to_have_count(9)
    fixture.no_scores = True
    await expect(weapon).to_have_count(0)
    await expect(section.locator("tbody tr")).to_have_count(8)
    await expect(section.locator("tbody td:last-child")).to_have_text(["—"] * 8)
    fixture.no_scores = False
    await expect(weapon.locator("option")).to_have_count(9)
    fixture.empty = True
    await expect(section.get_by_text("No results yet.", exact=True)).to_be_visible()
    await expect(section.locator("tbody tr")).to_have_count(0)
    await expect(weapon).to_have_count(0)
    fixture.empty = False
    await expect(weapon.locator("option")).to_have_count(9)
    await expect(section.locator("tbody tr")).to_have_count(8)
    await page.set_viewport_size({"width": 390, "height": 900})


async def verify_appearance(check: SmokeCheck, page: Page) -> None:
    section = page.get_by_role("region", name="Standings", exact=True)
    weapon = section.get_by_role("combobox", name="Weapon", exact=True)
    for palette in PALETTES:
        for mode in ("light", "dark"):
            await choose(page, palette, mode)
            await preference(page, palette, mode)
            await close_menu(page)
            await page.keyboard.press("Tab")
            await check_keyboard_focus(weapon)
            await check_keyboard_focus(section.get_by_role("button", name="Wins", exact=True))
            await check_contrast(
                page,
                ".sidecomp-weapon-filter label, .sidecomp-weapon-filter select, "
                ".sidecomp-standings-table th, .sidecomp-standings-table .fw-semibold, "
                ".sidecomp-standings-table .text-muted, .sidecomp-standings-table .table-number",
            )
            await check_table_layout(page)
            await page.evaluate("window.scrollTo(0, 0); document.activeElement?.blur()")
            await check.check_layout(page, f"sidecomp-{palette}-{mode}", 390)
            check.visited += 1


async def verify_large_values(check: SmokeCheck, page: Page, fixture: StandingsFixture) -> None:
    """Unusually large counts use the accessible table scroll region."""
    section = page.get_by_role("region", name="Standings", exact=True)
    fixture.large_numbers = True
    try:
        await expect(section.locator("tbody tr:first-child td:first-child")).to_have_text("2147483601")
        await expect(section.locator("tbody tr:first-child td:nth-child(3)")).to_have_text("2147483647")
        for width in (320, 390, 1280):
            await page.set_viewport_size({"width": width, "height": 900})
            await check_table_layout(page, allow_horizontal_scroll=True)
            wrapper = section.get_by_role("region", name="Standings standings", exact=True)
            await expect(wrapper).to_have_attribute("tabindex", "0")
            assert await wrapper.evaluate("el => getComputedStyle(el).overflowX") == "auto"
            await check.check_layout(page, "sidecomp-large-values", width)
            check.visited += 1
    finally:
        fixture.large_numbers = False
        await page.set_viewport_size({"width": 390, "height": 900})
    await expect(section.locator("tbody tr:first-child td:first-child")).to_have_text("1")


async def verify_large_text(check: SmokeCheck, page: Page) -> None:
    """Check the standings at 200% text size independently of the shared header."""
    await page.set_viewport_size({"width": 320, "height": 900})
    await page.evaluate("document.documentElement.style.fontSize = '200%'")
    try:
        await check_table_layout(page, allow_horizontal_scroll=True)
        section = page.get_by_role("region", name="Standings", exact=True)
        bounds = await section.bounding_box()
        assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= 321, bounds
        if check.screenshots:
            await section.screenshot(path=str(check.screenshots / "0320-sidecomp-enlarged-text.png"))
        check.visited += 1
    finally:
        await page.evaluate("document.documentElement.style.removeProperty('font-size')")
        await page.set_viewport_size({"width": 390, "height": 900})


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        context = await browser.new_context(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
        context.set_default_timeout(TIMEOUT_MS)
        fixture = None
        try:
            marker = await api_json(context, check.base_url, "/_api/demo-info")
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER
            competitions = await api_json(context, check.base_url, "/_api/demo-open/sidecomps")
            fixture = StandingsFixture(competitions[0]["id"])
            await context.route("**/*", check.guard_requests)
            await context.route(f"**/_api/sidecomps/{fixture.comp_id}/standings*", fixture.handle)
            await context.route("**/_api/me", fixture.account)
            await context.route("**/_api/logout", fixture.logout)
            page = await context.new_page()
            check.observe(page)
            await check.workflow(page, "sidecomp-standings", 390, lambda: verify(check, page, fixture))
            await check.workflow(
                page, "sidecomp-personal-rank", 390, lambda: verify_personal_rank(check, page, fixture)
            )
            await check.workflow(page, "sidecomp-organizer", 390, lambda: verify_organizer(check, page, fixture))
            await check.workflow(
                page, "sidecomp-access-revocation", 390, lambda: verify_access_revocation(check, page, fixture)
            )
            await check.workflow(
                page, "sidecomp-account-change", 390, lambda: verify_account_change(check, page, fixture)
            )
            await check.workflow(page, "sidecomp-bear-pit", 390, lambda: verify_bear(check, page, fixture))
            await check.workflow(
                page, "sidecomp-loss-tracking", 390, lambda: verify_loss_tracking(check, page, fixture)
            )
            await check.workflow(
                page, "sidecomp-weapon-choices", 390, lambda: verify_weapon_choices(check, page, fixture)
            )
            await check.workflow(page, "sidecomp-appearance", 390, lambda: verify_appearance(check, page))
            await check.workflow(
                page, "sidecomp-large-number-regression", 390, lambda: verify_large_values(check, page, fixture)
            )
            await check.workflow(page, "sidecomp-large-text", 390, lambda: verify_large_text(check, page))
        finally:
            if fixture is not None:
                fixture.release_delay.set()
                for _, release, _ in fixture.held_policies.values():
                    release.set()
            await context.close()
            await browser.close()
    print(f"{check.visited} standings screens; {check.workflows} workflows; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures))


if __name__ == "__main__":
    sys.exit(asyncio.run(run(arguments(__doc__))))
