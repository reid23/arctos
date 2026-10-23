"""Check profile tabs, page banners, and organizer access on the local demo.

    uv run python scripts/check_profile_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-profiles

Uses the shared loopback/demo marker and mutation guard. Profile PUT and image
POST/DELETE responses and deterministic raster fixtures are fulfilled in the browser;
no profile or event changes reach the backend. The only real mutation is signing into a demo account.
"""

from __future__ import annotations

import asyncio
from email import policy
from email.parser import BytesParser
import json
from io import BytesIO
import re
import sys
from urllib.parse import urlsplit

from PIL import Image
from playwright.async_api import Error, Page, Route, async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu, isolated_page, open_menu
from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments

BANNER_KEY = "arctos.page-banners.hidden.v1"
INTRODUCTIONS = (
    ("/", "Events", "#event-search", "#event-discovery"),
    ("/leagues", "Leagues", "#league-search", "#page-content"),
    ("/players", "Players", "#player-search", "#page-content"),
    ("/teams", "Teams", "#team-search", "#page-content"),
    ("/stones", "Stones", "#stones-start-pause", "#stones-start-pause"),
)


async def restore_banner(page: Page) -> None:
    await open_menu(page)
    setting = page.get_by_role("checkbox", name="Show page introduction", exact=True)
    await expect(setting).not_to_be_checked()
    target = await page.locator('label[for="page-introduction-setting"]').bounding_box()
    assert target and target["width"] >= 44 and target["height"] >= 44, target
    await setting.check()
    await expect(page.locator("#page-banner")).to_be_visible()
    await expect(page.locator("#appearance-options")).to_be_visible()
    await setting.uncheck()
    await expect(page.locator("#page-banner")).to_be_hidden()
    await expect(page.locator("#appearance-options")).to_be_visible()
    await setting.check()
    await close_menu(page)


async def introductions(check: SmokeCheck, page: Page) -> None:
    """Hiding one introduction must not hide another page's introduction."""
    for path, title, target, focus in INTRODUCTIONS:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        banner = page.locator("#page-banner")
        await expect(banner).to_be_visible()
        await expect(page.locator("#page-banner-heading")).to_have_text(title)
        assert await banner.evaluate("el => getComputedStyle(el).textAlign") == "center"
        dismiss = page.locator("#page-banner-dismiss")
        box = await dismiss.bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44, box
        frame = await banner.bounding_box()
        assert frame and 0 <= box["y"] - frame["y"] <= 12
        assert abs(box["x"] + box["width"] - frame["x"] - frame["width"]) <= 12
        heading = await page.locator("#page-banner-heading").evaluate("""el => {
            const range = document.createRange(); range.selectNodeContents(el);
            const r=range.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};
        }""")
        assert abs(heading["x"] + heading["width"] / 2 - frame["x"] - frame["width"] / 2) <= 1
        assert min(box["x"] + box["width"], heading["x"] + heading["width"]) <= max(box["x"], heading["x"]) or min(
            box["y"] + box["height"], heading["y"] + heading["height"]
        ) <= max(box["y"], heading["y"])
        await expect(dismiss).to_have_attribute("aria-label", f"Collapse {title} introduction")
        await expect(dismiss).to_have_attribute("aria-controls", "page-banner-content")
        await expect(dismiss).to_have_attribute("aria-expanded", "true")
        await expect(dismiss.locator(".fa-chevron-up")).to_have_count(1)
        await expect(dismiss.locator(".fa-xmark")).to_have_count(0)
        if target.endswith("search"):
            await page.locator(target).fill("demo")
        await dismiss.focus()
        await page.keyboard.press("Enter")
        await expect(banner).to_be_hidden()
        await expect(dismiss).to_have_attribute("aria-expanded", "false")
        await expect(page.locator(focus)).to_be_focused()
        assert await page.evaluate("!['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)")
        if target.endswith("search"):
            await expect(page.locator(target)).to_have_value("demo")
        await page.reload(wait_until="domcontentloaded")
        await expect(page.locator(target)).to_be_visible()
        await expect(banner).to_be_hidden()
    stored = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", BANNER_KEY)
    assert set(stored) == {title.lower() for _, title, _, _ in INTRODUCTIONS}, stored
    for path, title, target, _focus in INTRODUCTIONS:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        await expect(page.locator(target)).to_be_visible()
        await expect(page.locator("#page-banner")).to_be_hidden()
        await restore_banner(page)
        await expect(page.locator("#page-banner")).to_be_visible()
        await page.reload(wait_until="domcontentloaded")
        await expect(page.locator("#page-banner")).to_be_visible()
        await check.check_layout(page, f"introduction-{title.lower()}", 320)
        check.visited += 1


async def legacy_introduction(check: SmokeCheck, page: Page) -> None:
    await page.goto(check.base_url + "/_api/demo-info", wait_until="domcontentloaded")
    await page.evaluate("localStorage.setItem('arctos.introduction.hidden.v1', 'true')")
    await page.goto(check.base_url, wait_until="domcontentloaded")
    await expect(page.locator("#event-search")).to_be_visible()
    await expect(page.locator("#page-banner")).to_be_hidden()
    await page.goto(check.base_url + "/players", wait_until="domcontentloaded")
    await expect(page.locator("#page-banner")).to_be_visible()
    await page.goto(check.base_url, wait_until="domcontentloaded")
    await expect(page.locator("#event-search")).to_be_visible()
    await restore_banner(page)
    await page.reload(wait_until="domcontentloaded")
    await expect(page.locator("#page-banner")).to_be_visible()


async def selected_profile(page: Page, kind: str, tab: str) -> None:
    await expect(page.locator(f"#{kind}-tab-{tab}")).to_have_attribute("aria-current", "page")
    await expect(page.locator(".profile-section:visible")).to_have_count(1)
    await expect(page.locator(".event-route-tabs [aria-current='page']")).to_have_count(1)


async def profile_composition(page: Page) -> None:
    await expect(page.locator(".profile-cover")).to_be_visible()
    await expect(page.locator(".profile-section.card, .profile-section > .card")).to_have_count(0)
    geometry = await page.locator(".profile-page").evaluate("""profile => {
        const box = selector => {const r = profile.querySelector(selector).getBoundingClientRect();
            return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
        return {cover:box('.profile-cover'),avatar:box('.profile-avatar'),identity:box('.profile-identity'),
            meta:box('.profile-meta'),tabs:box('.event-route-tabs'),
            alignment:getComputedStyle(profile.querySelector('.profile-identity')).textAlign};
    }""")
    cover, avatar, identity = (geometry[key] for key in ("cover", "avatar", "identity"))
    assert abs(cover["width"] / cover["height"] - 3) < 0.02, geometry
    assert avatar["top"] < cover["bottom"] < avatar["bottom"], geometry
    assert abs(avatar["width"] - avatar["height"]) <= 1, geometry
    assert geometry["alignment"] in ("start", "left"), geometry
    assert abs(identity["left"] - avatar["left"]) <= 2, geometry
    assert identity["top"] >= avatar["bottom"] - 1, geometry
    assert geometry["tabs"]["top"] >= geometry["meta"]["bottom"] - 1, geometry


