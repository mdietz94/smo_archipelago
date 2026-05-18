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


def test_moon_pool_counts_by_kingdom_empty_until_ap_lands():
    """Pre-Connected the AP datapackage hasn't populated item_id_to_name yet,
    so the count map is empty (Odyssey tab shows the apworld-side checked/
    received numbers and just elides the / pool denominator)."""
    dp = DataPackage()  # no apworld data, no AP datapackage
    assert dp.moon_pool_counts_by_kingdom() == {}


def test_moon_pool_counts_by_kingdom_counts_single_and_multi():
    """Multi-Moon items weight 3 to mirror the Switch's moon-credit grant."""
    dp = DataPackage()
    # Simulate the AP server's DataPackage update.
    dp.item_id_to_name = {
        1: "Cascade Kingdom Power Moon",
        2: "Cascade Kingdom Power Moon",
        3: "Cascade Kingdom Multi-Moon",
        4: "Cap Kingdom Power Moon",
        5: "Goomba",  # capture — should be ignored
        6: "Not a moon at all",  # untyped — should be ignored
    }
    counts = dp.moon_pool_counts_by_kingdom()
    # Cascade: 2 PM (+2) + 1 MM (+3) = 5; Cap: 1 PM (+1) = 1.
    assert counts == {"Cascade": 5, "Cap": 1}
