"""Zip apworld/smo_archipelago/ into vendor/Archipelago/custom_worlds/meatballs.apworld.

An `.apworld` file is just a zip with the world package at its root. Archipelago
0.5+ auto-discovers `.apworld` files in `<checkout>/custom_worlds/` at startup,
so this is the supported way to ship a custom world without polluting
`vendor/Archipelago/worlds/` (which would also get clobbered by `git submodule
update`).

The zip is named `meatballs.apworld` (so Archipelago imports the world as
`worlds.meatballs`) while the source folder on disk stays
`apworld/smo_archipelago/` — the in-repo name was kept to avoid churning
every dev-workflow path reference, but the deployed/distributed identifier
is `meatballs`. The 2026-05-20 rename from `smo.apworld` → `meatballs.apworld`
moved us off the `worlds.smo` slot that an existing upstream apworld already
claims (it uses the `.apsmo` namespace), so installing both side-by-side no
longer trips Archipelago's duplicate-game-name check.

As a side-effect, any previously-installed `smo.apworld` or
`smo_archipelago.apworld` from before this rename is deleted; otherwise
Archipelago would register both and fail the duplicate-game-name check.

Idempotent: re-running overwrites the existing zip.

Run from anywhere; paths resolve relative to this file.

    # Dev / unit-test default: apworld files only
    python scripts/install_apworld.py

    # Release build: also bundle switch-mod sources + extractor scripts
    # under _setup/ inside the zip so the first-run wizard has everything
    # it needs to compile the Switch mod on the user's machine.
    python scripts/install_apworld.py --bundle-mod --bundle-scripts
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "apworld" / "smo_archipelago"
DST_DIR = REPO / "vendor" / "Archipelago" / "custom_worlds"
DST = DST_DIR / "meatballs.apworld"
# Stale predecessors; left in place they would clash on the AP game name
# (or, in the case of smo.apworld, on the `worlds.smo` module slot the
# upstream apworld already owns).
LEGACY_DSTS = (
    DST_DIR / "smo.apworld",
    DST_DIR / "smo_archipelago.apworld",
)

# These get shipped inside the zip and never produce real source content.
# `tests` is the in-apworld pytest tree — useful for dev but bloats the
# install and pulls in seeds the user doesn't need at runtime.
SKIP_NAMES = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "tests"}

# IP-discipline allowlist for files under apworld/smo_archipelago/client/data/.
# That directory commonly contains the extracted Nintendo USen strings
# (shine_map.json + capture_map.json + their *_review.json diagnostics) that
# the user generated locally via scripts/extract_shine_map.py. Those files
# are gitignored and MUST NOT ship in the released apworld zip — they're
# Nintendo IP. Any other file dropped under client/data/ (e.g. a future
# constant we want to bundle) passes through.
#
# The wizard generates the maps on the user's machine and writes them to
# %APPDATA%/SMOArchipelago/data/, which SMOClient checks before falling
# back to the bundled client/data/ location — so a release zip with no
# bundled maps is functionally correct.
CLIENT_DATA_IP_BLOCKLIST = frozenset({
    "shine_map.json",
    "shine_map_review.json",
    "capture_map.json",
    "capture_map_review.json",
})

# Extra dirs that should be skipped from --bundle-mod (the switch-mod tree
# contains build artifacts and dev-only test outputs that bloat the zip).
MOD_SKIP_NAMES = SKIP_NAMES | {
    "build",          # cmake build dir
    "sd-overlay",     # cmake install staging
    ".git",
    ".github",
    ".vscode",
    "test_json.exe",  # host-test binaries dropped at repo root by docs example
    "test_protocol.exe",
}

# Extension blacklist for --bundle-mod (binaries that have no business in
# the source bundle).
MOD_SKIP_SUFFIXES = {".exe", ".dll", ".pdb", ".obj", ".o", ".a"}

# The subset of scripts/ we bundle for --bundle-scripts. The wizard only
# needs the extractor + the sync helper; other dev scripts (release
# automation, smoke tests) would just add weight.
BUNDLED_SCRIPT_NAMES = (
    "extract_shine_map.py",
    "sync_capture_table.py",
    "check_nso_symbols.py",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--bundle-mod",
        action="store_true",
        help="Also copy switch-mod/ sources (incl. submodule contents) under "
             "_setup/switch_mod/ in the zip. Required for the first-run wizard "
             "to build subsdk9 on the user's machine.",
    )
    p.add_argument(
        "--bundle-scripts",
        action="store_true",
        help="Also copy extract_shine_map.py + sync_capture_table.py under "
             "_setup/scripts/ in the zip. Required for the wizard to extract "
             "moon/capture maps from the user's NSP and sync the capture "
             "bit-index table.",
    )
    return p.parse_args(argv)


def _collect_files(root: Path, *, skip_names: set[str],
                   skip_suffixes: set[str] | None = None) -> list[Path]:
    """Recursively gather files under `root`, skipping any path whose parts
    intersect `skip_names` or whose suffix is in `skip_suffixes`."""
    files: list[Path] = []
    skip_suffixes = skip_suffixes or set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip_names for part in p.parts):
            continue
        if p.suffix.lower() in skip_suffixes:
            continue
        files.append(p)
    return files


def _is_ip_blocklisted(p: Path) -> bool:
    """True iff `p` (relative to the apworld source dir) is one of the
    Nintendo-IP files we must never ship. Currently scopes to
    `client/data/<blocklist>` to avoid blocking unrelated future
    `<basename>.json` files elsewhere in the tree."""
    parts = p.parts
    # Find the index of "client" then check the next parts are "data/<name>".
    try:
        idx = parts.index("client")
    except ValueError:
        return False
    if idx + 2 >= len(parts):
        return False
    if parts[idx + 1] != "data":
        return False
    return parts[idx + 2] in CLIENT_DATA_IP_BLOCKLIST


def _add_to_zip(zf: zipfile.ZipFile, src: Path, arcname: Path) -> None:
    """Write src to zf at arcname, using forward-slash POSIX arcnames so
    the zip works the same on Windows/Linux/Mac."""
    zf.write(src, arcname.as_posix())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not SRC.is_dir():
        print(f"FAIL: apworld source not found at {SRC}", file=sys.stderr)
        return 2
    if not DST_DIR.parent.is_dir():
        print(f"FAIL: Archipelago checkout not found at {DST_DIR.parent}",
              file=sys.stderr)
        print("      (run `git submodule update --init --recursive` first)",
              file=sys.stderr)
        return 2
    DST_DIR.mkdir(parents=True, exist_ok=True)

    apworld_files = _collect_files(SRC, skip_names=SKIP_NAMES)
    # IP discipline gate: drop the Nintendo-content map files that the
    # user's local extract_shine_map.py run leaves behind in client/data/.
    # See CLIENT_DATA_IP_BLOCKLIST docstring.
    blocked = [p for p in apworld_files if _is_ip_blocklisted(p.relative_to(SRC))]
    if blocked:
        print("     skipping IP-blocked files:", file=sys.stderr)
        for p in blocked:
            print(f"       {p.relative_to(SRC).as_posix()}", file=sys.stderr)
    apworld_files = [p for p in apworld_files
                     if not _is_ip_blocklisted(p.relative_to(SRC))]
    if not apworld_files:
        print(f"FAIL: no files under {SRC}", file=sys.stderr)
        return 2

    bundled_mod_files: list[Path] = []
    if args.bundle_mod:
        mod_root = REPO / "switch-mod"
        if not mod_root.is_dir():
            print(f"FAIL: --bundle-mod requested but {mod_root} missing",
                  file=sys.stderr)
            return 2
        # Submodule presence check: if lunakit-vendor/cmake/toolchain.cmake
        # isn't there the build will fail on the user's machine for an
        # entirely cosmetic reason. Catch it now.
        toolchain = mod_root / "lunakit-vendor" / "cmake" / "toolchain.cmake"
        if not toolchain.exists():
            print(
                f"FAIL: --bundle-mod requested but {toolchain} missing "
                f"(run `git submodule update --init --recursive` first)",
                file=sys.stderr,
            )
            return 2
        bundled_mod_files = _collect_files(
            mod_root,
            skip_names=MOD_SKIP_NAMES,
            skip_suffixes=MOD_SKIP_SUFFIXES,
        )

    bundled_script_files: list[Path] = []
    if args.bundle_scripts:
        scripts_root = REPO / "scripts"
        missing = []
        for name in BUNDLED_SCRIPT_NAMES:
            p = scripts_root / name
            if not p.exists():
                missing.append(p)
            else:
                bundled_script_files.append(p)
        if missing:
            print(f"FAIL: --bundle-scripts requested but missing: {missing}",
                  file=sys.stderr)
            return 2

    with zipfile.ZipFile(DST, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in apworld_files:
            # Inside the zip, paths must be `meatballs/...` so Archipelago
            # imports it as `worlds.meatballs` (line 196 of
            # vendor/Archipelago/worlds/__init__.py does
            # `importer.find_spec(f"worlds.{Path(apworld.path).stem}")` —
            # the spec derivation pins the inner folder name to the zip stem).
            _add_to_zip(zf, p, Path("meatballs") / p.relative_to(SRC))

        # Bundled mod sources land under meatballs/_setup/switch_mod/ so the
        # wizard can locate them via __file__ relative paths regardless
        # of whether the apworld was installed as a loose source tree or
        # a zip.
        for p in bundled_mod_files:
            _add_to_zip(
                zf, p,
                Path("meatballs") / "_setup" / "switch_mod" / p.relative_to(REPO / "switch-mod"),
            )

        # Bundled scripts land under meatballs/_setup/scripts/ — same rationale.
        for p in bundled_script_files:
            _add_to_zip(
                zf, p,
                Path("meatballs") / "_setup" / "scripts" / p.name,
            )

    for legacy in LEGACY_DSTS:
        if legacy.exists():
            legacy.unlink()
            print(f"     removed stale {legacy.name}")

    total_files = len(apworld_files) + len(bundled_mod_files) + len(bundled_script_files)
    size_kb = DST.stat().st_size / 1024
    extras = []
    if args.bundle_mod:
        extras.append(f"+{len(bundled_mod_files)} mod")
    if args.bundle_scripts:
        extras.append(f"+{len(bundled_script_files)} script")
    extras_str = (" " + " ".join(extras)) if extras else ""
    print(f"OK: wrote {DST} ({total_files} files{extras_str}, {size_kb:.1f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