async def public_profiles(check: SmokeCheck, page: Page) -> None:
    for width in (320, 360, 1280):
        await page.set_viewport_size({"width": width, "height": 900})
        for kind in ("player", "team"):
            path = f"/{kind}s/demo-{kind}"
            payload = await api_json(page.context, check.base_url, "/_api" + path)
            await page.goto(check.base_url + path, wait_until="domcontentloaded")
            await selected_profile(page, kind, "info")
            await profile_composition(page)
            await expect(page.get_by_role("link", name="Edit profile", exact=True)).to_have_count(0)
            await expect(page.locator("#profile-banner-file, #profile-banner-remove")).to_have_count(0)
            await expect(page.locator(".profile-event-stat dd")).to_have_text(str(payload["past_event_count"]))
            optional = (
                {"infractions": payload["player_notes"], "injuries": payload["injuries"]}
                if kind == "player"
                else {"notes": payload["team_notes"]}
            )
            if kind == "player":
                assert all(injury["show"] for injury in payload["injuries"]), "Guest profile exposed private injuries"
            for tab, records in optional.items():
                await expect(page.locator(f"#{kind}-tab-{tab}")).to_have_count(1 if records else 0)
                if not records:
                    await page.goto(check.base_url + path + "?tab=" + tab, wait_until="domcontentloaded")
                    await selected_profile(page, kind, "info")
            await page.goto(check.base_url + path + "?tab=history", wait_until="domcontentloaded")
            await selected_profile(page, kind, "history")
            await check.profile_rows_fit(page, ".profile-history-table")
            await page.locator(f"#{kind}-tab-info").click()
            await selected_profile(page, kind, "info")
            await page.go_back()
            await selected_profile(page, kind, "history")
            await page.go_forward()
            await selected_profile(page, kind, "info")
            links = page.locator(".event-route-tabs a")
            await links.first.focus()
            for index in range(1, await links.count()):
                await page.keyboard.press("Tab")
                await expect(links.nth(index)).to_be_focused()
            controls = await links.evaluate_all(
                "els => els.map(el => {const r=el.getBoundingClientRect();return {width:r.width,height:r.height};})"
            )
            assert all(c["width"] >= 44 and c["height"] >= 44 for c in controls), controls
            await check.check_layout(page, f"{kind}-public-tabs", width)
            check.visited += 1
    await page.set_viewport_size({"width": 360, "height": 900})


async def detail_banners(check: SmokeCheck, page: Page) -> None:
    """Legacy hidden entity keys cannot remove identity or metadata from details."""
    await page.goto(check.base_url + "/_api/demo-info", wait_until="domcontentloaded")
    for kind, path, next_path, entity_key in (
        ("player", "/players/demo-player", "/players/demo-player?tab=history", "player:demo-player"),
        ("team", "/teams/demo-team", "/teams/demo-team?tab=history", "team:demo-team"),
        ("event", "/demo-open", "/demo-open/results", "event:demo-open"),
        ("league", "/leagues/demo-coast-league", "/leagues/demo-coast-league/results", "league:demo-coast-league"),
    ):
        await page.evaluate(
            "([key, entity]) => localStorage.setItem(key, JSON.stringify(['events', entity]))",
            [BANNER_KEY, entity_key],
        )
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        heading = page.locator("#page-banner-heading")
        await expect(heading).to_be_visible()
        title = await heading.inner_text()

        async def visible_detail() -> None:
            banner = page.locator("#page-banner")
            await expect(banner).to_be_visible()
            await expect(banner).not_to_have_class(re.compile(r"\bis-collapsed\b"))
            await expect(heading).to_have_text(title)
            await expect(page.locator("#page-banner-dismiss")).to_have_count(0)
            if kind in ("event", "league"):
                await expect(page.locator(".event-hero-badges")).to_be_visible()
                await expect(page.locator(".event-hero-meta")).to_be_visible()
                await expect(page.locator(".event-hero-actions .btn").first).to_be_visible()
            else:
                await profile_composition(page)
                await expect(page.locator(".profile-event-stat")).to_be_visible()
            await expect(page.locator(".event-route-tabs")).to_be_visible()
            await page.locator("#utility-trigger").click()
            await expect(page.locator("#utility-options")).to_be_visible()
            await expect(page.locator("#page-banner-restore")).to_have_count(0)
            await page.get_by_role("button", name="Appearance", exact=True).click()
            await expect(page.locator("#appearance-options")).to_be_visible()
            await expect(page.locator("#page-introduction-setting")).to_have_count(0)
            await close_menu(page)

        await visible_detail()
        await page.goto(check.base_url + next_path, wait_until="domcontentloaded")
        await visible_detail()
        await page.locator(f"#{kind}-tab-info").click()
        await expect(page.locator(f"#{kind}-tab-info")).to_have_attribute("aria-current", "page")
        await visible_detail()
        await page.go_back(wait_until="domcontentloaded")
        await expect(page).to_have_url(check.base_url + next_path)
        await visible_detail()
        await page.go_forward(wait_until="domcontentloaded")
        await expect(page.locator(f"#{kind}-tab-info")).to_have_attribute("aria-current", "page")
        await visible_detail()
        await page.reload(wait_until="domcontentloaded")
        await visible_detail()
        await check.check_layout(page, f"{kind}-banner-always-visible", 360)
        check.visited += 1


