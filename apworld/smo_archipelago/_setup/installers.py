"""Silent installers for the wizard's auto-install mode.

The wizard's prereq page offers two paths (see `wizard.py`):
  - **Manual**: surface install links + Browse buttons (today's behavior).
  - **Auto**: run silent installers from this module.

Coverage:
  - winget tools (CMake, Ninja, Python 3.12, hactool) — silent install +
    PATH prepend.
  - Direct downloads to `%LOCALAPPDATA%\\SMOArchipelago\\<tool>\\`:
      * LLVM 19.1.7 (cross-compiler, ABI-pinned by LibHakkun)
      * WinLibs (mingw-w64 g++, replaces W64DevKit — Defender PUA
        scanning false-positives on the latter)
      * Sail's Python deps (`pyelftools` + `mmh3` + `lz4`)
  - hactool: direct GitHub-release zip extract.

Each installer:
  - Streams subprocess / download progress line-by-line to an `on_line`
    callback so the wizard's log popup shows live progress.
  - Pre-checks disk space before any download begins; refuses with a
    clear "not enough space on <drive>" message rather than starting a
    download we can't complete.
  - Verifies SHA-256 after download (pinned constants below).
  - Returns an `InstallResult` with ok/returncode/log so the wizard can
    distinguish "succeeded" from "failed" without parsing the stream.

The LLVM and WinLibs installs land in `%LOCALAPPDATA%\\SMOArchipelago\\`
rather than touching anything global, so the wizard coexists with any
LLVM/msys2/W64DevKit the user already has installed. Uninstall =
`rmdir %LOCALAPPDATA%\\SMOArchipelago\\`.

prod.keys is intentionally NOT installable — the user has to dump them
from their own Switch with Lockpick_RCM. The wizard's prereq row keeps
its Browse-button path for that case.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import appdata_root
from .prereqs import (
    _prepend_path,
    _winget_ninja_paths,
    _winget_python312_commands,
    bundled_hactool_path,
    llvm_portable_root,
    localappdata_tools_root,
    sail_deps_marker_path,
    winlibs_portable_root,
)


# ---------------------------------------------------------------------------
# Pinned-version constants for the portable installs.
#
# Bumping any of these is a four-line change: VERSION + URL + SHA256 +
# the matching _BYTES counts (download size for disk-space precheck and
# unpacked size for the wizard's UI). Verify the new SHA256 against the
# upstream release page (LLVM: github.com/llvm/llvm-project/releases ;
# WinLibs: github.com/brechtsanders/winlibs_mingw/releases — each ships
# a `.sha256` file alongside the zip).
# ---------------------------------------------------------------------------

# LLVM 19 is ABI-pinned by LibHakkun's libc++ headers. Anything 20+ fails
# to link at sail's host-compile step; pre-19.1.x lacks the C++23
# features. The Windows MSVC build is the right ABI for our cross-compile.
LLVM_VERSION = "19.1.7"
LLVM_URL = (
    "https://github.com/llvm/llvm-project/releases/download/"
    "llvmorg-19.1.7/clang+llvm-19.1.7-x86_64-pc-windows-msvc.tar.xz"
)
LLVM_SHA256 = "b4557b4f012161f56a2f5d9e877ab9635cafd7a08f7affe14829bd60c9d357f0"
LLVM_DOWNLOAD_BYTES = 845_236_708       # ~806 MB compressed
LLVM_UNPACKED_BYTES = 3_563_705_149     # ~3.32 GB on disk

# WinLibs is a clean-room mingw-w64 distribution; the UCRT-flavored x86_64
# build is what sail wants for CC=gcc / CXX=g++. We pin to a specific
# UCRT release for reproducibility. Picked over W64DevKit because the
# latter is heuristically flagged by Windows Defender's PUA scan (its
# minimal binutils layout looks suspicious); WinLibs's fuller package is
# not flagged. The upstream `.sha256` file alongside the zip is what
# this hash matches.
WINLIBS_VERSION = "gcc-16.1.0-mingw-w64ucrt-14.0.0-r2"
WINLIBS_URL = (
    "https://github.com/brechtsanders/winlibs_mingw/releases/download/"
    "16.1.0posix-14.0.0-ucrt-r2/"
    "winlibs-x86_64-posix-seh-gcc-16.1.0-mingw-w64ucrt-14.0.0-r2.zip"
)
WINLIBS_SHA256 = (
    "78eff1e2e804b6a6320c713f084b8f820c662104a24cea6a3bfcab82032bdd60"
)
WINLIBS_DOWNLOAD_BYTES = 274_571_646    # ~262 MB compressed
WINLIBS_UNPACKED_BYTES = 962_598_090    # ~0.90 GB on disk

# Sail's three host-Python deps (pyelftools + mmh3 + lz4). pip will pull
# whatever versions are current at install time; the marker file the
# detector writes is what flips the prereq row green.
SAIL_PIP_PACKAGES = ("pyelftools", "mmh3", "lz4")
SAIL_DEPS_DOWNLOAD_BYTES = 5 * 1024 * 1024    # rough — ~5 MB total
SAIL_DEPS_UNPACKED_BYTES = 5 * 1024 * 1024    # same on disk


class InsufficientDiskError(RuntimeError):
    """Raised by `_check_disk_space` when the target drive has less free
    space than the install needs. Caller surfaces this as a clean
    InstallResult failure rather than letting the download crash mid-write."""


def _check_disk_space(target: Path, need_bytes: int) -> None:
    """Refuse to start an install we can't finish.

    `shutil.disk_usage(<existing dir>)` returns the free space on the
    volume containing that dir. We walk up to the nearest existing
    parent of `target` so callers can pass a not-yet-created install
    path.
    """
    probe = target
    for _ in range(20):
        if probe.exists():
            break
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as e:
        raise InsufficientDiskError(
            f"could not determine free space on {probe}: {e}"
        ) from e
    if usage.free < need_bytes:
        drive = str(probe)
        # `:.2f` of GiB is the only granularity that matters at this scale.
        raise InsufficientDiskError(
            f"not enough free space on {drive}: need "
            f"{need_bytes / (1024 ** 3):.2f} GiB, have "
            f"{usage.free / (1024 ** 3):.2f} GiB. Free up space and re-run."
        )


def cleanup_portable_deps(on_line: ProgressFn | None = None) -> InstallResult:
    """Remove the two portable toolchain dirs (LLVM + WinLibs) after a
    successful build, if the user chose Remove on the prereq screen.

    Does NOT touch:
      - `%LOCALAPPDATA%\\SMOArchipelago\\` parent (it can contain other
        wizard state)
      - The sail-deps marker file (sail deps were `pip install --user`'d
        into the user's Python and we don't try to pip-uninstall them
        — the cost was already paid).
      - `%APPDATA%\\SMOArchipelago\\` (the persistent wizard state, build
        outputs, extract caches, etc.).

    Best-effort: a partial removal leaves the rest in place so a retry
    can pick up where we left off. Returns ok even if the dirs are
    already absent (idempotent — user might re-click after a clean install).
    """
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    log_lines: list[str] = []
    removed: list[str] = []
    errors: list[str] = []
    for label, root in (
        ("LLVM 19", llvm_portable_root()),
        ("WinLibs", winlibs_portable_root()),
    ):
        if not root.exists():
            log_lines.append(f"[cleanup] {label} not present at {root}; skipping")
            continue
        try:
            shutil.rmtree(root)
            emit(f"[cleanup] removed {label} at {root}")
            log_lines.append(f"[cleanup] removed {label} at {root}")
            removed.append(label)
        except OSError as e:
            msg = (
                f"[cleanup] failed to remove {label} at {root}: {e}. Close "
                f"any program holding files there (Explorer, antivirus) "
                f"and click Remove again."
            )
            emit(msg)
            log_lines.append(msg)
            errors.append(label)

    if errors:
        return InstallResult(
            ok=False, returncode=1,
            log="\n".join(log_lines),
            detail=f"could not fully remove: {', '.join(errors)}",
        )
    detail = (
        "no portable deps were installed (nothing to remove)"
        if not removed
        else f"removed: {', '.join(removed)}"
    )
    return InstallResult(ok=True, returncode=0,
                         log="\n".join(log_lines), detail=detail)

# Suppress per-child console window when the wizard runs under the AP
# Launcher's windowed PyInstaller (no parent console → Windows opens a
# fresh console for each CONSOLE-subsystem child, which steals focus).
# No-op on non-Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Callback receiving one rstripped line of installer output per call.
ProgressFn = Callable[[str], None]


@dataclass
class InstallResult:
    """Outcome of one install attempt.

    `ok` is the green-light flag. `returncode` is the underlying tool's
    exit code (winget / installer .exe / urllib download). `log` is the
    full captured stream for the wizard's "Copy log" button. `detail` is
    a short human-readable summary for the row's status flip.
    """
    ok: bool
    returncode: int
    log: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def check_winget(on_line: ProgressFn | None = None) -> InstallResult:
    """Verify winget is present on PATH.

    winget ships with Windows 10 1809+ via the App Installer package,
    but LTSC images and stripped Win11 setups can lack it. We probe
    once at the top of "Install all missing" so the wizard can surface
    a single clear "install App Installer from the Microsoft Store"
    error instead of three confusing winget-not-found errors in a row.
    """
    exe = shutil.which("winget")
    msg = (
        "winget not found on PATH — install \"App Installer\" from the "
        "Microsoft Store, or switch to Manual mode to install CMake, "
        "Ninja, and Python 3.12 by hand."
    )
    if exe is None:
        if on_line:
            on_line(msg)
        return InstallResult(ok=False, returncode=127, log=msg, detail=msg)
    if on_line:
        on_line(f"[winget] resolved to {exe}")
    return InstallResult(ok=True, returncode=0, log=exe, detail=exe)


def check_internet(on_line: ProgressFn | None = None) -> InstallResult:
    """Single connectivity probe before bulk install.

    Hits `https://github.com` with a HEAD request. We don't actually
    care if GitHub is up — we care that *some* HTTPS host on the
    network responds, because every auto-installer in this module
    pulls from a https URL. Surface ONE clear "no internet" error
    instead of N timeouts deep inside per-tool installers.
    """
    msg_ok = "internet reachable"
    msg_fail = (
        "no internet connectivity (HEAD https://github.com timed out / "
        "failed). Connect to the internet and click Install all missing "
        "again, or switch to Manual mode."
    )
    try:
        req = urllib.request.Request("https://github.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 400:
                if on_line:
                    on_line(f"[net] {msg_ok} ({resp.status})")
                return InstallResult(ok=True, returncode=0,
                                     log=str(resp.status), detail=msg_ok)
    except (urllib.error.URLError, OSError) as e:
        if on_line:
            on_line(f"[net] {msg_fail} ({e})")
        return InstallResult(ok=False, returncode=1,
                             log=f"{type(e).__name__}: {e}", detail=msg_fail)
    if on_line:
        on_line(f"[net] {msg_fail}")
    return InstallResult(ok=False, returncode=1, log="non-2xx", detail=msg_fail)


# ---------------------------------------------------------------------------
# winget runner + tool-specific wrappers
# ---------------------------------------------------------------------------

def _stream_subprocess(
    cmd: list[str],
    *,
    on_line: ProgressFn | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> InstallResult:
    """Run a subprocess, streaming stdout/stderr line-by-line.

    Mirrors `build._stream_subprocess` but lives here so installers.py
    doesn't depend on the build pipeline. stderr is merged into stdout
    so winget's per-line progress (which it logs to stderr) interleaves
    correctly with whatever it puts on stdout.
    """
    log_lines: list[str] = []

    def _emit(line: str) -> None:
        log_lines.append(line)
        if on_line is not None:
            on_line(line)

    _emit(f"[install] spawning: {cmd}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, OSError) as e:
        msg = f"failed to spawn {cmd[0]}: {e}"
        _emit(msg)
        return InstallResult(ok=False, returncode=127,
                             log=msg, detail=msg)

    assert proc.stdout is not None
    for raw in proc.stdout:
        _emit(raw.rstrip("\r\n"))
    rc = proc.wait()
    _emit(f"[install] subprocess exited with code {rc}")
    return InstallResult(ok=(rc == 0), returncode=rc,
                         log="\n".join(log_lines))


def winget_install(
    package_id: str,
    *,
    on_line: ProgressFn | None = None,
) -> InstallResult:
    """Silent winget install of a single package.

    `-e --id <id>` is exact-match by package identifier (so we don't
    accidentally install a near-name match). `--silent` suppresses the
    package's own GUI; `--accept-package-agreements --accept-source-
    agreements` declines the otherwise-interactive EULA prompts;
    `--disable-interactivity` is winget's own "never prompt" master
    switch.
    """
    wg = shutil.which("winget")
    if wg is None:
        return InstallResult(
            ok=False, returncode=127,
            log="winget not on PATH",
            detail="winget not found (install App Installer from the Microsoft Store)",
        )
    return _stream_subprocess(
        [
            wg, "install",
            "-e", "--id", package_id,
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        on_line=on_line,
    )


def install_cmake(on_line: ProgressFn | None = None) -> InstallResult:
    """winget-install Kitware CMake and prepend its install dir to PATH.

    Kitware's MSI puts cmake at `C:/Program Files/CMake/bin/cmake.exe`.
    That dir IS on PATH for newly-spawned processes after the install,
    but NOT for the already-running wizard. Prepending here lets the
    next Re-check resolve cmake without restarting the wizard.
    """
    r = winget_install("Kitware.CMake", on_line=on_line)
    if not r.ok:
        return r
    for candidate in (
        Path("C:/Program Files/CMake/bin/cmake.exe"),
        Path("C:/Program Files (x86)/CMake/bin/cmake.exe"),
    ):
        if candidate.is_file():
            _prepend_path(candidate.parent)
            if on_line:
                on_line(f"[install] prepended {candidate.parent} to PATH")
            return InstallResult(ok=True, returncode=0, log=r.log,
                                 detail=str(candidate))
    msg = (
        "winget reported success but cmake.exe was not at the canonical "
        "Kitware install path. Try restarting the wizard."
    )
    if on_line:
        on_line(f"[install] {msg}")
    return InstallResult(ok=False, returncode=1, log=r.log, detail=msg)


def install_ninja(on_line: ProgressFn | None = None) -> InstallResult:
    """winget-install Ninja and prepend its install dir to PATH.

    winget drops ninja.exe under `%LOCALAPPDATA%/Microsoft/WinGet/Packages/
    Ninja-build.Ninja_*/ninja.exe`. The `_*` is winget's source tag. Use
    the same probe `prereqs._winget_ninja_paths` uses so the two stay in
    lockstep — when this prepends and exits, the next `check_ninja()`
    finds the same file.
    """
    r = winget_install("Ninja-build.Ninja", on_line=on_line)
    if not r.ok:
        return r
    paths = _winget_ninja_paths()
    if paths:
        _prepend_path(paths[0].parent)
        if on_line:
            on_line(f"[install] prepended {paths[0].parent} to PATH")
        return InstallResult(ok=True, returncode=0, log=r.log,
                             detail=str(paths[0]))
    msg = (
        "winget reported success but ninja.exe was not found under "
        "%LOCALAPPDATA%/Microsoft/WinGet/Packages/Ninja-build.Ninja_*/. "
        "Try restarting the wizard."
    )
    if on_line:
        on_line(f"[install] {msg}")
    return InstallResult(ok=False, returncode=1, log=r.log, detail=msg)


def install_python312(on_line: ProgressFn | None = None) -> InstallResult:
    """winget-install Python 3.12 and prepend its dir to PATH.

    winget lands py.exe at `%LOCALAPPDATA%/Programs/Python/Launcher/`.
    Reuse `prereqs._winget_python312_commands` to find it post-install
    so installer and detector stay in lockstep.
    """
    r = winget_install("Python.Python.3.12", on_line=on_line)
    if not r.ok:
        return r
    cmds = _winget_python312_commands()
    if cmds:
        # cmds[0] is the py.exe form (preferred for the build step's
        # `shutil.which("py")` lookup); prepend its dir.
        _prepend_path(Path(cmds[0][0]).parent)
        if on_line:
            on_line(f"[install] prepended {Path(cmds[0][0]).parent} to PATH")
        return InstallResult(ok=True, returncode=0, log=r.log,
                             detail=cmds[0][0])
    msg = (
        "winget reported success but py.exe was not found at "
        "%LOCALAPPDATA%/Programs/Python/Launcher/. Try restarting the wizard."
    )
    if on_line:
        on_line(f"[install] {msg}")
    return InstallResult(ok=False, returncode=1, log=r.log, detail=msg)


# ---------------------------------------------------------------------------
# hactool — direct GitHub-release download (no winget package)
# ---------------------------------------------------------------------------

# Pin the version and (optionally) the SHA-256 of the Windows release zip.
# Leave _HACTOOL_SHA256 = "" to skip verification — set to the hex digest
# of `hactool-1.4.0-win.zip` for tamper-detection. The wizard's install
# popup surfaces this value so users can verify out-of-band.
_HACTOOL_VERSION = "1.4.0"
_HACTOOL_URL = (
    f"https://github.com/SciresM/hactool/releases/download/"
    f"{_HACTOOL_VERSION}/hactool-{_HACTOOL_VERSION}-win.zip"
)
_HACTOOL_SHA256 = ""  # populate at release-pin time; "" skips verification


def _download(
    url: str,
    dst: Path,
    *,
    on_line: ProgressFn | None = None,
    timeout: float = 120.0,
    expected_sha256: str = "",
) -> InstallResult:
    """Download `url` → `dst.part` → atomic rename to `dst`.

    Follows redirects (GitHub release assets 302 to objects.githubusercontent.com).
    Optional SHA-256 check if `expected_sha256` is non-empty. urllib +
    hashlib only — keeps installer.py free of `requests` / `httpx` deps.
    """
    import hashlib
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")

    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    emit(f"[download] GET {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            total_str = resp.headers.get("Content-Length")
            total = int(total_str) if total_str and total_str.isdigit() else None
            digest = hashlib.sha256()
            written = 0
            last_emit = time.monotonic()
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    now = time.monotonic()
                    if now - last_emit >= 1.0:
                        last_emit = now
                        if total:
                            pct = 100 * written / total
                            emit(f"[download] {written}/{total} bytes ({pct:.1f}%)")
                        else:
                            emit(f"[download] {written} bytes")
    except (urllib.error.URLError, OSError) as e:
        msg = f"download failed: {e}"
        emit(f"[download] {msg}")
        part.unlink(missing_ok=True)
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    got = digest.hexdigest()
    emit(f"[download] sha256={got}")
    if expected_sha256 and got.lower() != expected_sha256.lower():
        msg = (
            f"sha256 mismatch: expected {expected_sha256}, got {got}. "
            f"Download discarded."
        )
        emit(f"[download] {msg}")
        part.unlink(missing_ok=True)
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    # Atomic-ish rename. On Windows the dst must not exist or rename fails.
    if dst.exists():
        dst.unlink()
    part.replace(dst)
    emit(f"[download] saved {dst}")
    return InstallResult(ok=True, returncode=0, log=str(dst), detail=str(dst))


def install_hactool(on_line: ProgressFn | None = None) -> InstallResult:
    """Download SciresM/hactool 1.4.0 win zip and unpack hactool.exe to
    `%APPDATA%/SMOArchipelago/bundled/hactool.exe`.

    Pinned version (not "latest") so the wizard install is reproducible
    and the optional SHA-256 check is meaningful. The destination matches
    `prereqs.bundled_hactool_path()` and the extractor's fallback
    constant, so check_hactool flips green on the next Re-check without
    any additional state update.
    """
    dest = bundled_hactool_path()
    if dest.is_file():
        if on_line:
            on_line(f"[hactool] already installed at {dest}; skipping download")
        return InstallResult(ok=True, returncode=0,
                             log=str(dest), detail=str(dest))

    with tempfile.TemporaryDirectory(prefix="smoap-hactool-") as td:
        td_path = Path(td)
        zip_path = td_path / f"hactool-{_HACTOOL_VERSION}-win.zip"
        r = _download(_HACTOOL_URL, zip_path,
                      on_line=on_line, expected_sha256=_HACTOOL_SHA256)
        if not r.ok:
            return r
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                # The zip should contain a single hactool.exe (1.4.0 layout).
                # Be forgiving in case future releases nest it: extract the
                # first .exe whose name ends with hactool.exe.
                target = next(
                    (n for n in names
                     if n.lower().endswith("hactool.exe")), None,
                )
                if target is None:
                    msg = (
                        f"downloaded zip {zip_path} did not contain "
                        f"hactool.exe; entries: {names}"
                    )
                    if on_line:
                        on_line(f"[hactool] {msg}")
                    return InstallResult(ok=False, returncode=1,
                                         log=msg, detail=msg)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(target) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        except (zipfile.BadZipFile, OSError) as e:
            msg = f"unzip failed: {e}"
            if on_line:
                on_line(f"[hactool] {msg}")
            return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
    if on_line:
        on_line(f"[hactool] installed to {dest}")
    return InstallResult(ok=True, returncode=0,
                         log=str(dest), detail=str(dest))


# ---------------------------------------------------------------------------
# LLVM 19.1.7 — direct tar.xz extract to %LOCALAPPDATA%\SMOArchipelago\llvm\
# ---------------------------------------------------------------------------

def _extract_tar_xz_renamed(
    src: Path,
    dst: Path,
    *,
    on_line: ProgressFn | None = None,
) -> InstallResult:
    """Extract `src` (.tar.xz) into `dst`, stripping the single top-level
    directory the archive contains. LLVM's tarball is laid out as
    `clang+llvm-19.1.7-x86_64-pc-windows-msvc/...`; we land its contents
    directly under `<dst>/` (so `<dst>/bin/clang.exe` is the result, not
    `<dst>/clang+llvm-.../bin/clang.exe`)."""
    import tarfile

    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    emit(f"[extract] opening {src} (tar.xz)")
    dst.mkdir(parents=True, exist_ok=True)
    last_emit = time.monotonic()
    n = 0
    try:
        with tarfile.open(src, "r:xz") as tf:
            for member in tf:
                # Strip the top-level dir from the in-archive path.
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue  # skip the top-level dir entry itself
                stripped = parts[1]
                if any(seg in (".", "..") for seg in stripped.split("/")):
                    raise RuntimeError(
                        f"refusing to extract suspicious entry: {member.name!r}"
                    )
                target = dst / stripped
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    # Windows tarball shouldn't contain these, but skip
                    # quietly if it does — extractfile will return None
                    # for a symlink anyway.
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # Stream the file body directly to disk so we don't hold
                # ~3 GB in memory.
                f = tf.extractfile(member)
                if f is None:
                    continue
                with f, open(target, "wb") as out:
                    shutil.copyfileobj(f, out)
                # Preserve executable bit (irrelevant on Windows; cheap on POSIX).
                try:
                    os.chmod(target, member.mode & 0o777)
                except OSError:
                    pass
                n += 1
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    emit(f"[extract] {n} files written...")
    except (tarfile.TarError, OSError, RuntimeError) as e:
        msg = f"extract failed: {e}"
        emit(f"[extract] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
    emit(f"[extract] done ({n} files)")
    return InstallResult(ok=True, returncode=0, log=str(dst), detail=str(dst))


def _extract_zip_renamed(
    src: Path,
    dst: Path,
    *,
    on_line: ProgressFn | None = None,
) -> InstallResult:
    """Extract `src` (.zip) into `dst`, stripping the single top-level
    directory the archive contains. WinLibs's zip is laid out as
    `mingw64/bin/g++.exe`; we land contents directly under `<dst>/`
    so the final path is `<dst>/bin/g++.exe`."""
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    emit(f"[extract] opening {src} (zip)")
    dst.mkdir(parents=True, exist_ok=True)
    last_emit = time.monotonic()
    n = 0
    try:
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                parts = info.filename.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                stripped = parts[1]
                if any(seg in (".", "..") for seg in stripped.split("/")):
                    raise RuntimeError(
                        f"refusing to extract suspicious entry: {info.filename!r}"
                    )
                target = dst / stripped
                if info.is_dir() or info.filename.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src_f, open(target, "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f)
                n += 1
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    emit(f"[extract] {n} files written...")
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        msg = f"extract failed: {e}"
        emit(f"[extract] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
    emit(f"[extract] done ({n} files)")
    return InstallResult(ok=True, returncode=0, log=str(dst), detail=str(dst))


def install_llvm19(on_line: ProgressFn | None = None) -> InstallResult:
    """Download LLVM 19.1.7 to `%LOCALAPPDATA%\\SMOArchipelago\\llvm\\`.

    Coexists with any other LLVM the user has installed — we never touch
    `C:\\Program Files\\LLVM\\` or PATH globally. The build step's PATH
    prepend is scoped to the build subprocess only.

    Flow:
      1. Disk-space precheck against the LLVM total (download + unpacked).
      2. Skip download if a portable install already exists with a
         working `clang.exe`.
      3. Download to a temp file, verify SHA-256.
      4. Extract to `<root>/llvm/`, stripping the archive's top-level
         `clang+llvm-19.1.7-x86_64-pc-windows-msvc/` dir.
      5. The next `check_llvm19()` finds `bin/clang.exe` and flips the
         prereq row green.
    """
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    if sys.platform != "win32":
        msg = "install_llvm19 is Windows-only (the pinned tarball is the MSVC build)."
        emit(f"[llvm] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    dst = llvm_portable_root()
    clang = dst / "bin" / "clang.exe"
    if clang.is_file():
        emit(f"[llvm] already installed at {dst}; skipping download")
        return InstallResult(ok=True, returncode=0,
                             log=str(dst), detail=str(dst))

    try:
        # Need download + unpacked + a little headroom for tarfile's
        # streaming reads. Allow ~10% slack.
        need = int((LLVM_DOWNLOAD_BYTES + LLVM_UNPACKED_BYTES) * 1.10)
        _check_disk_space(dst, need)
    except InsufficientDiskError as e:
        emit(f"[llvm] {e}")
        return InstallResult(ok=False, returncode=1, log=str(e), detail=str(e))

    with tempfile.TemporaryDirectory(prefix="smoap-llvm-") as td:
        td_path = Path(td)
        tarball = td_path / f"clang+llvm-{LLVM_VERSION}-windows-msvc.tar.xz"
        emit(f"[llvm] downloading LLVM {LLVM_VERSION} ({LLVM_DOWNLOAD_BYTES / (1024**2):.0f} MB)...")
        r = _download(
            LLVM_URL, tarball,
            on_line=on_line, timeout=600.0,
            expected_sha256=LLVM_SHA256,
        )
        if not r.ok:
            return r
        emit(f"[llvm] extracting to {dst} (~{LLVM_UNPACKED_BYTES / (1024**3):.1f} GB unpacked)...")
        r = _extract_tar_xz_renamed(tarball, dst, on_line=on_line)
        if not r.ok:
            return r

    if not clang.is_file():
        msg = (
            f"extraction reported success but {clang} is missing. The "
            f"tarball layout may have changed in a newer release."
        )
        emit(f"[llvm] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
    emit(f"[llvm] ready: {clang}")
    return InstallResult(ok=True, returncode=0,
                         log=str(dst), detail=str(dst))


def install_winlibs(on_line: ProgressFn | None = None) -> InstallResult:
    """Download WinLibs to `%LOCALAPPDATA%\\SMOArchipelago\\winlibs\\`.

    WinLibs replaces W64DevKit because the latter's minimal-binutils
    layout heuristically trips Windows Defender's PUA scan (false
    positive — gcc + binutils zipped without much else looks suspicious
    to the scanner). WinLibs's fuller package isn't flagged.

    Same coexistence guarantee as LLVM: never touches `C:\\msys64\\` or
    `C:\\winlibs\\` if the user has those, never modifies global PATH.
    """
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    if sys.platform != "win32":
        msg = "install_winlibs is Windows-only (WinLibs is a Windows mingw distro)."
        emit(f"[winlibs] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    dst = winlibs_portable_root()
    gxx = dst / "bin" / "g++.exe"
    if gxx.is_file():
        emit(f"[winlibs] already installed at {dst}; skipping download")
        return InstallResult(ok=True, returncode=0,
                             log=str(dst), detail=str(dst))

    try:
        need = int((WINLIBS_DOWNLOAD_BYTES + WINLIBS_UNPACKED_BYTES) * 1.10)
        _check_disk_space(dst, need)
    except InsufficientDiskError as e:
        emit(f"[winlibs] {e}")
        return InstallResult(ok=False, returncode=1, log=str(e), detail=str(e))

    with tempfile.TemporaryDirectory(prefix="smoap-winlibs-") as td:
        td_path = Path(td)
        zip_path = td_path / f"winlibs-{WINLIBS_VERSION}.zip"
        emit(f"[winlibs] downloading WinLibs ({WINLIBS_DOWNLOAD_BYTES / (1024**2):.0f} MB)...")
        r = _download(
            WINLIBS_URL, zip_path,
            on_line=on_line, timeout=600.0,
            expected_sha256=WINLIBS_SHA256,
        )
        if not r.ok:
            return r
        emit(f"[winlibs] extracting to {dst} (~{WINLIBS_UNPACKED_BYTES / (1024**3):.2f} GB unpacked)...")
        r = _extract_zip_renamed(zip_path, dst, on_line=on_line)
        if not r.ok:
            return r

    if not gxx.is_file():
        msg = (
            f"extraction reported success but {gxx} is missing. The "
            f"zip's top-level dir name may have changed."
        )
        emit(f"[winlibs] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
    emit(f"[winlibs] ready: {gxx}")
    return InstallResult(ok=True, returncode=0,
                         log=str(dst), detail=str(dst))


def install_sail_python_deps(on_line: ProgressFn | None = None) -> InstallResult:
    """`pip install --user pyelftools mmh3 lz4` into the resolved Python.

    Sail's host tools import all three (pyelftools parses .nso/.elf;
    mmh3 hashes symbols; lz4 compresses the symbol DB). LibHakkun's
    README typos the second as "mmh"; the actual import is `mmh3`.

    Uses `_winget_python312_commands` first (so a winget-installed
    Python is preferred), then `py -3.12`, then `python3.12`. On
    success, writes a marker file so the prereq check skips its import
    probe on the next run.
    """
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    # Resolve which Python to install into. Mirror the probe order
    # `check_sail_python_deps` uses so detector + installer stay in lockstep.
    py_candidates: list[list[str]] = []
    for cmd in _winget_python312_commands():
        # `_winget_python312_commands` returns "--version" probes; strip
        # the trailing arg to get an invocation prefix we can extend.
        py_candidates.append(list(cmd[:-1]))
    py_candidates.append(["py", "-3.12"])
    py_candidates.append(["python3.12"])

    last_err = "no Python 3.12 found"
    for prefix in py_candidates:
        # Verify the candidate actually runs before we try pip.
        probe = _stream_subprocess([*prefix, "--version"], on_line=on_line)
        if not probe.ok:
            last_err = f"{prefix} unavailable"
            continue
        emit(f"[sail-deps] using {prefix[0]} for pip install --user")
        cmd = [
            *prefix, "-m", "pip", "install", "--user",
            "--disable-pip-version-check",
            *SAIL_PIP_PACKAGES,
        ]
        result = _stream_subprocess(cmd, on_line=on_line)
        if result.ok:
            # Write the marker so the prereq check short-circuits next time.
            marker = sail_deps_marker_path()
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("ok\n", encoding="utf-8")
            except OSError as e:
                emit(f"[sail-deps] could not write marker {marker}: {e}")
            emit(f"[sail-deps] installed: {' '.join(SAIL_PIP_PACKAGES)}")
            return InstallResult(
                ok=True, returncode=0,
                log=result.log,
                detail=f"pip install --user {' '.join(SAIL_PIP_PACKAGES)}",
            )
        last_err = f"pip install failed (exit {result.returncode})"
    msg = f"could not install sail deps: {last_err}"
    emit(f"[sail-deps] {msg}")
    return InstallResult(ok=False, returncode=1, log=msg, detail=msg)


# ---------------------------------------------------------------------------
# Public registry: wizard maps PrereqResult.key → installer function
# ---------------------------------------------------------------------------

INSTALLERS: dict[str, Callable[[ProgressFn | None], InstallResult]] = {
    "llvm19": install_llvm19,
    "winlibs": install_winlibs,
    "sail_python_deps": install_sail_python_deps,
    "cmake": install_cmake,
    "ninja": install_ninja,
    "python312": install_python312,
    "hactool": install_hactool,
}

# Order the wizard's "Install all missing" walker uses. The two big
# downloads first while attention is fresh — LLVM is the largest (~806
# MB) so it goes first to fail-fast on disk-space / network issues.
# Python 3.12 goes before sail_python_deps because pip install needs it.
INSTALL_ORDER: tuple[str, ...] = (
    "llvm19",
    "winlibs",
    "cmake",
    "ninja",
    "python312",
    "sail_python_deps",
    "hactool",
)
