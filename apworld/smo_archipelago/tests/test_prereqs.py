"""Tests for `_setup.prereqs` — the detectors that drive the wizard's
Prereq-check page.

All shell-outs go through `_setup.prereqs._run`; we monkeypatch it per-test
to script success / failure without touching the user's actual machine.
Filesystem-touching detectors are tested by manipulating `Path.home`
(prod.keys check) or `os.environ` (devkitPro check) via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from _setup import prereqs
from _setup.prereqs import (
    PrereqResult,
    all_ok,
    check_all,
    check_cmake,
    check_devkitpro,
    check_hactool,
    check_ninja,
    check_prod_keys,
    check_python312,
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
    wizard. The deterministic path also prepends to PATH so the
    extract step's `_python_invoker` finds `py.exe` for bare-name
    `shutil.which("py")` lookup."""
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


# ---------- check_devkitpro ----------

def test_devkitpro_env_missing_and_no_default(monkeypatch) -> None:
    """No DEVKITPRO + no install at the default paths → not ok, and the
    detail enumerates the default paths that WERE checked so the user
    knows where to install (or which path-probe to extend if their
    layout is non-standard)."""
    monkeypatch.delenv("DEVKITPRO", raising=False)
    # Point default-path probe at locations that don't exist so the test
    # is independent of whether the machine running it has devkitPro.
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS",
                        (Path("/nope/devkitpro-not-real-1"),
                         Path("/nope/devkitpro-not-real-2")))
    r = check_devkitpro()
    assert not r.ok
    assert "not found" in r.detail
    assert "devkitpro-not-real-1" in r.detail
    assert "devkitpro-not-real-2" in r.detail


def test_devkitpro_env_missing_but_found_at_default(
    monkeypatch, tmp_path, fake_run,
) -> None:
    """No DEVKITPRO env var, but a valid install exists at C:/devkitPro
    (or /opt/devkitpro etc) — fall back to it AND set the env var so
    downstream cmake subprocesses inherit the path."""
    monkeypatch.delenv("DEVKITPRO", raising=False)
    # Build a fake devkitPro tree at a tmp location, then point the
    # default-path probe at it.
    fake_root = tmp_path / "devkitPro"
    bindir = fake_root / "devkitA64" / "bin"
    bindir.mkdir(parents=True)
    gxx = bindir / "aarch64-none-elf-g++.exe"
    gxx.write_text("")
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS", (fake_root,))
    fake_run({f"{gxx} --version": (0, "g++ (devkitA64) 15.1.0\n", "")})

    r = check_devkitpro()
    assert r.ok
    # Detector must mutate env so cmake child processes inherit it.
    import os
    assert os.environ.get("DEVKITPRO") == str(fake_root)


def test_devkitpro_env_set_binary_present(monkeypatch, tmp_path, fake_run) -> None:
    # Build a fake devkitPro tree with the cross-compiler.
    bindir = tmp_path / "devkitA64" / "bin"
    bindir.mkdir(parents=True)
    gxx = bindir / "aarch64-none-elf-g++.exe"
    gxx.write_text("")
    monkeypatch.setenv("DEVKITPRO", str(tmp_path))
    fake_run({f"{gxx} --version": (0, "g++ (devkitA64) 15.1.0\n", "")})
    r = check_devkitpro()
    assert r.ok
    assert "g++" in r.detail


def test_devkitpro_env_set_binary_missing(monkeypatch, tmp_path) -> None:
    # Env points at empty dir AND no default-path install exists either —
    # installer aborted or got cleaned up.
    monkeypatch.setenv("DEVKITPRO", str(tmp_path))
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS",
                        (Path("/nope/devkitpro-not-real"),))
    r = check_devkitpro()
    assert not r.ok
    assert "not found" in r.detail
    assert "DEVKITPRO env var" in r.detail


