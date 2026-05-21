"""Silent installers for the wizard's auto-install mode.

The wizard's prereq page offers two paths (see `wizard.py`):
  - **Manual**: surface install links + Browse buttons (today's behavior).
  - **Auto**: run silent installers from this module.

Three winget tools (CMake, Ninja, Python 3.12) and one direct-installer
tool (devkitPro) plus one direct download (hactool) live here. Each
installer:

  - Streams subprocess output line-by-line to an `on_line` callback so
    the wizard's log popup shows live progress.
  - Probes the deterministic post-install path and prepends its dir to
    `os.environ["PATH"]` for the running process — winget doesn't refresh
    the shell's environment, but we know exactly where the MSIs land.
  - Returns an `InstallResult` with ok/returncode/log so the wizard can
    distinguish "succeeded" from "failed" without parsing the stream.

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
    _DEVKITPRO_DEFAULT_ROOTS,
    _devkitpro_gxx_under,
    _prepend_path,
    _winget_ninja_paths,
    _winget_python312_commands,
    bundled_hactool_path,
)

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
# devkitPro — silent NSIS installer, requires UAC
# ---------------------------------------------------------------------------

# Hit the public releases API to find the latest win64 installer asset.
# Pin to the API endpoint (not a hardcoded /releases/download/<version>/...
# URL) so new releases pick up automatically. The API is unauthenticated
# and rate-limited to 60 req/hr from a single IP — fine for the wizard's
# once-per-machine usage pattern.
_DEVKITPRO_RELEASES_URL = (
    "https://api.github.com/repos/devkitPro/installer/releases/latest"
)


def _find_devkitpro_asset(on_line: ProgressFn | None = None) -> str | None:
    """Hit GitHub's releases API, return the URL of the latest win64
    installer asset. Returns None on any error (caller surfaces the
    failure to the user). Match pattern: `devkitpro-updater-*-win64.exe`
    — the long-stable Windows-installer naming convention."""
    try:
        req = urllib.request.Request(
            _DEVKITPRO_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        if on_line:
            on_line(f"[devkitpro] failed to query releases API: {e}")
        return None
    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("devkitpro-updater-") and name.endswith("-win64.exe"):
            url = asset.get("browser_download_url")
            if url:
                if on_line:
                    on_line(f"[devkitpro] resolved asset: {name} → {url}")
                return url
    if on_line:
        on_line(
            f"[devkitpro] no matching win64 installer in releases payload "
            f"(saw {[a.get('name') for a in payload.get('assets', [])]})"
        )
    return None


def install_devkitpro(on_line: ProgressFn | None = None) -> InstallResult:
    """Silent devkitPro install with UAC elevation.

    Flow:
      1. Resolve the latest win64 installer URL from GitHub's API.
      2. Download to a tempfile.
      3. Spawn via `ShellExecuteW` with `lpVerb="runas"` — Windows shows
         the UAC consent dialog; the NSIS installer's `/S` silent flag
         only works WITH admin elevation (without, it fails silently
         and `C:\\devkitPro` is never created).
      4. Poll for the cross-compiler binary for up to 10 minutes
         (devkitPro installs ~600 MB on disk plus a msys2 stage).
      5. On success, the next `check_devkitpro()` call resolves naturally
         because we land at one of `_DEVKITPRO_DEFAULT_ROOTS`.

    If the user clicks No on the UAC prompt, ShellExecuteW returns
    SE_ERR_ACCESSDENIED (5) and the polling phase never sees the
    install dir appear — we time out with a clear "UAC denied" message.
    """
    def emit(msg: str) -> None:
        if on_line:
            on_line(msg)

    if sys.platform != "win32":
        msg = (
            "install_devkitpro is Windows-only (uses ShellExecuteW for UAC). "
            "Install devkitPro from devkitpro.org manually on this platform."
        )
        emit(f"[devkitpro] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    url = _find_devkitpro_asset(on_line=on_line)
    if url is None:
        msg = (
            "failed to resolve the latest devkitPro installer URL; "
            "check your internet connection and try again, or install "
            "from devkitpro.org manually."
        )
        emit(f"[devkitpro] {msg}")
        return InstallResult(ok=False, returncode=1, log=msg, detail=msg)

    with tempfile.TemporaryDirectory(prefix="smoap-devkitpro-") as td:
        td_path = Path(td)
        # Preserve the URL's filename so we can identify the exact version
        # in temp / wizard log if something goes sideways.
        installer = td_path / url.rsplit("/", 1)[-1]
        r = _download(url, installer, on_line=on_line, timeout=600.0)
        if not r.ok:
            return r

        emit(
            "[devkitpro] launching installer with UAC elevation. "
            "Click YES on the Windows User Account Control prompt. "
            "Install will take ~5-10 minutes after that."
        )
        # ShellExecuteW("runas", ...) triggers a proper UAC consent dialog.
        # subprocess.Popen with the same args silently inherits the current
        # token and the NSIS /S flag becomes a no-op when not elevated.
        import ctypes
        SW_HIDE = 0
        # SEE_MASK_NOCLOSEPROCESS = 0x40 — we don't actually use it here
        # because ShellExecuteW (not ShellExecuteExW) returns an HINSTANCE
        # that's > 32 on success. Poll the FS instead of the process handle.
        try:
            rc_hinst = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None,
                "runas",
                str(installer),
                "/S /D=C:\\devkitPro",
                str(td_path),
                SW_HIDE,
            )
        except OSError as e:
            msg = f"ShellExecuteW failed: {e}"
            emit(f"[devkitpro] {msg}")
            return InstallResult(ok=False, returncode=1, log=msg, detail=msg)
        if rc_hinst <= 32:
            # 5 = SE_ERR_ACCESSDENIED (UAC dialog cancelled by user)
            msg_map = {
                0: "out of memory",
                2: "file not found",
                3: "path not found",
                5: "UAC denied — re-click Auto-install (or switch to manual mode)",
                8: "out of memory",
                11: "bad format",
                26: "sharing violation",
                27: "association incomplete",
                28: "DDE timeout",
                29: "DDE fail",
                30: "DDE busy",
                31: "no association",
                32: "DLL not found",
            }
            err = msg_map.get(rc_hinst, f"unknown ShellExecute error {rc_hinst}")
            emit(f"[devkitpro] {err}")
            return InstallResult(ok=False, returncode=rc_hinst,
                                 log=err, detail=err)

        emit("[devkitpro] installer launched; polling for completion...")
        # Poll up to 10 minutes for the cross-compiler binary to appear at
        # one of the default install roots. Polling the install dir's
        # presence isn't enough (the NSIS installer creates C:\devkitPro
        # near the start of its run) — wait for the actual cross-compiler.
        deadline = time.monotonic() + 600.0
        last_msg_at = 0.0
        while time.monotonic() < deadline:
            for root in _DEVKITPRO_DEFAULT_ROOTS:
                gxx = _devkitpro_gxx_under(root)
                if gxx is not None:
                    emit(f"[devkitpro] installed: {gxx}")
                    os.environ["DEVKITPRO"] = str(root)
                    return InstallResult(
                        ok=True, returncode=0,
                        log=str(gxx), detail=str(root),
                    )
            now = time.monotonic()
            if now - last_msg_at >= 30:
                last_msg_at = now
                remaining = int(deadline - now)
                emit(f"[devkitpro] still installing... ({remaining}s left)")
            time.sleep(2)

    msg = (
        "devkitPro install timed out after 10 minutes. If you saw the UAC "
        "prompt and clicked Yes, the install may still complete in the "
        "background — click Re-check in a few minutes. If you clicked No, "
        "click Auto-install again (or switch to manual mode and install "
        "from devkitpro.org)."
    )
    emit(f"[devkitpro] {msg}")
    return InstallResult(ok=False, returncode=1, log=msg, detail=msg)


# ---------------------------------------------------------------------------
# Public registry: wizard maps PrereqResult.key → installer function
# ---------------------------------------------------------------------------

INSTALLERS: dict[str, Callable[[ProgressFn | None], InstallResult]] = {
    "devkitpro": install_devkitpro,
    "cmake": install_cmake,
    "ninja": install_ninja,
    "python312": install_python312,
    "hactool": install_hactool,
}

# Order the wizard's "Install all missing" walker uses. devkitPro first
# so the user clears its UAC prompt while attention is fresh — the
# other installers are background winget noise that don't compete for
# focus. Hactool last because it's the smallest download.
INSTALL_ORDER: tuple[str, ...] = (
    "devkitpro",
    "cmake",
    "ninja",
    "python312",
    "hactool",
)
