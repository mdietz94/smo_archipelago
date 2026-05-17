"""Detect the tools the setup wizard needs.

The wizard runs these detectors on the Prereq-check page. Each detector
returns a `PrereqResult` with a `ok` flag, a human-readable status detail
(e.g. "cmake 3.30.5" on success, or "not found on PATH" on failure), and
an `install_url` the wizard surfaces as a clickable link when `ok=False`.

Detectors are intentionally pure-Python and stdlib-only so they import on
any Python 3.10+ — no Kivy, no third-party deps. The wizard module is the
only thing that pulls in Kivy.

For unit-testability every shell-out goes through `_run`, which is a thin
wrapper around `subprocess.run`. Tests monkeypatch `_run` to return scripted
results without touching the user's machine. Filesystem checks use
`pathlib.Path` directly because mocking `Path.exists` per-test is cleaner
than abstracting a filesystem facade.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Hard min for cmake — lunakit's toolchain file uses target_link_options
# (3.13+) and project(... LANGUAGES CXX) policies (3.24 enables CMP0135),
# and our switch-mod uses target features that landed in 3.24.
MIN_CMAKE = (3, 24)

# Install pages we link to from the wizard. Kept in this module so the
# wizard layer is pure layout; copy-paste from
# https://devkitpro.org/wiki/Getting_Started → the Windows-installer page
# is the canonical entry.
INSTALL_URLS = {
    "python312": "https://www.python.org/downloads/release/python-3120/",
    "devkitpro": "https://devkitpro.org/wiki/Getting_Started",
    "cmake": "https://cmake.org/download/",
    "ninja": "https://github.com/ninja-build/ninja/releases",
    "hactool": "https://github.com/SciresM/hactool/releases",
    "prodkeys": "https://github.com/Lockpick-Switch/Lockpick_RCM",
}


@dataclass
class PrereqResult:
    """Outcome of a single detector.

    `key` is the stable identifier the wizard uses to map back into
    `INSTALL_URLS` and to render the right label. `detail` is the
    human-readable extra (version string, error message). `install_url`
    is non-empty when `ok=False` so the wizard can surface a clickable
    link.

    `picker_label` + `picker_filter` opt the row into a "Browse..." button.
    Non-empty `picker_label` tells the wizard to render the button with
    that label as the file-picker dialog title; the picked path is then
    persisted (typically into setup_state.json) so subsequent wizard
    invocations + the build / extract subprocesses can pick it up. Useful
    for tools that aren't really "installed" on Windows (hactool — a
    bare .exe most users drop into a folder of their choosing).
    """
    key: str
    name: str
    ok: bool
    detail: str
    install_url: str = ""
    picker_label: str = ""
    picker_filter: tuple[str, ...] = ()


def _run(cmd: list[str], *, timeout: float = 10.0) -> tuple[int, str, str]:
    """Subprocess wrapper that returns (returncode, stdout, stderr).

    Centralized so tests can monkeypatch one function instead of mocking
    `subprocess.run` per-detector. Non-zero exit codes are NOT exceptions
    — they're the normal "tool not found" signal.

    Raises `FileNotFoundError` only when the executable name itself can't
    be resolved (i.e. not on PATH); detectors catch this and treat it as
    "not installed".
    """
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return res.returncode, res.stdout or "", res.stderr or ""


def _safe_run(cmd: list[str]) -> tuple[int, str, str] | None:
    """`_run` that returns None instead of raising on FileNotFoundError /
    OSError. Use when a detector wants to treat 'executable missing' the
    same as 'executable exists but exited non-zero'."""
    try:
        return _run(cmd)
    except (FileNotFoundError, OSError):
        return None
    except subprocess.TimeoutExpired:
        return (1, "", "timeout")


def check_python312() -> PrereqResult:
    """Python 3.12 launcher availability.

    The shine-map extractor scripts (`extract_shine_map.py`) self-bootstrap
    a Python 3.12 venv because `oead` (the BYML/MSBT parser) has no wheel
    for Python 3.13+. The wizard inherits this requirement.

    On Windows we look for the `py -3.12` launcher first (standard on a
    full Python install), and fall back to plain `python3.12` for users
    whose installer didn't register the launcher.
    """
    for cmd in (["py", "-3.12", "--version"], ["python3.12", "--version"]):
        r = _safe_run(cmd)
        if r is None:
            continue
        rc, out, err = r
        if rc == 0:
            ver = (out + err).strip()
            return PrereqResult("python312", "Python 3.12", True, ver)
    return PrereqResult(
        "python312", "Python 3.12", False,
        "not found (the moon/capture extractor needs Python 3.12 because "
        "`oead` has no 3.13 wheel)",
        INSTALL_URLS["python312"],
    )


# Default install roots probed when DEVKITPRO env var is missing. The
# Windows installer does NOT reliably set the system env var (devkitPro
# uses its msys2 shell to set it for that shell only), so a brand-new
# devkitPro install often shows up with no env var visible to a fresh
# Python process. We fall back to these well-known defaults so the wizard
# Just Works on a vanilla install. Order matters: most-specific first.
_DEVKITPRO_DEFAULT_ROOTS = (
    Path("C:/devkitPro"),
    Path("/opt/devkitpro"),
    Path("/usr/local/devkitpro"),
)


def _devkitpro_gxx_under(root: Path) -> Path | None:
    """Return the cross-compiler path under `root` if it exists. Tries
    both Windows (.exe) and POSIX layouts."""
    win = root / "devkitA64" / "bin" / "aarch64-none-elf-g++.exe"
    if win.exists():
        return win
    posix = root / "devkitA64" / "bin" / "aarch64-none-elf-g++"
    if posix.exists():
        return posix
    return None


def check_devkitpro() -> PrereqResult:
    """devkitPro installation (devkitA64 cross-compiler).

    Probes a chain of candidate install roots in order; the first one
    with a working cross-compiler wins. The chain is:

      1. `DEVKITPRO` env var, if set.
      2. Well-known default install roots (`_DEVKITPRO_DEFAULT_ROOTS`).

    Important: the env var is checked AND the defaults are probed even
    when the env var IS set. On Windows the devkitPro installer often
    sets `DEVKITPRO=/opt/devkitpro` — its msys2-rooted convention path,
    which a native-Windows Python process resolves to a non-existent
    `\\opt\\devkitpro\\…`. Falling through to the defaults (where
    `C:/devkitPro` is the real install root) is how we recover.

    Side effect: on success, sets `os.environ["DEVKITPRO"]` to the
    resolved-working path so downstream `run_cmake_configure` subprocess
    invocations inherit a value that actually resolves. This OVERRIDES
    a bogus env-var value (like the msys2-form `/opt/devkitpro` above)
    rather than passing the broken value through to cmake. The mutation
    is process-local; nothing persists to the user's environment.
    """
    candidates: list[tuple[Path, str]] = []
    env_val = os.environ.get("DEVKITPRO")
    if env_val:
        candidates.append((Path(env_val), f"DEVKITPRO env var ({env_val})"))
    for default in _DEVKITPRO_DEFAULT_ROOTS:
        if not any(str(c) == str(default) for c, _ in candidates):
            candidates.append((default, f"default install path ({default})"))

    tried: list[str] = []
    for root, source in candidates:
        gxx = _devkitpro_gxx_under(root)
        if gxx is not None:
            # Overwrite env so cmake sees the path that actually works,
            # even when the user's environment had a bogus value.
            os.environ["DEVKITPRO"] = str(root)
            return _verify_devkitpro_gxx(gxx, str(root))
        tried.append(source)

    return PrereqResult(
        "devkitpro", "devkitPro / devkitA64", False,
        f"aarch64-none-elf-g++ not found at any of: {'; '.join(tried)}",
        INSTALL_URLS["devkitpro"],
    )


def _verify_devkitpro_gxx(gxx: Path, root: str) -> PrereqResult:
    """Run `g++ --version` against a discovered cross-compiler; return the
    success/failure PrereqResult for it."""
    r = _safe_run([str(gxx), "--version"])
    if r and r[0] == 0:
        first_line = (r[1] or r[2]).splitlines()[0] if (r[1] or r[2]) else "ok"
        return PrereqResult(
            "devkitpro", "devkitPro / devkitA64", True,
            f"{root} ({first_line})",
        )
    return PrereqResult(
        "devkitpro", "devkitPro / devkitA64", False,
        f"DEVKITPRO={root}; binary exists but failed to run --version",
        INSTALL_URLS["devkitpro"],
    )


def _parse_cmake_version(text: str) -> tuple[int, int, int] | None:
    """`cmake --version` prints `cmake version 3.30.5\\nCMake suite ...`."""
    m = re.search(r"cmake version (\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return None
    major, minor, patch = m.group(1), m.group(2), m.group(3) or "0"
    return (int(major), int(minor), int(patch))


# Canonical Windows-CMake install locations from Kitware's MSI. Probed
# BEFORE the bare-name PATH lookup because devkitPro's installer adds
# `C:\devkitPro\msys2\usr\bin` to the front of PATH, which means a plain
# `cmake` resolves to msys2's posix-style cmake — and that build treats
# `C:\path` as a relative path (msys2 uses `:` as a path separator, not
# a drive letter), turning the Switch-mod source path into
# "/c/cwd/C:/Users/.../switch_mod" → CMake error: source dir does not
# exist. The Kitware-built Windows cmake handles drive letters
# correctly. CLAUDE.md flags this gotcha.
_CMAKE_DEFAULT_PATHS = (
    Path("C:/Program Files/CMake/bin/cmake.exe"),
    Path("C:/Program Files (x86)/CMake/bin/cmake.exe"),
)

# Module-level cache for the resolved cmake binary path. `check_cmake`
# writes it; `resolved_cmake()` reads. Other modules (build.py) use the
# getter rather than re-running detection.
_resolved_cmake: str | None = None


def resolved_cmake() -> str:
    """Return the cmake binary path resolved by the most recent
    `check_cmake` call, or the bare name "cmake" if detection hasn't
    been run (fallback so callers don't crash on direct invocation in
    tests etc.). Production callers should always run check_cmake first.
    """
    return _resolved_cmake if _resolved_cmake is not None else "cmake"


def check_cmake() -> PrereqResult:
    """Probe Windows-native CMake first, then fall back to PATH.

    Side effect: writes the resolved binary path to module-level
    `_resolved_cmake` so `run_cmake_configure` / `run_cmake_build` can
    invoke the SAME cmake the prereq check passed — without this, the
    build step could pick up a different (msys2) cmake from PATH and
    blow up with drive-letter resolution errors.
    """
    global _resolved_cmake

    # Try canonical Kitware install paths first.
    candidates: list[str] = []
    for default in _CMAKE_DEFAULT_PATHS:
        if default.exists():
            candidates.append(str(default))
    # Then fall back to whatever's on PATH (might be msys2 cmake — works
    # for many users but breaks on the Switch-mod build; if it's the
    # only option, surface it anyway so we can produce a useful error
    # later).
    candidates.append("cmake")

    for cand in candidates:
        r = _safe_run([cand, "--version"])
        if r is None or r[0] != 0:
            continue
        ver = _parse_cmake_version(r[1] or r[2])
        if ver is None:
            continue
        if (ver[0], ver[1]) < MIN_CMAKE:
            # Found a working cmake but it's too old; keep looking in
            # case another candidate is newer.
            continue
        _resolved_cmake = cand
        return PrereqResult(
            "cmake", f"CMake {MIN_CMAKE[0]}.{MIN_CMAKE[1]}+", True,
            f"{ver[0]}.{ver[1]}.{ver[2]} ({cand})",
        )

    # Nothing usable — replay PATH cmake one more time so the failure
    # detail mirrors what the user would see manually.
    r = _safe_run(["cmake", "--version"])
    if r is None or r[0] != 0:
        return PrereqResult(
            "cmake", f"CMake {MIN_CMAKE[0]}.{MIN_CMAKE[1]}+", False,
            "not found on PATH",
            INSTALL_URLS["cmake"],
        )
    ver = _parse_cmake_version(r[1] or r[2])
    if ver is None:
        return PrereqResult(
            "cmake", f"CMake {MIN_CMAKE[0]}.{MIN_CMAKE[1]}+", False,
            "found, but couldn't parse `cmake --version` output",
            INSTALL_URLS["cmake"],
        )
    return PrereqResult(
        "cmake", f"CMake {MIN_CMAKE[0]}.{MIN_CMAKE[1]}+", False,
        f"{ver[0]}.{ver[1]}.{ver[2]} too old (need "
        f"{MIN_CMAKE[0]}.{MIN_CMAKE[1]}+)",
        INSTALL_URLS["cmake"],
    )


def check_ninja() -> PrereqResult:
    r = _safe_run(["ninja", "--version"])
    if r is None or r[0] != 0:
        return PrereqResult(
            "ninja", "Ninja", False,
            "not found on PATH",
            INSTALL_URLS["ninja"],
        )
    ver = (r[1] or r[2]).strip()
    return PrereqResult("ninja", "Ninja", True, ver)


def check_hactool(override_path: Path | None = None) -> PrereqResult:
    """`hactool` for unpacking the user's SMO NSP during map extraction.

    The extractor script (`scripts/extract_shine_map.py`) calls hactool
    to extract program NCA → RomFS. It is NOT bundled (Switch-tooling
    license + the extractor already accepts an explicit `--hactool` path
    override).

    Detection order:
      1. `override_path` if provided (typically read from
         setup_state.json's `hactool_path` key — set when the user
         pointed the wizard's "Browse..." button at a hactool.exe).
      2. PATH lookup via `shutil.which`.

    Fails open (returns not-ok with picker_label set) when neither works,
    so the wizard can surface a "Browse..." button. hactool is unusual
    among our prereqs because Windows users don't typically "install" it
    — they download a single .exe and drop it somewhere of their choosing
    — so requiring PATH membership is a poor UX.
    """
    if override_path is not None:
        if override_path.is_file():
            return PrereqResult(
                "hactool", "hactool", True,
                f"{override_path} (user-picked)",
            )
        # Fall through to PATH lookup — the persisted path may have moved
        # since the user picked it; we should not silently lock them out.

    exe = shutil.which("hactool") or shutil.which("hactool.exe")
    if exe:
        return PrereqResult("hactool", "hactool", True, exe)

    detail = "not found on PATH (needed to extract RomFS from your SMO NSP)"
    if override_path is not None:
        detail = (
            f"previously-picked path {override_path} no longer exists, and "
            "hactool not found on PATH"
        )
    return PrereqResult(
        "hactool", "hactool", False,
        detail,
        INSTALL_URLS["hactool"],
        picker_label="Locate hactool.exe",
        picker_filter=("hactool*", "*.exe", "*"),
    )


def check_prod_keys() -> PrereqResult:
    """Switch console keys at the standard hactool default location.

    The extractor needs `prod.keys` to decrypt the NSP. Users typically dump
    these via Lockpick_RCM into `~/.switch/prod.keys` (hactool's default
    location); we look there only. If a user has them elsewhere they can
    point the extractor at them via the `--keys` arg (the wizard will
    surface that override option in a future revision).
    """
    p = Path.home() / ".switch" / "prod.keys"
    if not p.exists():
        return PrereqResult(
            "prodkeys", "prod.keys", False,
            f"not found at {p} (dump with Lockpick_RCM)",
            INSTALL_URLS["prodkeys"],
        )
    return PrereqResult("prodkeys", "prod.keys", True, str(p))


def check_all(*, hactool_override: Path | None = None) -> list[PrereqResult]:
    """Run every detector. Order is wizard-display order — heaviest /
    most-likely-missing first so the user isn't surprised at the end of
    the list.

    `hactool_override` flows from the wizard's persisted user-picked
    path (setup_state.json's `hactool_path` key); pass None on first
    invocation or when the user has not yet picked a custom location."""
    return [
        check_devkitpro(),
        check_cmake(),
        check_ninja(),
        check_python312(),
        check_hactool(override_path=hactool_override),
        check_prod_keys(),
    ]


def all_ok(results: list[PrereqResult]) -> bool:
    return all(r.ok for r in results)
