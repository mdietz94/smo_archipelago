"""Tests for `_setup.smoap_file` — the .meatballsap-file format that triggers
the first-run wizard or pre-fills SMOClient on subsequent runs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from _setup.smoap_file import (
    GAME_NAME,
    SMOAP_METADATA_ENTRY,
    SMOAP_SCHEMA_VERSION,
    SmoapFile,
    parse_smoap,
    smoap_to_launch_args,
)


def test_round_trip_minimal(tmp_path: Path) -> None:
    """A fresh-AP-gen .meatballsap with only the slot name round-trips losslessly."""
    s = SmoapFile(slot_name="Mario")
    p = tmp_path / "Mario_P1.meatballsap"
    s.write(p)

    parsed = parse_smoap(p)
    assert parsed.slot_name == "Mario"
    assert parsed.game == GAME_NAME
    assert parsed.version == SMOAP_SCHEMA_VERSION
    assert parsed.server_address == ""
    assert parsed.password == ""


def test_round_trip_all_fields(tmp_path: Path) -> None:
    s = SmoapFile(
        slot_name="Luigi",
        seed_name="ABCD1234EFGH5678",
        server_address="archipelago.gg:38281",
        password="hunter2",
    )
    p = tmp_path / "Luigi_P2.meatballsap"
    s.write(p)

    parsed = parse_smoap(p)
    assert parsed.slot_name == "Luigi"
    assert parsed.seed_name == "ABCD1234EFGH5678"
    assert parsed.server_address == "archipelago.gg:38281"
    assert parsed.password == "hunter2"


def test_human_readable_field_order(tmp_path: Path) -> None:
    """A human inspecting the .meatballsap should see `game` and `version` first
    so they can tell what kind of file it is."""
    s = SmoapFile(slot_name="Mario")
    p = tmp_path / "x.meatballsap"
    s.write(p)

    with zipfile.ZipFile(p) as zf:
        text = zf.read(SMOAP_METADATA_ENTRY).decode("utf-8")
    game_pos = text.index("game")
    version_pos = text.index("version")
    slot_pos = text.index("slot_name")
    assert game_pos < slot_pos
    assert version_pos < slot_pos


def test_writes_zip_archive(tmp_path: Path) -> None:
    """The on-disk format is a ZIP — renaming to .zip and extracting works
    the same as for every other AP patch file."""
    s = SmoapFile(slot_name="Mario")
    p = tmp_path / "x.meatballsap"
    s.write(p)

    assert p.read_bytes()[:2] == b"PK"
    with zipfile.ZipFile(p) as zf:
        assert SMOAP_METADATA_ENTRY in zf.namelist()


def test_reads_legacy_bare_json(tmp_path: Path) -> None:
    """Back-compat: a .meatballsap from a pre-zip alpha build (raw JSON on disk)
    still parses. Sniffs magic bytes, not extension."""
    p = tmp_path / "legacy.meatballsap"
    p.write_text(
        json.dumps({"game": GAME_NAME, "version": 1, "slot_name": "Mario"}),
        encoding="utf-8",
    )
    parsed = parse_smoap(p)
    assert parsed.slot_name == "Mario"


def test_zip_missing_metadata_entry_raises(tmp_path: Path) -> None:
    """A ZIP that is missing the expected `metadata.json` entry must fail
    loudly rather than silently producing an empty SmoapFile."""
    p = tmp_path / "broken.meatballsap"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("something_else.json", "{}")
    with pytest.raises(ValueError, match="metadata.json"):
        parse_smoap(p)


def test_zip_with_extra_entries_still_parses(tmp_path: Path) -> None:
    """Forward-compat: future versions may add files (icon, scout cache,
    ...) alongside metadata.json. The reader must ignore extras."""
    s = SmoapFile(slot_name="Mario")
    p = tmp_path / "future.meatballsap"
    s.write(p)
    with zipfile.ZipFile(p, "a") as zf:
        zf.writestr("scout_cache.json", "{}")
        zf.writestr("icon.png", b"\x89PNG\r\n\x1a\n")
    parsed = parse_smoap(p)
    assert parsed.slot_name == "Mario"


def test_rejects_wrong_game() -> None:
    bad = json.dumps({
        "game": "A Link to the Past",
        "version": 1,
        "slot_name": "Link",
    })
    with pytest.raises(ValueError, match="not for"):
        parse_smoap(bad)


def test_rejects_missing_slot() -> None:
    bad = json.dumps({
        "game": GAME_NAME,
        "version": 1,
        "slot_name": "",
    })
    with pytest.raises(ValueError, match="slot_name"):
        parse_smoap(bad)


def test_rejects_missing_version() -> None:
    bad = json.dumps({
        "game": GAME_NAME,
        "slot_name": "Mario",
    })
    with pytest.raises(ValueError, match="version"):
        parse_smoap(bad)


def test_rejects_future_version() -> None:
    """A .meatballsap from a future client should refuse to load, not silently
    drop fields the older client doesn't know how to interpret."""
    bad = json.dumps({
        "game": GAME_NAME,
        "version": SMOAP_SCHEMA_VERSION + 5,
        "slot_name": "Mario",
    })
    with pytest.raises(ValueError, match="newer"):
        parse_smoap(bad)


def test_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_smoap('{this is not json')


def test_ignores_unknown_fields() -> None:
    """Forward-compat: a future .meatballsap with extra fields should still load
    on an older client as long as game+version are within range."""
    forward = json.dumps({
        "game": GAME_NAME,
        "version": 1,
        "slot_name": "Mario",
        "future_field": {"nested": [1, 2, 3]},
    })
    parsed = parse_smoap(forward)
    assert parsed.slot_name == "Mario"


def test_launch_args_minimal() -> None:
    """`--name` is always present; `--connect` and `--password` are
    skipped when their .meatballsap field is empty."""
    s = SmoapFile(slot_name="Mario")
    assert smoap_to_launch_args(s) == ["--name", "Mario"]


def test_launch_args_with_server_and_password() -> None:
    s = SmoapFile(
        slot_name="Luigi",
        server_address="localhost:38281",
        password="p4ss",
    )
    args = smoap_to_launch_args(s)
    assert args == [
        "--name", "Luigi",
        "--connect", "localhost:38281",
        "--password", "p4ss",
    ]


def test_parse_raw_json_string() -> None:
    """`parse_smoap` should accept JSON text directly (not just a path) so
    tests + the wizard can construct + parse without bouncing off the fs."""
    s = SmoapFile(slot_name="Mario")
    parsed = parse_smoap(s.to_json())
    assert parsed.slot_name == "Mario"
