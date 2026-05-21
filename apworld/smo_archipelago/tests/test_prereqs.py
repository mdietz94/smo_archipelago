"""Tests for `_setup.prereqs` — the detectors that drive the wizard's
Prereq-check page.

All shell-outs go through `_setup.prereqs._run`; we monkeypatch it per-test
to script success / failure without touching the user's actual machine.
Filesystem-touching detectors are tested by manipulating tmp_path-rooted
helpers (`llvm_portable_root`, `winlibs_portable_root`, `sail_deps_marker_path`)
via monkeypatch.

Post-Hakkun the cross-compile toolchain changed: devkitPro → LLVM 19 (for
the Switch target) + WinLibs / mingw64 g++ (for sail's host build) + a
small set of host Python deps (pyelftools + mmh3 + lz4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _setup import prereqs
from _setup.prereqs import (
    PrereqResult,
    all_ok,
    check_all,
    check_cmake,
    check_hactool,
    check_llvm19,
    check_ninja,
    check_prod_keys,
    check_python312,
    check_sail_python_deps,
    check_winlibs,
)


@pytest.fixture
def fake_run(monkeypatch):
    """Replace `prereqs._run` with a scripted responder.

    Usage:
        fake_run({"cmake --version": (0, "cmake version 3.30.5\\n", "")})
    """
    def install(cmd_to_result: dict[str, tuple[int, str, str]]):
        def _impl(cmd, *, timeout=10.0):
            key = " ".join(cmd)
            if key in cmd_to_result:
                return cmd_to_result[key]
            # Default: pretend the binary doesn't exist.
            raise FileNotFoundError(cmd[0])
        monkeypatch.setattr(prereqs, "_run", _impl)
    return install


@pytest.fixture
def isolated_portable_roots(monkeypatch, tmp_path):
    """Redirect the wizard's portable-tool roots + sail-deps marker so
    they live under tmp_path, and clear the module-level resolved
    caches so each test starts from a known state."""
    monkeypatch.setattr(
        prereqs, "llvm_portable_root", lambda: tmp_path / "llvm",
    )
    monkeypatch.setattr(
        prereqs, "winlibs_portable_root", lambda: tmp_path / "winlibs",
    )
    monkeypatch.setattr(
        prereqs, "sail_deps_marker_path",
        lambda: tmp_path / "sail_python_deps.ok",
    )
    monkeypatch.setattr(prereqs, "_resolved_llvm_bin", None)
    monkeypatch.setattr(prereqs, "_resolved_mingw_bin", None)


# ---------- check_python312 ----------

def test_python312_found_via_py_launcher(fake_run, monkeypatch) -> None:
    monkeypatch.setattr(prereqs, "_winget_python312_commands", lambda: [])
    fake_run({"py -3.12 --version": (0, "Python 3.12.7\n", "")})
    r = check_python312()
    assert r.ok
    assert "3.12.7" in r.detail
    assert r.auto_installable


def test_python312_falls_back_to_python312_command(fake_run, monkeypatch) -> None:
    monkeypatch.setattr(prereqs, "_winget_python312_commands", lambda: [])
    fake_run({"python3.12 --version": (0, "Python 3.12.3\n", "")})
    r = check_python312()
    assert r.ok
    assert "3.12.3" in r.detail


def test_python312_missing(fake_run, monkeypatch) -> None:
    monkeypatch.setattr(prereqs, "_winget_python312_commands", lambda: [])
    fake_run({})
    r = check_python312()
    assert not r.ok
    assert r.install_url.startswith("https://")
    assert r.auto_installable


def test_python312_found_at_winget_path_even_when_not_on_path(
    monkeypatch, tmp_path, fake_run,
) -> None:
    """Counterpart to the Ninja winget-path test: a manual-mode user
    `winget install Python.Python.3.12`s in a separate terminal,
    clicks Re-check, and gets a green row without restarting the
    wizard."""
    launcher_dir = tmp_path / "Programs" / "Python" / "Launcher"
    launcher_dir.mkdir(parents=True)
    py_exe = launcher_dir / "py.exe"
    py_exe.write_text("")
    monkeypatch.setattr(
        prereqs, "_winget_python312_commands",
        lambda: [[str(py_exe), "-3.12", "--version"]],
    )
    fake_run({f"{py_exe} -3.12 --version": (0, "Python 3.12.7\n", "")})
    monkeypatch.setenv("PATH", "")

    r = check_python312()
    assert r.ok
    assert "3.12.7" in r.detail
    assert str(py_exe) in r.detail
    import os
    assert str(launcher_dir) in os.environ["PATH"].split(os.pathsep)


# ---------- check_llvm19 ----------

def test_llvm19_missing(isolated_portable_roots, fake_run) -> None:
    """No portable install + no clang on PATH → not ok, with the
    Auto-install note surfaced for the wizard."""
    fake_run({})
    r = check_llvm19()
    assert not r.ok
    assert r.auto_installable
    assert "LLVM 19.1.7" in r.note or "Auto-install" in r.note
    assert prereqs.resolved_llvm_bin() is None


def test_llvm19_portable_install_correct_version(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """When the wizard's portable LLVM is installed with the right
    version, the detector resolves it AND records the bin dir for
    build.py to mirror in the subprocess PATH."""
    llvm_root = tmp_path / "llvm"
    bindir = llvm_root / "bin"
    bindir.mkdir(parents=True)
    clang = bindir / "clang.exe"
    clang.write_text("")
    fake_run({f"{clang} --version": (
        0, "clang version 19.1.7\nTarget: x86_64-pc-windows-msvc\n", "")})

    r = check_llvm19()
    assert r.ok
    assert "19.1.7" in r.detail
    assert "portable" in r.detail
    assert prereqs.resolved_llvm_bin() == str(bindir)


def test_llvm19_rejects_llvm20_on_path(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """LibHakkun's libc++ ABI is pinned at LLVM 19. An LLVM 20 install
    on PATH must be rejected with a clear reason — even though
    `clang --version` succeeded — so the wizard prompts the user to
    Auto-install the pinned LLVM alongside instead of overriding their
    existing install."""
    fake_run({"clang --version": (0, "clang version 20.1.0\n", "")})
    r = check_llvm19()
    assert not r.ok
    assert "LLVM 20" in r.detail or "20.1.0" in r.detail
    assert "19" in r.detail.lower() or "19" in r.note.lower()
    # The note must spell out that the system LLVM stays untouched —
    # otherwise users worry the wizard will downgrade them.
    assert "untouched" in r.note.lower() or "parallel" in r.note.lower()


def test_llvm19_rejects_llvm18_on_path(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """Pre-19 LLVM lacks C++23 features sail uses."""
    fake_run({"clang --version": (0, "clang version 18.1.8\n", "")})
    r = check_llvm19()
    assert not r.ok
    assert "18" in r.detail or "19" in r.detail


def test_llvm19_rejects_19_0_x_on_path(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """19.0.x predates libc++ features sail's host-binary uses; only
    19.1.x is accepted."""
    fake_run({"clang --version": (0, "clang version 19.0.0\n", "")})
    r = check_llvm19()
    assert not r.ok
    assert "19.1.x" in r.detail or "19.0" in r.detail


def test_llvm19_accepts_19_1_x_on_path(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """A developer with their own LLVM 19.1.7 on PATH should skip the
    ~3.3 GB download — accept as long as it's in the 19.1.x range."""
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which",
                        lambda name: "C:/Program Files/LLVM/bin/clang.exe" if name == "clang" else None)
    fake_run({"clang --version": (0, "clang version 19.1.7\n", "")})
    r = check_llvm19()
    assert r.ok
    assert "19.1.7" in r.detail
    assert "PATH" in r.detail
    # resolved_llvm_bin should point at the PATH-resolved dir.
    assert prereqs.resolved_llvm_bin() == str(
        Path("C:/Program Files/LLVM/bin/clang.exe").parent
    )


