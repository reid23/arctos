"""Prepare shared public presentation and a verified Dioxus entry manifest.

Run after ``dx bundle --web --release``. ``--theme-only`` prepares the public
styles without requiring a Rust build.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import posixpath
import re
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DESIGN_SOURCES = (
    "frontend/src/theme.css",
    "frontend/src/pages/public_pages.css",
    "frontend/src/components/event_details.css",
    "frontend/src/components/organizer_tools.css",
    "frontend/src/components/appearance_menu.css",
)


def design_css() -> str:
    """Generate public-document styles from Dioxus component rules."""
    return "/* Generated from the existing application styles; do not edit here. */\n" + "\n".join(
        f"/* Source: {path} */\n" + (ROOT / path).read_text() for path in DESIGN_SOURCES
    )


def presentation_assets() -> dict[str, bytes]:
    return {
        "bootstrap.css": (ROOT / "frontend/assets/vendor/bootstrap.min.css").read_bytes().rstrip(b"\n") + b"\n",
        "design.css": design_css().encode(),
        "theme.css": theme_css().encode(),
    }


def prepare_presentation(destination: Path = ROOT / "static/public") -> None:
    """Write public styles from their shared sources without modifying unchanged files."""
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in presentation_assets().items():
        path = destination / name
        if not path.is_file() or path.read_bytes() != data:
            _atomic_write(path, data)


def theme_css() -> str:
    source = (ROOT / "frontend/src/theme.css").read_text()
    blocks = []
    dark_blocks = []
    for match in re.finditer(r"(\.arctos-app(?:\[data-[^\]]+\])*)\s*\{([^{}]*)\}", source):
        selector, body = match.groups()
        declarations = [f"{name}:{value.strip()};" for name, value in re.findall(r"(--ui-[\w-]+)\s*:\s*([^;]+);", body)]
        if not declarations:
            continue
        selector = selector.replace(".arctos-app", ".arctos-public")
        # Root preferences are applied in the head before the first paint. Mirror
        # them to the public wrapper even before its data attributes are set;
        # the same mapping also gives no-JavaScript visitors system dark mode.
        block = selector + "," + selector + " .public-shell{" + "".join(declarations) + "}"
        blocks.append(block)
        if '[data-bs-theme="dark"]' in selector:
            dark_blocks.append(block.replace('[data-bs-theme="dark"]', ":not([data-bs-theme])"))
    if len(blocks) != 8:
        raise ValueError("Theme structure changed; review token extraction before publishing.")
    return (
        "/* Generated from frontend/src/theme.css; run scripts/prepare_public_assets.py --theme-only. */\n"
        + "\n".join(blocks)
        + '\n.arctos-public{color-scheme:light}.arctos-public[data-bs-theme="dark"]{color-scheme:dark}\n'
        + "@media(prefers-color-scheme:dark){.arctos-public:not([data-bs-theme]){color-scheme:dark}"
        + "".join(dark_blocks)
        + "}\n"
    )


class EntryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "module" and attributes.get("src"):
            self.entries.append(attributes["src"])


def inspect_dist(dist: Path) -> dict:
    """Validate generated entry/Wasm references without changing the build."""
    dist = dist.resolve()
    parser = EntryParser()
    parser.feed((dist / "index.html").read_text())
    if len(parser.entries) != 1:
        raise ValueError("Expected exactly one Dioxus module entry in the generated index.html.")
    parsed = urlsplit(parser.entries[0])
    normalized = posixpath.normpath(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "/assets/" not in normalized
        or ".." in parsed.path.split("/")
        or "\\" in parsed.path
    ):
        raise ValueError("The Dioxus module entry must be a local assets/ path.")
    base_path, asset_name = normalized.split("/assets/", 1)
    entry = "assets/" + asset_name
    entry_file = (dist / entry).resolve()
    if not entry_file.is_relative_to(dist / "assets") or entry_file.suffix != ".js" or not entry_file.is_file():
        raise ValueError("Dioxus module entry is missing or outside the asset directory.")
    # Check Wasm references in the generated entry before exposing a broken release.
    wasm = re.findall(r'["\'](/[^"\']*assets/[^"\']+\.wasm)["\']', entry_file.read_text())
    wasm_files = []
    for path in wasm:
        normalized_wasm = posixpath.normpath(path)
        if not normalized_wasm.startswith(base_path + "/assets/") or ".." in path.split("/"):
            raise ValueError("Wasm references must stay beneath the application's asset prefix.")
        wasm_files.append((dist / normalized_wasm[len(base_path) :].lstrip("/")).resolve())
    if not wasm_files or any(not path.is_relative_to(dist / "assets") or not path.is_file() for path in wasm_files):
        raise ValueError("The generated entry does not reference an available Wasm build.")
    return {
        "version": 1,
        "entry": "/" + entry,
        "base_path": base_path,
        "entry_sha256": hashlib.sha256(entry_file.read_bytes()).hexdigest(),
    }


def prepare_dist(dist: Path) -> dict:
    dist = dist.resolve()
    manifest = inspect_dist(dist)
    for asset in (dist / "assets").rglob("*"):
        if asset.is_symlink():
            raise ValueError("Frontend bundles must not contain symlinks.")
        if asset.is_file() and asset.suffix in {".js", ".wasm", ".css", ".svg"}:
            compressed = gzip.compress(asset.read_bytes(), compresslevel=9, mtime=0)
            _atomic_write(asset.with_name(asset.name + ".gz"), compressed)
    _atomic_write(dist / "arctos-assets.json", (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def _atomic_write(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prepare-", delete=False) as output:
        pending = Path(output.name)
        output.write(data)
    try:
        pending.chmod(0o644)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend/dist/public")
    parser.add_argument("--theme-only", action="store_true")
    args = parser.parse_args()
    prepare_presentation()
    if not args.theme_only:
        manifest = prepare_dist(args.dist)
        print(f"Prepared application entry {manifest['entry']} and compressed assets.")


if __name__ == "__main__":
    main()