def test_devkitpro_env_set_to_msys2_path_falls_back_to_default(
    monkeypatch, tmp_path, fake_run,
) -> None:
    """The devkitPro Windows installer sets DEVKITPRO=/opt/devkitpro (its
    msys2-rooted convention path) which is meaningless to a native-Windows
    Python process. The detector must fall through to the well-known
    default install root (typically C:/devkitPro) — and overwrite
    os.environ["DEVKITPRO"] so the downstream cmake child process gets
    the resolved-working path instead of the broken msys2-form value.

    Regression test for v0.1.5-alpha bug report: prereq page showed
    "DEVKITPRO=/opt/devkitpro but aarch64-none-elf-g++ not found
    (install incomplete?)" when devkitPro was actually installed at the
    canonical C:/devkitPro location."""
    # The env var points at a path that doesn't resolve to an install.
    monkeypatch.setenv("DEVKITPRO", "/opt/devkitpro")
    # The "real" install lives at a default path. Use tmp_path stand-in.
    fake_root = tmp_path / "devkitPro"
    bindir = fake_root / "devkitA64" / "bin"
    bindir.mkdir(parents=True)
    gxx = bindir / "aarch64-none-elf-g++.exe"
    gxx.write_text("")
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS", (fake_root,))
    fake_run({f"{gxx} --version": (0, "g++ (devkitA64) 15.2.0\n", "")})

    r = check_devkitpro()
    assert r.ok, (
        f"detector should fall through from broken env-var value to default "
        f"install path. detail={r.detail!r}"
    )
    # Critical: the env var must be REWRITTEN to the resolved path so
    # cmake doesn't choke on the bogus /opt/devkitpro value.
    import os
    assert os.environ["DEVKITPRO"] == str(fake_root)


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
    """Regression test for v0.1.x-alpha wizard build crash on a user
    machine where ONLY msys2 cmake (from devkitPro's bundled msys2) is
    available:
      CMake Error: The source directory "/c/ProgramData/Archipelago/
        C:/Users/.../switch_mod" does not exist.

    Root cause: when Kitware's CMake isn't installed at the canonical
    `C:/Program Files/CMake/bin/cmake.exe` location, `check_cmake`
    falls back to bare `cmake` on PATH. devkitPro prepends its msys2
    bin dir to PATH, so the resolved cmake mangles `C:\\…` into
    `/cwd/C:/…` and the build dies before configure finishes.

    Expected behavior: detector must REJECT msys2 cmake during the
    bare-name fallback (even though `cmake --version` succeeds), and
    surface an actionable install-Kitware-CMake error."""
    monkeypatch.setattr(prereqs, "_CMAKE_DEFAULT_PATHS", ())  # no Kitware install
    monkeypatch.setattr(prereqs, "_resolved_cmake", None)
    # `shutil.which("cmake")` resolves to devkitPro's msys2 cmake — the
    # exact path layout Kayla's machine had.
    monkeypatch.setattr(
        prereqs.shutil, "which",
        lambda name: "C:\\devkitPro\\msys2\\usr\\bin\\cmake.exe",
    )
    fake_run({"cmake --version": (0, "cmake version 3.28.0\n", "")})
    r = check_cmake()
    assert not r.ok, "msys2 cmake must not satisfy the cmake prereq"
    assert "msys2" in r.detail.lower()
    assert r.install_url, "Install... button needs a URL"
    # winget one-liner is the load-bearing remediation the wizard surfaces
    # beneath the row — losing it from the note would silently degrade the
    # error from "here's the fix" back to "go figure out CMake yourself".
    assert "winget install Kitware.CMake" in r.note
    # The note must point the user at the wizard's auto-install path OR
    # explain that the wizard now probes the install dir directly (no
    # shell restart needed). Earlier versions of the note relied on a
    # "CLOSE and REOPEN this app" reminder because the wizard couldn't
    # see winget-installed cmake without a PATH refresh; that's no
    # longer true now that `_CMAKE_DEFAULT_PATHS` is probed ahead of PATH.
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
    # No winget-deterministic install — fall through to PATH probe.
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
    # The Windows-easy install path must be load-bearing in the failure
    # surface; the Auto-install reminder is the new equivalent of the
    # earlier restart-reminder (we no longer require a wizard restart
    # because the detector probes the winget install dir directly).
    assert "winget install Ninja-build.Ninja" in r.note
    assert "Auto-install" in r.note or "auto-install" in r.note.lower()


def test_ninja_found_at_winget_path_even_when_not_on_path(
    monkeypatch, tmp_path, fake_run,
) -> None:
    """Regression test for the one-click-installer flow: a manual-mode
    user runs `winget install Ninja-build.Ninja` in a separate terminal,
    then clicks Re-check in the wizard. Without the deterministic-path
    probe, the wizard's already-running process has a stale PATH and
    `_safe_run(["ninja", "--version"])` returns "not found" even though
    winget just installed it. With the probe, the row turns green
    immediately and the parent dir gets prepended to PATH so the
    downstream `cmake --build` step finds ninja too."""
    # Fake winget install dir with ninja.exe.
    pkg_dir = tmp_path / "Ninja-build.Ninja_winget_x64"
    pkg_dir.mkdir(parents=True)
    ninja = pkg_dir / "ninja.exe"
    ninja.write_text("")
    monkeypatch.setattr(prereqs, "_winget_ninja_paths", lambda: [ninja])
    fake_run({f"{ninja} --version": (0, "1.12.1\n", "")})
    # Explicitly empty PATH — proves the detector doesn't need the dir
    # on PATH to find the binary.
    monkeypatch.setenv("PATH", "")

    r = check_ninja()
    assert r.ok, f"detector should find ninja at winget path; detail={r.detail!r}"
    assert "1.12.1" in r.detail
    assert str(ninja) in r.detail
    # Side effect: parent dir must have been prepended to PATH for
    # downstream subprocess invocations (cmake's bare-name `ninja` spawn).
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
    """Failure case must include a `picker_label` so the wizard can
    render a Browse button. hactool is the canonical not-installed-via-
    installer case; PATH-only detection is too strict on Windows."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = check_hactool()
    assert not r.ok
    assert r.picker_label, "missing picker_label — wizard can't render Browse button"
    assert r.picker_filter, "missing picker_filter — file dialog needs an extension filter"


def test_hactool_user_picked_path_used(monkeypatch, tmp_path) -> None:
    """When the user has used the wizard's Browse button to point at a
    hactool.exe, that path wins even when hactool isn't on PATH."""
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
    """If the persisted user-picked path no longer exists but hactool is
    on PATH, prefer PATH rather than locking the user out. Persisted
    state can rot; PATH is authoritative for the current shell."""
    stale = tmp_path / "deleted" / "hactool.exe"  # never created
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
    """Both override and PATH missing → fail with a detail that names
    the missing override (so the user understands what to re-pick)."""
    stale = tmp_path / "deleted" / "hactool.exe"
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = check_hactool(override_path=stale)
    assert not r.ok
    assert str(stale) in r.detail
    assert r.picker_label


