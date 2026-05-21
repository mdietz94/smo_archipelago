"""Tests for `_setup.deploy` — the SD-card / Ryujinx file copy layer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from _setup.deploy import (
    RYU_MOD_NAME,
    SMO_TITLE_ID,
    DeployResult,
    _ryujinx_layout,
    _sd_layout,
    deploy_to_custom_folder,
    deploy_to_ryujinx,
    deploy_to_sd,
    detect_ryujinx_path,
    detect_sd_candidates,
)


def _make_fake_build(tmp_path: Path) -> dict[str, Path]:
    """Build a fake build-output dir with the two artifacts so deploy
    has something to copy. Post-Hakkun the runtime SD-read path is dead;
    bridge_host is baked into subsdk9 at compile time, so ap_config.json
    no longer ships."""
    build = tmp_path / "build" / "cmake"
    build.mkdir(parents=True)
    (build / "subsdk9").write_bytes(b"\x7fELF...subsdk9 placeholder")
    (build / "main.npdm").write_bytes(b"META...npdm placeholder")
    return {
        "subsdk9": build / "subsdk9",
        "main.npdm": build / "main.npdm",
    }


def test_sd_layout_matches_atmosphere_convention(tmp_path: Path) -> None:
    """SD card paths must match Atmosphere's expected layout exactly —
    any deviation means the mod silently won't load on boot."""
    dests = _sd_layout(tmp_path)
    base = tmp_path / "atmosphere" / "contents" / SMO_TITLE_ID
    assert dests["subsdk9"] == base / "exefs" / "subsdk9"
    assert dests["main.npdm"] == base / "exefs" / "main.npdm"
    # ap_config.json was retired by the Hakkun cutover — pin its
    # absence so a future re-introduction surfaces here.
    assert "ap_config.json" not in dests


def test_ryujinx_layout_matches_cmake_post_build_hook(tmp_path: Path) -> None:
    """Ryujinx paths must match the existing -DRYU_PATH=... post-build
    hook in switch-mod/CMakeLists.txt, otherwise the dev loop and the
    wizard would target different directories."""
    dests = _ryujinx_layout(tmp_path)
    mods_base = tmp_path / "mods" / "contents" / SMO_TITLE_ID / RYU_MOD_NAME
    assert dests["subsdk9"] == mods_base / "exefs" / "subsdk9"
    assert dests["main.npdm"] == mods_base / "exefs" / "main.npdm"
    assert "ap_config.json" not in dests


def test_deploy_to_sd_copies_and_creates_parents(tmp_path: Path) -> None:
    sources = _make_fake_build(tmp_path)
    sd_root = tmp_path / "sdcard_root"
    sd_root.mkdir()
    # Note: NOT creating atmosphere/ first — deploy must mkdir parents.
    result = deploy_to_sd(sd_root, sources)
    assert result.ok, result.error
    assert len(result.files) == 2
    expected = _sd_layout(sd_root)
    for key, dest in expected.items():
        assert dest.exists(), f"{key} not at {dest}"
        assert dest.read_bytes() == sources[key].read_bytes()


def test_deploy_to_ryujinx_copies_and_creates_parents(tmp_path: Path) -> None:
    sources = _make_fake_build(tmp_path)
    ryu_root = tmp_path / "ryujinx_root"
    ryu_root.mkdir()
    result = deploy_to_ryujinx(ryu_root, sources)
    assert result.ok, result.error
    assert len(result.files) == 2
    expected = _ryujinx_layout(ryu_root)
    for key, dest in expected.items():
        assert dest.exists(), f"{key} not at {dest}"
        assert dest.read_bytes() == sources[key].read_bytes()


def test_deploy_to_sd_handles_permission_error(monkeypatch, tmp_path: Path) -> None:
    sources = _make_fake_build(tmp_path)
    sd_root = tmp_path / "sdcard_root"
    sd_root.mkdir()
    import shutil
    def fake_copy2(src, dst):
        raise PermissionError("simulated read-only volume")
    monkeypatch.setattr(shutil, "copy2", fake_copy2)
    result = deploy_to_sd(sd_root, sources)
    assert not result.ok
    assert "PermissionError" in result.error


def test_detect_sd_candidates_returns_list() -> None:
    """Doesn't validate finding the user's actual SD (CI won't have one)
    — just confirms the function returns a list without throwing."""
    result = detect_sd_candidates()
    assert isinstance(result, list)


