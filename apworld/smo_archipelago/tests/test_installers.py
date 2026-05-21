"""Tests for `_setup.installers` — the silent install runners that power
the wizard's "Install them for me" mode.

All shell-outs and network calls are monkeypatched. We assert on:

  - winget command lines (flags must match what `--silent --accept-*
    --disable-interactivity` expects, otherwise winget pops a prompt
    and the install hangs the wizard).
  - Post-install PATH prepending (the parent dir of each
    winget-deterministic install path must end up in
    os.environ["PATH"] so downstream tools find the binary by
    bare name).
  - Hactool download → unzip → cache path matches the detector's
    `bundled_hactool_path()` (drift here means a successful install
    that the prereq check still reports as failed — silent UX bug).
  - install_devkitpro refuses to run on non-Windows (no ShellExecuteW).
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from _setup import installers, prereqs


# ---------- winget command line ----------

def test_winget_install_builds_correct_command(monkeypatch, tmp_path) -> None:
    """Regression target: silent winget install MUST include all four of
    --silent --accept-package-agreements --accept-source-agreements
    --disable-interactivity. Dropping any one of these makes winget
    surface an interactive prompt that hangs the wizard forever (the
    subprocess pipe blocks waiting for stdin we never provide)."""
    captured: dict[str, list[str]] = {}

    class _FakeProc:
        def __init__(self, cmd):
            captured["cmd"] = cmd
            self.stdout = io.StringIO("Installing...\nDone.\n")
            self.pid = 12345

        def wait(self):
            return 0

    def _fake_popen(cmd, **kwargs):
        return _FakeProc(cmd)

    # Pretend winget exists on PATH.
    monkeypatch.setattr(installers.shutil, "which",
                        lambda name: "C:/winget.exe" if name == "winget" else None)
    monkeypatch.setattr(installers.subprocess, "Popen", _fake_popen)

    r = installers.winget_install("Kitware.CMake")
    assert r.ok
    cmd = captured["cmd"]
    assert cmd[0] == "C:/winget.exe"
    assert cmd[1:4] == ["install", "-e", "--id"]
    assert "Kitware.CMake" in cmd
    # Each of these flags is load-bearing — winget will prompt without
    # them. Checked individually so a regression names the missing flag.
    assert "--silent" in cmd
    assert "--accept-package-agreements" in cmd
    assert "--accept-source-agreements" in cmd
    assert "--disable-interactivity" in cmd


def test_winget_install_surfaces_winget_missing(monkeypatch) -> None:
    """If winget itself isn't on PATH (LTSC, stripped Win11), the
    installer must fail cleanly with a "install App Installer" hint —
    NOT trip an unrelated stack trace from Popen("winget", ...)."""
    monkeypatch.setattr(installers.shutil, "which", lambda name: None)
    r = installers.winget_install("Kitware.CMake")
    assert not r.ok
    assert "App Installer" in r.detail or "winget" in r.detail


# ---------- post-install PATH prepending ----------

def test_install_ninja_prepends_winget_path(monkeypatch, tmp_path) -> None:
    """After `winget install Ninja-build.Ninja` returns success, the
    installer must prepend the install dir to os.environ["PATH"] for
    the current process. Without this, cmake's bare-name `ninja` spawn
    inside `cmake --build` fails to find ninja even though winget just
    put it in place — Windows doesn't refresh PATH for running procs."""
    pkg_dir = tmp_path / "Ninja-build.Ninja_winget_x64"
    pkg_dir.mkdir(parents=True)
    ninja = pkg_dir / "ninja.exe"
    ninja.write_text("")

    monkeypatch.setattr(installers.shutil, "which",
                        lambda name: "C:/winget.exe" if name == "winget" else None)

    class _FakeProc:
        def __init__(self, cmd):
            self.stdout = io.StringIO("ok\n")

        def wait(self):
            return 0

    monkeypatch.setattr(installers.subprocess, "Popen",
                        lambda cmd, **kw: _FakeProc(cmd))
    monkeypatch.setattr(installers, "_winget_ninja_paths", lambda: [ninja])
    monkeypatch.setenv("PATH", "")

    r = installers.install_ninja()
    assert r.ok
    import os
    assert str(pkg_dir) in os.environ["PATH"].split(os.pathsep)