def test_check_all_threads_hactool_override(monkeypatch, tmp_path) -> None:
    """check_all must forward the persisted hactool path through to
    the per-detector function — without this the wizard's persistence
    has no effect."""
    user_picked = tmp_path / "hactool.exe"
    user_picked.write_text("")
    monkeypatch.setattr("shutil.which", lambda name: None)
    # Also stub the devkitpro probe so check_all doesn't fail on
    # machines without devkitPro installed at the default location.
    monkeypatch.delenv("DEVKITPRO", raising=False)
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS", ())

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
    """Failure case must include a `picker_label` so the wizard can render
    a Browse button. Not every user keeps prod.keys at the hactool default
    `~/.switch/prod.keys` — emulator users especially keep them with their
    emulator config."""
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    r = check_prod_keys()
    assert not r.ok
    # No canonical Lockpick_RCM URL — the upstream repo was archived
    # and active forks are too volatile to hard-code. The detail string
    # still tells the user what tool to search for; the wizard surfaces a
    # Browse button instead of a link.
    assert r.install_url == ""
    assert "Lockpick" in r.detail
    assert r.picker_label, "missing picker_label — wizard can't render Browse button"
    assert r.picker_filter, "missing picker_filter — file dialog needs an extension filter"


def test_prod_keys_user_picked_path_used(monkeypatch, tmp_path) -> None:
    """When the user has pointed the wizard's Browse button at a prod.keys,
    that path wins even when the default `~/.switch/prod.keys` is absent."""
    home = tmp_path / "userhome"
    home.mkdir()  # no .switch subdir → default location missing
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_picked = tmp_path / "elsewhere" / "prod.keys"
    user_picked.parent.mkdir(parents=True)
    user_picked.write_text("# keys\n")
    r = check_prod_keys(override_path=user_picked)
    assert r.ok
    assert str(user_picked) in r.detail
    assert "user-picked" in r.detail


def test_prod_keys_user_picked_wins_over_default(monkeypatch, tmp_path) -> None:
    """If the user has explicitly picked a prod.keys, prefer it over the
    one at the default location — they may be intentionally pointing at a
    different keyset (e.g. a separate Switch's dump)."""
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
    """If the persisted user-picked path no longer exists but the default
    `~/.switch/prod.keys` is present, fall back to the default rather than
    locking the user out. Persisted state can rot; the default location
    is still authoritative if it exists."""
    home = tmp_path / "userhome"
    (home / ".switch").mkdir(parents=True)
    (home / ".switch" / "prod.keys").write_text("# keys\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    stale = tmp_path / "deleted" / "prod.keys"  # never created
    r = check_prod_keys(override_path=stale)
    assert r.ok
    assert str(home / ".switch" / "prod.keys") in r.detail


def test_prod_keys_stale_picked_and_no_default_fails_clearly(
    monkeypatch, tmp_path,
) -> None:
    """Both override and default missing → fail with a detail that names
    the missing override (so the user understands what to re-pick)."""
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    stale = tmp_path / "deleted" / "prod.keys"
    r = check_prod_keys(override_path=stale)
    assert not r.ok
    assert str(stale) in r.detail
    assert r.picker_label


def test_check_all_threads_prod_keys_override(monkeypatch, tmp_path) -> None:
    """check_all must forward the persisted prod_keys path through to the
    per-detector function — without this the wizard's persistence has no
    effect."""
    home = tmp_path / "userhome"
    home.mkdir()  # no default keys present
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_picked = tmp_path / "prod.keys"
    user_picked.write_text("# keys\n")
    # Stub the devkitpro probe so check_all doesn't fail on machines
    # without devkitPro installed at the default location.
    monkeypatch.delenv("DEVKITPRO", raising=False)
    monkeypatch.setattr(prereqs, "_DEVKITPRO_DEFAULT_ROOTS", ())

    results = check_all(prod_keys_override=user_picked)
    prodkeys = next(r for r in results if r.key == "prodkeys")
    assert prodkeys.ok
    assert str(user_picked) in prodkeys.detail


# ---------- check_all / all_ok ----------

def test_check_all_runs_every_detector() -> None:
    results = check_all()
    keys = {r.key for r in results}
    assert {"devkitpro", "cmake", "ninja", "python312",
            "hactool", "prodkeys"} <= keys


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