def sample_png(width: int, height: int) -> bytes:
    """A deterministic color gradient makes crop movement observable in saved pixels."""
    image = Image.new("RGB", (width, height))
    image.putdata([(x * 255 // (width - 1), y * 255 // (height - 1), 80) for y in range(height) for x in range(width)])
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


PNG = sample_png(1200, 800)
SMALL_PNG = sample_png(120, 80)
URL_MONITOR = """window.cropUrls = {created: [], revoked: []};
const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
URL.createObjectURL = value => {const url = create(value); cropUrls.created.push(url); return url;};
URL.revokeObjectURL = url => {cropUrls.revoked.push(url); revoke(url);};"""


async def simple_position_controls(page: Page, viewport, zoom) -> None:
    """Reset keeps zoom; pointer and keyboard alternatives work without extra controls."""
    await expect(page.get_by_text("Fine positioning", exact=True)).to_have_count(0)
    await expect(page.get_by_text("Position controls", exact=True)).to_have_count(0)
    await expect(page.get_by_label("Horizontal position", exact=True)).to_have_count(0)
    await expect(page.get_by_label("Vertical position", exact=True)).to_have_count(0)
    reset = viewport.locator("..").get_by_role("button", name="Reset position", exact=True)
    await expect(reset).to_be_visible()
    reset_box = await reset.bounding_box()
    assert reset_box and reset_box["width"] >= 44 and reset_box["height"] >= 44
    image = viewport.locator("img")
    await reset.click()
    await expect(reset).to_be_focused()
    await expect(zoom).to_have_value("3")
    await page.wait_for_function(
        """el => {
        const frame=el.getBoundingClientRect(), image=el.querySelector('img').getBoundingClientRect();
        return Math.abs(frame.x+frame.width/2-image.x-image.width/2)<1 &&
            Math.abs(frame.y+frame.height/2-image.y-image.height/2)<1;
    }""",
        arg=await viewport.element_handle(),
    )
    centered = await image.get_attribute("style")
    await viewport.scroll_into_view_if_needed()
    box = await viewport.bounding_box()
    assert box
    # Upper-right stays clear of the reset and camera overlays, including the 88px avatar.
    await viewport.tap(position={"x": box["width"] * 0.7, "y": box["height"] * 0.3})
    await expect(image).not_to_have_attribute("style", centered)
    await expect(zoom).to_have_value("3")
    await reset.click()
    await expect(image).to_have_attribute("style", centered)
    await viewport.focus()
    await page.keyboard.press("ArrowLeft")
    await expect(image).not_to_have_attribute("style", centered)
    await reset.click()
    await expect(image).to_have_attribute("style", centered)
    # A drag must not perform an extra tap-to-center action when released.
    await viewport.scroll_into_view_if_needed()
    box = await viewport.bounding_box()
    assert box
    x, y = box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.3
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x - 15, y + 8, steps=3)
    await expect(image).not_to_have_attribute("style", centered)
    dragged = await image.get_attribute("style")
    await page.mouse.up()
    await expect(image).to_have_attribute("style", dragged)
    await reset.click()
    await expect(image).to_have_attribute("style", centered)
    await expect(zoom).to_have_value("3")


async def adjust_crop(page: Page, crop_id: str) -> None:
    viewport = page.locator("#" + crop_id)
    zoom = page.locator("#" + crop_id + "-zoom")
    await expect(page.locator(".image-crop-editor input[type=range]")).to_have_count(1)
    await zoom.focus()
    await page.keyboard.press("End")
    await expect(zoom).to_have_value("3")
    await viewport.scroll_into_view_if_needed()
    image = viewport.locator("img")
    style = await image.get_attribute("style")
    box = await viewport.bounding_box()
    assert box
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 30, y + 12, steps=5)
    await page.mouse.up()
    await expect(image).not_to_have_attribute("style", style)
    if crop_id == "image-crop-avatar":
        style = await image.get_attribute("style")
        cdp = await page.context.new_cdp_session(page)
        touch_active = False
        try:
            point = {"x": x, "y": y, "id": 1}
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
            touch_active = True
            await cdp.send(
                "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{**point, "x": x - 20, "y": y - 15}]}
            )
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            touch_active = False
            await expect(image).not_to_have_attribute("style", style)
        finally:
            if touch_active:
                await cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
            await cdp.detach()
    await simple_position_controls(page, viewport, zoom)


async def image_upload(check: SmokeCheck, page: Page, kind: str, avatar: bool = False) -> None:
    assert kind in ("tournament", "league"), "Profile images use the staged final Save workflow"
    profile = False
    noun = "photo" if avatar else "banner"
    field = "profile_photo" if avatar else "banner_photo"
    owner = f"demo-{kind}" if profile else DEMO_USER
    await check.login(page, owner)
    path = f"/{kind}s/{owner}" if profile else "/demo-open" if kind == "tournament" else "/leagues/demo-coast-league"
    api_path = "/_api" + ("/tournaments" if kind == "tournament" else "") + path
    endpoint = api_path + ("/profile-photo" if avatar else "/banner-photo")
    edit_path = path + ("/edit" if profile else "/settings")
    data = await api_json(page.context, check.base_url, api_path)
    current = "smoke-profile/old.png"
    source = SMALL_PNG if avatar else PNG
    error = "Smoke check: image upload failed. Existing image kept."
    succeed = False
    writes, images = [], []
    gate = asyncio.Event()
    request_seen = asyncio.Event()
    check.mock_gate = gate

    async def fixture(route: Route) -> None:
        nonlocal current
        request, parsed = route.request, urlsplit(route.request.url)
        if parsed.netloc != check.origin:
            await route.fallback()
        elif request.method == "GET" and parsed.path.startswith("/static/smoke-profile/"):
            await route.fulfill(
                body=images[-1] if images and parsed.path.endswith("new.png") else source, content_type="image/png"
            )
        elif request.method == "GET" and parsed.path == api_path:
            await route.fulfill(json={**data, kind: {**data[kind], field: current}})
        elif parsed.path == endpoint and request.method in ("POST", "DELETE"):
            writes.append(request.method)
            if request.method == "POST":
                images.append(request.post_data_buffer)
                request_seen.set()
                await gate.wait()
            if request.method == "DELETE":
                current = None
                await route.fulfill(json={"success": True})
            elif not succeed:
                await route.fulfill(json={"success": False, "error": error})
            else:
                current = "smoke-profile/new.png"
                await route.fulfill(json={"success": True, "path": current})
        else:
            await route.fallback()

    await page.add_init_script(URL_MONITOR)
    await page.context.route("**/*", fixture)
    try:
        await page.goto(check.base_url + edit_path, wait_until="domcontentloaded")
        upload = page.locator("#profile-photo-file" if avatar else "#profile-banner-file")
        await expect(upload).to_be_visible()
        palette, mode = {
            ("player", False): ("california", "light"),
            ("team", False): ("field", "dark"),
            ("tournament", False): ("neutral", "light"),
            ("league", False): ("california", "dark"),
            ("player", True): ("field", "light"),
            ("team", True): ("neutral", "dark"),
        }[kind, avatar]
        await choose(page, palette, mode)
        await close_menu(page)
        widget = page.get_by_role("region", name="Profile photo" if avatar else "Banner image", exact=True)
        crop_id = "image-crop-avatar" if avatar else "image-crop-banner"
        crop = page.locator("#" + crop_id)
        save_form = page.locator("main form button[type=submit]").first
        save_crop = widget.get_by_role("button", name="Save " + noun, exact=True)
        draft = page.locator("#about") if kind == "league" else page.locator('main form input[type="text"]').first
        await draft.fill("Unsaved local details")
        registration_open = None
        if not profile:
            await page.locator("#team_reg_fee").fill("123.45")
            registration_open = not await page.locator("#team_registration_open").is_checked()
            await page.locator('label[for="team_registration_open"]').click()

        async def draft_retained() -> None:
            await expect(draft).to_have_value("Unsaved local details")
            if not profile:
                await expect(page.locator("#team_reg_fee")).to_have_value("123.45")
                await expect(page.locator("#team_registration_open")).to_be_checked(checked=registration_open)

        preview = widget.locator(".photo-upload-preview" if avatar else ".banner-upload-preview")
        await expect(preview).to_have_attribute("src", check.base_url + "/static/" + current)
        if kind == "player" and not avatar:
            for name, body, message in (
                ("broken.png", b"not an image", "valid JPEG"),
                ("large.png", PNG + bytes(8 * 1024 * 1024), "8 MB"),
            ):
                await upload.set_input_files({"name": name, "mimeType": "image/png", "buffer": body})
                await expect(widget.get_by_role("alert")).to_contain_text(message)
                await expect(crop).to_have_count(0)
                await expect(save_form).to_be_enabled()
            assert not writes

        await upload.set_input_files({"name": "cancel.png", "mimeType": "image/png", "buffer": source})
        await expect(crop).to_be_visible()
        cancelled_url = await crop.locator("img").get_attribute("src")
        await expect(page.locator("#" + crop_id + "-zoom")).to_be_focused()
        await expect(save_form).to_be_disabled()
        await adjust_crop(page, crop_id)
        assert not writes, "Choosing or adjusting a crop uploaded it before Save"
        await widget.get_by_role("button", name="Cancel", exact=True).click()
        await expect(crop).to_have_count(0)
        await expect(upload).to_be_focused()
        await expect(save_form).to_be_enabled()
        assert await page.evaluate("url => cropUrls.revoked.includes(url)", cancelled_url)
        await draft_retained()

        await upload.set_input_files({"name": "save.png", "mimeType": "image/png", "buffer": source})
        await expect(crop).to_be_visible()
        saved_url = await crop.locator("img").get_attribute("src")
        await adjust_crop(page, crop_id)
        positions = await widget.locator("input[type=range]").evaluate_all("els => els.map(el => el.value)")
        await check_contrast(
            page,
            ".image-crop-editor, .image-crop-editor .form-label, .image-crop-actions .btn, .image-position-reset, .image-upload > .form-text",
        )
        await check.check_layout(page, f"{kind}-{noun}-crop-{palette}-{mode}", 320)
        check.visited += 1
        await save_crop.click()
        await asyncio.wait_for(request_seen.wait(), 10)
        await expect(save_form).to_be_disabled()
        gate.set()
        await expect(widget.get_by_role("alert")).to_have_text(error)
        await expect(crop).to_be_visible()
        await expect(preview).to_have_attribute("src", check.base_url + "/static/" + current)
        await expect(page.locator(".event-banner-image img")).to_have_attribute(
            "src", check.base_url + "/static/" + current
        )
        assert await widget.locator("input[type=range]").evaluate_all("els => els.map(el => el.value)") == positions
        await draft_retained()
        succeed = True
        gate.clear()
        request_seen.clear()
        await save_crop.click()
        await asyncio.wait_for(request_seen.wait(), 10)
        gate.set()
        await expect(widget.get_by_role("status")).to_have_text(noun.title() + " updated.")
        await expect(crop).to_have_count(0)
        await expect(upload).to_be_focused()
        await expect(save_form).to_be_enabled()
        await draft_retained()
        assert images[0] == images[1], "Retry changed the user's selected crop"
        with Image.open(BytesIO(images[-1])) as result, Image.open(BytesIO(source)) as original:
            assert result.format == "PNG"
            assert result.width == result.height * (1 if avatar else 3), result.size
            assert result.width <= (512 if avatar else 1500) and result.height <= (512 if avatar else 500), result.size
            assert result.width <= original.width / 3 + 1 and result.height <= original.height / 3 + 1, (
                "Zoomed crop was upscaled"
            )
            center = result.convert("RGB").getpixel((result.width // 2, result.height // 2))
            original_center = original.getpixel((original.width // 2, original.height // 2))
            assert sum(abs(a - b) for a, b in zip(center, original_center)) <= 5, (
                "Saved crop did not preserve reset position"
            )
        assert await page.evaluate("url => cropUrls.revoked.includes(url)", saved_url)
        await expect(page.locator(".event-banner-image img")).to_have_attribute(
            "src", check.base_url + "/static/smoke-profile/new.png"
        )
        viewer = await page.context.new_page()
        check.observe(viewer)
        try:
            await viewer.goto(check.base_url + path, wait_until="domcontentloaded")
            image = ".profile-avatar" if avatar else ".profile-cover-image" if profile else ".event-banner-image img"
            await expect(viewer.locator(image)).to_have_attribute(
                "src", check.base_url + "/static/smoke-profile/new.png"
            )
            await viewer.locator(image).evaluate("img => img.decode()")
            await check.check_layout(viewer, f"{kind}-uploaded-{noun}", 320)
            check.visited += 1
        finally:
            await viewer.close()
        await widget.get_by_role("button", name="Remove " + noun, exact=True).click()
        await expect(widget.get_by_role("status")).to_have_text(noun.title() + " removed.")
        await expect(preview).to_have_count(0)
        await expect(page.locator(".event-banner-image img")).to_have_count(0)
        await expect(page.locator(".event-banner-image")).to_be_visible()
        await draft_retained()
        assert writes == ["POST", "POST", "DELETE"], writes
        await check.check_layout(page, f"{kind}-{noun}-upload", 320)
        check.visited += 1
        await upload.set_input_files({"name": "leave.png", "mimeType": "image/png", "buffer": source})
        await expect(crop).to_be_visible()
        await (
            page.get_by_role("navigation", name="Main navigation")
            .get_by_role("link", name="Events", exact=True)
            .click()
        )
        await expect(page.locator("#event-search")).to_be_visible()
        assert await page.evaluate("cropUrls.created.every(url => cropUrls.revoked.includes(url))"), (
            "Crop object URL leaked on route change"
        )
        assert writes == ["POST", "POST", "DELETE"], "Leaving the crop submitted it"
    finally:
        gate.set()
        await page.context.unroute("**/*", fixture)


def multipart_parts(request) -> dict[str, bytes]:
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data;"), content_type
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.post_data_buffer
    )
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        assert name not in parts, f"Duplicate profile field: {name}"
        parts[name] = part.get_payload(decode=True)
    return parts


async def staged_adjust_crop(page: Page, avatar: bool) -> None:
    viewport = page.locator("#image-crop-avatar" if avatar else "#image-crop-banner")
    zoom = page.locator("#profile-image-zoom")
    await expect(page.locator(".profile-image-controls input[type=range]")).to_have_count(1)
    await zoom.focus()
    await page.keyboard.press("End")
    await expect(zoom).to_have_value("3")
    image = viewport.locator("img")
    style = await image.get_attribute("style")
    await viewport.focus()
    await page.keyboard.press("ArrowLeft")
    await expect(image).not_to_have_attribute("style", style)
    style = await image.get_attribute("style")
    await viewport.scroll_into_view_if_needed()
    box = await viewport.bounding_box()
    assert box
    x, y = box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.3
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x - 20, y + 10, steps=5)
    await page.mouse.up()
    await expect(image).not_to_have_attribute("style", style)
    if avatar:
        style = await image.get_attribute("style")
        cdp = await page.context.new_cdp_session(page)
        try:
            point = {"x": x, "y": y, "id": 1}
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{**point, "x": x - 15}]})
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            await expect(image).not_to_have_attribute("style", style)
        finally:
            await cdp.detach()
    await simple_position_controls(page, viewport, zoom)