def test_llvm19_portable_install_wrong_version_surfaces_actionable_error(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """A stale portable install (e.g. user manually replaced clang.exe
    with a different version, or a future version bump changed the
    pinned major) must surface a clear remediation — delete the
    portable dir + Auto-install — rather than silently falling back."""
    llvm_root = tmp_path / "llvm"
    bindir = llvm_root / "bin"
    bindir.mkdir(parents=True)
    clang = bindir / "clang.exe"
    clang.write_text("")
    fake_run({f"{clang} --version": (0, "clang version 20.0.0\n", "")})

    r = check_llvm19()
    assert not r.ok
    # Note should mention deleting the portable dir.
    assert "SMOArchipelago" in r.note
    assert "delete" in r.note.lower() or "remove" in r.note.lower()


# ---------- check_winlibs ----------

def test_winlibs_missing(isolated_portable_roots, monkeypatch, fake_run) -> None:
    """No g++ at any of the probe paths → not ok with auto-install note."""
    # Point all probes at locations that don't exist.
    monkeypatch.setattr(
        prereqs, "_winlibs_probe_paths",
        lambda: [
            ("portable", Path("/nope/winlibs-not-real/bin/g++.exe")),
            ("system", Path("/nope/msys2-not-real/bin/g++.exe")),
        ],
    )
    fake_run({})
    r = check_winlibs()
    assert not r.ok
    assert r.auto_installable
    assert "WinLibs" in r.note or "auto-install" in r.note.lower()


def test_winlibs_portable_install_wins(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """When the portable WinLibs install is present, it's resolved
    even if a system msys2 is also available (predictable: wizard
    always uses what IT installed)."""
    portable = tmp_path / "winlibs" / "bin" / "g++.exe"
    system = tmp_path / "msys2" / "mingw64" / "bin" / "g++.exe"
    for p in (portable, system):
        p.parent.mkdir(parents=True)
        p.write_text("")
    monkeypatch.setattr(
        prereqs, "_winlibs_probe_paths",
        lambda: [
            ("portable", portable),
            ("C:\\msys64", system),
        ],
    )
    fake_run({
        f"{portable} --version": (0, "g++ (GCC) 16.1.0 (winlibs)\n", ""),
        f"{system} --version": (0, "g++ (mingw-w64) 14.0.0\n", ""),
    })

    r = check_winlibs()
    assert r.ok
    assert "winlibs" in r.detail.lower() or "16.1.0" in r.detail
    assert "portable" in r.detail
    assert prereqs.resolved_mingw_bin() == str(portable.parent)


def test_winlibs_falls_through_to_msys2(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """A user with msys2 mingw64 g++ at the standard path
    (`C:\\msys64\\mingw64\\bin\\g++.exe`) should NOT need to re-download
    WinLibs — the existing install is accepted. Coexistence guarantee."""
    msys2 = tmp_path / "msys2" / "mingw64" / "bin" / "g++.exe"
    msys2.parent.mkdir(parents=True)
    msys2.write_text("")
    monkeypatch.setattr(
        prereqs, "_winlibs_probe_paths",
        lambda: [
            ("portable", Path("/nope/no-portable-install/bin/g++.exe")),
            ("C:\\msys64", msys2),
        ],
    )
    fake_run({f"{msys2} --version": (0, "g++ (Rev1, Built by MSYS2 project) 13.2.0\n", "")})

    r = check_winlibs()
    assert r.ok
    assert "msys" in r.detail.lower() or "C:\\msys64" in r.detail or "g++" in r.detail.lower()


def test_winlibs_existing_install_not_re_downloaded(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """Pin a coexistence guarantee: if either the portable OR the
    system install is usable, the detector flips green and does NOT
    flag the row for the auto-install button (`ok=True` keeps the
    Auto-install button suppressed)."""
    sys_g = tmp_path / "winlibs" / "mingw64" / "bin" / "g++.exe"
    sys_g.parent.mkdir(parents=True)
    sys_g.write_text("")
    monkeypatch.setattr(
        prereqs, "_winlibs_probe_paths",
        lambda: [
            ("portable", Path("/nope/no-portable-install/bin/g++.exe")),
            ("C:\\winlibs", sys_g),
        ],
    )
    fake_run({f"{sys_g} --version": (0, "g++ (Rev1) 14.0.0\n", "")})
    r = check_winlibs()
    assert r.ok


# ---------- check_sail_python_deps ----------

def test_sail_deps_marker_short_circuits(
    isolated_portable_roots, monkeypatch, tmp_path, fake_run,
) -> None:
    """Once installed, the wizard writes a marker file. Subsequent
    detector calls skip the import-probe — faster and avoids spawning
    a Python subprocess on every Re-check."""
    marker = tmp_path / "sail_python_deps.ok"
    marker.write_text("ok\n")
    fake_run({})  # no Python on PATH — proves the marker short-circuits
    r = check_sail_python_deps()
    assert r.ok
    assert "marker" in r.detail


def test_sail_deps_probes_via_python_import(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """No marker → probe by `python -c 'import elftools, mmh3, lz4'`.
    Success writes the marker so the next call short-circuits."""
    monkeypatch.setattr(prereqs, "_winget_python312_commands", lambda: [])
    fake_run({
        "py -3.12 -c import elftools, mmh3, lz4": (0, "", ""),
    })
    r = check_sail_python_deps()
    assert r.ok
    assert prereqs.sail_deps_marker_path().is_file()


def test_sail_deps_missing(
    isolated_portable_roots, monkeypatch, fake_run,
) -> None:
    """All Python candidates fail the import → not ok with auto-install
    note. The detail names ModuleNotFoundError when we have one."""
    monkeypatch.setattr(prereqs, "_winget_python312_commands", lambda: [])
    fake_run({
        "py -3.12 -c import elftools, mmh3, lz4": (
            1, "", "ModuleNotFoundError: No module named 'elftools'\n",
        ),
    })
    r = check_sail_python_deps()
    assert not r.ok
    assert r.auto_installable
    assert "pyelftools" in r.note or "pip install" in r.note
    # Marker should NOT have been written on failure.
    assert not prereqs.sail_deps_marker_path().is_file()


# ---------- check_cmake ----------

@pytest.fixture
def cmake_path_is_kitware(monkeypatch):
    """Default `shutil.which("cmake")` to point at a Kitware install for
    cmake tests that exercise the bare-name PATH fallback. Without this
    the test machine's REAL `shutil.which` runs — and on devkitPro-equipped
    machines that resolves to msys2's cmake, which `check_cmake` now
    correctly rejects (see `test_cmake_rejects_msys2_path_fallback`)."""
    monkeypatch.setattr(
        prereqs.shutil, "which",
        lambda name: "C:\\Program Files\\CMake\\bin\\cmake.exe",
    )


def test_cmake_modern_version(fake_run, cmake_path_is_kitware) -> None:
    fake_run({"cmake --version": (
        0, "cmake version 3.30.5\nCMake suite maintained by Kitware\n", "")})
    r = check_cmake()
    assert r.ok
    assert "3.30.5" in r.detail


def test_cmake_too_old(fake_run) -> None:
    fake_run({"cmake --version": (0, "cmake version 3.20.1\n", "")})
    r = check_cmake()
    assert not r.ok
    assert "too old" in r.detail
    assert "3.24" in r.name


def test_cmake_unparseable_output(fake_run) -> None:
    fake_run({"cmake --version": (0, "garbage from a wrapper\n", "")})
    r = check_cmake()
    assert not r.ok
    assert "couldn't parse" in r.detail


def test_cmake_missing(fake_run) -> None:
    fake_run({})
    r = check_cmake()
    assert not r.ok
    assert r.install_url


def test_cmake_3_24_0_exact_boundary(fake_run, cmake_path_is_kitware) -> None:
    """3.24.0 should be accepted (>= 3.24)."""
    fake_run({"cmake --version": (0, "cmake version 3.24.0\n", "")})
    r = check_cmake()
    assert r.ok


def test_cmake_prefers_windows_native_over_path(monkeypatch, tmp_path, fake_run) -> None:
    """Regression test for v0.1.8-alpha build crash:
      CMake Error: The source directory "/c/.../C:/Users/.../switch_mod" does not exist.

    Root cause: devkitPro's installer prepends `C:\\devkitPro\\msys2\\usr\\bin`
    to PATH. A bare `cmake` then resolves to msys2's posix-style cmake,
    which treats `:` as a path separator (not a drive-letter marker) and
    mangles `C:\\Users\\...` into `/c/cwd/C:/Users/...`. The Windows-native
    Kitware CMake at C:/Program Files/CMake/bin/cmake.exe handles
    drive letters correctly. Detector must probe canonical install paths
    FIRST, fall back to PATH only when none of them exist."""
    fake_cmake = tmp_path / "kitware-cmake.exe"
    fake_cmake.write_text("")  # exists() must be True
    monkeypatch.setattr(prereqs, "_CMAKE_DEFAULT_PATHS", (fake_cmake,))
    fake_run({
        f"{fake_cmake} --version": (0, "cmake version 3.30.5\n", ""),
        "cmake --version": (0, "cmake version 3.28.0\n", ""),  # would-be msys2 fallback
    })
    r = check_cmake()
    assert r.ok
    assert "3.30.5" in r.detail, (
        f"expected Windows-native cmake (3.30.5), got {r.detail!r}; "
        f"PATH-fallback 3.28.0 should have been skipped"
    )
    assert prereqs.resolved_cmake() == str(fake_cmake), (
        f"resolved_cmake() must return the Windows-native path so the "
        f"build step doesn't re-resolve to msys2's cmake via PATH"
    )


def test_cmake_falls_back_to_path_when_no_windows_install(
    monkeypatch, fake_run,
) -> None:
    """No Kitware cmake installed at the canonical Windows paths → use
    whatever's on PATH. Most users have it via PATH only; that case has
    to stay working."""
    monkeypatch.setattr(prereqs, "_CMAKE_DEFAULT_PATHS", ())  # empty probe list
    monkeypatch.setattr(
        prereqs.shutil, "which",
        lambda name: "C:\\Program Files\\CMake\\bin\\cmake.exe",
    )
    fake_run({"cmake --version": (0, "cmake version 3.30.5\n", "")})
    r = check_cmake()
    assert r.ok
    assert prereqs.resolved_cmake() == "cmake"


def test_cmake_rejects_msys2_path_fallback(monkeypatch, fake_run) -> None:
    """msys2 cmake (posix path semantics) mangles `C:\\…` paths and
    must be rejected during bare-name PATH fallback, with an
    actionable winget-install note."""
    monkeypatch.setattr(prereqs, "_CMAKE_DEFAULT_PATHS", ())  # no Kitware install
    monkeypatch.setattr(prereqs, "_resolved_cmake", None)
    monkeypatch.setattr(
        prereqs.shutil, "which",
        lambda name: "C:\\devkitPro\\msys2\\usr\\bin\\cmake.exe",
    )
    fake_run({"cmake --version": (0, "cmake version 3.28.0\n", "")})
    r = check_cmake()
    assert not r.ok, "msys2 cmake must not satisfy the cmake prereq"
    assert "msys2" in r.detail.lower()
    assert r.install_url, "Install... button needs a URL"
    assert "winget install Kitware.CMake" in r.note
    note_lower = r.note.lower()
    assert ("auto-install" in note_lower
            or "probes" in note_lower
            or "no shell restart" in note_lower), (
        "note must surface either the auto-install path or the direct-probe "
        "fact so the user knows their remediation options without a wizard "
        f"restart. got: {r.note!r}"
    )
    assert prereqs.resolved_cmake() == "cmake", (
        "no working cmake was found, so resolved_cmake() should fall "
        "back to the bare-name default rather than caching a known-bad path"
    )


def test_resolved_cmake_defaults_to_bare_name_when_check_not_run(monkeypatch) -> None:
    """Callers that bypass check_cmake (tests, direct REPL usage) get a
    sensible fallback rather than a None-deref."""
    monkeypatch.setattr(prereqs, "_resolved_cmake", None)
    assert prereqs.resolved_cmake() == "cmake"


# ---------- check_ninja ----------

def test_ninja_present(fake_run, monkeypatch) -> None:
    monkeypatch.setattr(prereqs, "_winget_ninja_paths", lambda: [])
    fake_run({"ninja --version": (0, "1.12.1\n", "")})
    r = check_ninja()
    assert r.ok
    assert "1.12.1" in r.detail
    assert r.auto_installable


def test_ninja_missing(fake_run, monkeypatch) -> None:
    monkeypatch.setattr(prereqs, "_winget_ninja_paths", lambda: [])
    fake_run({})
    r = check_ninja()
    assert not r.ok
    assert r.auto_installable
    assert "winget install Ninja-build.Ninja" in r.note
    assert "Auto-install" in r.note or "auto-install" in r.note.lower()


def test_ninja_found_at_winget_path_even_when_not_on_path(
    monkeypatch, tmp_path, fake_run,
) -> None:
    """Manual-mode user runs `winget install Ninja-build.Ninja` in a
    separate terminal, then clicks Re-check; the detector's
    deterministic-path probe flips the row green without a wizard
    restart."""
    pkg_dir = tmp_path / "Ninja-build.Ninja_winget_x64"
    pkg_dir.mkdir(parents=True)
    ninja = pkg_dir / "ninja.exe"
    ninja.write_text("")
    monkeypatch.setattr(prereqs, "_winget_ninja_paths", lambda: [ninja])
    fake_run({f"{ninja} --version": (0, "1.12.1\n", "")})
    monkeypatch.setenv("PATH", "")

    r = check_ninja()
    assert r.ok, f"detector should find ninja at winget path; detail={r.detail!r}"
    assert "1.12.1" in r.detail
    assert str(ninja) in r.detail
    import os
    assert str(pkg_dir) in os.environ["PATH"].split(os.pathsep)


# ---------- check_hactool ----------

def test_hactool_present(monkeypatch, tmp_path) -> None:
    hac = tmp_path / "hactool.exe"
    hac.write_text("")
    monkeypatch.setattr("shutil.which", lambda name: str(hac) if name in (
        "hactool", "hactool.exe") else None)
    r = check_hactool()
    assert r.ok
    assert str(hac) in r.detail


def test_hactool_missing_surfaces_picker(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = check_hactool()
    assert not r.ok
    assert r.picker_label
    assert r.picker_filter


def test_hactool_user_picked_path_used(monkeypatch, tmp_path) -> None:
    user_picked = tmp_path / "MyTools" / "hactool.exe"
    user_picked.parent.mkdir(parents=True)
    user_picked.write_text("")
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = check_hactool(override_path=user_picked)
    assert r.ok
    assert str(user_picked) in r.detail
    assert "user-picked" in r.detail


def test_hactool_stale_user_picked_path_falls_back_to_path(
    monkeypatch, tmp_path,
) -> None:
    stale = tmp_path / "deleted" / "hactool.exe"
    hac = tmp_path / "hactool.exe"
    hac.write_text("")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: str(hac) if name in ("hactool", "hactool.exe") else None,
    )
    r = check_hactool(override_path=stale)
    assert r.ok
    assert str(hac) in r.detail


def test_hactool_stale_picked_path_and_no_path_fails_clearly(
    monkeypatch, tmp_path,
) -> None:
    stale = tmp_path / "deleted" / "hactool.exe"
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = check_hactool(override_path=stale)
    assert not r.ok
    assert str(stale) in r.detail
    assert r.picker_label


def test_check_all_threads_hactool_override(
    isolated_portable_roots, monkeypatch, tmp_path,
) -> None:
    user_picked = tmp_path / "hactool.exe"
    user_picked.write_text("")
    monkeypatch.setattr("shutil.which", lambda name: None)

    results = check_all(hactool_override=user_picked)
    hactool = next(r for r in results if r.key == "hactool")
    assert hactool.ok
    assert str(user_picked) in hactool.detail


# ---------- check_prod_keys ----------

def test_prod_keys_present(monkeypatch, tmp_path) -> None:
    home = tmp_path / "userhome"
    (home / ".switch").mkdir(parents=True)
    (home / ".switch" / "prod.keys").write_text("# keys\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    r = check_prod_keys()
    assert r.ok


def test_prod_keys_missing_surfaces_picker(monkeypatch, tmp_path) -> None:
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    r = check_prod_keys()
    assert not r.ok
    assert r.install_url == ""
    assert "Lockpick" in r.detail
    assert r.picker_label
    assert r.picker_filter


def test_prod_keys_user_picked_path_used(monkeypatch, tmp_path) -> None:
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_picked = tmp_path / "elsewhere" / "prod.keys"
    user_picked.parent.mkdir(parents=True)
    user_picked.write_text("# keys\n")
    r = check_prod_keys(override_path=user_picked)
    assert r.ok
    assert str(user_picked) in r.detail
    assert "user-picked" in r.detail


def test_prod_keys_user_picked_wins_over_default(monkeypatch, tmp_path) -> None:
    home = tmp_path / "userhome"
    (home / ".switch").mkdir(parents=True)
    (home / ".switch" / "prod.keys").write_text("# default keys\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_picked = tmp_path / "elsewhere" / "prod.keys"
    user_picked.parent.mkdir(parents=True)
    user_picked.write_text("# user keys\n")
    r = check_prod_keys(override_path=user_picked)
    assert r.ok
    assert str(user_picked) in r.detail


def test_prod_keys_stale_user_picked_path_falls_back_to_default(
    monkeypatch, tmp_path,
) -> None:
    home = tmp_path / "userhome"
    (home / ".switch").mkdir(parents=True)
    (home / ".switch" / "prod.keys").write_text("# keys\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    stale = tmp_path / "deleted" / "prod.keys"
    r = check_prod_keys(override_path=stale)
    assert r.ok
    assert str(home / ".switch" / "prod.keys") in r.detail


def test_prod_keys_stale_picked_and_no_default_fails_clearly(
    monkeypatch, tmp_path,
) -> None:
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    stale = tmp_path / "deleted" / "prod.keys"
    r = check_prod_keys(override_path=stale)
    assert not r.ok
    assert str(stale) in r.detail
    assert r.picker_label


def test_check_all_threads_prod_keys_override(
    isolated_portable_roots, monkeypatch, tmp_path,
) -> None:
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_picked = tmp_path / "prod.keys"
    user_picked.write_text("# keys\n")

    results = check_all(prod_keys_override=user_picked)
    prodkeys = next(r for r in results if r.key == "prodkeys")
    assert prodkeys.ok
    assert str(user_picked) in prodkeys.detail


# ---------- check_all / all_ok ----------

def test_check_all_runs_every_detector(isolated_portable_roots) -> None:
    """The detector list must include the Hakkun-era cross-compile
    trio (LLVM 19 + WinLibs + sail Python deps) in addition to the
    legacy shared deps. devkitpro is REMOVED — pin that here so a
    future re-introduction surfaces as a test failure."""
    results = check_all()
    keys = {r.key for r in results}
    assert {"llvm19", "winlibs", "sail_python_deps",
            "cmake", "ninja", "python312",
            "hactool", "prodkeys"} <= keys
    assert "devkitpro" not in keys, (
        "devkitPro detector was removed in the Hakkun cutover; "
        "re-adding it without rewriting the toolchain story is a bug"
    )


def test_all_ok_aggregate() -> None:
    assert all_ok([
        PrereqResult("a", "A", True, ""),
        PrereqResult("b", "B", True, ""),
    ])
    assert not all_ok([
        PrereqResult("a", "A", True, ""),
        PrereqResult("b", "B", False, "missing"),
    ])
    assert all_ok([])  # vacuously true; check_all() should never return empty
