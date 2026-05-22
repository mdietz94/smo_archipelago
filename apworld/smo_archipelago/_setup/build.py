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

import json
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

    Resolution order:
      1. The wizard-verified Python 3.12 (resolved by `check_python312`,
         cached in prereqs._resolved_python312_bin). This is the SAME
         interpreter `install_sail_python_deps` pip-installed lz4 into,
         and the SAME 3.12 the extractor's venv targets. Preferring it
         means child scripts (build_switchmod.py, extract_shine_map.py,
         patch_hakkun.py) run under the right Python even when the
         wizard itself is being run by Archipelago's launcher under a
         different version (3.13 / 3.14 / Store stub).
      2. `sys.executable` if it's named like a real Python interpreter
         (`python` / `python3` / `py` / `pythonw`). Covers the dev-
         source-checkout case where the wizard prereq check hasn't
         been run (no cached path) and the dev launched us under their
         own venv Python — that's also where their deps live.
      3. `py -3.12` on PATH (covers the frozen-launcher case before
         prereq check ran).
      4. `python3.12` / `python3` / `python` on PATH as a last resort.
      5. `sys.executable` as the irrecoverable fallback (better to
         spawn SOMETHING with a clear error than nothing at all).

    Under AP's official Windows installer, `sys.executable` is
    `ArchipelagoLauncher.exe` — a PyInstaller-bundled launcher that
    argparse-parses its own argv. Spawning `[sys.executable, "-u",
    "script.py", ...]` doesn't run Python on script.py; it re-invokes
    the launcher with those args, which fails with "unrecognized
    arguments". Step 1 + 3 cover that case.
    """
    # Lazy import to avoid a top-level circular dep risk (prereqs.py
    # doesn't import build.py today but is closer to the leaf of the
    # _setup graph, so importing it from build.py is fine — we just
    # avoid the top-level for clarity that this is a "use if available"
    # lookup, not a hard dep).
    from .prereqs import resolved_python312_bin
    pinned = resolved_python312_bin()
    if pinned:
        py_exe = Path(pinned) / "python.exe"
        if py_exe.is_file():
            return [str(py_exe)]

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


def _staging_has_files(staging: Path) -> bool:
    """True iff at least one of the expected subdirs landed non-empty —
    used to reject a zip whose prefix filters matched no real entries
    (a truncated or corrupt apworld)."""
    for subdir in ("scripts", "switch_mod", "data"):
        d = staging / subdir
        try:
            if d.is_dir() and any(d.iterdir()):
                return True
        except OSError:
            continue
    return False


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

    # Cache freshness check. Bare directory existence
    # (`(dst / "scripts").exists()`) is unsafe — a previous extraction
    # that crashed mid-write leaves the directories in place but the
    # entry-point files missing, and subsequent cmake / extract runs
    # fail far downstream with confusing "file not found" errors.
    #
    # We don't pin a specific sentinel-file list because the bundled
    # tree's contents depend on which `install_apworld.py --bundle-*`
    # flags were passed when building the zip — a data-only zip
    # legitimately has no `scripts/` or `switch_mod/`. Instead we
    # require that AT LEAST ONE expected subdir is present and
    # non-empty: that signals the previous extraction made it past
    # mkdir into actually writing files. The per-function callers
    # (`bundled_script`, `bundled_switch_mod`, `bundled_data_file`)
    # then raise their own specific FileNotFoundError if their needed
    # file is the one missing — that's already a clear "rebuild the
    # apworld with --bundle-mod" message.
    def _cache_looks_intact() -> bool:
        for subdir in ("scripts", "switch_mod", "data"):
            d = dst / subdir
            try:
                if d.is_dir() and any(d.iterdir()):
                    return True
            except OSError:
                continue
        return False

    if marker.exists():
        try:
            cached_mtime = float(marker.read_text(encoding="utf-8").strip())
            if cached_mtime == src_mtime and _cache_looks_intact():
                _extracted_bundled_root = dst
                return _extracted_bundled_root
        except (ValueError, OSError):
            pass  # corrupt marker — re-extract

    # Stale or absent — wipe and re-extract. Use a NamedTemporaryFile-
    # style "extract to <dst>.new, then swap" pattern so a crash mid-
    # extraction can't poison the cache: the marker file is the LAST
    # thing written, and only after the swap; until then any prior good
    # extraction at `dst` remains usable on a retry.
    staging = dst.with_name(dst.name + ".new")
    if staging.exists():
        try:
            shutil.rmtree(staging)
        except OSError as e:
            raise RuntimeError(
                f"Could not clear stale bundled-tree staging dir at "
                f"{staging}: {e}. Close any program that might be "
                f"holding files in this folder (Ryujinx, an antivirus "
                f"realtime scan, Explorer windows) and re-run setup."
            ) from e
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Could not create bundled-tree staging dir at {staging}: "
            f"{e}. Check that %APPDATA% is writable and has free space."
        ) from e

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
    current_target: Path | None = None
    try:
        with zipfile.ZipFile(apworld_zip) as zf:
            for info in zf.infolist():
                name = info.filename
                for src_prefix, dst_prefix in prefixes:
                    if not name.startswith(src_prefix):
                        continue
                    rel = dst_prefix + name[len(src_prefix):]
                    if rel == dst_prefix:  # the prefix entry itself
                        break
                    target = staging / rel
                    current_target = target
                    if info.is_dir() or name.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                        break
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src_f, open(target, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                    # Post-write size assertion. ZIP's CRC is checked by
                    # `zf.open` when the stream is fully consumed, but a
                    # disk-full or AV-injected truncation between read
                    # and `open(target, "wb")` write completion can leave
                    # the destination shorter than expected without
                    # raising. Catch that explicitly so we don't write
                    # the marker and poison the cache.
                    actual = target.stat().st_size
                    if actual != info.file_size:
                        raise RuntimeError(
                            f"bundled tree extraction wrote {actual} "
                            f"bytes for {rel} but the zip entry "
                            f"declares {info.file_size}"
                        )
                    break  # name matched this prefix; don't try the next
    except (OSError, zipfile.BadZipFile, RuntimeError) as e:
        # Best-effort cleanup of the staging dir so the next retry
        # starts fresh. The marker is NOT written, so the cache is
        # unchanged — either the previous good extraction stays in
        # place at `dst`, or no cache exists (first run) and the next
        # call will retry from scratch.
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass
        loc = f" while writing {current_target}" if current_target else ""
        raise RuntimeError(
            f"Failed to extract bundled tree from {apworld_zip.name}"
            f"{loc}: {type(e).__name__}: {e}. Common causes: disk full, "
            f"antivirus blocking the write, or the .apworld zip is "
            f"corrupt. Free space under %APPDATA%, then re-run setup."
        ) from e

    # Verify the staged tree has SOMETHING in it before swapping in. A
    # zip that matches our prefix filters but contains no actual files
    # (e.g. an empty meatballs/_setup/ with no children) would silently
    # produce an empty staging dir; swapping that in over a previously
    # good cache would be worse than failing here.
    if not _staging_has_files(staging):
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass
        raise FileNotFoundError(
            f"bundled tree extracted from {apworld_zip.name} contains "
            f"no files under scripts/, switch_mod/, or data/. The "
            f"apworld zip is likely truncated or corrupt — re-download "
            f"the release zip and try again."
        )

    # Swap-in: remove the old `dst` (if any), then rename staging→dst.
    # On Windows a rename across a non-empty target is a hard error, so
    # the rmtree has to come first. If it fails because the old tree is
    # locked, surface the actionable cause rather than silently leaving
    # the stale tree in place.
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except OSError as e:
            try:
                shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                pass
            raise RuntimeError(
                f"Could not remove old bundled tree at {dst}: {e}. "
                f"Close any program holding files in that folder "
                f"(Ryujinx, antivirus realtime scan, Explorer window) "
                f"and re-run setup."
            ) from e
    try:
        staging.rename(dst)
    except OSError as e:
        raise RuntimeError(
            f"Could not finalize bundled tree at {dst}: {e}. The "
            f"staged tree at {staging} is intact; you can rename it "
            f"manually or re-run setup."
        ) from e

    try:
        marker.write_text(str(src_mtime), encoding="utf-8")
    except OSError as e:
        raise RuntimeError(
            f"Bundled tree extracted to {dst} but the cache marker "
            f"could not be written: {e}. The next run will re-extract "
            f"unnecessarily — non-fatal but wastes ~25 MB of writes."
        ) from e
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


# Exit code we synthesize when the child gets killed for exceeding a
# timeout. Picked from the "deliberately distinct from likely real
# returncodes" range — child processes typically return 0–127 or signal
# values 128–255. 124 mirrors `timeout(1)`'s convention on Linux.
TIMEOUT_RETURNCODE = 124


# Per-step subprocess timeouts. Two independent timers per step:
#   wall — total wall-clock cap from spawn to exit
#   stall — maximum time without any stdout line
# Tuned so a healthy run is comfortably under wall and almost never
# triggers stall, while a wedged hactool / cmake / ninja gets killed in
# minutes instead of hanging the wizard indefinitely. Stall is usually
# the more useful of the two for long builds — "5 minutes with no
# output from ninja" is a much sharper signal of deadlock than "an hour
# of wall-clock". Bumped upward when a step has known long-silence
# phases (extract: hactool decrypts ~5 GB of NCAs silently; build:
# clang's C++ template instantiations can compile for minutes
# without printing anything).
_TIMEOUTS = {
    # Extractor needs to bootstrap a Python 3.12 venv on first run
    # (pip-installing oead — ~30-90s of network silence), then hactool
    # decrypts every NCA. Generous limits because failing here forces
    # the user to re-download a 5 GB NSP from a clean source.
    "extract":           {"wall": 1800.0, "stall": 600.0},
    # Tiny script, no network, no cross-compile — should finish in
    # under a second on a warm cache, a few seconds cold.
    "sync_capture":      {"wall": 120.0,  "stall": 60.0},
    # build_switchmod.py wraps the full Hakkun compile: patch_hakkun
    # (idempotent, fast), sail build (~30-90s first run, instant after),
    # cmake configure (~10s), ninja build (~30s warm, ~3 min cold). A
    # single template-heavy compilation unit can be silent for minutes
    # so the stall cap is more lenient than wall would suggest. First
    # cold build pulls / re-patches sail and may compile sail's host
    # binary, so the wall cap covers a ~5-minute worst case.
    "build_switchmod":   {"wall": 1800.0, "stall": 600.0},
}


def _stream_subprocess(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    on_line: ProgressFn | None = None,
    wall_timeout_s: float | None = None,
    stall_timeout_s: float | None = None,
) -> BuildResult:
    """Run a subprocess, streaming stdout + stderr line-by-line to
    `on_line` and accumulating the full text into `log` for failure diag.

    stderr is merged into stdout so cmake's "this file failed to compile"
    interleaves correctly with the progress chatter on stdout.

    The exact `cmd` is emitted via `on_line` BEFORE spawning so the
    wizard's file log + Kivy widget show what would have been run, even
    if the child produces zero output (which has been the failure mode
    we keep chasing in the extract step).

    Two independent timeouts bound the child's lifetime so a wedged
    subprocess can't hang the wizard forever:

    - `wall_timeout_s` — total wall-clock cap from spawn to exit. None
      means no cap (use only for known-fast operations).
    - `stall_timeout_s` — maximum interval between stdout lines. None
      means no cap. This is usually the more useful of the two for long
      builds, since "no output for 5 minutes" is a much sharper signal
      of a wedged hactool/cmake/ninja than total wall-clock time.

    On timeout the child is SIGTERM'd; if it doesn't exit within 5s, it
    gets SIGKILL'd. The result reports `ok=False`, `returncode=124`, and
    a `log` entry explaining which timeout fired so the caller can
    surface a precise message to the user.
    """
    import queue as _queue
    import threading as _threading
    import time as _time

    log_lines: list[str] = []

    def _emit(line: str) -> None:
        log_lines.append(line)
        if on_line is not None:
            on_line(line)

    _emit(f"[stream] spawning: {cmd}")
    if wall_timeout_s is not None:
        _emit(f"[stream] wall timeout: {wall_timeout_s:.0f}s")
    if stall_timeout_s is not None:
        _emit(f"[stream] stall timeout: {stall_timeout_s:.0f}s")

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

    # Reader thread pumps stdout into a queue so the main loop can poll
    # with a short timeout and decide whether to kill the child. Direct
    # iteration over `proc.stdout` blocks forever on a wedged child;
    # there's no public Python API to read with a timeout.
    line_queue: "_queue.Queue[str | None]" = _queue.Queue()

    def _reader() -> None:
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line_queue.put(raw)
        except Exception as e:
            line_queue.put(f"[stream] reader thread crashed: {e}\n")
        finally:
            line_queue.put(None)  # sentinel

    reader = _threading.Thread(target=_reader, daemon=True)
    reader.start()

    spawn_ts = _time.monotonic()
    last_output_ts = spawn_ts
    timeout_reason: str | None = None

    while True:
        try:
            raw = line_queue.get(timeout=0.5)
        except _queue.Empty:
            raw = ""
        if raw is None:
            # Reader thread saw EOF — child closed stdout. Now wait for
            # the child to actually exit so we can collect the return
            # code. Bounded so a child that closes stdout but never
            # exits doesn't hang us either.
            break
        if raw:
            _emit(raw.rstrip("\r\n"))
            last_output_ts = _time.monotonic()

        now = _time.monotonic()
        if wall_timeout_s is not None and (now - spawn_ts) > wall_timeout_s:
            timeout_reason = (
                f"wall-clock timeout exceeded "
                f"({wall_timeout_s:.0f}s total cap)"
            )
            break
        if stall_timeout_s is not None and (now - last_output_ts) > stall_timeout_s:
            timeout_reason = (
                f"stall timeout exceeded "
                f"({stall_timeout_s:.0f}s with no subprocess output)"
            )
            break

    if timeout_reason is not None:
        _emit(f"[stream] killing pid={proc.pid}: {timeout_reason}")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                _emit(f"[stream] pid={proc.pid} didn't exit within 5s of SIGTERM; SIGKILL'ing")
                proc.kill()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    _emit(
                        f"[stream] pid={proc.pid} still running 5s after "
                        f"SIGKILL — orphaning the process; check Task "
                        f"Manager and end it manually before retrying"
                    )
        except (OSError, ProcessLookupError) as e:
            _emit(f"[stream] terminate raised {type(e).__name__}: {e}")
        # Drain remaining queue entries best-effort so the log captures
        # any output the reader managed to push before we killed.
        try:
            while True:
                raw = line_queue.get_nowait()
                if raw is None:
                    break
                _emit(raw.rstrip("\r\n"))
        except _queue.Empty:
            pass
        msg = f"[stream] {timeout_reason}; subprocess killed"
        _emit(msg)
        return BuildResult(
            ok=False,
            returncode=TIMEOUT_RETURNCODE,
            log="\n".join(log_lines),
        )

    # Normal exit path: stdout EOF reached; wait briefly for the child
    # to actually exit. If it doesn't exit within 5s, kill it — a
    # process that closed stdout but won't return is just as wedged as
    # one that hung mid-write.
    try:
        rc = proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        _emit(
            f"[stream] pid={proc.pid} closed stdout but didn't exit "
            f"within 5s; killing"
        )
        try:
            proc.kill()
            try:
                rc = proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                _emit(
                    f"[stream] pid={proc.pid} ignored SIGKILL; "
                    f"orphaning — end it manually before retrying"
                )
                return BuildResult(
                    ok=False,
                    returncode=TIMEOUT_RETURNCODE,
                    log="\n".join(log_lines),
                )
        except (OSError, ProcessLookupError):
            rc = TIMEOUT_RETURNCODE
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
    t = _TIMEOUTS["sync_capture"]
    return _stream_subprocess(
        [
            *_python_invoker(), str(script),
            "--items", str(items),
            "--out", str(out_header),
            "--capture-map", str(capture_map),
        ],
        on_line=on_line,
        wall_timeout_s=t["wall"],
        stall_timeout_s=t["stall"],
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
    t = _TIMEOUTS["extract"]
    return _stream_subprocess(
        args, env=env, on_line=on_line,
        wall_timeout_s=t["wall"],
        stall_timeout_s=t["stall"],
    )


def run_build_switchmod(
    bridge_host: str,
    *,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """Drive the full Switch mod build via `scripts/build_switchmod.py`.

    The wrapper script does its own patch_hakkun → setup_sail → cmake
    configure → ninja build in a single subprocess, so the wizard goes
    from two build-step screens (configure + build) down to one. This
    matches what `smo-build` runs from the dev machine.

    `bridge_host` is baked into subsdk9 at compile time via
    `-DBRIDGE_HOST=`. ApDiscovery uses that IP's /24 as the unicast sweep
    range. The actual SMOClient may be on a neighbouring octet, so the
    sweep covers a moving target within the same LAN — but the baked
    seed has to be correct or the sweep range is wrong.

    PATH wiring: the wrapper reads `SMOAP_LLVM_BIN` / `SMOAP_MINGW_BIN`
    / `SMOAP_CMAKE_BIN` / `SMOAP_NINJA_BIN` env vars (defaulting to
    a dev's winget paths) and prepends them to the subprocess PATH.
    We populate those vars from the prereq checker's resolved bin dirs
    so the build uses the SAME toolchain the prereq check passed —
    not whatever's first on the user's PATH.
    """
    from .prereqs import (
        resolved_llvm_bin, resolved_mingw_bin, resolved_python312_bin,
        resolved_ninja_bin, resolved_cmake,
    )

    script = bundled_script("build_switchmod.py")
    mod_root = bundled_switch_mod()
    env = os.environ.copy()
    # Hand the wrapper its toolchain dirs explicitly. None means "let the
    # script's hardcoded default win" (the dev machine layout), which is
    # also what happens if the prereq check didn't run yet.
    #
    # IMPORTANT: every prereq the user verified MUST be plumbed through
    # here, or build_switchmod.py's hardcoded defaults fire for end-user
    # machines (those defaults match a dev's winget + msys2 layout, with
    # at least one literal username in the Ninja path). Resolver/consumer
    # misalignment is the same bug class as PR #169's Python pin.
    llvm = resolved_llvm_bin()
    if llvm:
        env["SMOAP_LLVM_BIN"] = llvm
    mingw = resolved_mingw_bin()
    if mingw:
        env["SMOAP_MINGW_BIN"] = mingw
    # Pin Python to the EXACT 3.12 that install_sail_python_deps pip-
    # installed lz4 into. Without this, build_switchmod.py falls back to
    # dirname(sys.executable), which is whatever Python is currently
    # running the wizard — and that can be 3.14 if Archipelago's
    # launcher chose a system Python via its fallback chain. cmake's
    # bare `python elf2nso.py` would then resolve to 3.14, which doesn't
    # have lz4 (lz4 lives in 3.12's user-site).
    python312 = resolved_python312_bin()
    if python312:
        env["SMOAP_PYTHON_BIN"] = python312
    # Pin ninja to the dir check_ninja resolved. build_switchmod.py's
    # default is hardcoded to a dev-machine winget path that contains a
    # literal username — broken for any other user. The wizard's
    # check_ninja prepends the right dir to its own PATH (so the build
    # subprocess inherits it at the tail of its PATH chain), but
    # plumbing the dir explicitly here makes the prepended slot point
    # at the verified ninja instead of the broken default.
    ninja = resolved_ninja_bin()
    if ninja:
        env["SMOAP_NINJA_BIN"] = ninja
    # Pin cmake similarly. `resolved_cmake()` returns the full exe path
    # (or the bare-name "cmake" if detection didn't find a real path);
    # take the parent dir to feed build_switchmod.py's bin-dir-shaped
    # SMOAP_CMAKE_BIN slot. Skip the bare-name case — no useful dir to
    # plumb, and build_switchmod.py's `C:\Program Files\CMake\bin`
    # default is at least a real Kitware install location to fall back
    # on, vs the username-hardcoded ninja default.
    cmake = resolved_cmake()
    if cmake and cmake != "cmake":
        env["SMOAP_CMAKE_BIN"] = os.path.dirname(cmake)
    # The wrapper hardcodes its source dir relative to its own location,
    # so it'll find `<bundled>/switch_mod/` correctly when invoked as
    # `python <bundled>/scripts/build_switchmod.py`.
    args = [
        *_python_invoker(), "-u", str(script),
        f"-DBRIDGE_HOST={bridge_host}",
    ]
    t = _TIMEOUTS["build_switchmod"]
    return _stream_subprocess(
        args,
        env=env,
        on_line=on_line,
        wall_timeout_s=t["wall"],
        stall_timeout_s=t["stall"],
    )


def collect_build_outputs() -> dict[str, Path]:
    """Returns {logical_name: path} for the two artifacts the deploy
    step needs (or raises FileNotFoundError if any is missing — caller
    should treat that as a build failure even if cmake returned 0).

    `build_switchmod.py` lays its output under `switch_mod/build/sd/...`
    (matching the dev-machine convention from the smo-build skill).
    Subsdk9 lands inside `atmosphere/contents/0100000000010000/exefs/`;
    the deploy step copies the whole `sd/atmosphere/...` subtree.

    ap_config.json used to ship alongside (legacy exefs-runtime SD-read
    path) but the Hakkun cutover retired that read path. The Switch
    discovers the PC's IP at runtime via UDP subnet sweep, so the same
    subsdk9 binary works on every LAN. Matches the two-key shape of
    deploy.py's _sd_layout / _ryujinx_layout."""
    mod_root = bundled_switch_mod()
    sd = mod_root / "build" / "sd" / "atmosphere" / "contents" / "0100000000010000"
    outputs = {
        "subsdk9": sd / "exefs" / "subsdk9",
        "main.npdm": sd / "exefs" / "main.npdm",
    }
    missing = [name for name, p in outputs.items() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"build did not produce expected outputs: {missing} "
            f"(check {mod_root / 'build'} for details)"
        )
    return outputs


def maps_ready() -> bool:
    """True iff both extracted maps live in the per-user data dir."""
    d = data_dir()
    return (d / "shine_map.json").exists() and (d / "capture_map.json").exists()


# SHA-256 of the deterministic extraction output from a canonical SMO 1.0.0
# USen dump. These are opaque 256-bit fingerprints — they contain none of the
# underlying Nintendo strings and cannot be reversed to recover them (same
# rationale by which RetroArch / No-Intro / Dolphin ship hash databases of
# copyrighted assets without infringing). The fingerprints exist only to flag
# dump-version drift (localized release, future v1.x patch, corrupted dump);
# they are NOT used to gate functionality — see `verify_map_hashes`.
EXPECTED_MAP_SHA256: dict[str, str] = {
    "shine_map.json":   "87184e27f21cfc7117231a27f025f6ae3a99300d76265b0615c6740f8326e5e7",
    "capture_map.json": "798de7c816d74d10a4a19b1c7462f6b048084bd8bf29927be0651acee0e9ebad",
}


@dataclass
class MapHashCheck:
    """One entry per file in `verify_map_hashes` output."""
    filename: str
    expected: str
    actual: str        # "" if the file was missing or unreadable
    present: bool
    match: bool


def verify_map_hashes() -> list[MapHashCheck]:
    """SHA-256 each extracted map and compare to the canonical fingerprint.

    The extraction is deterministic across every legitimate SMO 1.0.0
    source we support (eShop NSP, cartridge dump, XCI, any valid ticket).
    Both maps come from the USen locale data which is identical across
    region SKUs. So a hash mismatch is a real "your dump isn't what we
    expect" signal — usually a wrong version (1.1.0 or later patch), a
    different game, or a corrupted dump. The wizard uses this as a hard
    gate after extraction.
    """
    import hashlib
    d = data_dir()
    out: list[MapHashCheck] = []
    for name, expected in EXPECTED_MAP_SHA256.items():
        p = d / name
        if not p.exists():
            out.append(MapHashCheck(
                filename=name, expected=expected, actual="",
                present=False, match=False,
            ))
            continue
        try:
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            # File got locked / deleted between exists() and read_bytes() —
            # surface as missing so the wizard prompts a retry rather than
            # silently treating it as a hash mismatch with empty actual.
            out.append(MapHashCheck(
                filename=name, expected=expected, actual="",
                present=False, match=False,
            ))
            continue
        out.append(MapHashCheck(
            filename=name, expected=expected, actual=actual,
            present=True, match=(actual == expected),
        ))
    return out


def build_ready() -> bool:
    """True iff a usable set of build outputs lives in the per-user
    build dir. Doesn't validate freshness — re-running setup
    re-overwrites."""
    try:
        collect_build_outputs()
        return True
    except FileNotFoundError:
        return False