async def staged_profile_edit(check: SmokeCheck, page: Page, kind: str) -> None:
    """Profile fields and both images remain local until one guarded multipart Save."""
    owner = f"demo-{kind}"
    await check.login(page, owner)
    path, api_path = f"/{kind}s/{owner}", f"/_api/{kind}s/{owner}"
    data = await api_json(page.context, check.base_url, api_path)
    data[kind].update(profile_photo="smoke-profile/photo-old.png", banner_photo="smoke-profile/banner-old.png")
    rasters = {"photo-old.png": SMALL_PNG, "banner-old.png": PNG}
    writes = []
    succeed = False
    error = "Smoke check: profile save failed. Your changes are still here."
    gate, seen = asyncio.Event(), asyncio.Event()
    check.mock_gate = gate

    async def fixture(route: Route) -> None:
        request, parsed = route.request, urlsplit(route.request.url)
        if parsed.netloc != check.origin:
            await route.fallback()
        elif request.method == "GET" and parsed.path.startswith("/static/smoke-profile/"):
            await route.fulfill(body=rasters[parsed.path.rsplit("/", 1)[1]], content_type="image/png")
        elif request.method == "GET" and parsed.path == api_path:
            await route.fulfill(json=data)
        elif request.method == "PUT" and parsed.path == api_path:
            parts = multipart_parts(request)
            writes.append(parts)
            seen.set()
            await gate.wait()
            if not succeed:
                await route.fulfill(json={"success": False, "error": error})
            else:
                data[kind].update(json.loads(parts["profile"]))
                for field, basename in (("profile_photo", "photo-new.png"), ("banner_photo", "banner-new.png")):
                    if field in parts:
                        rasters[basename] = parts[field]
                        data[kind][field] = "smoke-profile/" + basename
                    elif parts.get("remove_" + field) == b"true":
                        data[kind][field] = None
                await route.fulfill(json={"success": True})
        else:
            await route.fallback()

    async def image_menu(avatar: bool, action: str) -> None:
        noun = "photo" if avatar else "banner"
        await page.locator(f"#profile-{noun}-options-trigger").click()
        await page.locator(f"#profile-{noun}-options").get_by_role("button", name=action, exact=True).click()

    async def choose_image(avatar: bool) -> None:
        async with page.expect_file_chooser() as chooser:
            await image_menu(avatar, "Replace image")
        await (await chooser.value).set_files(
            {"name": "draft.png", "mimeType": "image/png", "buffer": SMALL_PNG if avatar else PNG}
        )
        crop = page.locator("#image-crop-avatar" if avatar else "#image-crop-banner")
        await expect(crop.locator("img")).to_have_attribute("src", re.compile(r"^blob:"))
        await expect(crop).to_be_focused()
        await expect(page.locator("#profile-image-zoom")).to_be_visible()

    async def fields_visible() -> None:
        edit = page.get_by_role("button", name="Edit details", exact=True)
        if await edit.is_visible():
            await edit.click()

    async def preview_visible() -> None:
        preview = page.get_by_role("button", name="Preview profile", exact=True)
        if await preview.is_visible():
            await preview.click()

    await page.add_init_script(URL_MONITOR)
    await page.context.route("**/*", fixture)
    try:
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        form = page.locator("#profile-edit-form")
        save = form.locator("button[type=submit]")
        await expect(form).to_be_visible()
        if kind == "team":
            await expect(page.locator("#profile-edit-pronouns, .player-pompfen-editor")).to_have_count(0)
        await expect(form.locator(".card")).to_have_count(0)
        await expect(save).to_be_disabled()
        settings = page.locator(".profile-account-settings")
        await settings.locator("summary").click()
        await expect(settings.get_by_role("heading", name="Connected apps", exact=True)).to_be_visible()
        await settings.locator("summary").click()
        await page.locator("#profile-edit-name").fill("Local profile preview")
        await page.locator("#profile-edit-location").fill("Preview City")
        await page.locator("#profile-edit-about").fill("**Unsaved preview bio**")
        if kind == "player":
            await page.locator("#profile-edit-phone").fill("555-0100-private")
        await preview_visible()
        await expect(page.locator("#profile-preview-name")).to_have_text("Local profile preview")
        await expect(page.locator("#profile-edit-preview .profile-location")).to_have_text("Preview City")
        await expect(page.locator(".profile-preview-content")).to_contain_text("Unsaved preview bio")
        await expect(page.locator("#profile-edit-preview")).not_to_contain_text("555-0100-private")
        await page.set_viewport_size({"width": 1280, "height": 900})
        await page.locator("#profile-edit-name").fill("Updated live name")
        await expect(page.locator("#profile-preview-name")).to_have_text("Updated live name")
        await expect(page.locator(".profile-preview-content strong")).to_have_text("Unsaved preview bio")
        await check.check_layout(page, f"{kind}-live-profile-editor-desktop", 1280)
        check.visited += 1
        await page.set_viewport_size({"width": 320, "height": 900})
        await fields_visible()
        await choose_image(False)
        await choose_image(True)
        assert not writes, "Selecting images wrote to the backend"
        await image_menu(True, "Remove photo")
        assert not writes, "Removing a staged image wrote to the backend"
        await form.get_by_role("button", name="Cancel", exact=True).click()
        await expect(page).to_have_url(check.base_url + path)
        assert await page.evaluate("cropUrls.created.every(url => cropUrls.revoked.includes(url))")
        await expect(page.locator(".profile-avatar[src]")).to_have_attribute(
            "src", check.base_url + "/static/smoke-profile/photo-old.png"
        )
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        await expect(save).to_be_disabled()
        await image_menu(False, "Remove banner")
        await image_menu(True, "Remove photo")
        await expect(page.locator("#image-crop-banner img, #image-crop-avatar img")).to_have_count(0)
        assert not writes, "Removal must be staged until final Save"
        await form.get_by_role("button", name="Cancel", exact=True).click()
        await expect(page).to_have_url(check.base_url + path)
        await expect(page.locator(".profile-cover-image")).to_have_attribute(
            "src", check.base_url + "/static/smoke-profile/banner-old.png"
        )
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        await page.locator("#profile-edit-name").fill("")
        await preview_visible()
        await save.click()
        await expect(page.locator(".profile-edit-error")).to_contain_text("Enter a display name")
        await expect(page.locator("#profile-edit-name")).to_be_visible()
        await expect(page.locator("#profile-edit-name")).to_be_focused()
        assert not writes, "Required name validation sent a profile update"
        await page.locator("#profile-edit-name").fill("Saved draft name")
        await expect(page.locator(".profile-edit-error")).to_have_count(0)
        await page.locator("#profile-edit-location").fill("Saved draft city")
        await page.locator("#profile-edit-about").fill("**Saved draft bio**")
        if kind == "player":
            for filename, body, message in (
                ("broken.png", b"not an image", "valid JPEG"),
                ("large.png", PNG + bytes(8 * 1024 * 1024), "8 MB"),
            ):
                await page.locator("#profile-banner-file").set_input_files(
                    {"name": filename, "mimeType": "image/png", "buffer": body}
                )
                await expect(page.locator(".profile-image-message[role=alert]")).to_contain_text(message)
                await expect(page.locator("#profile-image-zoom")).to_have_count(0)
                await expect(save).to_be_enabled()
        await image_menu(False, "Adjust position")
        await expect(page.locator("#image-crop-banner img")).to_have_attribute("src", re.compile(r"^blob:"))
        assert not writes, "Adjusting an existing image uploaded it"
        await choose_image(False)
        await staged_adjust_crop(page, False)
        banner_style = await page.locator("#image-crop-banner img").get_attribute("style")
        await choose_image(True)
        await staged_adjust_crop(page, True)
        await expect(page.locator("#image-crop-banner img")).to_have_attribute("style", banner_style)
        styles = await page.locator(".profile-image-viewport img").evaluate_all(
            "els => els.map(el => el.getAttribute('style'))"
        )
        await page.get_by_role("button", name="Done", exact=True).click()
        await expect(page.locator("#profile-image-zoom")).to_have_count(0)
        assert not writes, "Done must retain the crop without uploading"
        await image_menu(True, "Adjust position")
        await expect(page.locator("#profile-image-zoom")).to_have_value("3")
        for palette in PALETTES:
            mode = "light" if kind == "player" else "dark"
            await choose(page, palette, mode)
            await close_menu(page)
            await check_contrast(
                page,
                ".profile-image-controls, .profile-image-controls .btn, .profile-image-camera, .profile-image-reset, .profile-editor-toolbar .btn",
            )
            await check.check_layout(page, f"{kind}-staged-profile-{palette}-{mode}", 320)
            check.visited += 1
        await preview_visible()
        await save.click()
        await asyncio.wait_for(seen.wait(), 10)
        await expect(save).to_be_disabled()
        await form.dispatch_event("submit")
        assert len(writes) == 1, "Pending Save submitted twice"
        gate.set()
        await expect(page.locator(".profile-edit-error")).to_have_text(error)
        await expect(save).to_be_enabled()
        await expect(page.locator("#profile-edit-name")).to_have_value("Saved draft name")
        await expect(page.locator("#profile-edit-location")).to_have_value("Saved draft city")
        await expect(page.locator("#profile-edit-about")).to_have_value("**Saved draft bio**")
        assert (
            await page.locator(".profile-image-viewport img").evaluate_all(
                "els => els.map(el => el.getAttribute('style'))"
            )
            == styles
        )
        succeed = True
        seen.clear()
        gate.clear()
        await save.click()
        await asyncio.wait_for(seen.wait(), 10)
        gate.set()
        await expect(page).to_have_url(check.base_url + path)
        assert len(writes) == 2 and writes[0] == writes[1], "Retry must preserve both crops and all fields"
        assert set(writes[-1]) == {"profile", "profile_photo", "banner_photo"}, writes[-1].keys()
        profile = json.loads(writes[-1]["profile"])
        if kind == "team":
            assert "pronouns" not in profile and "pompfen" not in profile
        assert profile["name"] == "Saved draft name" and profile["location"] == "Saved draft city"
        assert profile["about" if kind == "team" else "bio"] == "**Saved draft bio**"
        for field, maximum, aspect, source in (("profile_photo", 512, 1, SMALL_PNG), ("banner_photo", 1500, 3, PNG)):
            with Image.open(BytesIO(writes[-1][field])) as image, Image.open(BytesIO(source)) as original:
                assert image.format == "PNG" and image.width == image.height * aspect and image.width <= maximum
                assert image.width <= original.width / 3 + 1 and image.height <= original.height / 3 + 1
        assert await page.evaluate("cropUrls.created.every(url => cropUrls.revoked.includes(url))")
        await expect(page.locator(".profile-cover-image")).to_have_attribute(
            "src", check.base_url + "/static/smoke-profile/banner-new.png"
        )
        await expect(page.locator(".profile-avatar[src]")).to_have_attribute(
            "src", check.base_url + "/static/smoke-profile/photo-new.png"
        )
        await page.locator(".profile-cover-image, .profile-avatar[src]").evaluate_all(
            "els => Promise.all(els.map(img => img.decode()))"
        )
        await check.check_layout(page, f"{kind}-staged-profile-saved", 320)
        check.visited += 1
    finally:
        gate.set()
        await page.context.unroute("**/*", fixture)


