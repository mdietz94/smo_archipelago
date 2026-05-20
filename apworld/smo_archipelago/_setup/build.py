"""Drive the actual build steps the wizard sequences.

This module is the seam between the Kivy wizard pages (which are mostly
layout) and the on-disk reality of cmake + the extractor scripts. Each
function streams its child process's stdout/stderr through a callback so
the wizard can render live progress; tests can inject a callback that
records lines into a list.

Layout-wise the bundled tools live under `_setup/switch_mod/` and
`_setup/scripts/`, dropped there by `scripts/install_apworld.py
--bundle-mod --bundle-scripts` at apworld-zip time. We resolve them
relative to this file so the wizard works regardless of how the apworld
got installed (loose source for repo devs vs zip for end users).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import appdata_root, build_dir, data_dir

# Suppress the per-child console window when the wizard runs under the
# Launcher's windowed PyInstaller (no parent console → Windows opens a
# fresh console for each CONSOLE-subsystem child, which steals focus
# from the Kivy wizard). No-op on non-Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Where the bundled C++ sources + extractor scripts live inside the apworld.
# Filled by `install_apworld.py --bundle-mod --bundle-scripts`. On dev
# checkouts you can populate it manually by symlinking switch-mod/ +
# scripts/ in, or by running install_apworld.py.
_SETUP_ROOT = Path(__file__).resolve().parent
_BUNDLED_MOD = _SETUP_ROOT / "switch_mod"
_BUNDLED_SCRIPTS = _SETUP_ROOT / "scripts"

# Memoizes the resolved on-disk location of the bundled tree. None means
# "haven't checked yet"; once resolved we never re-extract during the same
# process lifetime.
_extracted_bundled_root: Path | None = None


def _python_invoker() -> list[str]:
    """Return the command prefix that invokes a Python script via subprocess.

    Under AP's official Windows installer, `sys.executable` is
    `ArchipelagoLauncher.exe` — a PyInstaller-bundled launcher that
    argparse-parses its own argv. Spawning `[sys.executable, "-u",
    "script.py", "--nsp", ...]` doesn't run Python on script.py; it
    re-invokes the launcher with those args, which fails with
    "unrecognized arguments: -u --nsp ...". (Reproduced in the
    diagnostic build's extract.log.)

    Fall back to the `py` launcher (`py -3.12`), which the wizard's
    prereq check has already confirmed exists and works. We prefer 3.12
    over the system default because the extractor's bootstrap re-execs
    into a 3.12 venv anyway — invoking with 3.12 from the start means
    the os.execv is a no-op when oead is already installed.

    On a dev source checkout, `sys.executable` IS a Python interp and
    we use it directly so the script runs under the same venv the
    developer set up for the rest of SMOClient.
    """
    exe_name = Path(sys.executable).stem.lower()
    if exe_name in ("python", "python3", "py", "pythonw"):
        return [sys.executable]
    # Frozen-launcher path: probe alternatives in preference order.
    if shutil.which("py"):
        return ["py", "-3.12"]
    for candidate in ("python3.12", "python3", "python"):
        if shutil.which(candidate):
            return [candidate]
    # Last-resort: return sys.executable so the resulting error at
    # least shows what we tried (better than spawning nothing at all).
    return [sys.executable]

# Progress-line callback type: receives one rstripped line of stdout/stderr
# from the child process per call. None means "process finished" — wizard
# uses it to flip the spinner off.
ProgressFn = Callable[[str], None]


@dataclass
class BuildResult:
    """Outcome of a single subprocess invocation.

    `ok` is the green-light flag. `returncode` and `log` are surfaced so
    the wizard's "Copy log to clipboard" button has something to copy on
    failure.
    """
    ok: bool
    returncode: int
    log: str


def _find_apworld_zip(setup_root: Path) -> Path | None:
    """Walk up from `_SETUP_ROOT` looking for a `.apworld` file ancestor.

    Returns the zip path if `setup_root` is inside a zip-loaded apworld
    (the production case under AP's frozen Launcher), or None on a dev
    source checkout where `_SETUP_ROOT` is a real on-disk directory."""
    cur = setup_root
    # Walk up at most ~10 levels; .apworld is normally 2-3 levels above us.
    for _ in range(10):
        # `is_file()` distinguishes "real zip ancestor" from "real directory".
        if cur.suffix == ".apworld" and cur.is_file():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent
    return None


def _extract_bundled_tree() -> Path:
    """Extract the bundled `scripts/` + `switch_mod/` trees from inside
    `meatballs.apworld` to a real filesystem location, and return that location.

    Necessary because:
      - AP loads `.apworld` files via Python's zipimporter. Code inside
        the zip imports fine, but `Path(__file__).parent / "scripts" /
        "x.py"` is a path string with the .apworld ZIP-file as a midpoint
        directory — `Path.exists()` returns False, `subprocess.run()`
        can't invoke files at such paths.
      - The extractor script bootstraps a venv next to itself
        (`<script_dir>/.extract-venv/`); cmake reads switch_mod/ as a
        regular source tree. Both need real on-disk files.

    Caches in `_extracted_bundled_root` so we extract once per process.
    On a dev source checkout where `_SETUP_ROOT` is a real directory,
    the in-place path is returned without copying.

    Extraction target: `%APPDATA%/SMOArchipelago/bundled/`. The 1500-odd
    files (~25 MB unpacked) plus the eventual ~5 GB RomFS cache and
    Python 3.12 venv live there too — kept off C: root and out of the
    AP install dir (which on the official installer requires admin to
    write to)."""
    global _extracted_bundled_root
    if _extracted_bundled_root is not None:
        return _extracted_bundled_root

    apworld_zip = _find_apworld_zip(_SETUP_ROOT)
    if apworld_zip is None:
        # Dev / source checkout — _SETUP_ROOT IS the real on-disk dir.
        _extracted_bundled_root = _SETUP_ROOT
        return _extracted_bundled_root

    dst = appdata_root() / "bundled"
    # Marker file records the source-zip mtime so a refresh of the
    # apworld (e.g. user upgrades to a new release) triggers re-extract
    # instead of using a stale cached copy.
    marker = dst / ".source-zip-mtime"
    src_mtime = apworld_zip.stat().st_mtime
    if marker.exists():
        try:
            cached_mtime = float(marker.read_text(encoding="utf-8").strip())
            if cached_mtime == src_mtime and (dst / "scripts").exists():
                _extracted_bundled_root = dst
                return _extracted_bundled_root
        except (ValueError, OSError):
            pass  # corrupt marker — re-extract

    # Stale or absent — wipe and re-extract.
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)

    # We extract two subtrees from inside `meatballs.apworld`:
    #   meatballs/_setup/scripts/...  ->  <bundled>/scripts/...
    #   meatballs/_setup/switch_mod/... -> <bundled>/switch_mod/...
    #   meatballs/data/...            ->  <bundled>/data/...
    #
    # The `meatballs/_setup/` ones are the cross-compile scripts + sources
    # subprocesses invoke directly. The `meatballs/data/` ones are items.json
    # and locations.json, which the extractor reads on disk for
    # cross-validation against the SMO RomFS dump. (The rest of the
    # apworld — Python modules, client/, hooks/ — is loaded by zipimport
    # from inside the .apworld zip and doesn't need extraction.)
    prefixes = (
        ("meatballs/_setup/", ""),     # extract sibling to "scripts/" + "switch_mod/"
        ("meatballs/data/", "data/"),  # extract at <bundled>/data/<filename>
    )
    with zipfile.ZipFile(apworld_zip) as zf:
        for info in zf.infolist():
            name = info.filename
            for src_prefix, dst_prefix in prefixes:
                if not name.startswith(src_prefix):
                    continue
                rel = dst_prefix + name[len(src_prefix):]
                if rel == dst_prefix:  # the prefix entry itself
                    break
                target = dst / rel
                if info.is_dir() or name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    break
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src_f, open(target, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                break  # name matched this prefix; don't try the next

    marker.write_text(str(src_mtime), encoding="utf-8")
    _extracted_bundled_root = dst
    return _extracted_bundled_root


def bundled_switch_mod() -> Path:
    """Path to the bundled `switch_mod/` source tree, or raises if absent.

    On a frozen-Launcher install this extracts the tree out of the
    apworld zip to %APPDATA%/SMOArchipelago/bundled/ on first call.
    Absent (after a successful extract) means the apworld zip was built
    without `--bundle-mod`, or this is a dev checkout where the bundling
    hasn't been done."""
    root = _extract_bundled_tree()
    mod = root / "switch_mod"
    if not (mod / "CMakeLists.txt").exists():
        raise FileNotFoundError(
            f"bundled switch_mod sources not found at {mod}. "
            f"Run `python scripts/install_apworld.py --bundle-mod` first."
        )
    return mod


def bundled_script(name: str) -> Path:
    """Path to a bundled extractor/sync script, or raises if absent.

    On a frozen-Launcher install this extracts the scripts/ folder out of
    the apworld zip to %APPDATA%/SMOArchipelago/bundled/scripts/ on first
    call. Subsequent calls hit the cached extraction. The script can
    therefore write its venv + RomFS cache to siblings of its own
    location, which it expects to be a real on-disk directory."""
    root = _extract_bundled_tree()
    p = root / "scripts" / name
    if not p.exists():
        raise FileNotFoundError(
            f"bundled script {name!r} not found at {p}. "
            f"Run `python scripts/install_apworld.py --bundle-scripts` first."
        )
    return p


def bundled_data_file(name: str) -> Path:
    """Path to a bundled apworld data file (items.json, locations.json),
    or raises if absent.

    Same zip-extraction story as bundled_script: the extractor reads
    these on disk for cross-validation, so we need a real filesystem
    path, not a zipimport-style path inside the .apworld."""
    root = _extract_bundled_tree()
    p = root / "data" / name
    if not p.exists():
        raise FileNotFoundError(
            f"bundled data file {name!r} not found at {p}. "
            f"This usually means the apworld was built without the data "
            f"directory — re-run `python scripts/install_apworld.py`."
        )
    return p


def _stream_subprocess(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """Run a subprocess, streaming stdout + stderr line-by-line to
    `on_line` and accumulating the full text into `log` for failure diag.

    stderr is merged into stdout so cmake's "this file failed to compile"
    interleaves correctly with the progress chatter on stdout.

    The exact `cmd` is emitted via `on_line` BEFORE spawning so the
    wizard's file log + Kivy widget show what would have been run, even
    if the child produces zero output (which has been the failure mode
    we keep chasing in the extract step).
    """
    log_lines: list[str] = []

    def _emit(line: str) -> None:
        log_lines.append(line)
        if on_line is not None:
            on_line(line)

    _emit(f"[stream] spawning: {cmd}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError) as e:
        msg = f"failed to spawn {cmd[0]}: {e}"
        _emit(msg)
        return BuildResult(ok=False, returncode=127, log=msg)

    _emit(f"[stream] spawned pid={proc.pid}; waiting for stdout...")
    assert proc.stdout is not None
    for raw in proc.stdout:
        _emit(raw.rstrip("\r\n"))
    rc = proc.wait()
    _emit(f"[stream] subprocess exited with code {rc}")
    return BuildResult(ok=(rc == 0), returncode=rc, log="\n".join(log_lines))


def run_sync_capture_table(on_line: ProgressFn | None = None) -> BuildResult:
    """Regenerate `switch_mod/src/ap/capture_table.h` from items.json.

    Runs the bundled `sync_capture_table.py`. Output is the C++ header the
    Switch mod build needs at compile time. Idempotent — safe to run before
    every build. The build will fail with a compiler error if this is
    skipped (the header is gitignored).

    All three paths are passed explicitly because the script's
    `Path(__file__).parent.parent`-relative defaults assume a dev source
    checkout layout. In the bundled layout: items.json lives at
    <bundled>/data/, switch_mod uses an underscore (Python module-name
    convention), and capture_map.json is wherever the extract step wrote
    it under %APPDATA%/SMOArchipelago/data/.
    """
    script = bundled_script("sync_capture_table.py")
    items = bundled_data_file("items.json")
    out_header = bundled_switch_mod() / "src" / "ap" / "capture_table.h"
    capture_map = data_dir() / "capture_map.json"
    return _stream_subprocess(
        [
            *_python_invoker(), str(script),
            "--items", str(items),
            "--out", str(out_header),
            "--capture-map", str(capture_map),
        ],
        on_line=on_line,
    )


def run_extract_maps(
    dump_path: Path,
    *,
    keys_path: Path | None = None,
    hactool_path: Path | None = None,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """Generate `%APPDATA%/SMOArchipelago/data/{shine,capture}_map.json`.

    Wraps the bundled `extract_shine_map.py`. The script self-bootstraps
    a Python 3.12 venv on first run because `oead` has no wheel for
    Python 3.13+ — wizard's prereq check (`check_python312`) confirms
    that's possible before we ever get here.

    `dump_path` may be an NSP or an XCI; the extractor flag is picked
    from the file extension. XCI dumps additionally require `title.keys`
    populated with the SMO entry (NSPs ship a .tik inside the package
    we can lift directly; XCIs do not).

    Outputs are written into the per-user `%APPDATA%/SMOArchipelago/data/`
    so SMOClient picks them up via the search path added in `client/main.py`.
    Nothing lands inside the repo.

    The child process is forced unbuffered via `-u` + `PYTHONUNBUFFERED=1`.
    Without this, the wizard's log box stays blank for the full 30-90s of
    venv-creation + `pip install oead` (both produce zero output normally,
    AND Python buffers stdout when it isn't a TTY — combined silence makes
    the wizard look frozen). The env var also propagates through the
    `os.execv` the bootstrap does to relaunch under the new venv's
    interpreter, where the `-u` flag is otherwise lost.
    """
    script = bundled_script("extract_shine_map.py")
    out_dir = data_dir()
    # Pick --nsp vs --xci from the file extension. Anything other than
    # .xci falls through to --nsp so unknown / missing extensions still
    # hit the established code path with its clear "NSP not found"
    # error rather than failing in a less obvious place inside hactool.
    dump_flag = "--xci" if dump_path.suffix.lower() == ".xci" else "--nsp"
    # Point the extractor at the bundled apworld data files explicitly.
    # Its REPO_ROOT-relative default (`<__file__>.parent.parent / "apworld"
    # / "smo_archipelago" / "data" / "locations.json"`) assumes a dev
    # source checkout layout; the post-zip-extract bundled layout puts
    # them at <bundled>/data/.
    args = [
        *_python_invoker(), "-u", str(script),
        dump_flag, str(dump_path),
        "--out", str(out_dir / "shine_map.json"),
        "--review", str(out_dir / "shine_map_review.json"),
        "--cap-out", str(out_dir / "capture_map.json"),
        "--cap-review", str(out_dir / "capture_map_review.json"),
        "--locations", str(bundled_data_file("locations.json")),
        "--items", str(bundled_data_file("items.json")),
    ]
    if keys_path:
        args += ["--keys", str(keys_path)]
    if hactool_path:
        args += ["--hactool", str(hactool_path)]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return _stream_subprocess(args, env=env, on_line=on_line)


def run_cmake_configure(
    bridge_host: str,
    *,
    devkitpro: str | None = None,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """CMake configure step: produces the Ninja build files.

    `bridge_host` is baked into the resulting binary via the toolchain's
    `add_compile_definitions(BRIDGE_HOST_STRING=...)`. This is the choice
    point that makes one user's `subsdk9` different from another's.
    `DEVKITPRO` must be in env (set by the devkitPro installer); pass
    explicitly here only for tests / non-standard installs.
    """
    mod_root = bundled_switch_mod()
    env = os.environ.copy()
    if devkitpro:
        env["DEVKITPRO"] = devkitpro
    toolchain = mod_root / "lunakit-vendor" / "cmake" / "toolchain.cmake"
    # Use the cmake binary the prereq check resolved (Windows-native if
    # available). A bare `"cmake"` here would re-resolve via PATH, and
    # devkitPro's installer puts `C:\devkitPro\msys2\usr\bin` ahead of
    # the Windows CMake install dir — msys2 cmake then mangles
    # `C:\Users\...` paths into `/c/cwd/C:/Users/...` because it treats
    # `:` as a path separator rather than a drive-letter marker.
    from .prereqs import resolved_cmake
    return _stream_subprocess(
        [
            resolved_cmake(),
            "-S", str(mod_root),
            "-B", str(build_dir() / "cmake"),
            "-G", "Ninja",
            f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
            f"-DBRIDGE_HOST={bridge_host}",
        ],
        env=env,
        on_line=on_line,
    )


def run_cmake_build(on_line: ProgressFn | None = None) -> BuildResult:
    """CMake build step: invokes Ninja under the hood, produces
    `subsdk9`, `subsdk9.elf`, `main.npdm`, `ap_config.json` inside
    `%APPDATA%/SMOArchipelago/build/cmake/`."""
    # Same Windows-vs-msys2 cmake-binary rationale as run_cmake_configure.
    from .prereqs import resolved_cmake
    return _stream_subprocess(
        [resolved_cmake(), "--build", str(build_dir() / "cmake")],
        on_line=on_line,
    )


def collect_build_outputs() -> dict[str, Path]:
    """Returns {logical_name: path} for the three artifacts the deploy
    step needs (or raises FileNotFoundError if any is missing — caller
    should treat that as a build failure even if cmake returned 0)."""
    cmake_build = build_dir() / "cmake"
    outputs = {
        "subsdk9": cmake_build / "subsdk9",
        "main.npdm": cmake_build / "main.npdm",
        "ap_config.json": cmake_build / "ap_config.json",
    }
    missing = [name for name, p in outputs.items() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"build did not produce expected outputs: {missing} "
            f"(check {cmake_build} for details)"
        )
    return outputs


def maps_ready() -> bool:
    """True iff both extracted maps live in the per-user data dir."""
    d = data_dir()
    return (d / "shine_map.json").exists() and (d / "capture_map.json").exists()


def build_ready() -> bool:
    """True iff a usable set of build outputs lives in the per-user
    build dir. Doesn't validate freshness — re-running setup
    re-overwrites."""
    try:
        collect_build_outputs()
        return True
    except FileNotFoundError:
        return False