def test_install_python312_prepends_winget_path(monkeypatch, tmp_path) -> None:
    launcher_dir = tmp_path / "Programs" / "Python" / "Launcher"
    launcher_dir.mkdir(parents=True)
    py_exe = launcher_dir / "py.exe"
    py_exe.write_text("")

    monkeypatch.setattr(installers.shutil, "which",
                        lambda name: "C:/winget.exe" if name == "winget" else None)

    class _FakeProc:
        def __init__(self, cmd):
            self.stdout = io.StringIO("ok\n")

        def wait(self):
            return 0

    monkeypatch.setattr(installers.subprocess, "Popen",
                        lambda cmd, **kw: _FakeProc(cmd))
    monkeypatch.setattr(
        installers, "_winget_python312_commands",
        lambda: [[str(py_exe), "-3.12", "--version"]],
    )
    monkeypatch.setenv("PATH", "")

    r = installers.install_python312()
    assert r.ok
    import os
    assert str(launcher_dir) in os.environ["PATH"].split(os.pathsep)


# ---------- install_hactool ----------

def test_install_hactool_downloads_and_unzips_to_bundled_path(
    monkeypatch, tmp_path,
) -> None:
    """End-to-end install_hactool: stub the download to feed a real zip
    containing hactool.exe, verify the .exe lands at exactly
    bundled_hactool_path() so the prereq detector picks it up on the
    next Re-check without any state update.

    Drift between installer destination and detector probe = silent
    UX bug (install succeeds, prereq stays red, user confused).
    """
    # Redirect bundled_hactool_path to a temp location so the test
    # doesn't write into the real %APPDATA%.
    cache = tmp_path / "bundled" / "hactool.exe"
    monkeypatch.setattr(prereqs, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "bundled_hactool_path", lambda: cache)

    # Build a fake hactool-1.4.0-win.zip in memory.
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("hactool.exe", b"FAKE_HACTOOL_BINARY")
    zip_bytes.seek(0)

    # Stub urlopen to serve our in-memory zip.
    class _FakeResp:
        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}
            self.status = 200

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._buf.close()

    def _fake_urlopen(url, *a, **kw):
        return _FakeResp(zip_bytes.getvalue())

    monkeypatch.setattr(installers.urllib.request, "urlopen", _fake_urlopen)

    r = installers.install_hactool()
    assert r.ok, f"install_hactool should succeed; detail={r.detail!r}"
    assert cache.is_file(), "hactool.exe must land at bundled_hactool_path()"
    assert cache.read_bytes() == b"FAKE_HACTOOL_BINARY"


def test_install_hactool_skips_when_already_installed(monkeypatch, tmp_path) -> None:
    """Idempotent: if the .exe already exists at the cache location,
    skip the download entirely. Re-clicking Auto-install must not
    burn 5 MB of bandwidth on every redo."""
    cache = tmp_path / "bundled" / "hactool.exe"
    cache.parent.mkdir(parents=True)
    cache.write_text("existing")
    monkeypatch.setattr(prereqs, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "bundled_hactool_path", lambda: cache)

    # Make urlopen explode if called — proves the download was skipped.
    def _explode(*a, **kw):
        raise AssertionError("download should not happen on already-installed")

    monkeypatch.setattr(installers.urllib.request, "urlopen", _explode)

    r = installers.install_hactool()
    assert r.ok
    assert cache.read_text() == "existing"  # untouched


def test_install_hactool_rejects_sha256_mismatch(monkeypatch, tmp_path) -> None:
    """If a SHA-256 is pinned and the download doesn't match, the
    installer must reject the file (no quarantine in the cache) and
    return failure. Tamper detection is the whole point of pinning."""
    cache = tmp_path / "bundled" / "hactool.exe"
    monkeypatch.setattr(prereqs, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "_HACTOOL_SHA256", "00" * 32)

    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("hactool.exe", b"any-bytes")
    zip_bytes.seek(0)

    class _FakeResp:
        def __init__(self, data):
            self._buf = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self): return self

        def __exit__(self, *a): pass

    monkeypatch.setattr(
        installers.urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(zip_bytes.getvalue()),
    )

    r = installers.install_hactool()
    assert not r.ok
    assert "sha256" in r.log.lower()
    assert not cache.exists(), "rejected download must not land in cache"


# ---------- install_devkitpro ----------

