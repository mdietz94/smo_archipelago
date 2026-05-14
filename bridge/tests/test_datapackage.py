"""Tests for DataPackage classification using the vendored apworld."""

from __future__ import annotations

from pathlib import Path

import pytest

from smo_ap_bridge.datapackage import DataPackage
from smo_ap_bridge.protocol import ItemKind

APWORLD_DATA = Path(__file__).resolve().parent.parent.parent / "apworld" / "smo_archipelago" / "data"


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