async def organizer_tools(check: SmokeCheck, page: Page) -> None:
    paths = ("/demo-open", "/leagues/demo-coast-league")
    for path in paths:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        await expect(page.locator("#page-banner-heading")).to_be_visible()
        await expect(page.locator("#organizer-tools-trigger")).to_have_count(0)
    await check.login(page)
    for path in paths:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        trigger = page.locator("#organizer-tools-trigger")
        options = page.locator("#organizer-tools-options")
        await expect(trigger).to_be_visible()
        await expect(options).to_be_hidden()
        await trigger.focus()
        await page.keyboard.press("Enter")
        await expect(trigger).to_be_focused()
        await expect(options).to_be_visible()
        settings = options.get_by_role("link", name="Settings", exact=True)
        await page.keyboard.press("Tab")
        await expect(settings).to_be_focused()
        await expect(settings).to_have_attribute("href", path + "/settings")
        controls = await options.locator("a, button").evaluate_all(
            "els => els.map(el => {const r=el.getBoundingClientRect();return {w:r.width,h:r.height,left:r.left,right:r.right};})"
        )
        assert all(c["w"] >= 44 and c["h"] >= 44 and c["left"] >= -1 and c["right"] <= 321 for c in controls), controls
        await page.keyboard.press("Escape")
        await expect(options).to_be_hidden()
        await expect(trigger).to_be_focused()
        await trigger.click()
        await page.locator(".organizer-tools-dismiss").click(position={"x": 2, "y": 2})
        await expect(options).to_be_hidden()
        await expect(trigger).to_be_focused()
        await expect(page.locator("#page-banner-dismiss")).to_have_count(0)
        await expect(page.locator(".event-hero-meta")).to_be_visible()
        await trigger.click()
        await check.check_layout(page, f"organizer-tools-{'league' if path.startswith('/leagues') else 'event'}", 320)
        check.visited += 1
        await settings.click()
        await expect(page).to_have_url(check.base_url + path + "/settings")
        await expect(page.locator("#profile-banner-file")).to_be_visible()


