"""Bridge-side resolution of raw SMO identifiers to apworld-canonical names.

The Switch sends raw identifiers it can read off SMO's own structs:
  - moons:    {stage_name, object_id, shine_uid}
  - captures: {hack_name}

The bridge translates those into the names the AP DataPackage uses:
  - moons    -> (kingdom, shine_id)  e.g. ("Cap", "Our First Power Moon")
  - captures -> cap                  e.g. "Goomba"

The lookup tables live as JSON files alongside this module under data/ so they
can be hand-edited without rebuilding the Switch module. Unknown raw IDs are
logged loudly so the user knows what to add.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MoonResolution:
    kingdom: str
    shine_id: str


class ShineMap:
    """Resolve (stage_name, object_id) -> (kingdom, shine_id).

    JSON schema (a list of objects):
      [
        {"stage_name":"CapWorldHomeStage", "object_id":"MoonOurFirst",
         "kingdom":"Cap", "shine_id":"Our First Power Moon"}
      ]

    Lookup key is (stage_name, object_id). shine_uid is accepted for future
    fallback lookups but not part of the primary key today.
    """

    def __init__(self, path: Path | None = None):
        self._by_pair: dict[tuple[str, str], MoonResolution] = {}
        self._by_uid: dict[int, MoonResolution] = {}
        # Inverse for LocationScouts -> shine_uid resolution: (kingdom, shine_id)
        # is the canonical AP location form, shine_uid is what the Switch keys
        # the palette table by. Built at load() time alongside the forward maps.
        self._uid_by_location: dict[tuple[str, str], int] = {}
        self._source = path
        if path is not None and path.exists():
            self.load(path)

    @classmethod
    def from_package(cls, package: str, filename: str = "shine_map.json") -> "ShineMap":
        """Load via importlib.resources so it works for both loose-source
        and zipped apworld installs. Used by the Launcher-spawned client
        because the apworld zip's internal paths don't resolve via
        Path.exists()."""
        m = cls()
        from importlib.resources import files
        try:
            text = files(package).joinpath("client", "data", filename).read_text(encoding="utf-8")
        except (ModuleNotFoundError, FileNotFoundError, OSError):
            log.warning("ShineMap: %s missing from package %r", filename, package)
            return m
        m._load_text(text, source=f"{package}:client/data/{filename}")
        return m

    def load(self, path: Path) -> None:
        self._load_text(path.read_text(encoding="utf-8"), source=str(path))

    def _load_text(self, text: str, *, source: str) -> None:
        entries = json.loads(text)
        if not isinstance(entries, list):
            raise ValueError(f"{source}: expected a JSON list")
        for e in entries:
            stage = e.get("stage_name")
            obj = e.get("object_id")
            kingdom = e.get("kingdom")
            shine = e.get("shine_id")
            if not (stage and obj and kingdom and shine):
                continue
            res = MoonResolution(kingdom=kingdom, shine_id=shine)
            self._by_pair[(stage, obj)] = res
            uid = e.get("shine_uid")
            if isinstance(uid, int):
                self._by_uid[uid] = res
                self._uid_by_location[(kingdom, shine)] = uid
        log.info("ShineMap loaded %d entries from %s", len(self._by_pair), source)

    def resolve(
        self,
        stage_name: str | None,
        object_id: str | None,
        shine_uid: int | None = None,
    ) -> MoonResolution | None:
        if stage_name and object_id:
            res = self._by_pair.get((stage_name, object_id))
            if res is not None:
                return res
        if isinstance(shine_uid, int) and shine_uid >= 0:
            res = self._by_uid.get(shine_uid)
            if res is not None:
                return res
        return None

    def resolve_uid_by_location(
        self,
        kingdom: str | None,
        shine_id: str | None,
    ) -> int | None:
        """Inverse of resolve(): (kingdom, shine_id) -> shine_uid.

        Used by the LocationScouts handler to key per-classification palette
        entries by the same uid the Switch's MoonGetHook reports.
        """
        if not (kingdom and shine_id):
            return None
        return self._uid_by_location.get((kingdom, shine_id))


class CaptureMap:
    """Resolve raw hack_name -> apworld-canonical cap name (and vice versa).

    Default pass-through: if a hack_name isn't in the table we return it
    unchanged (most match 1:1 between SMO internals and apworld items.json).

    JSON schema (a list of objects):
      [
        {"hack_name":"Kuribo", "cap":"Goomba"}
      ]

    The reverse direction (cap -> hack_name) is used by M6 phase B item
    application: when AP grants a capture item, the bridge needs to send
    the raw SMO hack_name on the wire so the mod can feed it into
    GameDataFunction::addHackDictionary.
    """

    def __init__(self, path: Path | None = None):
        self._table: dict[str, str] = {}        # hack_name -> cap
        self._reverse: dict[str, str] = {}      # cap -> hack_name
        self._source = path
        if path is not None and path.exists():
            self.load(path)

    @classmethod
    def from_package(cls, package: str, filename: str = "capture_map.json") -> "CaptureMap":
        """Load via importlib.resources so it works for both loose-source
        and zipped apworld installs (see ShineMap.from_package)."""
        m = cls()
        from importlib.resources import files
        try:
            text = files(package).joinpath("client", "data", filename).read_text(encoding="utf-8")
        except (ModuleNotFoundError, FileNotFoundError, OSError):
            log.warning("CaptureMap: %s missing from package %r", filename, package)
            return m
        m._load_text(text, source=f"{package}:client/data/{filename}")
        return m

    def load(self, path: Path) -> None:
        self._load_text(path.read_text(encoding="utf-8"), source=str(path))

    def _load_text(self, text: str, *, source: str) -> None:
        entries = json.loads(text)
        if not isinstance(entries, list):
            raise ValueError(f"{source}: expected a JSON list")
        for e in entries:
            hack = e.get("hack_name")
            cap = e.get("cap")
            if hack and cap:
                self._table[hack] = cap
                # First write wins if a single cap maps from multiple
                # hack_names (rare; aliases in the M5.8 extractor handle
                # the known cases).
                self._reverse.setdefault(cap, hack)
        log.info("CaptureMap loaded %d entries from %s "
                 "(%d unique caps in reverse map)",
                 len(self._table), source, len(self._reverse))

    def resolve(self, hack_name: str | None) -> str | None:
        if not hack_name:
            return None
        return self._table.get(hack_name, hack_name)

    def cap_to_hack(self, cap: str | None) -> str | None:
        """Reverse lookup: apworld cap name -> raw SMO hack_name.

        Returns None when the cap isn't in the table — caller decides
        whether to fall through (pass `cap` directly to addHackDictionary,
        works for the ~36/42 captures whose names are 1:1) or to drop the
        item with a log line. M6 phase B picks the former: empty maps
        gracefully degrade to identity.
        """
        if not cap:
            return None
        return self._reverse.get(cap, cap)

    def iter_all(self) -> list[tuple[str, str]]:
        """Return every (cap, hack_name) pair in deterministic order.

        Used by the capturesanity-OFF replay path in switch_server.py:
        when the AP option is disabled, the bridge synthesizes one
        ItemMsg per pair so the Switch's captures_unlocked bitset gets
        every bit set (otherwise CaptureStartHook blocks every capture).
        """
        return sorted(self._reverse.items())
