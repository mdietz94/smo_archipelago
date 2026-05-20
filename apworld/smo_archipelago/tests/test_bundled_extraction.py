"""Tests for `_setup.build._extract_bundled_tree`.

Regression coverage for the v0.1.7-alpha bug where the wizard's extract
step crashed with "bundled script 'extract_shine_map.py' not found at
C:\\ProgramData\\Archipelago\\custom_worlds\\meatballs.apworld\\meatballs\\_setup\\
scripts\\extract_shine_map.py" — the apworld is loaded via Python's
zipimporter, so `Path(__file__).parent / "scripts" / "x.py"` is a path
string that traverses through `meatballs.apworld` (a real ZIP file, not a
directory). `Path.exists()` returns False on such paths; subprocess
can't invoke files at them either.

The fix extracts the bundled tree to a real filesystem location once
per process and rewrites the bundled_script / bundled_switch_mod
return paths to point there.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from _setup import build


@pytest.fixture(autouse=True)
def reset_extraction_cache():
    """Each test gets a clean memoization cache so they don't interfere."""
    build._extracted_bundled_root = None
    yield
    build._extracted_bundled_root = None


def test_find_apworld_zip_walks_up_to_zip_ancestor(tmp_path) -> None:
    """When _SETUP_ROOT is a path with a .apworld file as a midpoint, the
    walker must return that zip path. Note: we synthesize the path string
    rather than constructing a real zip — `_find_apworld_zip` checks
    `is_file()`, which requires the .apworld to actually exist."""
    fake_zip = tmp_path / "meatballs.apworld"
    fake_zip.write_bytes(b"")  # empty file is enough for is_file()
    setup_root = fake_zip / "meatballs" / "_setup"
    assert build._find_apworld_zip(setup_root) == fake_zip


def test_find_apworld_zip_returns_none_for_real_dir(tmp_path) -> None:
    """Dev/source checkout: _SETUP_ROOT is a real directory, no .apworld
    ancestor. Must return None so the caller stays on the in-place path."""
    real_setup = tmp_path / "apworld" / "smo_archipelago" / "_setup"
    real_setup.mkdir(parents=True)
    assert build._find_apworld_zip(real_setup) is None


def test_extract_bundled_tree_returns_setup_root_on_dev_checkout(
    tmp_path, monkeypatch,
) -> None:
    """On a dev checkout, _SETUP_ROOT is real and bundled files live
    directly under it. No extraction needed; the in-place path wins."""
    fake_setup = tmp_path / "_setup"
    (fake_setup / "scripts").mkdir(parents=True)
    (fake_setup / "scripts" / "extract_shine_map.py").write_text("# fake")
    monkeypatch.setattr(build, "_SETUP_ROOT", fake_setup)
    monkeypatch.setattr(build, "_find_apworld_zip", lambda _: None)

    result = build._extract_bundled_tree()
    assert result == fake_setup


def test_extract_bundled_tree_unpacks_zip_to_appdata(
    tmp_path, monkeypatch,
) -> None:
    """On a frozen-Launcher install (.apworld zip in the import path), the
    bundled tree must be unpacked to %APPDATA%/SMOArchipelago/bundled/.
    Validates the full unpacking machinery against a real zip.

    Three subtrees are extracted (everything subprocesses access by path):
      meatballs/_setup/scripts/   -> <bundled>/scripts/
      meatballs/_setup/switch_mod -> <bundled>/switch_mod/
      meatballs/data/             -> <bundled>/data/
    The apworld's Python modules (meatballs/client/, meatballs/Items.py, etc.) are
    NOT extracted because they're imported via zipimport, not invoked
    as files."""
    # Build a fake meatballs.apworld with the same layout as the real one.
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/__init__.py", "# stub")
        zf.writestr("meatballs/_setup/__init__.py", "# stub")
        zf.writestr("meatballs/_setup/scripts/extract_shine_map.py", "print('hi')")
        zf.writestr("meatballs/_setup/scripts/sync_capture_table.py", "# sync")
        zf.writestr("meatballs/_setup/switch_mod/CMakeLists.txt", "# cmake")
        zf.writestr("meatballs/_setup/switch_mod/src/main.cpp", "int main() {}")
        zf.writestr("meatballs/data/locations.json", '{"locations":[]}')
        zf.writestr("meatballs/data/items.json", '{"items":[]}')
        # Files at other prefixes that must NOT be extracted (the apworld
        # also bundles the world code itself; that's loaded via zipimport).
        zf.writestr("meatballs/client/main.py", "# client")
        zf.writestr("meatballs/Items.py", "# items")

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    extracted = build._extract_bundled_tree()
    assert extracted == fake_appdata / "SMOArchipelago" / "bundled"
    assert (extracted / "scripts" / "extract_shine_map.py").is_file()
    assert (extracted / "scripts" / "sync_capture_table.py").is_file()
    assert (extracted / "switch_mod" / "CMakeLists.txt").is_file()
    assert (extracted / "switch_mod" / "src" / "main.cpp").is_file()
    # Data dir is extracted too — needed by the extractor's cross-validation.
    assert (extracted / "data" / "locations.json").is_file()
    assert (extracted / "data" / "items.json").is_file()
    assert (extracted / "data" / "locations.json").read_text() == '{"locations":[]}'
    # The Python-code subtree must NOT be extracted (zipimport handles it).
    assert not (extracted / "client").exists()
    assert not (extracted / "Items.py").exists()
    # Content survives intact.
    assert (extracted / "scripts" / "extract_shine_map.py").read_text() == "print('hi')"


