"""Tests for DataPackage classification using the vendored apworld."""

from __future__ import annotations

from pathlib import Path

import pytest

from client.datapackage import DataPackage
from client.protocol import ItemKind

APWORLD_DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def dp() -> DataPackage:
    if not APWORLD_DATA.exists():
        pytest.skip(f"apworld data not present at {APWORLD_DATA}")
    return DataPackage(apworld_data_dir=APWORLD_DATA)


def test_classifies_a_capture(dp: DataPackage):
    # Goomba is a capture in upstream — bare enemy name (no "Capture: " prefix).
    ci = dp.classify_item("Goomba")
    assert ci.kind == ItemKind.CAPTURE
    assert ci.cap == "Goomba"


def test_classifies_a_capture_location(dp: DataPackage):
    # Capture locations have "Capture: " prefix and "Capture" category.
    ci = dp.classify_location("Capture: Goomba")
    assert ci.kind == ItemKind.CAPTURE
    assert ci.cap == "Goomba"


def test_classifies_a_moon_location(dp: DataPackage):
    # Moon locations live under "Cap Kingdom" etc. and have "Cap: " prefix.
    ci = dp.classify_location("Cap: Frog-Jumping Above the Fog")
    assert ci.kind == ItemKind.MOON
    assert ci.kingdom == "Cap"
    assert ci.shine_id == "Frog-Jumping Above the Fog"


def test_classifies_a_moon_item(dp: DataPackage):
    # Find one item classified as a Moon and check classification works.
    moon_items = [n for n, cats in dp._item_categories.items()
                  if any(c.lower() in {"moon", "moons"} for c in cats)]
    if not moon_items:
        pytest.skip("no moon items in vendored apworld")
    ci = dp.classify_item(moon_items[0])
    assert ci.kind == ItemKind.MOON


def test_classify_kingdom_specific_moon_item(dp: DataPackage):
    """`X Kingdom <type>` items split into kingdom + shine_id."""
    ci = dp.classify_item("Cascade Kingdom Power Moon")
    assert ci.kind == ItemKind.MOON
    assert ci.kingdom == "Cascade"
    assert ci.shine_id == "Power Moon"

    # Cascade has a Multi-Moon (count=1) in items.json; Cap Kingdom does not,
    # so use Cascade here.
    ci = dp.classify_item("Cascade Kingdom Multi-Moon")
    assert ci.kind == ItemKind.MOON
    assert ci.kingdom == "Cascade"
    assert ci.shine_id == "Multi-Moon"


def test_classify_unknown_item_is_other(dp: DataPackage):
    ci = dp.classify_item("Definitely Not A Real Item Name 12345")
    assert ci.kind == ItemKind.OTHER
    assert ci.name == "Definitely Not A Real Item Name 12345"


def test_split_kingdom_prefix():
    dp = DataPackage()
    k, sid = dp._split_kingdom_prefix("Cascade: Frog-Jumping Above the Fog")
    assert k == "Cascade"
    assert sid == "Frog-Jumping Above the Fog"
    k, sid = dp._split_kingdom_prefix("PlainName")
    assert k is None
    assert sid == "PlainName"


def test_progression_locations_loaded_from_apworld(dp: DataPackage):
    """The 22 audited scenario-advance moons (Multi Moons, seal prereqs,
    Bowser's chain) are flagged `progression: true` in locations.json.

    The Talkatoo% follow-up (Gap #1) needs DataPackage to expose this
    flag so the bridge-side pool builder can skip these moons — they're
    always collectible via the isProgressionShine bypass, so Talkatoo
    naming one wastes a hint slot. The canonical 22-entry set is
    audited in test_progression_moons.py; here we just spot-check that
    the loader populated the same flags the audit relies on."""
    # Spot-check a representative subset across distinct schemas:
    # Multi Moon, scenario-step opener, seal, Bowser's chain entry.
    assert dp.is_progression_location("Cascade: Multi Moon Atop the Falls")
    assert dp.is_progression_location("Cascade: Our First Power Moon")
    assert dp.is_progression_location("Seaside: The Stone Pillar Seal")
    assert dp.is_progression_location("Bowser's: Showdown at Bowser's Castle")
    # Negative: a vanilla Cascade moon should NOT be flagged.
    assert not dp.is_progression_location("Cap: Frog-Jumping Above the Fog")
    # Negative: captures are never progression (asserted by
    # test_progression_moons.test_no_capture_marked_progression).
    assert not dp.is_progression_location("Capture: Goomba")


def test_is_progression_location_returns_false_for_unknown_name():
    """Defensive: lookup of an unloaded name returns False, not KeyError.
    Used by the talkatoo pool filter which iterates raw AP location names
    that may not all be in the apworld's loaded set (e.g. cross-game
    entries an exotic seed could produce)."""
    dp = DataPackage()
    assert not dp.is_progression_location("Anything")


def test_is_progression_location_empty_when_no_data_loaded():
    """A DataPackage constructed without data_dir/package has no
    progression set — the gate stays open in the degenerate case
    (better than crashing on the filter path)."""
    dp = DataPackage()
    assert dp._progression_locations == set()


def test_kingdom_exit_thresholds_empty_without_regions():
    """No apworld data means no regions.json — the Odyssey tab elides the
    `/ needed` denominator entirely in that case."""
    dp = DataPackage()
    assert dp.kingdom_exit_thresholds() == {}


def test_kingdom_exit_thresholds_from_real_apworld(dp: DataPackage):
    """Spot-check the Odyssey-power thresholds parsed from regions.json.

    These mirror the in-game leave-thresholds and are what the Odyssey tab
    shows next to the per-kingdom moons-received counter. Ungated kingdoms
    (Cap, Cloud, Ruined, Mushroom, Moon, Dark/Darker Side) are absent.
    Ruined is ungated by design — its moons are filler and entry to
    Bowser's Kingdom has no per-kingdom moon requirement; Ruined exists
    for the Lord-of-Lightning boss fight only."""
    thresholds = dp.kingdom_exit_thresholds()
    assert thresholds["Cascade"] == 5
    assert thresholds["Sand"] == 16
    assert thresholds["Lake"] == 8
    assert thresholds["Wooded"] == 16
    assert thresholds["Lost"] == 10
    assert thresholds["Metro"] == 20
    assert thresholds["Snow"] == 10
    assert thresholds["Seaside"] == 10
    assert thresholds["Luncheon"] == 18
    assert thresholds["Bowser's"] == 8
    # Ungated kingdoms — no `{KingdomMoons(X,N)}` clause references them.
    for k in ("Cap", "Cloud", "Ruined", "Mushroom", "Moon"):
        assert k not in thresholds
