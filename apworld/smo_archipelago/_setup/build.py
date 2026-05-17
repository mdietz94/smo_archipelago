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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import build_dir, data_dir

# Where the bundled C++ sources + extractor scripts live inside the apworld.
# Filled by `install_apworld.py --bundle-mod --bundle-scripts`. On dev
# checkouts you can populate it manually by symlinking switch-mod/ +
# scripts/ in, or by running install_apworld.py.
_SETUP_ROOT = Path(__file__).resolve().parent
_BUNDLED_MOD = _SETUP_ROOT / "switch_mod"
_BUNDLED_SCRIPTS = _SETUP_ROOT / "scripts"

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


def bundled_switch_mod() -> Path:
    """Path to the bundled `switch_mod/` source tree, or raises if absent.

    Absent means the apworld zip was built without `--bundle-mod`, or this
    is a dev checkout where the bundling hasn't been done. In production
    this never raises (CI always passes --bundle-mod); in dev the message
    tells you to run install_apworld.py with the right flags."""
    if not (_BUNDLED_MOD / "CMakeLists.txt").exists():
        raise FileNotFoundError(
            f"bundled switch_mod sources not found at {_BUNDLED_MOD}. "
            f"Run `python scripts/install_apworld.py --bundle-mod` first."
        )
    return _BUNDLED_MOD


def bundled_script(name: str) -> Path:
    """Path to a bundled extractor/sync script, or raises if absent."""
    p = _BUNDLED_SCRIPTS / name
    if not p.exists():
        raise FileNotFoundError(
            f"bundled script {name!r} not found at {p}. "
            f"Run `python scripts/install_apworld.py --bundle-scripts` first."
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
    """
    log_lines: list[str] = []

    def _emit(line: str) -> None:
        log_lines.append(line)
        if on_line is not None:
            on_line(line)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
        )
    except (FileNotFoundError, OSError) as e:
        msg = f"failed to spawn {cmd[0]}: {e}"
        _emit(msg)
        return BuildResult(ok=False, returncode=127, log=msg)

    assert proc.stdout is not None
    for raw in proc.stdout:
        _emit(raw.rstrip("\r\n"))
    rc = proc.wait()
    return BuildResult(ok=(rc == 0), returncode=rc, log="\n".join(log_lines))


def run_sync_capture_table(on_line: ProgressFn | None = None) -> BuildResult:
    """Regenerate `switch_mod/src/ap/capture_table.h` from items.json.

    Runs the bundled `sync_capture_table.py`. Output is the C++ header the
    Switch mod build needs at compile time. Idempotent — safe to run before
    every build. The build will fail with a compiler error if this is
    skipped (the header is gitignored).
    """
    script = bundled_script("sync_capture_table.py")
    return _stream_subprocess(
        [sys.executable, str(script)],
        on_line=on_line,
    )


def run_extract_maps(
    nsp_path: Path,
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
    args = [
        sys.executable, "-u", str(script),
        "--nsp", str(nsp_path),
        "--out", str(out_dir / "shine_map.json"),
        "--review", str(out_dir / "shine_map_review.json"),
        "--cap-out", str(out_dir / "capture_map.json"),
        "--cap-review", str(out_dir / "capture_map_review.json"),
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
    return _stream_subprocess(
        [
            "cmake",
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
    return _stream_subprocess(
        ["cmake", "--build", str(build_dir() / "cmake")],
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
    """True iff both extracted maps live in the per-user data dir.

    Used by the wizard's resume logic and by `client/main.py`'s
    `is_setup_complete()` check."""
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