def test_bundled_data_file_works_from_zip(tmp_path, monkeypatch) -> None:
    """End-to-end: `bundled_data_file('locations.json')` must return an
    on-disk path. Regression test: without data/ extraction, the wizard's
    extract step crashed with 'apworld locations.json not found at
    C:\\...\\bundled\\apworld\\smo_archipelago\\data\\locations.json'
    because REPO_ROOT-relative paths inside the extractor don't apply to
    the bundled layout."""
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/data/locations.json", '{"locations":[]}')
        zf.writestr("meatballs/data/items.json", '{"items":[]}')

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    p = build.bundled_data_file("locations.json")
    assert p.is_file()
    assert "meatballs.apworld" not in str(p), (
        f"path still contains 'meatballs.apworld' as a directory segment: {p}"
    )
    assert p.read_text() == '{"locations":[]}'


def test_extract_bundled_tree_skips_when_marker_matches(
    tmp_path, monkeypatch,
) -> None:
    """Subsequent wizard runs in the SAME apworld version skip the
    extraction (zip mtime matches the cached marker). Important because
    extraction is ~25 MB of file I/O — re-doing it on every wizard open
    is needless work."""
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/_setup/scripts/extract_shine_map.py", "v1")

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    extracted_first = build._extract_bundled_tree()
    # Tamper with the extracted file to prove the second call didn't re-extract.
    (extracted_first / "scripts" / "extract_shine_map.py").write_text("tampered")
    build._extracted_bundled_root = None  # force re-check (but not re-extract)

    extracted_second = build._extract_bundled_tree()
    assert extracted_second == extracted_first
    assert (extracted_second / "scripts" / "extract_shine_map.py").read_text() == "tampered"


def test_extract_bundled_tree_re_extracts_when_zip_mtime_changes(
    tmp_path, monkeypatch,
) -> None:
    """When the user upgrades the apworld, the zip's mtime changes and
    we must wipe + re-extract — otherwise they keep running the OLD
    extracted scripts and bugfixes never reach them."""
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/_setup/scripts/extract_shine_map.py", "v1")

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    extracted = build._extract_bundled_tree()
    assert (extracted / "scripts" / "extract_shine_map.py").read_text() == "v1"
    build._extracted_bundled_root = None

    # Simulate user upgrading the apworld: rewrite the zip and bump mtime.
    fake_zip_path.unlink()
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/_setup/scripts/extract_shine_map.py", "v2 has the fix")
    import os, time
    new_mtime = fake_zip_path.stat().st_mtime + 1.0
    os.utime(fake_zip_path, (new_mtime, new_mtime))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    extracted_after = build._extract_bundled_tree()
    assert (extracted_after / "scripts" / "extract_shine_map.py").read_text() == "v2 has the fix"


def test_bundled_script_works_from_zip(tmp_path, monkeypatch) -> None:
    """End-to-end: `bundled_script` must return an on-disk path even when
    the apworld is loaded from a zip. This is the regression test for the
    v0.1.7-alpha bug report ("bundled script 'extract_shine_map.py' not
    found at C:\\...\\meatballs.apworld\\meatballs\\_setup\\scripts\\extract_shine_map.py")."""
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/_setup/scripts/extract_shine_map.py", "# real script")

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    p = build.bundled_script("extract_shine_map.py")
    assert p.is_file(), f"bundled_script returned a non-existent path: {p}"
    # And it should be invokable as a normal subprocess arg (not a path
    # inside a zip the OS can't traverse).
    assert "meatballs.apworld" not in str(p), (
        f"path still contains 'meatballs.apworld' as a directory segment: {p}"
    )


def test_bundled_switch_mod_works_from_zip(tmp_path, monkeypatch) -> None:
    """Same fix applies to the switch_mod source tree cmake reads as
    its -S source dir."""
    fake_zip_path = tmp_path / "meatballs.apworld"
    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("meatballs/_setup/switch_mod/CMakeLists.txt", "# cmake")
        zf.writestr("meatballs/_setup/switch_mod/src/x.cpp", "// src")

    fake_appdata = tmp_path / "AppData"
    fake_appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(
        build, "_SETUP_ROOT", fake_zip_path / "meatballs" / "_setup",
    )

    mod = build.bundled_switch_mod()
    assert mod.is_dir(), f"bundled_switch_mod returned non-dir: {mod}"
    assert (mod / "CMakeLists.txt").is_file()
    assert (mod / "src" / "x.cpp").is_file()