async def owner_profile_tabs(check: SmokeCheck, page: Page, kind: str) -> None:
    if kind == "team":
        await check.login(page, "demo-team")
        await check.team_profile_tabs(page)
    else:
        await check.player_profile_tabs(page)
    await check.check_layout(page, f"{kind}-owner-tabs", 320)
    check.visited += 1


async def player_identity(check: SmokeCheck, page: Page) -> None:
    """Optional public player attributes stay local until the existing atomic Save."""
    await check.login(page, "demo-player")
    path, api_path = "/players/demo-player", "/_api/players/demo-player"
    data = await api_json(page.context, check.base_url, api_path)
    data["player"].update(pronouns=None, pompfen=[])
    writes = []
    succeed = False
    gate, seen = asyncio.Event(), asyncio.Event()
    check.mock_gate = gate

    async def compact_metadata(scope: str) -> None:
        header = page.locator(scope)
        icons = header.get_by_role("list", name="Pompfen", exact=True)
        await expect(icons.locator(".player-pompfen-item")).to_have_count(8)
        await expect(icons.locator(".player-pompfen-item.is-selected")).to_have_count(2)
        await expect(icons.locator("button, input, h2, .fa-check")).to_have_count(0)
        await expect(icons.locator('[data-pompfen="CHAIN"]')).to_have_attribute("title", "Chain (selected)")
        await expect(icons.locator('[data-pompfen="LONG"]')).to_have_attribute("title", "Long (not selected)")
        await expect(icons.locator('[data-pompfen="CHAIN"] .visually-hidden')).to_have_text("Selected: Chain")
        metrics = await icons.evaluate("""el => {
            const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,height:r.height}};
            const tabs=el.closest('.profile-page').querySelector('.event-route-tabs, .profile-preview-tabs');
            return {row:box(el),stat:box(el.parentElement.querySelector('.profile-event-stat')),tabs:box(tabs),
                items:[...el.children].map(item=>{const s=getComputedStyle(item),symbol=item.querySelector('.player-pompfen-symbol');
                    return {...box(item),border:s.borderWidth,bg:s.backgroundColor,opacity:getComputedStyle(symbol).opacity}})};
        }""")
        assert metrics["row"]["height"] == 28 and metrics["row"]["bottom"] <= metrics["tabs"]["y"], metrics
        assert len({item["y"] for item in metrics["items"]}) == 1, metrics
        assert all(
            item["height"] == 28 and item["border"] == "0px" and item["bg"] == "rgba(0, 0, 0, 0)"
            for item in metrics["items"]
        ), metrics
        assert metrics["items"][0]["opacity"] == "1" and float(metrics["items"][1]["opacity"]) < 0.5, metrics
        if page.viewport_size["width"] <= 575:
            assert metrics["row"]["y"] >= metrics["stat"]["bottom"], metrics
        else:
            assert (
                metrics["row"]["x"] >= metrics["stat"]["right"] and abs(metrics["row"]["y"] - metrics["stat"]["y"]) <= 8
            ), metrics

    async def fixture(route: Route) -> None:
        request, parsed = route.request, urlsplit(route.request.url)
        if parsed.netloc != check.origin or parsed.path != api_path:
            await route.fallback()
        elif request.method == "GET":
            await route.fulfill(json=data)
        elif request.method == "PUT":
            parts = multipart_parts(request)
            assert set(parts) == {"profile"}, "Identity-only Save uploaded an unchanged image"
            payload = json.loads(parts["profile"])
            writes.append(payload)
            seen.set()
            await gate.wait()
            if succeed:
                data["player"].update(payload)
                data["player"]["pronouns"] = payload["pronouns"].strip() or None
                await route.fulfill(json={"success": True})
            else:
                await route.fulfill(json={"success": False, "error": "Profile fixture: please retry."})
        else:
            await route.fallback()

    async def edit_choices() -> None:
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        pronouns = page.locator("#profile-edit-pronouns")
        await expect(pronouns).to_have_attribute("maxlength", "64")
        await pronouns.fill("they/them")
        choices = page.locator(".player-pompfen-editor")
        await expect(choices.get_by_role("checkbox")).to_have_count(8)
        for name in ("Chain", "Staff"):
            await choices.get_by_role("checkbox", name=name, exact=True).check()
        long = choices.get_by_role("checkbox", name="Long", exact=True)
        await long.focus()
        await page.keyboard.press("Space")
        await expect(long).to_be_checked()
        await page.keyboard.press("Space")
        await expect(long).not_to_be_checked()
        boxes = await choices.locator("label").evaluate_all(
            "els=>els.map(el=>{const r=el.getBoundingClientRect();return [r.width,r.height]})"
        )
        assert all(width >= 44 and height >= 44 for width, height in boxes), boxes
        await page.get_by_role("button", name="Preview profile", exact=True).click()
        await expect(page.locator("#profile-edit-preview .profile-pronouns")).to_contain_text("they/them")
        await compact_metadata("#profile-edit-preview .profile-detail-header")
        await expect(page.locator(".profile-preview-content .player-pompfen")).to_have_count(0)

    await page.context.route("**/_api/**", fixture)
    try:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        await expect(page.locator("#page-banner-heading")).to_be_visible()
        await expect(page.locator(".profile-pronouns, .player-pompfen")).to_have_count(0)
        await edit_choices()
        await page.get_by_role("button", name="Cancel", exact=True).click()
        await expect(page).to_have_url(check.base_url + path)
        await expect(page.locator(".profile-pronouns, .player-pompfen")).to_have_count(0)
        assert not writes, "Cancel saved new player attributes"
        await edit_choices()
        save = page.locator("#profile-edit-form button[type=submit]")
        await save.click()
        await asyncio.wait_for(seen.wait(), 10)
        await expect(save).to_be_disabled()
        gate.set()
        await expect(page.locator(".profile-edit-error")).to_have_text("Profile fixture: please retry.")
        await expect(page.locator("#profile-edit-pronouns")).to_have_value("they/them")
        for code in ("CHAIN", "STAFF"):
            await expect(page.locator(f"#profile-pompfen-{code}")).to_be_checked()
        succeed = True
        seen.clear()
        gate.clear()
        await save.click()
        await asyncio.wait_for(seen.wait(), 10)
        gate.set()
        await expect(page).to_have_url(check.base_url + path)
        assert writes == [writes[0], writes[0]] and writes[0]["pronouns"] == "they/them"
        assert writes[0]["pompfen"] == ["CHAIN", "STAFF"]
        await expect(page.locator(".profile-pronouns")).to_contain_text("they/them")
        await compact_metadata(".profile-detail-header")
        await expect(page.locator(".profile-detail-content .player-pompfen")).to_have_count(0)
        for mode in ("light", "dark"):
            for palette in PALETTES:
                await choose(page, palette, mode)
                await close_menu(page)
                await check_contrast(page, ".profile-pronouns")
                await compact_metadata(".profile-detail-header")
                await check.check_layout(page, f"player-pronouns-pompfen-{palette}-{mode}", 320)
                check.visited += 1
        await page.set_viewport_size({"width": 1280, "height": 900})
        await choose(page, "california", "light")
        await close_menu(page)
        await compact_metadata(".profile-detail-header")
        await check.check_layout(page, "player-pronouns-pompfen-desktop", 1280)
        check.visited += 1
        await page.set_viewport_size({"width": 320, "height": 900})
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        await expect(page.locator("#profile-edit-pronouns")).to_have_value("they/them")
        await page.locator("#profile-edit-pronouns").fill("")
        for code in ("CHAIN", "STAFF"):
            await page.locator(f"#profile-pompfen-{code}").uncheck()
        await page.locator("#profile-edit-form button[type=submit]").click()
        await expect(page).to_have_url(check.base_url + path)
        await expect(page.locator(".profile-pronouns, .player-pompfen")).to_have_count(0)
        assert writes[-1]["pronouns"] == "" and writes[-1]["pompfen"] == []
    finally:
        gate.set()
        await page.context.unroute("**/_api/**", fixture)


