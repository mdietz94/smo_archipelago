"""Tests for `scripts/install_apworld.py` — both the default behavior
(apworld only) and the release-build behavior (--bundle-mod / --bundle-scripts).

These tests invoke install_apworld.py as a subprocess against the real repo
layout and inspect the resulting zip. Doing it as a subprocess (vs. importing
and calling main directly) catches argument-parsing and exit-code regressions.

Skipped when run outside the repo (e.g. against a zip-installed apworld via
the in-zip tests/, which we don't ship — tests are excluded from the zip).

`--bundle-mod` tests self-skip when `switch-mod/lunakit-vendor/` isn't
populated — the python-unit CI job intentionally doesn't init the C++
submodules (they're AArch64-only and the host tests don't use them); the
release workflow does init them, so the bundling code is still exercised
end-to-end when it actually matters. Per-submodule guard rather than a
single broad skip so `--bundle-scripts` keeps running even when the
switch-mod submodule is absent.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

# REPO is the actual checkout root: tests/ → smo_archipelago/ → apworld/ → REPO.
REPO = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = REPO / "scripts" / "install_apworld.py"
OUTPUT_PATH = REPO / "vendor" / "Archipelago" / "custom_worlds" / "smo.apworld"
LUNAKIT_TOOLCHAIN = REPO / "switch-mod" / "lunakit-vendor" / "cmake" / "toolchain.cmake"


def _switch_mod_submodule_present() -> bool:
    """True iff the switch-mod submodules are populated (toolchain.cmake
    is the sentinel — install_apworld.py also checks this exact file)."""
    return LUNAKIT_TOOLCHAIN.exists()


# Module-level skip marker for tests that need the C++ submodules.
needs_switch_mod_submodule = pytest.mark.skipif(
    not _switch_mod_submodule_present(),
    reason=f"switch-mod submodules not populated ({LUNAKIT_TOOLCHAIN} "
           f"missing); run `git submodule update --init --recursive` to "
           f"enable --bundle-mod tests",
)


def _run_install(args: list[str]) -> tuple[int, str, str]:
    """Invoke install_apworld.py with `args`, returning (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    return result.returncode, result.stdout, result.stderr


def _zip_members(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


@pytest.fixture(scope="module")
def install_script_present() -> None:
    if not INSTALL_SCRIPT.exists():
        pytest.skip(f"install script missing at {INSTALL_SCRIPT}")


def test_default_install_succeeds(install_script_present) -> None:
    rc, out, err = _run_install([])
    assert rc == 0, f"exit {rc}: stdout={out!r} stderr={err!r}"
    assert OUTPUT_PATH.exists()


def test_default_install_excludes_nintendo_ip(install_script_present) -> None:
    """Smoke test for IP discipline: the four Nintendo-content JSONs must
    never end up in the released zip even when they exist locally."""
    rc, out, err = _run_install([])
    assert rc == 0
    members = _zip_members(OUTPUT_PATH)
    for forbidden in (
        "smo/client/data/shine_map.json",
        "smo/client/data/capture_map.json",
        "smo/client/data/shine_map_review.json",
        "smo/client/data/capture_map_review.json",
    ):
        assert forbidden not in members, (
            f"IP-blocked file leaked into zip: {forbidden}"
        )


def test_default_install_includes_core_apworld(install_script_present) -> None:
    rc, _, _ = _run_install([])
    assert rc == 0
    members = _zip_members(OUTPUT_PATH)
    for required in (
        "smo/__init__.py",
        "smo/client/main.py",
        "smo/client/maps.py",
        "smo/data/items.json",
        "smo/data/locations.json",
    ):
        assert required in members, f"missing {required}"


def test_default_install_excludes_setup_bundle(install_script_present) -> None:
    """Without --bundle-mod / --bundle-scripts, the heavy stuff should NOT
    be in the zip (saves AP-gen-host download for users who don't play)."""
    rc, _, _ = _run_install([])
    assert rc == 0
    members = _zip_members(OUTPUT_PATH)
    for not_required in (
        "smo/_setup/switch_mod/CMakeLists.txt",
        "smo/_setup/scripts/extract_shine_map.py",
    ):
        assert not_required not in members, (
            f"{not_required} should NOT be in the default-build zip"
        )


@needs_switch_mod_submodule
def test_bundle_mod_includes_switch_mod_sources(install_script_present) -> None:
    rc, out, err = _run_install(["--bundle-mod"])
    assert rc == 0, f"exit {rc}: stdout={out!r} stderr={err!r}"
    members = _zip_members(OUTPUT_PATH)
    for required in (
        "smo/_setup/switch_mod/CMakeLists.txt",
        "smo/_setup/switch_mod/src/main.cpp",
        "smo/_setup/switch_mod/lunakit-vendor/cmake/toolchain.cmake",
    ):
        assert required in members, f"missing {required}"


@needs_switch_mod_submodule
def test_bundle_mod_excludes_build_artifacts(install_script_present) -> None:
    rc, _, _ = _run_install(["--bundle-mod"])
    assert rc == 0
    members = _zip_members(OUTPUT_PATH)
    for forbidden in members:
        # No build outputs, no .git, no compiled binaries.
        assert "/build/" not in forbidden, f"build artifact leaked: {forbidden}"
        assert "/.git/" not in forbidden, f".git leaked: {forbidden}"
        assert not forbidden.endswith(".exe"), f".exe leaked: {forbidden}"
        assert not forbidden.endswith(".obj"), f".obj leaked: {forbidden}"


def test_bundle_scripts_includes_extractor(install_script_present) -> None:
    rc, _, _ = _run_install(["--bundle-scripts"])
    assert rc == 0
    members = _zip_members(OUTPUT_PATH)
    for required in (
        "smo/_setup/scripts/extract_shine_map.py",
        "smo/_setup/scripts/sync_capture_table.py",
    ):
        assert required in members, f"missing {required}"


@needs_switch_mod_submodule
def test_bundle_combined_produces_complete_release_zip(install_script_present) -> None:
    """The release CI calls with both flags. This is the 'full release zip'
    end-to-end test."""
    rc, out, err = _run_install(["--bundle-mod", "--bundle-scripts"])
    assert rc == 0, f"exit {rc}: stdout={out!r} stderr={err!r}"
    members = _zip_members(OUTPUT_PATH)
    # Apworld core
    assert "smo/__init__.py" in members
    # Wizard
    assert "smo/_setup/__init__.py" in members
    assert "smo/_setup/wizard.py" in members or "smo/_setup/prereqs.py" in members
    # Switch mod sources
    assert "smo/_setup/switch_mod/CMakeLists.txt" in members
    # Extractor scripts
    assert "smo/_setup/scripts/extract_shine_map.py" in members
    # NO Nintendo content
    assert "smo/client/data/shine_map.json" not in members
    assert "smo/client/data/capture_map.json" not in members
