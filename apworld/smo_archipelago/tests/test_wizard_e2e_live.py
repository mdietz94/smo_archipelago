"""End-to-end live-network wizard install test.

Replaces the disabled `clean-windows-audit` CI job. Where the CI job
tried (and failed) to do this from a cold windows-2022 runner inside
a 30-minute budget, this test runs on the maintainer's dev machine,
fired automatically by the `.githooks/pre-push` hook on every
`git push origin v*` tag push.

Gated on `SMOAP_LIVE_INSTALL=1` because it:
  - Downloads ~1 GB of toolchain (LLVM 19, WinLibs g++, hactool) from
    upstream github.com release URLs.
  - Pip-installs sail's host-Python deps (lz4, pyelftools, mmh3) into
    a PYTHONUSERBASE tempdir.
  - Runs a real cold switch-mod build (clang+ninja+sail; ~10 min).
  - Runs the audit walk against the resulting filesystem state.
Total wall time: ~15-20 min on broadband.

Sandbox strategy: three env vars route the wizard's three write roots
into tempdirs so a real install run leaves the user's actual machine
state byte-identical before/after.

  SMOAP_APPDATA_ROOT        -> %APPDATA%/SMOArchipelago/ (hactool, data,
                               setup_state, bundled extraction, wizard.log)
  SMOAP_LOCALAPPDATA_ROOT   -> %LOCALAPPDATA%/SMOArchipelago/ (LLVM 19,
                               WinLibs, sail deps marker)
  PYTHONUSERBASE            -> pip --user install root (sail's lz4/pyelftools/mmh3)

What this test does NOT exercise:
  - winget-installable prereqs (cmake, ninja, python312): these install
    system-wide, no per-process sandbox available. The test SKIPS if
    the user doesn't already have them on PATH.
  - The real extract phase: needs a 17 GB SMO 1.0.0 NSP and prod.keys,
    which are user-IP-sensitive and we can't bundle. We write stub maps
    via `release_audit.write_stub_maps` instead -- the extract phase
    is covered separately by test_shine_map_extraction.py against a
    real-NSP fixture the user opts into with SMOAP_NSP_FIXTURE_PATH.
  - The real deploy phase: would write to the user's SD card / Ryujinx
    mods dir, which is outside the sandbox model. Build outputs are
    validated; deploy is a `shutil.copy` step we trust.

If this test passes, every link in the chain from "fresh user machine"
to "subsdk9 ready to copy to SD" has been validated against current
upstream URLs, current pinned SHAs, current wizard orchestration, and
the real audit allowlist.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest


# The test is the entire opt-in payload of this file; if SMOAP_LIVE_INSTALL
# is unset (the default), skip at collection time so pytest doesn't even
# evaluate the test body's imports (which transitively touch wizard_cli
# internals we'd rather not pull in for fast unit-test runs).
pytestmark = pytest.mark.skipif(
    not os.environ.get("SMOAP_LIVE_INSTALL"),
    reason="opt-in live-install e2e test; set SMOAP_LIVE_INSTALL=1 (slow, network, ~1 GB download)",
)


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _require_winget_prereqs() -> None:
    """We can't sandbox winget installs (they're system-wide), so the
    test assumes cmake / ninja / python are already on PATH. Skip with
    a clear message if any is missing -- this isn't the test's job to
    validate; it's a documented prereq."""
    for tool in ("cmake", "ninja", "python"):
        if not shutil.which(tool):
            pytest.skip(
                f"prereq {tool!r} not on PATH. winget-installable; this test "
                f"assumes the maintainer's machine already has it. (The test "
                f"covers the LLVM + WinLibs + hactool + sail-deps installers; "
                f"the winget-driven ones are outside the sandbox model.)"
            )


@pytest.fixture
def short_sandbox_root() -> Path:
    """A tempdir at the root of the system drive instead of pytest's
    default `%TEMP%\\pytest-of-<user>\\pytest-NNN\\test_NAME\\...` path.

    Required because the switch-mod build's final ninja step chains
    sail + clang + cmake + python invocations through a single cmd.exe
    `&&` sequence that bakes the bundled-switch_mod path in literally
    several times -- and pytest's default tempdir is deep enough that
    the result exceeds Windows' ~8KB CommandLine limit, killing the
    build with "The command line is too long." Putting the sandbox at
    `C:\\smoape2e-XXXX\\` keeps the per-arg path under ~80 chars.
    """
    root = Path(tempfile.mkdtemp(prefix="smoape2e-", dir=os.environ.get("SYSTEMDRIVE", "C:") + "\\"))
    try:
        yield root
    finally:
        # `ignore_errors=True` so a half-cleaned-up sandbox doesn't fail
        # an otherwise-passing test. Leftover state under C:\smoape2e-*
        # is obvious enough that the maintainer can rm manually.
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(os.name != "nt",
                    reason="wizard is Windows-only; e2e test mirrors that")
