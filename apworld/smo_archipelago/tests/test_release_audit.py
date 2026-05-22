"""Tests for `scripts/release_audit.py`.

The script's three stages (--skip-extract / --build / --audit) are
exercised separately so we can validate the audit logic cross-platform
without needing an actual Windows toolchain to run the build step. The
Windows runner in `release.yml` is what exercises --build end-to-end.

What this file covers:
  - Stub map writer produces the expected schema (no Nintendo content).
  - `_matches_any` honors `**` recursion plus per-segment fnmatch globs.
  - `audit_tree` correctly partitions a synthetic build tree into
    allowed / unexpected.
  - `run_audit` fails when required artifacts are missing OR unexpected
    files exist OR both.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make `scripts.release_audit` importable. release_audit.py is the
# subject under test, and its parent directory (repo root) is two levels
# up from this test file (apworld/smo_archipelago/tests/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from scripts import release_audit  # noqa: E402


def test_stub_maps_have_no_nintendo_content(tmp_path):
    """The whole point of --skip-extract is to NEVER touch Nintendo IP.
    Make sure the stub strings stay synthetic — a future contributor
    might be tempted to add 'real-looking' data here for completeness."""
    written = release_audit.write_stub_maps(tmp_path)

    shine = json.loads((tmp_path / "shine_map.json").read_text(encoding="utf-8"))
    capture = json.loads((tmp_path / "capture_map.json").read_text(encoding="utf-8"))

    # Schema check: writer matches what extract_shine_map.py emits, so
    # downstream consumers (sync_capture_table.py, sync_shine_table.py)
    # don't choke on a missing key.
    assert shine and isinstance(shine, list)
    assert {"stage_name", "object_id", "kingdom", "shine_id", "shine_uid"} <= shine[0].keys()
    assert capture and isinstance(capture, list)
    assert {"cap", "hack_name"} <= capture[0].keys()

    # IP discipline: every string must be obviously a placeholder. We
    # check by prefix to keep the test simple — any future contributor
    # who adds a "real" name (e.g. "Cascade Kingdom") fails this.
    for entry in shine:
        for value in entry.values():
            if isinstance(value, str):
                assert value.startswith(("Stub", "obj")), (
                    f"shine_map stub leaked non-placeholder string: {value!r}"
                )
    for entry in capture:
        for value in entry.values():
            assert isinstance(value, str)
            assert value.startswith("Stub"), (
                f"capture_map stub leaked non-placeholder string: {value!r}"
            )

    # Sanity: the file paths returned match the files actually written.
    assert set(written) == {tmp_path / "shine_map.json", tmp_path / "capture_map.json"}


def test_write_stub_maps_refuses_to_clobber_real_extraction(tmp_path):
    """Safety net: if a dev runs --skip-extract on a machine with real
    extracted maps, we must NOT silently overwrite them. The user's
    extraction is the result of a 10-minute NSP decryption — losing it
    is a real cost. CI invocations on a fresh runner won't hit this
    branch because the maps don't exist yet."""
    real_shine = [
        {"stage_name": "CapWorld", "object_id": "obj99", "kingdom": "CapKingdom",
         "shine_id": "A Real Looking Moon", "shine_uid": 99},
        # ... pretend this list has 700+ entries
    ]
    (tmp_path / "shine_map.json").write_text(
        json.dumps(real_shine), encoding="utf-8",
    )
    (tmp_path / "capture_map.json").write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="real extraction"):
        release_audit.write_stub_maps(tmp_path)

    # ...and the contents are still intact.
    on_disk = json.loads((tmp_path / "shine_map.json").read_text(encoding="utf-8"))
    assert on_disk == real_shine


def test_write_stub_maps_force_overwrites_real_extraction(tmp_path):
    """The escape hatch: --force-stub-maps explicitly opts in to the
    overwrite. Required for any CI-like environment that starts with a
    seeded map (e.g. a test runner reusing %APPDATA% across jobs)."""
    real_shine = [{"stage_name": "X", "object_id": "Y", "kingdom": "Z",
                   "shine_id": "Real", "shine_uid": 1}]
    (tmp_path / "shine_map.json").write_text(
        json.dumps(real_shine), encoding="utf-8",
    )
    (tmp_path / "capture_map.json").write_text("[]", encoding="utf-8")

    release_audit.write_stub_maps(tmp_path, force=True)
    on_disk = json.loads((tmp_path / "shine_map.json").read_text(encoding="utf-8"))
    assert on_disk == release_audit.STUB_SHINE_MAP


