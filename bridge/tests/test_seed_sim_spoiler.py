"""Tests for the AP spoiler parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from smo_ap_bridge.seed_sim.spoiler import (
    SpoilerParseError,
    parse_spoiler,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_spoiler.txt"


def test_fixture_parses() -> None:
    data = parse_spoiler(FIXTURE)
    assert len(data.spheres) == 11
    # 4 distinct spheres in the fixture.
    assert {p.sphere for p in data.spheres} == {1, 2, 3, 4}


def test_slot_headers() -> None:
    data = parse_spoiler(FIXTURE)
    assert set(data.slots) == {"Mario", "Link"}
    assert data.slots["Mario"].game == "Manual_SMO_archipelago"
    assert data.slots["Link"].game == "A Link to the Past"


def test_smo_slot() -> None:
    data = parse_spoiler(FIXTURE)
    assert data.smo_slot().slot == "Mario"


def test_items_routed_to_mario() -> None:
    data = parse_spoiler(FIXTURE)
    routed = data.items_routed_to("Mario")
    # Link sends 3 items to Mario across spheres 1/2/3.
    assert "Link" in routed
    assert len(routed["Link"]) == 3
    assert {p.item for p in routed["Link"]} == {
        "Cap Kingdom Power Moon",
        "Cascade Kingdom Power Moon",
        "Sand Kingdom Power Moon",
    }


def test_per_slot_locations_populated() -> None:
    data = parse_spoiler(FIXTURE)
    # Mario should have 8 locations from the "Locations:" block.
    assert len(data.slots["Mario"].locations) == 8
    # Link's local Triforce shouldn't be misrouted.
    link_locs = data.slots["Link"].locations
    assert any(loc == "Tower of Hera - Big Key Chest" and item == "Triforce" and recip == "Link"
               for loc, item, recip in link_locs)


def test_missing_playthrough_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad_spoiler.txt"
    p.write_text("Archipelago Version 0.4.5 for Seed: 1\n\nPlayer 1: Mario\nGame: Manual_SMO_archipelago\n")
    with pytest.raises(SpoilerParseError, match="no Playthrough"):
        parse_spoiler(p)


def test_missing_file() -> None:
    with pytest.raises(SpoilerParseError, match="not found"):
        parse_spoiler("/nonexistent/spoiler.txt")
