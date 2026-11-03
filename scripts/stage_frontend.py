"""Publish a prepared frontend without deleting assets used by existing tabs.

This is an explicitly invoked deployment tool, never part of a web request.
Assets are copied first; index.html and the verified entry manifest are replaced
atomically last. Keep a previous manifest/index for rollback. Callers manage
Flask code/service deployment and must serialize deploys to one destination.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prepare_public_assets import inspect_dist


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".stage-", delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        shutil.copyfile(source, pending)
        pending.chmod(0o644)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)


def stage(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Use separate, non-overlapping build and deployment directories.")
    manifest = json.loads((source / "arctos-assets.json").read_text())
    # Recheck generated URLs and required Wasm before touching the live tree.
    # Preparation and deployment can happen separately; an incomplete copied
    # build must not replace the last working entry manifest.
    if manifest != inspect_dist(source):
        raise ValueError("The frontend entry changed after preparation; rebuild the manifest.")
    # The deployment account and nginx may run as different users. Public
    # assets need directory traversal even when the caller uses umask 0077.
    for directory in (destination, destination / "assets"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    for asset in (source / "assets").rglob("*"):
        if asset.is_symlink():
            raise ValueError("Frontend bundles must not contain symlinks.")
        target = destination / asset.relative_to(source)
        if asset.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
        elif asset.is_file():
            if not target.is_file() or asset.read_bytes() != target.read_bytes():
                atomic_copy(asset, target)
            else:
                target.chmod(0o644)
    for name in ("index.html", "arctos-assets.json"):
        target = destination / name
        if target.is_file():
            atomic_copy(target, destination / (name + ".previous"))
        atomic_copy(source / name, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage(args.source, args.destination)
    print(f"Frontend staged at {args.destination}; preceding assets and manifest retained.")


if __name__ == "__main__":
    main()
