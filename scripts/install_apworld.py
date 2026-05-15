"""Zip apworld/smo_archipelago/ into vendor/Archipelago/custom_worlds/smo_archipelago.apworld.

An `.apworld` file is just a zip with the world package at its root. Archipelago
0.5+ auto-discovers `.apworld` files in `<checkout>/custom_worlds/` at startup,
so this is the supported way to ship a forked Manual world without polluting
`vendor/Archipelago/worlds/` (which would also get clobbered by `git submodule
update`).

Idempotent: re-running overwrites the existing zip.

Run from anywhere; paths resolve relative to this file.

    python scripts/install_apworld.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "apworld" / "smo_archipelago"
DST_DIR = REPO / "vendor" / "Archipelago" / "custom_worlds"
DST = DST_DIR / "smo_archipelago.apworld"

# These get shipped inside the zip and never produce real source content.
SKIP_NAMES = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def main() -> int:
    if not SRC.is_dir():
        print(f"FAIL: apworld source not found at {SRC}", file=sys.stderr)
        return 2
    if not DST_DIR.parent.is_dir():
        print(f"FAIL: Archipelago checkout not found at {DST_DIR.parent}", file=sys.stderr)
        print("      (run `git submodule update --init --recursive` first)", file=sys.stderr)
        return 2
    DST_DIR.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for p in SRC.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_NAMES for part in p.parts):
            continue
        files.append(p)

    if not files:
        print(f"FAIL: no files under {SRC}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(DST, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            # Inside the zip, paths must be `smo_archipelago/...` so Archipelago
            # imports it under that module name.
            arcname = Path("smo_archipelago") / p.relative_to(SRC)
            zf.write(p, arcname.as_posix())

    size_kb = DST.stat().st_size / 1024
    print(f"OK: wrote {DST} ({len(files)} files, {size_kb:.1f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