PROFILE_MARKDOWN = """## Profile introduction

- First item
- Second item

**Strong words** and *emphasis*.

[Profile guide](https://example.com/profile)

<a href="javascript:window.profileMarkdownUnsafe=true" onclick="window.profileMarkdownUnsafe=true">Unsafe link</a>
<script>window.profileMarkdownUnsafe=true</script>
"""


async def profile_markdown(check: SmokeCheck, page: Page, kind: str) -> None:
    """Both bios use the real sanitizer; local editing cannot render stale responses."""
    owner = f"demo-{kind}"
    await check.login(page, owner)
    path, api_path = f"/{kind}s/{owner}", f"/_api/{kind}s/{owner}"
    data = await api_json(page.context, check.base_url, api_path)
    data[kind]["about" if kind == "team" else "bio"] = PROFILE_MARKDOWN
    if kind == "player":
        data[kind]["phone"] = "555-0100-private"
    requests = []
    delayed, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    fail_format = True

    async def fixture(route: Route) -> None:
        request, parsed = route.request, urlsplit(route.request.url)
        if parsed.netloc != check.origin:
            await route.fallback()
        elif request.method == "GET" and parsed.path == api_path:
            await route.fulfill(json=data)
        elif request.method == "POST" and parsed.path == "/_api/render-markdown":
            source = request.post_data_json["markdown"]
            requests.append(source)
            if source == "## Delayed bio":
                response = await route.fetch()
                delayed.set()
                await release.wait()
                try:
                    await route.fulfill(response=response)
                except Error:
                    # Replacing the text may cancel the obsolete browser request.
                    pass
                finally:
                    completed.set()
            elif source == "## Retry formatting" and fail_format:
                await route.fulfill(json={"error": "Formatting fixture temporarily unavailable"})
            else:
                await route.fallback()
        else:
            await route.fallback()

    async def rich_bio(container) -> None:
        await expect(container.get_by_role("heading", name="Profile introduction", exact=True)).to_be_visible()
        await expect(container.locator("ul > li")).to_have_text(["First item", "Second item"])
        await expect(container.locator("strong")).to_have_text("Strong words")
        await expect(container.locator("em")).to_have_text("emphasis")
        await expect(container.get_by_role("link", name="Profile guide", exact=True)).to_have_attribute(
            "href", "https://example.com/profile"
        )
        await expect(
            container.locator("script, iframe, svg, [onclick], [onerror], a[href^='javascript:']")
        ).to_have_count(0)
        assert not await page.evaluate("window.profileMarkdownUnsafe === true")

    await page.context.route("**/_api/**", fixture)
    try:
        await page.goto(check.base_url + path, wait_until="domcontentloaded")
        await rich_bio(page.locator(".profile-bio"))
        await check.check_layout(page, f"{kind}-public-markdown", 320)
        check.visited += 1
        await page.goto(check.base_url + path + "/edit", wait_until="domcontentloaded")
        bio = page.locator("#profile-edit-about")
        await expect(bio).to_have_attribute("aria-describedby", "profile-edit-about-help")
        await expect(page.locator("#profile-edit-about-help")).to_contain_text("Markdown supported")
        await page.get_by_role("button", name="Preview profile", exact=True).click()
        preview = page.locator(".profile-preview-content")
        await rich_bio(preview)
        await expect(preview).not_to_contain_text("555-0100-private")
        await preview.get_by_role("link", name="Profile guide", exact=True).click()
        assert page.url == check.base_url + path + "/edit", "Preview link left unsaved edits"
        await page.set_viewport_size({"width": 1280, "height": 900})
        await bio.fill("")
        await expect(preview).to_contain_text("No profile details added yet")
        await bio.press_sequentially("## Debounced bio", delay=10)
        await expect(preview.get_by_role("heading", name="Debounced bio", exact=True)).to_be_visible()
        assert [source for source in requests if "Debounced" in source] == ["## Debounced bio"], requests
        count = len(requests)
        await page.locator("#profile-edit-name").fill("Unsaved Markdown profile")
        await page.wait_for_timeout(350)  # One full debounce window: unrelated fields must not render the bio again.
        assert len(requests) == count, "Changing the name re-rendered unchanged Markdown"
        await bio.fill("## Delayed bio")
        await asyncio.wait_for(delayed.wait(), 10)
        await bio.fill("## Fresh bio")
        await expect(preview.get_by_role("heading", name="Debounced bio", exact=True)).to_have_count(0)
        await expect(preview.get_by_role("heading", name="Fresh bio", exact=True)).to_be_visible()
        release.set()
        await asyncio.wait_for(completed.wait(), 10)
        await expect(preview.get_by_role("heading", name="Fresh bio", exact=True)).to_be_visible()
        await expect(preview.get_by_role("heading", name="Delayed bio", exact=True)).to_have_count(0)
        await bio.fill("## Retry formatting")
        await expect(preview.get_by_role("status")).to_have_text("Formatting unavailable.")
        await expect(preview).to_contain_text("## Retry formatting")
        fail_format = False
        await preview.get_by_role("button", name="Retry formatting", exact=True).click()
        await expect(preview.get_by_role("heading", name="Retry formatting", exact=True)).to_be_visible()
        await bio.fill(PROFILE_MARKDOWN)
        await rich_bio(preview)
        await page.set_viewport_size({"width": 320, "height": 900})
        await check.check_layout(page, f"{kind}-live-markdown-preview", 320)
        check.visited += 1
        await page.get_by_role("button", name="Cancel", exact=True).click()
        await expect(page).to_have_url(check.base_url + path)
    finally:
        release.set()
        await page.context.unroute("**/_api/**", fixture)


