"""Headless installer audit — drive the wizard's build pipeline end-to-end
without booting Kivy and assert the filesystem footprint matches the
allowlist.

Two callers in CI:

  - `.github/workflows/release.yml` runs this on a clean windows-2022
    runner BEFORE publish-release fires, so a build that wandered outside
    its expected scope blocks the GitHub release.
  - `.github/workflows/test.yml` runs the same script with PATH narrowed
    to the vendored toolchain dirs only (no `python` / `python3` resolvable
    from anywhere else), as a cheaper PR-time guard against bare-name
    shell-outs sneaking back into build_switchmod.py or its callees.

Single entry point: `python scripts/release_audit.py --all` (default).
Individual stages are exposed via `--skip-extract`, `--build`, and
`--audit` for local debugging — `--all` is just the union with no
implicit dependencies between stages other than the obvious "build needs
maps, audit needs build outputs".

The stub maps written by `--skip-extract` use placeholder strings ONLY
("Stub Moon", "Stub Capture") so this script never touches Nintendo IP.
The real extractor (`scripts/extract_shine_map.py`) is what end users run
against their own SMO 1.0.0 dump — see CLAUDE.md's IP-discipline section.
"""

from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make `_setup` importable without dragging in the full apworld
# package's import chain. Importing `smo_archipelago._setup.X` would
# trigger `apworld/smo_archipelago/__init__.py`, which depends on
# Archipelago's `Utils` / `BaseClasses` / `worlds.AutoWorld` — none of
# which are needed for the build pipeline this script drives. Putting
# `apworld/smo_archipelago/` directly on sys.path lets us
# `from _setup.build import ...` instead.
sys.path.insert(0, str(REPO_ROOT / "apworld" / "smo_archipelago"))


# ---------------------------------------------------------------------------
# --skip-extract: synthesize stub maps so the build pipeline has the files
# it expects without needing a real Nintendo dump.
# ---------------------------------------------------------------------------

# Minimal shine_map.json entry shape — matches the writer in
# scripts/extract_shine_map.py (search for "stage_name=r.stage_name").
# Real extractions contain ~775 entries; one stub is enough because
# sync_capture_table.py / sync_shine_table.py only use these files to look
# up specific keys, and the audit doesn't validate the cross-reference.
STUB_SHINE_MAP = [
    {
        "stage_name": "StubStage",
        "object_id": "obj0",
        "kingdom": "StubKingdom",
        "shine_id": "Stub Moon",
        "shine_uid": 0,
    },
]

# Minimal capture_map.json entry — schema from sync_capture_table.py's
# {"cap", "hack_name"} read. Empty list is also valid (the script falls
# back to identity mapping); we include one entry to exercise the
# non-trivial path.
STUB_CAPTURE_MAP = [
    {"cap": "Stub Capture", "hack_name": "StubHack"},
]


def _looks_like_stub(path: Path) -> bool:
    """True if `path` contains stub data this script would have written
    (or doesn't exist). Used as a safety gate before overwriting — a
    file that holds the user's real extraction must NEVER be silently
    clobbered. Stub detection just compares against the constants we
    emit; a "real" extraction has hundreds of entries, all distinct."""
    if not path.exists():
        return True
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return contents in (STUB_SHINE_MAP, STUB_CAPTURE_MAP, [])