def test_matches_any_simple_glob():
    assert release_audit._matches_any("data/shine_map.json", ("data/*.json",))
    assert not release_audit._matches_any("data/shine_map.json", ("build/*",))


def test_matches_any_recursive_glob():
    # `**` should match any depth, including zero segments past the prefix.
    globs = ("bundled/**",)
    assert release_audit._matches_any("bundled/scripts/foo.py", globs)
    assert release_audit._matches_any("bundled/scripts/sub/nested/file.txt", globs)
    # The bare prefix without a trailing segment must also match (the
    # directory entry itself; fnmatch alone would miss this).
    assert release_audit._matches_any("bundled", globs)


def test_matches_any_path_separator_normalization(tmp_path):
    """Audit is run on Windows where os.sep is `\\`. The matcher must
    normalize to POSIX before comparing to the allowlist globs."""
    rel_windows = "build\\sd\\atmosphere\\contents\\file"
    assert release_audit._matches_any(rel_windows, ("build/**",))


def test_audit_tree_partitions_allowed_and_unexpected(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "shine_map.json").write_text("[]", encoding="utf-8")
    (tmp_path / "data" / "shine_map_review.json").write_text("[]", encoding="utf-8")
    (tmp_path / "leaked").mkdir()
    (tmp_path / "leaked" / "secret.bin").write_bytes(b"x")

    allowed, unexpected = release_audit.audit_tree(
        tmp_path,
        ("data/shine_map.json", "data/shine_map_review.json"),
    )

    assert sorted(allowed) == ["data/shine_map.json", "data/shine_map_review.json"]
    assert sorted(unexpected) == ["leaked/secret.bin"]


def test_audit_tree_handles_missing_root(tmp_path):
    """A missing sandbox root is not a failure — it just means the build
    didn't touch that location. Caller surfaces missing required
    artifacts separately."""
    allowed, unexpected = release_audit.audit_tree(
        tmp_path / "does_not_exist", ("**",),
    )
    assert allowed == []
    assert unexpected == []


def _build_clean_sandbox(appdata: Path, switch_mod: Path) -> None:
    """Lay down the minimum file set that run_audit accepts as clean.

    The switch_mod tree is initialized as a real git repo with the
    sentinel CMakeLists.txt committed — `audit_switch_mod` consults
    `git ls-files` to distinguish pre-existing source files from
    build-time leaks.
    """
    import subprocess

    # appdata side
    (appdata / "data").mkdir(parents=True)
    (appdata / "data" / "shine_map.json").write_text("[]", encoding="utf-8")
    (appdata / "data" / "capture_map.json").write_text("[]", encoding="utf-8")
    # switch-mod side: required artifacts must exist
    sd = switch_mod / "build" / "sd" / "atmosphere" / "contents" / "0100000000010000" / "exefs"
    sd.mkdir(parents=True)
    (sd / "subsdk9").write_bytes(b"stub")
    (sd / "main.npdm").write_bytes(b"stub")
    (switch_mod / "src" / "ap").mkdir(parents=True)
    (switch_mod / "src" / "ap" / "capture_table.h").write_text("// stub", encoding="utf-8")
    # cmake / ninja drop a CMakeLists.txt at the source-tree root; the
    # find_switch_mod_root() probe uses it as a sentinel.
    (switch_mod / "CMakeLists.txt").write_text("# stub", encoding="utf-8")

    # Mark CMakeLists.txt as git-tracked so the audit doesn't flag it
    # as an unexpected build-time write. Source files under switch_mod/
    # are expected to be in git; only output-root files and the explicit
    # capture_table.h write are allowed to appear post-build without
    # being tracked.
    env = {
        # Quiet `git init`'s "hint: Using 'master' as the name for the
        # initial branch" message in CI logs.
        "GIT_INIT_DEFAULT_BRANCH": "main",
        # Don't read the dev's gitconfig — we want a hermetic repo.
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(switch_mod),  # nowhere to find a gitconfig
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=switch_mod, env={**env}, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "CMakeLists.txt"],
        cwd=switch_mod, env={**env}, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=switch_mod, env={**env}, check=True,
    )