async def profile_themes(check: SmokeCheck, page: Page) -> None:
    for mode in ("light", "dark"):
        for palette in PALETTES:
            for kind in ("player", "team"):
                await page.goto(check.base_url + f"/{kind}s/demo-{kind}", wait_until="domcontentloaded")
                await selected_profile(page, kind, "info")
                await choose(page, palette, mode)
                await close_menu(page)
                await check_contrast(
                    page,
                    ".profile-section, .event-route-tab, .profile-detail-header h1, .profile-account-name, .profile-meta, .profile-event-stat dt, .profile-event-stat dd",
                )
                await check.check_layout(page, f"{kind}-hero-{palette}-{mode}", 360)
                check.visited += 1


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as playwright:
        api = await playwright.request.new_context()
        try:
            response = await api.get(args.base_url + "/_api/demo-info", timeout=TIMEOUT_MS)
            marker = await response.json() if response.ok else {}
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER, "Verified local demo required"
        finally:
            await api.dispose()
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        try:
            for name, width, action in (
                ("page-introductions", 320, introductions),
                ("legacy-introduction", 360, legacy_introduction),
                ("public-profile-tabs", 360, public_profiles),
                ("detail-banners-always-visible", 360, detail_banners),
                ("player-owner-tabs", 320, lambda check, page: owner_profile_tabs(check, page, "player")),
                ("team-owner-tabs", 320, lambda check, page: owner_profile_tabs(check, page, "team")),
                ("player-profile-editor", 320, lambda check, page: staged_profile_edit(check, page, "player")),
                ("team-profile-editor", 320, lambda check, page: staged_profile_edit(check, page, "team")),
                ("tournament-banner-upload", 320, lambda check, page: image_upload(check, page, "tournament")),
                ("league-banner-upload", 320, lambda check, page: image_upload(check, page, "league")),
                ("organizer-tools", 320, organizer_tools),
                ("profile-theme-matrix", 360, profile_themes),
                ("player-pronouns-pompfen", 320, player_identity),
                ("player-markdown", 320, lambda check, page: profile_markdown(check, page, "player")),
                ("team-markdown", 320, lambda check, page: profile_markdown(check, page, "team")),
            ):
                async with isolated_page(check, browser) as page:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await check.workflow(page, name, width, lambda: action(check, page))
        finally:
            await browser.close()
    print(
        f"{check.visited} profile/banner screens; {check.workflows} workflows passed; {len(check.failures)} failures."
    )
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return 1 if check.failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(arguments(__doc__))))
    except (AssertionError, RuntimeError) as error:
        print(f"Profile check stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