def test_detect_ryujinx_path_returns_none_when_missing(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # No Ryujinx subdir → should return None.
    assert detect_ryujinx_path() is None or sys.platform != "win32"


def test_detect_ryujinx_path_returns_path_when_present(
    monkeypatch, tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only auto-detect for v1")
    (tmp_path / "Ryujinx").mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert detect_ryujinx_path() == tmp_path / "Ryujinx"


def test_deploy_to_custom_folder_uses_sd_card_layout(tmp_path: Path) -> None:
    """The custom-folder deploy must lay files out using the SD-card
    layout (atmosphere/contents/<title-id>/{exefs,romfs}/) so the user
    can drop the entire subtree onto a Switch SD card root and have it
    work without any path rewriting."""
    sources = _make_fake_build(tmp_path)
    custom_root = tmp_path / "MyStaging"
    custom_root.mkdir()

    result = deploy_to_custom_folder(custom_root, sources)
    assert result.ok, f"deploy failed: {result.error}"
    assert "Custom folder" in result.target
    assert custom_root.name in result.target

    base = custom_root / "atmosphere" / "contents" / SMO_TITLE_ID
    assert (base / "exefs" / "subsdk9").is_file()
    assert (base / "exefs" / "main.npdm").is_file()
    # Bytes match — confirms it's a real copy, not just a touch.
    assert (base / "exefs" / "subsdk9").read_bytes() == sources["subsdk9"].read_bytes()


def test_deploy_to_custom_folder_handles_permission_error(
    monkeypatch, tmp_path: Path,
) -> None:
    """Same error-wrapping discipline as deploy_to_sd / deploy_to_ryujinx
    — surface the OSError as a DeployResult so the wizard can offer Retry
    instead of crashing."""
    sources = _make_fake_build(tmp_path)
    custom_root = tmp_path / "Locked"
    custom_root.mkdir()
    import shutil
    monkeypatch.setattr(
        shutil, "copy2",
        lambda src, dst: (_ for _ in ()).throw(PermissionError("no write")),
    )
    result = deploy_to_custom_folder(custom_root, sources)
    assert not result.ok
    assert "PermissionError" in result.error
    assert "Custom folder" in result.target


# ---------------------------------------------------------------------------
# Defensiveness pins — partial-write detection and contextual errors.
# These guard the wizard's "did the deploy actually finish" gate. If a
# future edit drops the size assertion in _copy_files, the wizard would
# silently mark a half-deploy "complete" and the user's Switch would boot
# with a truncated subsdk9.
# ---------------------------------------------------------------------------


def test_deploy_detects_partial_write(monkeypatch, tmp_path: Path) -> None:
    """If shutil.copy2 returns without raising but the destination ends
    up shorter than the source (a real failure mode on yanked SD cards
    and AV-filtered file systems), the deploy must FAIL and report the
    size mismatch — not silently mark itself complete."""
    sources = _make_fake_build(tmp_path)
    custom_root = tmp_path / "PartialDrive"
    custom_root.mkdir()

    import shutil as _shutil
    real_copy2 = _shutil.copy2

    def truncating_copy2(src, dst):
        real_copy2(src, dst)
        # Simulate "drive disconnected after copy started but before
        # all bytes flushed" — file exists at dst but shorter than src.
        with open(dst, "wb") as f:
            f.write(b"truncated")

    monkeypatch.setattr(_shutil, "copy2", truncating_copy2)

    result = deploy_to_custom_folder(custom_root, sources)
    assert not result.ok, "Partial write was not detected — deploy silently succeeded"
    assert "Partial write" in result.error or "bytes" in result.error
    # Source name must be in the error so the user knows what to re-copy.
    assert "subsdk9" in result.error or "main.npdm" in result.error or "ap_config" in result.error


def test_deploy_error_includes_source_and_dest_context(
    monkeypatch, tmp_path: Path,
) -> None:
    """The error string must identify which copy failed — both the
    source filename and the destination path. Without this the user
    sees just `OSError: [Errno 13] Permission denied` and has to dig
    through the log to figure out which file failed."""
    sources = _make_fake_build(tmp_path)
    sd_root = tmp_path / "sd"
    sd_root.mkdir()

    import shutil as _shutil
    monkeypatch.setattr(
        _shutil, "copy2",
        lambda src, dst: (_ for _ in ()).throw(
            PermissionError("access denied")
        ),
    )
    result = deploy_to_sd(sd_root, sources)
    assert not result.ok
    # Source filename present
    assert "subsdk9" in result.error or "main.npdm" in result.error or "ap_config" in result.error
    # Underlying OSError class preserved (so the wizard's status text
    # surfaces the OS-level cause).
    assert "PermissionError" in result.error


def test_deploy_detects_missing_source(monkeypatch, tmp_path: Path) -> None:
    """If a build output went missing between build verification and
    deploy (e.g. user manually deleted from build dir), the copy step
    must fail with a clear `Source file unreadable` message — not a
    bare FileNotFoundError from copy2."""
    sources = _make_fake_build(tmp_path)
    # Delete the source after the dict was built — simulates the race.
    sources["subsdk9"].unlink()
    sd_root = tmp_path / "sd"
    sd_root.mkdir()

    result = deploy_to_sd(sd_root, sources)
    assert not result.ok
    assert "Source file unreadable" in result.error
    assert "subsdk9" in result.error