def test_run_audit_passes_on_clean_tree(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)

    # appdata_root() reads from %APPDATA% env var; point it at our temp
    # so the audit doesn't pick up a real user install.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata_env"))
    # The _setup.appdata_root() helper joins APPDATA with "SMOArchipelago",
    # so we need our temp APPDATA to end in a parent of "SMOArchipelago".
    # Easier: stub find_switch_mod_root and appdata_root both to our paths.
    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)

    # Patch the _setup import inside run_audit. We can't reach inside
    # the function's lazy `from smo_archipelago._setup import appdata_root`
    # easily, so instead we make sure the imported function returns our
    # temp by installing a stub module.
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)

    rc = release_audit.run_audit()
    assert rc == 0


def test_run_audit_fails_on_missing_required_artifact(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)
    (switch_mod / "build" / "sd" / "atmosphere" / "contents" / "0100000000010000"
     / "exefs" / "subsdk9").unlink()

    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)

    assert release_audit.run_audit() == 1


def test_run_audit_fails_on_unexpected_appdata_file(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)
    # Drop a file under appdata that doesn't match any allowlist pattern.
    (appdata / "stray.bin").write_bytes(b"x")

    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)

    assert release_audit.run_audit() == 1


def test_run_audit_fails_on_unexpected_switch_mod_file(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)
    # Drop a file in the switch-mod source tree outside the allowed
    # write set. `src/ap/capture_table.h` is allowed, but `src/leak.cpp`
    # is not — the build is supposed to write to `build/` and that one
    # generated header, nothing else.
    (switch_mod / "src" / "leak.cpp").write_text("// leak", encoding="utf-8")

    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)

    assert release_audit.run_audit() == 1


def test_run_audit_passes_with_pycache_dir(tmp_path, monkeypatch):
    """__pycache__ is a Python runtime artifact -- in a dev checkout it
    lands inside switch-mod/sys/tools/ next to the .py scripts cmake
    invokes. The audit must ignore it; otherwise every release run
    would fail on whatever bytecode Python decided to cache."""
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)
    pycache = switch_mod / "sys" / "tools" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "nso.cpython-313.pyc").write_bytes(b"stub")

    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)

    assert release_audit.run_audit() == 0


def test_run_audit_passes_with_submodule_gitlink(tmp_path, monkeypatch):
    """A submodule's `.git` file (and any `.git` directory at a subdir
    level) marks a submodule root, not a build leak. Both shapes should
    be ignored. Same goes for the previously-flagged `.gitignore` /
    `LICENSE` / `README.md` that live inside an init'd submodule and
    aren't in the parent repo's `git ls-files`."""
    import subprocess
    appdata = tmp_path / "appdata"
    switch_mod = tmp_path / "switch_mod"
    _build_clean_sandbox(appdata, switch_mod)

    # Simulate an initialized submodule at `lib/sub/`. Its own git index
    # tracks `LICENSE` -- audit_switch_mod should recognize the gitlink
    # and call `_is_submodule_tracked` to validate.
    sub = switch_mod / "lib" / "sub"
    sub.mkdir(parents=True)
    (sub / "LICENSE").write_text("MIT", encoding="utf-8")
    env = {
        "GIT_INIT_DEFAULT_BRANCH": "main",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(sub),
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sub, env={**env}, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "LICENSE"],
        cwd=sub, env={**env}, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=sub, env={**env}, check=True,
    )

    monkeypatch.setattr(release_audit, "find_switch_mod_root", lambda: switch_mod)
    import types
    stub = types.ModuleType("_setup")
    stub.appdata_root = lambda: appdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_setup", stub)
    # Clear the per-test submodule-tracked memo so this test doesn't
    # inherit results from a prior fixture run.
    release_audit._submodule_tracked_cache.clear()

    assert release_audit.run_audit() == 0


def test_discover_submodule_paths_finds_nested(tmp_path):
    """`_discover_submodule_paths` must walk recursively so a nested
    submodule (e.g. sys/tools/senobi) is recognized -- otherwise the
    audit flags every senobi file as a build leak."""
    switch_mod = tmp_path / "switch_mod"
    (switch_mod / "sys").mkdir(parents=True)
    (switch_mod / "sys" / ".git").write_text("gitdir: ../.git/modules/sys", encoding="utf-8")
    (switch_mod / "sys" / "tools" / "senobi").mkdir(parents=True)
    (switch_mod / "sys" / "tools" / "senobi" / ".git").write_text(
        "gitdir: ../../../.git/modules/sys/modules/tools/senobi", encoding="utf-8"
    )

    found = release_audit._discover_submodule_paths(switch_mod)

    # Nested submodule (longer prefix) sorted first.
    assert found == ("sys/tools/senobi", "sys")