def test_full_wizard_install_against_real_network(
    short_sandbox_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single, expensive, release-only test.

    Runs the wizard's actual install pipeline against real upstream URLs,
    then a real switch-mod build, then walks the result with the audit.
    """
    _require_winget_prereqs()

    # --- 1. Sandbox -----------------------------------------------------
    sandbox_appdata = short_sandbox_root / "appdata"
    sandbox_localappdata = short_sandbox_root / "localappdata"
    sandbox_pip_user = short_sandbox_root / "pythonuserbase"
    monkeypatch.setenv("SMOAP_APPDATA_ROOT", str(sandbox_appdata))
    monkeypatch.setenv("SMOAP_LOCALAPPDATA_ROOT", str(sandbox_localappdata))
    monkeypatch.setenv("PYTHONUSERBASE", str(sandbox_pip_user))

    # Detector caches are module-level. If a prior test in this session
    # populated them against the dev's real machine, the wizard's build
    # phase would resolve toolchain bins from those (real) caches rather
    # than the (sandboxed) live install. Reset before we start.
    from _setup import prereqs
    monkeypatch.setattr(prereqs, "_resolved_llvm_bin", None)
    monkeypatch.setattr(prereqs, "_resolved_mingw_bin", None)
    monkeypatch.setattr(prereqs, "_resolved_python312_bin", None)
    monkeypatch.setattr(prereqs, "_resolved_ninja_bin", None)
    monkeypatch.setattr(prereqs, "_resolved_cmake", None)

    # --- 2. Build the apworld zip so _extract_bundled_tree has a source -
    # The wizard's bundled_script() resolution walks UP from _setup/ for a
    # `.apworld` ancestor. In a dev checkout that walk hits nothing -- the
    # zip lives at vendor/Archipelago/custom_worlds/, which is DOWN from
    # repo root. We materialize it now (so the file exists) and monkeypatch
    # the resolver to find it.
    install_apworld = _REPO_ROOT / "scripts" / "install_apworld.py"
    apworld_zip = _REPO_ROOT / "vendor" / "Archipelago" / "custom_worlds" / "meatballs.apworld"
    subprocess.run(
        [sys.executable, str(install_apworld), "--bundle-mod", "--bundle-scripts"],
        check=True, cwd=str(_REPO_ROOT),
    )
    assert apworld_zip.is_file(), f"install_apworld did not produce {apworld_zip}"

    from _setup import build as setup_build
    monkeypatch.setattr(setup_build, "_find_apworld_zip",
                        lambda setup_root: apworld_zip)
    monkeypatch.setattr(setup_build, "_extracted_bundled_root", None)

    # --- 3. Live install: real downloads --------------------------------
    # We exclude winget-installable keys (cmake/ninja/python312) because
    # they're system-wide -- the test gated on `_require_winget_prereqs`
    # so they're already present, and `check_all` after install picks them
    # up by detection.
    from _setup import wizard_cli, installers
    # Stream install logs to stdout so a watching maintainer sees real-time
    # progress on a 15-min test. Pytest with -s captures stdout verbatim.
    text_log = wizard_cli.make_text_callback()
    install_outcome = wizard_cli.run_install(
        keys=("llvm19", "winlibs", "sail_python_deps", "hactool"),
        preflight=True,
        callback=text_log,
    )
    assert install_outcome.ok, (
        f"wizard_cli.run_install failed: installed={install_outcome.installed} "
        f"failed={install_outcome.failed}"
    )
    assert set(install_outcome.installed) == {
        "llvm19", "winlibs", "sail_python_deps", "hactool",
    }

    # Every installed key's detector must now flip green. This is the
    # exact gap the user reported -- install succeeded but check_hactool
    # said "not found" -- so this assertion is the regression guard for
    # the bug class.
    results = prereqs.check_all()
    by_key = {r.key: r for r in results}
    for key in ("llvm19", "winlibs", "sail_python_deps", "hactool"):
        assert by_key[key].ok, (
            f"wizard_cli.run_install reported {key!r} installed, but "
            f"detector still says: {by_key[key].detail}"
        )

    # --- 4. Stub the extract phase --------------------------------------
    # No NSP available in the test; the extractor itself is exercised by
    # test_shine_map_extraction.py against an opt-in NSP fixture. Here we
    # just need shine_map.json / capture_map.json present so the build's
    # sync_capture_table has a capture_map to read from.
    sys.path.insert(0, str(_REPO_ROOT))
    from scripts import release_audit
    from _setup import data_dir
    release_audit.write_stub_maps(data_dir())

    # --- 5. Build phase: real cold switch-mod build ---------------------
    # wizard_cli.run_build calls _setup.build.run_sync_capture_table +
    # run_sync_shine_table + run_build_switchmod, which use bundled_script
    # / bundled_switch_mod -- our monkeypatched _find_apworld_zip routes
    # those reads through the apworld zip we built in step 2. shine_table.h
    # is built from the stub shine_map.json release_audit.write_stub_maps
    # plants in step 4 (no NSP available in this test).
    build_outcome = wizard_cli.run_build(
        bridge_host="127.0.0.1", callback=text_log,
    )
    assert build_outcome.ok, (
        f"wizard_cli.run_build failed: step_results="
        f"{ {k: getattr(v, 'returncode', v) for k, v in build_outcome.step_results.items()} }"
    )
    # collect_build_outputs returns {'subsdk9': ..., 'main.npdm': ...}
    assert set(build_outcome.outputs.keys()) >= {"subsdk9", "main.npdm"}, (
        f"build outputs missing required artifacts: {build_outcome.outputs!r}"
    )
    for name, path in build_outcome.outputs.items():
        assert path.is_file(), f"{name!r} reported at {path} but file missing"

    # --- 6. Audit phase -------------------------------------------------
    # release_audit.audit_switch_mod's existing logic uses `git ls-files`
    # to enumerate source files -- which works in dev mode but NOT against
    # a bundled tree extracted from the apworld zip (no .git markers, no
    # OdysseyHeaders/.git, etc.). For the bundled tree we instead
    # enumerate the apworld zip directly: anything in the bundled tree
    # that's NOT in the zip and NOT under a known build-output root is
    # a leak. That's the same guarantee audit_switch_mod gives via git,
    # just sourced from the zip manifest.
    bundled_switch_mod = sandbox_appdata / "bundled" / "switch_mod"
    assert (bundled_switch_mod / "CMakeLists.txt").is_file(), (
        f"bundled switch_mod not present at {bundled_switch_mod} -- the "
        f"build either failed silently or wrote elsewhere"
    )

    # Build the "source manifest" from the apworld zip. The zip stores
    # bundled switch_mod files under `meatballs/_setup/switch_mod/<rel>`.
    expected_sources: set[str] = set()
    with zipfile.ZipFile(apworld_zip) as zf:
        prefix = "meatballs/_setup/switch_mod/"
        for name in zf.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                expected_sources.add(name[len(prefix):])

    # Walk the bundled switch_mod and partition files. Known build-output
    # roots (mirroring release_audit.SWITCH_MOD_OUTPUT_ROOTS) are
    # unconditionally allowed; everything else must be in the zip
    # manifest. Anything outside both is a leak that the build introduced.
    output_roots = release_audit.SWITCH_MOD_OUTPUT_ROOTS  # ("build", "sys/sail/build", "lib/std")
    unexpected: list[str] = []
    for p in bundled_switch_mod.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(bundled_switch_mod).as_posix()
        if any(rel == r or rel.startswith(r + "/") for r in output_roots):
            continue
        if "/__pycache__/" in rel or rel.startswith("__pycache__/"):
            continue
        if rel in expected_sources:
            continue
        unexpected.append(rel)

    assert not unexpected, (
        f"build wrote {len(unexpected)} files outside output roots that "
        f"weren't in the apworld zip manifest -- this is the 'build leak' "
        f"audit's whole purpose. First 20:\n  " + "\n  ".join(sorted(unexpected)[:20])
    )

    # Required artifacts must be present. release_audit.REQUIRED_ARTIFACTS
    # is the source of truth.
    for rel in release_audit.REQUIRED_ARTIFACTS:
        assert (bundled_switch_mod / rel).is_file(), (
            f"required artifact missing: {rel}"
        )

    # APPDATA audit: the wizard's tree under sandbox_appdata must match
    # the allowlist. We use the existing release_audit.audit_tree with
    # the canonical glob set.
    appdata_allowed, appdata_unexpected = release_audit.audit_tree(
        sandbox_appdata, release_audit.ALLOWED_GLOBS["appdata"],
    )
    assert not appdata_unexpected, (
        f"wizard wrote {len(appdata_unexpected)} files under APPDATA outside "
        f"the allowlist. First 20:\n  " + "\n  ".join(sorted(appdata_unexpected)[:20])
    )