def write_stub_maps(data_dir: Path, *, force: bool = False) -> list[Path]:
    """Write Nintendo-free stub maps into `data_dir`. Returns the list of
    files written so the caller can log them.

    Refuses to overwrite an existing real extraction unless `force=True`.
    The CI runner starts with no maps so this never triggers there; on a
    dev machine the user's real `shine_map.json` (~775 entries of
    extracted USen strings) is what we MUST NOT clobber — re-running the
    real extractor takes 10+ minutes of NSP decryption.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    shine = data_dir / "shine_map.json"
    capture = data_dir / "capture_map.json"
    if not force:
        for p in (shine, capture):
            if p.exists() and not _looks_like_stub(p):
                raise RuntimeError(
                    f"refusing to overwrite {p} — it looks like a real "
                    f"extraction, not a stub. Pass --force to override "
                    f"(only safe on CI runners or in a sandbox)."
                )
    shine.write_text(json.dumps(STUB_SHINE_MAP, indent=2), encoding="utf-8")
    capture.write_text(json.dumps(STUB_CAPTURE_MAP, indent=2), encoding="utf-8")
    return [shine, capture]


# ---------------------------------------------------------------------------
# --build: drive the same subprocess wrappers the wizard uses, with the
# Kivy callbacks replaced by a stdout printer.
# ---------------------------------------------------------------------------

def run_build(bridge_host: str) -> int:
    """Call run_sync_capture_table → run_build_switchmod through the
    apworld's _setup.build module. Returns the subprocess return code of
    the failing step, or 0 on success."""
    from _setup.build import (  # type: ignore[import-not-found]
        run_build_switchmod,
        run_sync_capture_table,
    )

    def on_line(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    print("[audit] step 1/2: sync_capture_table")
    sync = run_sync_capture_table(on_line=on_line)
    if not sync.ok:
        print(f"[audit] sync_capture_table failed (rc={sync.returncode})")
        return sync.returncode or 1

    print(f"[audit] step 2/2: build_switchmod (bridge_host={bridge_host})")
    build = run_build_switchmod(bridge_host, on_line=on_line)
    if not build.ok:
        print(f"[audit] build_switchmod failed (rc={build.returncode})")
        return build.returncode or 1

    print("[audit] build stage completed")
    return 0


# ---------------------------------------------------------------------------
# --audit: walk the expected sandbox roots, classify every file as either
# allowed or unexpected.
# ---------------------------------------------------------------------------

# Files we REQUIRE to exist after a successful build. These are the
# concrete artifacts the deploy step copies to the SD card / Ryujinx mods
# dir; if any is missing the install is broken regardless of what other
# files landed.
REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9",
    "build/sd/atmosphere/contents/0100000000010000/exefs/main.npdm",
    "src/ap/capture_table.h",
)

# The build legitimately writes into a small, fixed set of subdirectories
# of the switch-mod source tree. Anything outside this list is treated as
# a write-scope violation. We intentionally do NOT audit the source tree
# at large — those files are git-tracked and predate the build run; only
# the OUTPUTS the build produced are in scope.
SWITCH_MOD_OUTPUT_ROOTS: tuple[str, ...] = (
    "build",         # cmake / ninja build tree
    "sys/sail/build",  # sail.cmake host-binary build dir
    "lib/std",       # aarch64 stdlib drop from setup_libcxx_prepackaged.py
)

# The single source-tree file the build is allowed to (re)write. It is
# gitignored and regenerated each build by sync_capture_table.py.
SWITCH_MOD_ALLOWED_SOURCE_WRITES: frozenset[str] = frozenset({
    "src/ap/capture_table.h",
})

# Glob patterns (POSIX-style; fnmatch.fnmatch is applied per-path-segment
# against the path relative to the sandbox root). Anything that does NOT
# match at least one pattern is flagged as unexpected — this is the
# "installer only touches things we expect" guarantee.
#
# Globs are intentionally broad inside each sandbox root because the
# build tree under switch-mod/build/ contains thousands of intermediate
# files (ninja CMake cache, .o objects, header dependency files, etc.).
# The audit's job is to ensure the build stays INSIDE the sandbox, not
# to inventory every .o file ninja produced.
ALLOWED_GLOBS: dict[str, tuple[str, ...]] = {
    # %APPDATA%/SMOArchipelago/ (or ~/.local/share/SMOArchipelago/ off
    # Windows). The wizard's setup_state.json + wizard.log are user-
    # session state; bundled/ is the apworld-zip extraction cache; data/
    # holds the extracted maps; build/ holds the deploy-staged outputs.
    "appdata": (
        "data/shine_map.json",
        "data/capture_map.json",
        "data/shine_map_review.json",
        "data/capture_map_review.json",
        "build/*",
        "bundled/**",
        "bundled/.source-zip-mtime",
        # Wizard-managed tool installs land top-level (sibling to
        # bundled/) so a bundled-tree refresh doesn't wipe them. Pre-fix
        # this lived inside `bundled/`; the legacy location is still
        # covered by the `bundled/**` glob above for users mid-migration.
        "hactool.exe",
        "setup_state.json",
        "wizard.log",
    ),
    # switch-mod/ — build outputs only. The audit walks SWITCH_MOD_OUTPUT_ROOTS
    # rather than the whole source tree, plus the single allowed source-tree
    # write (capture_table.h). Anything inside those output roots is fair
    # game.
    "switch_mod": tuple(f"{r}/**" for r in SWITCH_MOD_OUTPUT_ROOTS) + (
        *SWITCH_MOD_ALLOWED_SOURCE_WRITES,
    ),
}


def _matches_any(rel: str, globs: tuple[str, ...]) -> bool:
    """Return True if `rel` (POSIX-form relative path) matches at least
    one glob. `**` is honored by walking parent directories.

    Always normalizes backslashes to forward slashes — not just os.sep
    — because the same audit log file may be read on Linux for grep'ing
    even when produced on a Windows runner, and the inverse: a path
    written with `\\` literals in a unit test must validate the matcher
    works regardless of host platform.
    """
    rel_posix = rel.replace("\\", "/")
    for pat in globs:
        if fnmatch(rel_posix, pat):
            return True
        # fnmatch doesn't natively understand `**`; emulate by stripping
        # the suffix and checking the prefix.
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
                return True
    return False


def audit_tree(root: Path, globs: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Walk `root` recursively. Returns (allowed, unexpected) — both
    POSIX-form paths relative to `root`."""
    allowed: list[str] = []
    unexpected: list[str] = []
    if not root.exists():
        return allowed, unexpected
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _matches_any(rel, globs):
            allowed.append(rel)
        else:
            unexpected.append(rel)
    return allowed, unexpected


