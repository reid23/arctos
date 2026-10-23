"""Check Stones controls/audio state against the explicitly started local demo.

    uv run python scripts/check_stones_ui.py --base-url http://127.0.0.1:8444 \
        --browser-executable /usr/bin/google-chrome --screenshots /tmp/arctos-stones

Chromium output is muted; this checks real Web Audio scheduling and media state,
not speaker/Bluetooth latency. Uses the shared loopback/demo guard; no writes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import sys

from playwright.async_api import async_playwright, expect

from check_appearance_ui import PALETTES, check_contrast, choose, close_menu, preference
from check_mobile_ui import DEMO_USER, TIMEOUT_MS, SmokeCheck, api_json, arguments

# Observe the real audio pipeline without replacing playback or advancing clocks.
AUDIO_OBSERVER = """window.stonesSources = []; window.stonesDecoded = 0;
const start = AudioBufferSourceNode.prototype.start, stop = AudioBufferSourceNode.prototype.stop;
AudioBufferSourceNode.prototype.start = function(when = 0, ...rest) {
    this.stonesProbe = {when: Math.max(when, this.context.currentTime), ctx: this.context};
    window.stonesSources.push(this.stonesProbe); return start.call(this, when, ...rest);
};
AudioBufferSourceNode.prototype.stop = function(when = 0) {
    if (this.stonesProbe) this.stonesProbe.stop = Math.max(when, this.context.currentTime);
    return stop.call(this, when);
};
const decode = AudioContext.prototype.decodeAudioData;
AudioContext.prototype.decodeAudioData = function(...args) {
    return decode.apply(this, args).then(buffer => { window.stonesDecoded++; return buffer; });
};"""


async def controls(check, page, stones) -> None:
    context = page.context
    gate = asyncio.Event()
    pattern = check.base_url + "/static/stones/" + stones[0]["filename_encoded"]

    async def delayed_audio(route):
        await gate.wait()
        await route.fallback()

    await context.route(pattern, delayed_audio)
    try:
        await page.goto(check.base_url + "/stones", wait_until="domcontentloaded")
        start, reset = page.locator("#stones-start-pause"), page.locator("#stones-reset")
        count, audio = page.locator("#stones-count"), page.locator("#audio-stream")
        await expect(start).to_have_text("Start")
        await start.click()
        await expect(start).to_contain_text("Starting")
        await reset.click()
        gate.set()
        await page.wait_for_function("n => window.stonesDecoded >= n", arg=len(stones))
        await asyncio.sleep(1.7)  # Observe longer than one stone after the cancelled decode completes.
        await expect(count).to_have_text("0")
        await expect(start).to_have_text("Start")
        await expect(audio).to_have_js_property("paused", True)
    finally:
        gate.set()
        await context.unroute(pattern, delayed_audio)
    await start.click()
    await page.wait_for_function("Number(document.querySelector('#stones-count').textContent) >= 2")
    await expect(page.locator("#stones-preview")).to_be_disabled()
    await start.click()
    paused = await count.inner_text()
    await asyncio.sleep(1.7)  # A paused counter must remain stable across a full beat.
    await expect(count).to_have_text(paused)
    await expect(audio).to_have_js_property("paused", True)
    await start.click()
    await page.wait_for_function(
        "n => Number(document.querySelector('#stones-count').textContent) > n", arg=int(paused)
    )
    await page.locator("#stones-mute").click()
    await expect(audio).to_have_js_property("muted", True)
    muted_count = int(await count.inner_text())
    await page.wait_for_function(
        "n => Number(document.querySelector('#stones-count').textContent) > n", arg=muted_count
    )
    await page.locator("#stones-mute").click()
    await expect(audio).to_have_js_property("muted", False)
    await reset.click()
    await expect(count).to_have_text("0")
    await expect(audio).to_have_js_property("paused", True)
    preview = page.locator("#stones-preview")
    await preview.click()
    await expect(preview).to_be_enabled()
    await expect(count).to_have_text("0")
    await expect(start).to_have_text("Start")
    sound = page.locator("#stones-sound")
    await context.set_offline(True)
    try:
        await sound.select_option("1")
        await start.click()
        await page.wait_for_function("Number(document.querySelector('#stones-count').textContent) > 0")
        await reset.click()
    finally:
        await context.set_offline(False)
    await sound.select_option("custom")
    upload = page.locator("#stones-custom-file")
    short = (Path(__file__).resolve().parents[1] / "static/stones/2_Snare.mp3").read_bytes()
    for filename, data, message in (
        ("broken.mp3", b"not audio", "Could not decode"),
        ("long.mp3", short * 12, "under 1.5"),
        ("short.mp3", short, "Loaded"),
    ):
        await upload.set_input_files({"name": filename, "mimeType": "audio/mpeg", "buffer": data})
        feedback = page.locator(".stones-sound-picker").get_by_text(re.compile(message))
        if message != "Loaded":
            feedback = page.get_by_role("alert").filter(has_text=re.compile(message))
        await expect(feedback).to_be_visible()
    await preview.click()
    await expect(preview).to_be_enabled()
    await expect(count).to_have_text("0")
    await page.get_by_role("button", name="Calibrate manually", exact=True).click()
    dialog = page.get_by_role("dialog", name="Calibrate stone timing")
    await expect(dialog).to_be_visible()
    await dialog.get_by_role("button", name="Cancel", exact=True).click()
    await expect(dialog).to_be_hidden()
    await expect(start).to_be_enabled()
    await page.locator("details").filter(has=page.locator("#stones-resync")).locator("summary").click()
    await page.locator("#stones-resync").click()
    duplicates = await page.evaluate("""() => window.stonesSources.filter(s => s.stop === undefined || s.stop > s.when)
        .some((s, i, all) => all.slice(i + 1).some(t => s.ctx === t.ctx && Math.abs(s.when - t.when) < .05))""")
    assert not duplicates, "Duplicate audio sources were scheduled on the same beat"
    await start.click()
    await expect(start).to_have_text("Pause")
    await page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Events", exact=True).click()
    await expect(page.locator("#event-search")).to_be_visible()
    await page.wait_for_function("window.stonesSources.every(source => source.ctx.state === 'closed')")
    await page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Stones", exact=True).click()
    await expect(page.locator("#stones-count")).to_have_text("0")
    await expect(page.locator("#stones-start-pause")).to_have_text("Start")


async def themes(check, page) -> None:
    for width in (320, 360):
        await page.set_viewport_size({"width": width, "height": 900})
        for mode in ("light", "dark"):
            for palette in PALETTES:
                await choose(page, palette, mode)
                await preference(page, palette, mode)
                await close_menu(page)
                await check.check_layout(page, f"stones-{palette}-{mode}", width)
                await check_contrast(page, "#stones-count, #stones-start-pause, #stones-reset, #stones-sound")
                for selector in (
                    "#stones-start-pause",
                    "#stones-reset",
                    "#stones-mute",
                    "#stones-preview",
                    "#stones-sound",
                ):
                    box = await page.locator(selector).bounding_box()
                    assert box and box["width"] >= 44 and box["height"] >= 44, f"Small Stones target: {selector} {box}"
                check.visited += 1


async def run(args) -> int:
    check = SmokeCheck(args.base_url, args.screenshots)
    async with async_playwright() as p:
        launch = {"headless": True, "args": ["--mute-audio"]}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(viewport={"width": 360, "height": 900}, reduced_motion="reduce")
        context.set_default_timeout(TIMEOUT_MS)
        try:
            marker = await api_json(context, args.base_url, "/_api/demo-info")
            assert marker.get("demo") is True and marker.get("organizer") == DEMO_USER, "Verified demo required"
            stones = (await api_json(context, args.base_url, "/_api/stones"))["stones"]
            assert len(stones) >= 2, "Two built-in sounds required for cached-switch test"
            await context.add_init_script(AUDIO_OBSERVER)
            await context.route("**/*", check.guard_requests)
            page = await context.new_page()
            check.observe(page)
            await check.workflow(page, "stones-controls", 360, lambda: controls(check, page, stones))
            await check.workflow(page, "stones-themes", 360, lambda: themes(check, page))
        finally:
            await context.close()
            await browser.close()
    print(f"{check.visited} Stones theme checks; {check.workflows} workflows passed; {len(check.failures)} failures.")
    if check.failures:
        print("\n".join(check.failures), file=sys.stderr)
    return int(bool(check.failures))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(arguments(__doc__))))
