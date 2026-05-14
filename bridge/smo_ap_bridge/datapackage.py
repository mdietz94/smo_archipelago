"""AP datapackage loader + classifier.

Two sources combine:
  1. The DataPackage the AP server sends us at runtime — definitive name <-> id.
  2. The vendored apworld's data/{items,locations,categories}.json for category info
     AP doesn't carry. We use this to classify items into Moon/Capture/Kingdom/Shop
     so the Switch never has to deal with raw AP ids.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import ItemKind, ItemRef

log = logging.getLogger(__name__)

# Match e.g. "Cap: Frog-Jumping Above the Fog" -> kingdom="Cap", shine_id="Frog-Jumping Above the Fog"
_LOC_PREFIX_RE = re.compile(r"^([A-Za-z' ]+):\s*(.+)$")


@dataclass
class ClassifiedItem:
    kind: ItemKind
    name: str
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    slot: int | None = None

    def to_ref(self) -> ItemRef:
        return ItemRef(
            kind=self.kind.value,
            kingdom=self.kingdom,
            shine_id=self.shine_id,
            cap=self.cap,
            slot=self.slot,
            name=self.name if self.kind == ItemKind.OTHER else None,
        )


class DataPackage:
    """Wraps the AP datapackage plus apworld category info."""

    def __init__(self, apworld_data_dir: Path | None = None):
        self.item_id_to_name: dict[int, str] = {}
        self.location_id_to_name: dict[int, str] = {}
        self.item_name_to_id: dict[str, int] = {}
        self.location_name_to_id: dict[str, int] = {}

        # Built from the apworld's items.json / locations.json / categories.json
        self._item_categories: dict[str, list[str]] = {}
        self._location_categories: dict[str, list[str]] = {}

        if apworld_data_dir is not None:
            self._load_apworld(apworld_data_dir)

    def _load_apworld(self, data_dir: Path) -> None:
        items_path = data_dir / "items.json"
        locations_path = data_dir / "locations.json"
        if items_path.exists():
            for entry in json.loads(items_path.read_text(encoding="utf-8")):
                name = entry.get("name")
                if name:
                    self._item_categories[name] = entry.get("category", []) or []
        if locations_path.exists():
            for entry in json.loads(locations_path.read_text(encoding="utf-8")):
                name = entry.get("name")
                if name:
                    self._location_categories[name] = entry.get("category", []) or []

    # ---- Wired up at runtime when the AP server sends DataPackage ----

    def update_from_ap(self, game: str, package: dict[str, Any]) -> None:
        """Ingest an AP DataPackage entry for a single game."""
        item_map = package.get("item_name_to_id", {}) or {}
        loc_map = package.get("location_name_to_id", {}) or {}
        for name, item_id in item_map.items():
            self.item_name_to_id[name] = item_id
            self.item_id_to_name[item_id] = name
        for name, loc_id in loc_map.items():
            self.location_name_to_id[name] = loc_id
            self.location_id_to_name[loc_id] = name
        log.info("loaded datapackage for %s: %d items, %d locations",
                 game, len(item_map), len(loc_map))

    # ---- Classification ----

    def classify_item(self, name: str) -> ClassifiedItem:
        cats = [c.lower() for c in self._item_categories.get(name, [])]
        # Upstream uses "Moon", "Capture", "Action", "Coin", "Shop", "Regional",
        # plus moon-subtype tags like "genericmoon", "specificmoon", "post-metro".
        if "capture" in cats:
            # Capture items are bare enemy names (e.g. "Goomba", "Paragoomba").
            return ClassifiedItem(ItemKind.CAPTURE, name, cap=name)
        if "moon" in cats or "genericmoon" in cats or "specificmoon" in cats:
            kingdom, shine_id = self._split_kingdom_prefix(name)
            return ClassifiedItem(ItemKind.MOON, name, kingdom=kingdom, shine_id=shine_id)
        if "kingdom" in cats or "kingdom unlock" in cats:
            return ClassifiedItem(ItemKind.KINGDOM, name, kingdom=self._strip_prefix(name, ("Kingdom: ", "Unlock: ")))
        if "shop" in cats:
            kingdom, slot_label = self._split_kingdom_prefix(name)
            return ClassifiedItem(ItemKind.SHOP, name, kingdom=kingdom, shine_id=slot_label)
        return ClassifiedItem(ItemKind.OTHER, name)

    def classify_location(self, name: str) -> ClassifiedItem:
        # Locations have category tags like "Cap Kingdom", "Cascade Kingdom", "Capture",
        # "Shop", etc. We classify by the prefix on the location name (e.g. "Cap: …",
        # "Capture: …", "Cascade: …") since that's what the Switch will reconstruct.
        if name.startswith("Capture: "):
            return ClassifiedItem(ItemKind.CAPTURE, name, cap=name[len("Capture: "):])
        cats = [c.lower() for c in self._location_categories.get(name, [])]
        if any("kingdom" in c for c in cats):
            kingdom, shine_id = self._split_kingdom_prefix(name)
            return ClassifiedItem(ItemKind.MOON, name, kingdom=kingdom, shine_id=shine_id)
        if "shop" in cats:
            kingdom, slot_label = self._split_kingdom_prefix(name)
            return ClassifiedItem(ItemKind.SHOP, name, kingdom=kingdom, shine_id=slot_label)
        return ClassifiedItem(ItemKind.OTHER, name)

    @staticmethod
    def _split_kingdom_prefix(name: str) -> tuple[str | None, str | None]:
        m = _LOC_PREFIX_RE.match(name)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None, name

    @staticmethod
    def _strip_prefix(name: str, prefixes: tuple[str, ...]) -> str:
        for p in prefixes:
            if name.startswith(p):
                return name[len(p):].strip()
        return name
