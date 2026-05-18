"""AP datapackage loader + classifier.

Two sources combine:
  1. The DataPackage the AP server sends us at runtime — definitive name <-> id.
  2. The vendored apworld's data/{items,locations,categories}.json for category info
     AP doesn't carry. We use this to classify items into Moon/Capture/Kingdom
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

# Locations use ": " separator: "Cap: Frog-Jumping Above the Fog" ->
#   kingdom="Cap", shine_id="Frog-Jumping Above the Fog".
_LOC_PREFIX_RE = re.compile(r"^([A-Za-z' ]+):\s*(.+)$")

# Items use " Kingdom " (space-separated, no colon):
#   "Cap Kingdom Power Moon" -> kingdom="Cap",     shine_id="Power Moon"
#   "Cascade Kingdom Multi-Moon" -> kingdom="Cascade", shine_id="Multi-Moon"
# Non-greedy head captures multi-word kingdom names like "Dark Side".
_ITEM_MOON_KINGDOM_RE = re.compile(r"^(.+?) Kingdom (Power Moon|Multi-Moon)$")


@dataclass
class ClassifiedItem:
    kind: ItemKind
    name: str
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None

    def to_ref(self) -> ItemRef:
        return ItemRef(
            kind=self.kind.value,
            kingdom=self.kingdom,
            shine_id=self.shine_id,
            cap=self.cap,
            name=self.name,
        )


class DataPackage:
    """Wraps the AP datapackage plus apworld category info."""

    def __init__(
        self,
        apworld_data_dir: Path | None = None,
        *,
        apworld_package: str | None = None,
    ):
        """Load the apworld's items.json + locations.json categories.

        Two sources, tried in order. If both are None, nothing is loaded
        (best-effort fallback — every item then classifies as OTHER).

          apworld_data_dir: filesystem path to the apworld's `data/`
            directory. Used for the loose-source dev path and unit tests.

          apworld_package: import path of the apworld package (e.g.
            "worlds.smo" when running from the .apworld zip — Archipelago
            derives the module name from the zip stem `smo.apworld`, or
            "smo_archipelago" from a loose source on sys.path — the in-repo
            folder kept its historical name). Loaded via importlib.resources
            so it works whether the package is on the filesystem OR inside a
            zip — that's what the Launcher-spawned client needs because the
            apworld zip in custom_worlds/ isn't a real directory and
            `Path.exists()` returns False on virtual zip paths.
        """
        self.item_id_to_name: dict[int, str] = {}
        self.location_id_to_name: dict[int, str] = {}
        self.item_name_to_id: dict[str, int] = {}
        self.location_name_to_id: dict[str, int] = {}

        # Built from the apworld's items.json / locations.json / categories.json
        self._item_categories: dict[str, list[str]] = {}
        self._location_categories: dict[str, list[str]] = {}

        if apworld_data_dir is not None:
            self._load_apworld(apworld_data_dir)
        elif apworld_package is not None:
            self._load_apworld_from_package(apworld_package)

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

    def _load_apworld_from_package(self, package: str) -> None:
        """Load items.json + locations.json via importlib.resources.

        Works for both loose-source (filesystem) and zipped apworld
        installations. The package argument is the import name of the
        apworld root package (e.g. "worlds.smo").
        """
        from importlib.resources import files
        try:
            data_root = files(package).joinpath("data")
        except (ModuleNotFoundError, AttributeError):
            log.warning("apworld package %r not importable; categories empty", package)
            return
        for filename, target in (
            ("items.json", self._item_categories),
            ("locations.json", self._location_categories),
        ):
            try:
                text = data_root.joinpath(filename).read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                log.warning("apworld %s missing from package %r", filename, package)
                continue
            for entry in json.loads(text):
                name = entry.get("name")
                if name:
                    target[name] = entry.get("category", []) or []
        log.info(
            "DataPackage loaded from package %r: %d items, %d locations",
            package, len(self._item_categories), len(self._location_categories),
        )

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
        # Upstream uses "Moon", "Capture", "post-metro".
        if "capture" in cats:
            # Capture items are bare enemy names (e.g. "Goomba", "Paragoomba").
            return ClassifiedItem(ItemKind.CAPTURE, name, cap=name)
        if "moon" in cats:
            # Items use " Kingdom " separator, not ": " (that's location form).
            m = _ITEM_MOON_KINGDOM_RE.match(name)
            if m:
                return ClassifiedItem(ItemKind.MOON, name,
                                      kingdom=m.group(1).strip(),
                                      shine_id=m.group(2))
            return ClassifiedItem(ItemKind.OTHER, name)
        if "kingdom" in cats or "kingdom unlock" in cats:
            return ClassifiedItem(ItemKind.KINGDOM, name, kingdom=self._strip_prefix(name, ("Kingdom: ", "Unlock: ")))
        return ClassifiedItem(ItemKind.OTHER, name)

    def moon_pool_counts_by_kingdom(self) -> dict[str, int]:
        """Return per-kingdom totals for moon items in the AP pool.

        Counts every item in `item_id_to_name` whose name matches the
        `<Kingdom> Kingdom {Power Moon,Multi-Moon}` form, weighting
        Multi-Moon entries by 3 to reflect the moon-credit grant the
        Switch applies. Returns {} until AP datapackage lands (Connected).

        Used by the Odyssey tab as the "/ pool" denominator next to the
        collected/received counters. Recomputed on every refresh — cheap
        (datapackage is dict-of-int, ~70 entries for SMO) and dodges
        cache-invalidation when the apworld pool changes between runs.
        """
        counts: dict[str, int] = {}
        for name in self.item_id_to_name.values():
            m = _ITEM_MOON_KINGDOM_RE.match(name)
            if not m:
                continue
            kingdom = m.group(1).strip()
            weight = 3 if m.group(2) == "Multi-Moon" else 1
            counts[kingdom] = counts.get(kingdom, 0) + weight
        return counts

    def classify_location(self, name: str) -> ClassifiedItem:
        # Locations have category tags like "Cap Kingdom", "Cascade Kingdom",
        # "Capture", etc. We classify by the prefix on the location name (e.g.
        # "Cap: …", "Capture: …", "Cascade: …") since that's what the Switch
        # will reconstruct.
        if name.startswith("Capture: "):
            return ClassifiedItem(ItemKind.CAPTURE, name, cap=name[len("Capture: "):])
        cats = [c.lower() for c in self._location_categories.get(name, [])]
        if any("kingdom" in c for c in cats):
            kingdom, shine_id = self._split_kingdom_prefix(name)
            return ClassifiedItem(ItemKind.MOON, name, kingdom=kingdom, shine_id=shine_id)
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