def test_install_devkitpro_refuses_on_non_windows(monkeypatch) -> None:
    """The installer uses ShellExecuteW for UAC elevation, which only
    exists on Windows. On Linux/macOS it must fail cleanly with a
    "install manually" message — not crash trying to load shell32."""
    monkeypatch.setattr(installers.sys, "platform", "linux")
    r = installers.install_devkitpro()
    assert not r.ok
    assert "Windows-only" in r.detail or "devkitpro.org" in r.detail


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteW is win32-only")
def test_install_devkitpro_calls_shellexecutew_with_runas(monkeypatch, tmp_path) -> None:
    """The NSIS /S flag silently fails without admin elevation. We MUST
    spawn the installer through ShellExecuteW with lpVerb='runas' so
    Windows shows the UAC consent dialog — otherwise the user sees
    "installing for 10 minutes" while nothing happens, then a timeout.
    """
    captured: dict = {}

    monkeypatch.setattr(
        installers, "_find_devkitpro_asset",
        lambda on_line=None: "https://example.invalid/installer.exe",
    )

    # Stub _download to fake a successful download.
    def _fake_download(url, dst, *, on_line=None, timeout=600.0, expected_sha256=""):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"fake-installer")
        return installers.InstallResult(ok=True, returncode=0, log=str(dst), detail=str(dst))
    monkeypatch.setattr(installers, "_download", _fake_download)

    # Stub ShellExecuteW: capture args, return success (33) so the
    # installer proceeds into its polling loop. We exit the polling
    # loop by faking a gxx binary at one of the default roots before
    # the loop spins up.
    class _FakeShell:
        @staticmethod
        def ShellExecuteW(hwnd, verb, file, params, cwd, show):
            captured["verb"] = verb
            captured["file"] = file
            captured["params"] = params
            return 33  # > 32 = success

    class _FakeWinDLL:
        shell32 = _FakeShell()

    import ctypes
    monkeypatch.setattr(ctypes, "windll", _FakeWinDLL(), raising=False)

    # Plant a fake devkitPro install so the polling loop returns
    # immediately.
    fake_root = tmp_path / "devkitPro"
    bindir = fake_root / "devkitA64" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "aarch64-none-elf-g++.exe").write_text("")
    monkeypatch.setattr(installers, "_DEVKITPRO_DEFAULT_ROOTS", (fake_root,))

    r = installers.install_devkitpro()
    assert r.ok, f"install should succeed once gxx appears; detail={r.detail!r}"
    # The "runas" verb is what triggers UAC. Without it, NSIS /S
    # silently fails and the install never completes.
    assert captured["verb"] == "runas"
    assert "/S" in captured["params"]
    assert "C:\\devkitPro" in captured["params"]


# ---------- preflight ----------

def test_check_winget_succeeds_when_on_path(monkeypatch) -> None:
    monkeypatch.setattr(installers.shutil, "which",
                        lambda name: "C:/winget.exe" if name == "winget" else None)
    r = installers.check_winget()
    assert r.ok


def test_check_winget_surfaces_clear_msi_message_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(installers.shutil, "which", lambda name: None)
    r = installers.check_winget()
    assert not r.ok
    # The wizard surfaces this string verbatim. App Installer + Microsoft
    # Store are the load-bearing keywords that tell the user what to
    # search for.
    assert "App Installer" in r.detail
    assert "Microsoft Store" in r.detail


# ---------- registry ----------

def test_INSTALLERS_registry_covers_every_auto_installable_detector() -> None:
    """If a detector sets auto_installable=True there MUST be a matching
    entry in INSTALLERS. Without the entry, the wizard's Auto-install
    button silently no-ops — and the user has no way to recover from
    the prereq page."""
    # Run check_all with stubs that force everything into the "failed
    # auto-installable" path, then assert every key has a registry entry.
    from _setup.prereqs import check_all
    # We just need to enumerate the keys; the actual detector outcomes
    # don't matter for this test.
    results = check_all()
    auto_keys = {r.key for r in results if r.auto_installable}
    missing = auto_keys - set(installers.INSTALLERS.keys())
    assert not missing, (
        f"PrereqResult keys with auto_installable=True but no installer "
        f"registered: {missing}"
    )


def test_INSTALL_ORDER_puts_devkitpro_first() -> None:
    """devkitPro's UAC prompt must fire first so the user clears it
    while attention is fresh; the winget tools are background noise
    that doesn't compete for focus. Ordering is load-bearing UX."""
    assert installers.INSTALL_ORDER[0] == "devkitpro"