def find_switch_mod_root() -> Path:
    """Locate the switch-mod source tree the build was run against.

    Mirrors the resolution order in `scripts/build_switchmod.py`:
    dev-checkout `switch-mod/` first, then bundled-apworld `switch_mod/`.
    Falls back to the bundled-apworld path under %APPDATA%/SMOArchipelago/
    bundled/switch_mod/, which is where the frozen-Launcher build runs.
    """
    candidates = [
        REPO_ROOT / "switch-mod",
        REPO_ROOT / "switch_mod",
    ]
    # Add the appdata bundled extraction path. Import lazily to avoid
    # pulling apworld._setup into --audit-without-build runs that don't
    # need it.
    try:
        from _setup import appdata_root  # type: ignore[import-not-found]
        candidates.append(appdata_root() / "bundled" / "switch_mod")
    except ImportError:
        pass

    for c in candidates:
        if (c / "CMakeLists.txt").is_file():
            return c
    raise FileNotFoundError(
        f"could not find a switch-mod source tree (tried: {candidates})"
    )


def _is_under(rel_posix: str, root: str) -> bool:
    """True if `rel_posix` (a POSIX-form relative path) is the directory
    `root` itself or sits underneath it."""
    return rel_posix == root or rel_posix.startswith(root + "/")


def _list_git_tracked(switch_mod: Path) -> set[str] | None:
    """Return the set of POSIX-form paths git tracks under `switch_mod`,
    or None if `switch_mod` isn't in a git checkout (e.g. when running
    from a frozen-Launcher apworld extraction). The caller treats None
    as "skip the source-tree leak check" rather than failing — the leak
    check isn't applicable to non-git installs.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=switch_mod, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def audit_switch_mod(switch_mod: Path) -> tuple[list[str], list[str]]:
    """Walk the switch-mod source tree and partition every file into
    allowed (under an output root, or git-tracked, or an explicit
    allowed source-tree write) vs unexpected (everything else).

    This catches two distinct leaks:
      - The build dropping intermediate files OUTSIDE the output roots
        (e.g. a misbehaving cmake INSTALL command writing into src/).
      - A future patch_hakkun.py regression writing into git-tracked
        sources that should stay clean.

    On non-git installs (`_list_git_tracked` returns None), the git
    check is skipped and we fall back to "either under an output root
    or in SWITCH_MOD_ALLOWED_SOURCE_WRITES". That's looser but still
    catches the output-root-escape case, which is the more common
    failure mode.
    """
    allowed: list[str] = []
    unexpected: list[str] = []
    tracked = _list_git_tracked(switch_mod)
    submodule_paths = _discover_submodule_paths(switch_mod)
    for p in switch_mod.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(switch_mod).as_posix()
        # Skip git metadata. Three shapes show up:
        #   - "<sub>/.git"      (submodule gitlink file)
        #   - "<sub>/.gitignore" / "<sub>/.git/<...>"
        #   - "<...>/.git/<...>" anywhere in the tree
        # Strip them all so submodule init noise doesn't trip the audit.
        if (
            rel == ".git"
            or rel.startswith(".git/")
            or "/.git/" in rel
            or rel.endswith("/.git")
        ):
            continue
        # __pycache__ is Python bytecode that lands wherever .py scripts
        # are invoked from. In a dev checkout that's inside switch-mod/
        # (next to sys/tools/elf2nso.py etc.); in the wizard's frozen
        # runtime those scripts live under %APPDATA%/bundled/scripts/
        # and __pycache__ falls under the allowed "bundled/**" glob.
        # Skip these regardless — they're a runtime artifact, not a
        # build-scope concern.
        if "/__pycache__/" in rel or rel.startswith("__pycache__/"):
            continue
        if any(_is_under(rel, r) for r in SWITCH_MOD_OUTPUT_ROOTS):
            allowed.append(rel)
            continue
        if rel in SWITCH_MOD_ALLOWED_SOURCE_WRITES:
            allowed.append(rel)
            continue
        if tracked is not None and rel in tracked:
            allowed.append(rel)
            continue
        # The submodule-managed subdirs have their OWN git index, so
        # `git ls-files` at switch_mod's root doesn't see their files.
        # Probe each submodule's own ls-files to avoid flagging every
        # musl libc header / OdysseyHeaders type definition / Senobi
        # tool. Submodule list is discovered from `.gitmodules` so a
        # newly-added (or nested) submodule doesn't need an audit edit.
        if tracked is not None and _is_submodule_tracked(
            switch_mod, rel, submodule_paths
        ):
            allowed.append(rel)
            continue
        unexpected.append(rel)
    return allowed, unexpected


# Memoize per-submodule ls-files so we don't shell out once per file.
_submodule_tracked_cache: dict[Path, set[str] | None] = {}


def _discover_submodule_paths(switch_mod: Path) -> tuple[str, ...]:
    """Return POSIX-form submodule paths (relative to `switch_mod`) for
    every submodule found underneath it.

    Detection is by walking the tree and looking for `.git` entries --
    a submodule materializes a `.git` file (gitlink) or directory at
    its root regardless of whether `.gitmodules` lives in the parent
    repo. This avoids assuming `switch_mod/.gitmodules` exists (in this
    repo the submodules are registered in the outer .gitmodules at
    repo root) and also catches nested submodules (e.g. sys/tools/senobi).

    Sorted longest-first so the matching loop in `_is_submodule_tracked`
    hits the most specific submodule before walking up to its parent.
    """
    paths: list[str] = []
    for git_marker in switch_mod.rglob(".git"):
        # `rglob` matches both files (gitlink) and dirs. Either flavor
        # marks a submodule root.
        sub_dir = git_marker.parent
        if sub_dir == switch_mod:
            continue  # would be switch_mod itself's .git, not a submodule
        rel = sub_dir.relative_to(switch_mod).as_posix()
        paths.append(rel)
    return tuple(sorted(set(paths), key=len, reverse=True))


def _is_submodule_tracked(
    switch_mod: Path, rel_posix: str, submodule_paths: tuple[str, ...]
) -> bool:
    """True if `rel_posix` (relative to switch_mod) is git-tracked by one
    of `switch_mod`'s submodules. Each submodule has its own git index,
    so the top-level `git ls-files` from switch_mod misses them.
    """
    import subprocess
    for sub in submodule_paths:
        if not _is_under(rel_posix, sub):
            continue
        sub_path = switch_mod / sub
        if sub_path not in _submodule_tracked_cache:
            try:
                result = subprocess.run(
                    ["git", "ls-files"],
                    cwd=sub_path, capture_output=True, text=True, check=False,
                )
                if result.returncode == 0:
                    _submodule_tracked_cache[sub_path] = {
                        line.strip() for line in result.stdout.splitlines()
                        if line.strip()
                    }
                else:
                    _submodule_tracked_cache[sub_path] = None
            except FileNotFoundError:
                _submodule_tracked_cache[sub_path] = None
        tracked = _submodule_tracked_cache[sub_path]
        if tracked is None:
            return False
        # Strip the submodule prefix to get the path the submodule's
        # own ls-files reports it as.
        sub_rel = rel_posix[len(sub) + 1:]
        if sub_rel in tracked:
            return True
    return False


def run_audit() -> int:
    """Walk the expected sandbox roots, report unexpected files, and
    enforce the required-artifact list. Returns 0 on clean audit, 1 on
    any violation."""
    from _setup import appdata_root  # type: ignore[import-not-found]

    appdata = appdata_root()
    switch_mod = find_switch_mod_root()

    print(f"[audit] appdata root: {appdata}")
    print(f"[audit] switch-mod root: {switch_mod}")

    appdata_allowed, appdata_unexpected = audit_tree(
        appdata, ALLOWED_GLOBS["appdata"],
    )
    mod_allowed, mod_unexpected = audit_switch_mod(switch_mod)

    missing: list[str] = []
    for rel in REQUIRED_ARTIFACTS:
        if not (switch_mod / rel).exists():
            missing.append(rel)

    print(f"[audit] appdata: {len(appdata_allowed)} allowed, "
          f"{len(appdata_unexpected)} unexpected")
    print(f"[audit] switch-mod: {len(mod_allowed)} allowed, "
          f"{len(mod_unexpected)} unexpected")
    print(f"[audit] required artifacts: "
          f"{len(REQUIRED_ARTIFACTS) - len(missing)}/{len(REQUIRED_ARTIFACTS)} present")

    failed = False
    if appdata_unexpected:
        failed = True
        print(f"\n[audit] FAIL: unexpected files under {appdata}:")
        for rel in sorted(appdata_unexpected)[:50]:
            print(f"  {rel}")
        if len(appdata_unexpected) > 50:
            print(f"  ... ({len(appdata_unexpected) - 50} more)")
    if mod_unexpected:
        failed = True
        print(f"\n[audit] FAIL: unexpected files under {switch_mod}:")
        for rel in sorted(mod_unexpected)[:50]:
            print(f"  {rel}")
        if len(mod_unexpected) > 50:
            print(f"  ... ({len(mod_unexpected) - 50} more)")
    if missing:
        failed = True
        print(f"\n[audit] FAIL: required artifacts missing under {switch_mod}:")
        for rel in missing:
            print(f"  {rel}")

    if failed:
        return 1
    print("\n[audit] OK: all required artifacts present, no unexpected files")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--skip-extract",
        action="store_true",
        help="Write Nintendo-free stub shine_map.json + capture_map.json to "
             "%%APPDATA%%/SMOArchipelago/data/ instead of running the real "
             "extractor. Required for any --build run on a CI machine that "
             "doesn't have a real SMO dump.",
    )
    p.add_argument(
        "--build",
        action="store_true",
        help="Run sync_capture_table + build_switchmod via the apworld's "
             "_setup.build module (same code path the wizard uses).",
    )
    p.add_argument(
        "--audit",
        action="store_true",
        help="Walk the sandbox roots, enforce the allowlist, and check that "
             "the required artifacts (subsdk9, main.npdm, capture_table.h) "
             "exist.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Equivalent to --skip-extract --build --audit. The default if "
             "no stage flag is passed.",
    )
    p.add_argument(
        "--bridge-host",
        default="127.0.0.1",
        help="Stub bridge host baked into subsdk9 via cmake -DBRIDGE_HOST. "
             "Audit doesn't validate the value — the value just has to be "
             "syntactically a host. Default: 127.0.0.1.",
    )
    p.add_argument(
        "--force-stub-maps",
        action="store_true",
        help="Overwrite existing shine/capture maps even if they look "
             "like a real extraction. Only safe on a CI runner or in a "
             "sandboxed install — clobbering a dev machine's maps means "
             "rerunning the 10-minute extract_shine_map.py against the "
             "user's NSP.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # `--all` (or no stage flag at all) runs every stage. This is what
    # CI invokes; --skip-extract / --build / --audit individually are for
    # local debugging.
    if args.all or not (args.skip_extract or args.build or args.audit):
        args.skip_extract = True
        args.build = True
        args.audit = True

    if args.skip_extract:
        from _setup import data_dir  # type: ignore[import-not-found]
        written = write_stub_maps(data_dir(), force=args.force_stub_maps)
        print(f"[audit] wrote stub maps:")
        for p in written:
            print(f"  {p}")

    if args.build:
        rc = run_build(args.bridge_host)
        if rc != 0:
            return rc

    if args.audit:
        rc = run_audit()
        if rc != 0:
            return rc

    return 0


if __name__ == "__main__":
    sys.exit(main())
