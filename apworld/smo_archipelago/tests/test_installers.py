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
  - Post-Hakkun portable installs: LLVM 19 + WinLibs land under
    `%LOCALAPPDATA%\\SMOArchipelago\\<tool>\\`. Disk-space precheck
    refuses to start when free space is short. SHA-256 mismatches
    are rejected (no cache poisoning).
  - cleanup_portable_deps removes the two portable dirs on demand
    but leaves the surrounding wizard state alone.
"""

from __future__ import annotations

import io
import sys
import tarfile
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

    monkeypatch.setattr(installers.shutil, "which",
                        lambda name: "C:/winget.exe" if name == "winget" else None)
    monkeypatch.setattr(installers.subprocess, "Popen", _fake_popen)

    r = installers.winget_install("Kitware.CMake")
    assert r.ok
    cmd = captured["cmd"]
    assert cmd[0] == "C:/winget.exe"
    assert cmd[1:4] == ["install", "-e", "--id"]
    assert "Kitware.CMake" in cmd
    assert "--silent" in cmd
    assert "--accept-package-agreements" in cmd
    assert "--accept-source-agreements" in cmd
    assert "--disable-interactivity" in cmd


def test_winget_install_surfaces_winget_missing(monkeypatch) -> None:
    monkeypatch.setattr(installers.shutil, "which", lambda name: None)
    r = installers.winget_install("Kitware.CMake")
    assert not r.ok
    assert "App Installer" in r.detail or "winget" in r.detail


# ---------- post-install PATH prepending ----------

def test_install_ninja_prepends_winget_path(monkeypatch, tmp_path) -> None:
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
    cache = tmp_path / "bundled" / "hactool.exe"
    monkeypatch.setattr(prereqs, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "bundled_hactool_path", lambda: cache)

    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("hactool.exe", b"FAKE_HACTOOL_BINARY")
    zip_bytes.seek(0)

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
    assert cache.is_file()
    assert cache.read_bytes() == b"FAKE_HACTOOL_BINARY"


def test_install_hactool_skips_when_already_installed(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "bundled" / "hactool.exe"
    cache.parent.mkdir(parents=True)
    cache.write_text("existing")
    monkeypatch.setattr(prereqs, "bundled_hactool_path", lambda: cache)
    monkeypatch.setattr(installers, "bundled_hactool_path", lambda: cache)

    def _explode(*a, **kw):
        raise AssertionError("download should not happen on already-installed")

    monkeypatch.setattr(installers.urllib.request, "urlopen", _explode)

    r = installers.install_hactool()
    assert r.ok
    assert cache.read_text() == "existing"


def test_install_hactool_rejects_sha256_mismatch(monkeypatch, tmp_path) -> None:
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
    assert not cache.exists()


# ---------- disk-space precheck ----------

def test_check_disk_space_passes_when_room(monkeypatch, tmp_path) -> None:
    """Sanity: enough free space → no exception raised."""
    import collections
    stat = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(installers.shutil, "disk_usage",
                        lambda p: stat(total=100 * 1024**3,
                                       used=10 * 1024**3,
                                       free=90 * 1024**3))
    installers._check_disk_space(tmp_path / "nope", 1 * 1024**3)


def test_check_disk_space_refuses_when_short(monkeypatch, tmp_path) -> None:
    """Refuse to start a download we can't complete — surface a clear
    "need X GiB, have Y GiB on Z" so the user knows exactly what to
    free up."""
    import collections
    stat = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(installers.shutil, "disk_usage",
                        lambda p: stat(total=100 * 1024**3,
                                       used=99 * 1024**3,
                                       free=1 * 1024**3))
    with pytest.raises(installers.InsufficientDiskError) as exc:
        installers._check_disk_space(tmp_path / "nope", 4 * 1024**3)
    msg = str(exc.value)
    assert "need" in msg.lower()
    assert "GiB" in msg


def test_check_disk_space_walks_up_to_existing_parent(monkeypatch, tmp_path) -> None:
    """Target dir doesn't exist yet (first install). We must walk up to
    the nearest existing parent before calling disk_usage — otherwise
    shutil.disk_usage on a non-existent path raises and the install
    fails for a non-disk-space reason."""
    queried: list[str] = []
    import collections
    stat = collections.namedtuple("usage", "total used free")

    def _fake_usage(p):
        queried.append(str(p))
        return stat(total=100 * 1024**3, used=0, free=100 * 1024**3)

    monkeypatch.setattr(installers.shutil, "disk_usage", _fake_usage)
    target = tmp_path / "does" / "not" / "exist" / "yet"
    installers._check_disk_space(target, 1 * 1024**3)
    assert queried, "disk_usage was never called"
    # The path queried must be an existing ancestor.
    assert Path(queried[0]).exists()


# ---------- install_llvm19 ----------

def test_install_llvm19_refuses_on_non_windows(monkeypatch) -> None:
    """LLVM tarball is the Windows-MSVC build; the installer should
    refuse on other platforms with an actionable hint."""
    monkeypatch.setattr(installers.sys, "platform", "linux")
    r = installers.install_llvm19()
    assert not r.ok
    assert "Windows-only" in r.detail or "MSVC" in r.detail


@pytest.mark.skipif(sys.platform != "win32", reason="LLVM installer is win32-only")
def test_install_llvm19_skips_when_clang_already_present(monkeypatch, tmp_path) -> None:
    """Idempotent: an existing portable install with clang.exe present
    must skip the ~806 MB download. Re-clicking Install must not burn
    bandwidth on a redo."""
    dst = tmp_path / "llvm"
    (dst / "bin").mkdir(parents=True)
    (dst / "bin" / "clang.exe").write_text("existing")
    monkeypatch.setattr(installers, "llvm_portable_root", lambda: dst)

    def _explode(*a, **kw):
        raise AssertionError("download should not happen on already-installed")

    monkeypatch.setattr(installers.urllib.request, "urlopen", _explode)
    r = installers.install_llvm19()
    assert r.ok
    assert (dst / "bin" / "clang.exe").read_text() == "existing"


@pytest.mark.skipif(sys.platform != "win32", reason="LLVM installer is win32-only")
def test_install_llvm19_refuses_when_insufficient_disk(monkeypatch, tmp_path) -> None:
    """Disk-space precheck fires BEFORE the download starts — no
    partial download lying around to clean up."""
    dst = tmp_path / "llvm"
    monkeypatch.setattr(installers, "llvm_portable_root", lambda: dst)

    import collections
    stat = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(installers.shutil, "disk_usage",
                        lambda p: stat(total=100 * 1024**3,
                                       used=99 * 1024**3,
                                       free=1 * 1024**3))

    def _explode(*a, **kw):
        raise AssertionError("download should not start when disk is full")

    monkeypatch.setattr(installers.urllib.request, "urlopen", _explode)
    r = installers.install_llvm19()
    assert not r.ok
    assert "free space" in r.detail.lower() or "GiB" in r.detail


@pytest.mark.skipif(sys.platform != "win32", reason="LLVM installer is win32-only")
def test_install_llvm19_extracts_and_strips_top_level_dir(
    monkeypatch, tmp_path,
) -> None:
    """The LLVM tarball is laid out as
    `clang+llvm-19.1.7-x86_64-pc-windows-msvc/bin/clang.exe`. We MUST
    strip the top-level dir so the final layout is
    `<dst>/bin/clang.exe` — drift here means the detector won't find
    clang.exe and the install gets re-tried forever."""
    dst = tmp_path / "llvm"
    monkeypatch.setattr(installers, "llvm_portable_root", lambda: dst)
    # Plenty of disk.
    import collections
    stat = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(installers.shutil, "disk_usage",
                        lambda p: stat(total=100 * 1024**3,
                                       used=0,
                                       free=100 * 1024**3))

    # Build an in-memory tar.xz with the same top-level layout as the
    # real LLVM tarball.
    tar_path = tmp_path / "src" / "fake-llvm.tar.xz"
    tar_path.parent.mkdir()
    with tarfile.open(tar_path, "w:xz") as tf:
        for entry in (
            ("clang+llvm-19.1.7-x86_64-pc-windows-msvc/bin/clang.exe", b"CLANGBIN"),
            ("clang+llvm-19.1.7-x86_64-pc-windows-msvc/bin/clang++.exe", b"CLANGXX"),
            ("clang+llvm-19.1.7-x86_64-pc-windows-msvc/lib/clang/19/include/x.h", b"hdr"),
        ):
            data = entry[1]
            info = tarfile.TarInfo(entry[0])
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    # Stub the download to write our local tarball to dst.
    def _fake_download(url, dst_path, *, on_line=None, timeout=600.0,
                       expected_sha256=""):
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(tar_path.read_bytes())
        return installers.InstallResult(ok=True, returncode=0,
                                        log=str(dst_path), detail=str(dst_path))

    monkeypatch.setattr(installers, "_download", _fake_download)

    r = installers.install_llvm19()
    assert r.ok, f"install_llvm19 failed: {r.detail!r}\nlog: {r.log}"
    assert (dst / "bin" / "clang.exe").read_bytes() == b"CLANGBIN"
    assert (dst / "bin" / "clang++.exe").read_bytes() == b"CLANGXX"
    # Top-level dir must NOT survive — the detector looks for
    # <dst>/bin/clang.exe, not <dst>/clang+llvm-.../bin/clang.exe.
    assert not (dst / "clang+llvm-19.1.7-x86_64-pc-windows-msvc").exists()


# ---------- install_winlibs ----------

def test_install_winlibs_refuses_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(installers.sys, "platform", "linux")
    r = installers.install_winlibs()
    assert not r.ok
    assert "Windows-only" in r.detail or "Windows" in r.detail


@pytest.mark.skipif(sys.platform != "win32", reason="WinLibs installer is win32-only")
def test_install_winlibs_skips_when_already_installed(monkeypatch, tmp_path) -> None:
    dst = tmp_path / "winlibs"
    (dst / "bin").mkdir(parents=True)
    (dst / "bin" / "g++.exe").write_text("existing")
    monkeypatch.setattr(installers, "winlibs_portable_root", lambda: dst)

    def _explode(*a, **kw):
        raise AssertionError("download should not happen on already-installed")

    monkeypatch.setattr(installers.urllib.request, "urlopen", _explode)
    r = installers.install_winlibs()
    assert r.ok


@pytest.mark.skipif(sys.platform != "win32", reason="WinLibs installer is win32-only")
def test_install_winlibs_extracts_and_strips_top_level_dir(
    monkeypatch, tmp_path,
) -> None:
    """WinLibs zip is laid out as `mingw64/bin/g++.exe`. We MUST strip
    the `mingw64/` top-level dir so the final layout is
    `<dst>/bin/g++.exe` — matches what the detector probes for."""
    dst = tmp_path / "winlibs"
    monkeypatch.setattr(installers, "winlibs_portable_root", lambda: dst)
    import collections
    stat = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(installers.shutil, "disk_usage",
                        lambda p: stat(total=100 * 1024**3,
                                       used=0,
                                       free=100 * 1024**3))

    zip_path = tmp_path / "src" / "fake-winlibs.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mingw64/bin/g++.exe", b"GPPBIN")
        zf.writestr("mingw64/bin/gcc.exe", b"GCCBIN")
        zf.writestr("mingw64/lib/libstdc++.a", b"libdata")

    def _fake_download(url, dst_path, *, on_line=None, timeout=600.0,
                       expected_sha256=""):
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(zip_path.read_bytes())
        return installers.InstallResult(ok=True, returncode=0,
                                        log=str(dst_path), detail=str(dst_path))

    monkeypatch.setattr(installers, "_download", _fake_download)

    r = installers.install_winlibs()
    assert r.ok, f"install_winlibs failed: {r.detail!r}\nlog: {r.log}"
    assert (dst / "bin" / "g++.exe").read_bytes() == b"GPPBIN"
    assert (dst / "bin" / "gcc.exe").read_bytes() == b"GCCBIN"
    assert not (dst / "mingw64").exists()


# ---------- install_sail_python_deps ----------

def test_install_sail_python_deps_uses_pip_install_user(monkeypatch, tmp_path) -> None:
    """`pip install --user` writes the marker file on success so the
    detector skips its import-probe on the next Re-check."""
    captured: list[list[str]] = []

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None,
                    wall_timeout_s=None, stall_timeout_s=None):
        captured.append(cmd)
        return installers.InstallResult(ok=True, returncode=0, log="")

    monkeypatch.setattr(installers, "_stream_subprocess", fake_stream)
    monkeypatch.setattr(installers, "_winget_python312_commands",
                        lambda: [["py.exe", "-3.12", "--version"]])
    marker = tmp_path / "sail_python_deps.ok"
    monkeypatch.setattr(installers, "sail_deps_marker_path", lambda: marker)

    r = installers.install_sail_python_deps()
    assert r.ok, f"install failed: {r.detail!r}"
    assert marker.is_file()
    # Verify the pip install command was issued with the three packages.
    pip_cmds = [c for c in captured if "pip" in c]
    assert pip_cmds, f"no pip command in {captured}"
    cmd = pip_cmds[0]
    assert "install" in cmd
    assert "--user" in cmd
    assert "pyelftools" in cmd
    assert "mmh3" in cmd
    assert "lz4" in cmd


# ---------- cleanup_portable_deps ----------

def test_cleanup_removes_both_portable_dirs(monkeypatch, tmp_path) -> None:
    """User picked Remove after build → both LLVM + WinLibs dirs go
    away, but the parent SMOArchipelago dir and sibling state stay."""
    smoap = tmp_path / "SMOArchipelago"
    llvm = smoap / "llvm"
    winlibs = smoap / "winlibs"
    other = smoap / "wizard.json"  # sibling state that MUST NOT be touched
    for d in (llvm, winlibs):
        (d / "bin").mkdir(parents=True)
        (d / "bin" / "tool.exe").write_text("tool")
    other.write_text('{"keep_portable_deps": false}')

    monkeypatch.setattr(installers, "llvm_portable_root", lambda: llvm)
    monkeypatch.setattr(installers, "winlibs_portable_root", lambda: winlibs)

    r = installers.cleanup_portable_deps()
    assert r.ok
    assert not llvm.exists()
    assert not winlibs.exists()
    # Sibling state untouched.
    assert other.is_file()
    assert smoap.exists()


def test_cleanup_is_idempotent_when_dirs_absent(monkeypatch, tmp_path) -> None:
    """Already-clean state must return ok=True (idempotent), not error
    out on missing dirs — the user might click Remove twice."""
    smoap = tmp_path / "SMOArchipelago"
    monkeypatch.setattr(installers, "llvm_portable_root",
                        lambda: smoap / "llvm")
    monkeypatch.setattr(installers, "winlibs_portable_root",
                        lambda: smoap / "winlibs")
    r = installers.cleanup_portable_deps()
    assert r.ok
    assert "nothing to remove" in r.detail.lower() or "no portable" in r.detail.lower()


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
    assert "App Installer" in r.detail
    assert "Microsoft Store" in r.detail


# ---------- registry ----------

def test_INSTALLERS_registry_covers_every_auto_installable_detector(
    monkeypatch, tmp_path,
) -> None:
    """If a detector sets auto_installable=True there MUST be a matching
    entry in INSTALLERS. Without the entry, the wizard's Auto-install
    button silently no-ops — and the user has no way to recover from
    the prereq page."""
    # Redirect portable-tool roots to avoid touching the real machine.
    monkeypatch.setattr(prereqs, "llvm_portable_root", lambda: tmp_path / "llvm")
    monkeypatch.setattr(prereqs, "winlibs_portable_root", lambda: tmp_path / "winlibs")
    monkeypatch.setattr(prereqs, "sail_deps_marker_path",
                        lambda: tmp_path / "sail_python_deps.ok")
    from _setup.prereqs import check_all
    results = check_all()
    auto_keys = {r.key for r in results if r.auto_installable}
    missing = auto_keys - set(installers.INSTALLERS.keys())
    assert not missing, (
        f"PrereqResult keys with auto_installable=True but no installer "
        f"registered: {missing}"
    )


def test_INSTALL_ORDER_runs_heavy_downloads_first() -> None:
    """LLVM is the heaviest (~806 MB) so it goes first to fail-fast on
    disk-space / network issues while user attention is fresh.
    sail_python_deps requires Python 3.12, so Python must precede it."""
    order = installers.INSTALL_ORDER
    assert order[0] == "llvm19", (
        f"LLVM should be installed first (heaviest, fail-fast on disk space); "
        f"got order[0]={order[0]!r}"
    )
    # Order is total over only the keys present. Python 3.12 install
    # must come before sail's pip install --user step.
    assert order.index("python312") < order.index("sail_python_deps"), (
        "Python 3.12 must be installed before sail_python_deps "
        "(pip install --user needs the resolved Python)"
    )
    # devkitpro must be absent (post-Hakkun) — guard against re-introduction.
    assert "devkitpro" not in order
