"""Tolerant parser for Archipelago `_Spoiler.txt` files.

The spoiler format is mostly stable across recent AP versions but whitespace,
quoting, and arrow style vary slightly. The parser uses a forgiving regex
and bails with a clear error if the `Playthrough:` block is missing or empty
(the most common failure mode is `output_spoiler` set below 3 in host.yaml).

Only the bits we need are parsed:
  * Per-slot header: slot name, game name.
  * Playthrough block: sphere number -> list of (slot, game, location, item, recipient).

The full per-slot Locations listing is also captured so the sim knows which
specific items live in each coplayer's pool (and therefore can be routed to SMO).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SpherePlacement:
    sphere: int
    finder_slot: str
    finder_game: str
    location: str
    item: str
    recipient_slot: str


@dataclass
class SlotInfo:
    slot: str
    game: str
    # All (location, item, recipient) pairs found in this slot's "Locations:" block.
    locations: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class SpoilerData:
    spheres: list[SpherePlacement]
    slots: dict[str, SlotInfo]

    def smo_slot(self) -> SlotInfo:
        for s in self.slots.values():
            if s.game == "Manual_SMO_archipelago":
                return s
        raise ValueError(
            "no Manual_SMO_archipelago slot in spoiler — is the seed actually SMO?"
        )

    def items_routed_to(self, recipient: str) -> dict[str, list[SpherePlacement]]:
        """Group SpherePlacements by finder slot, restricted to those whose
        recipient is `recipient`. Useful for coplayer faucet construction."""
        out: dict[str, list[SpherePlacement]] = {}
        for p in self.spheres:
            if p.recipient_slot == recipient and p.finder_slot != recipient:
                out.setdefault(p.finder_slot, []).append(p)
        return out


# --- Parsing ---------------------------------------------------------------

# "Mario (Manual_SMO_archipelago):" anywhere a slot header sits.
_SLOT_HEADER_RE = re.compile(
    r"^(?P<slot>[^:()\n]+?)\s*\((?P<game>[^()]+)\):\s*$"
)

# Sphere line: "1: {" or " 1: {" (whitespace tolerant; brace may be on same or next line).
_SPHERE_OPEN_RE = re.compile(r"^\s*(?P<n>\d+):\s*\{?\s*$")

# Playthrough line: "Mario (Manual_SMO_archipelago): <middle> (Recipient)"
# where <middle> is either "Location -> Item" or "Location: Item". Location
# itself may contain colons (e.g. "Cap: Foo"), so we capture the whole middle
# and split it afterwards on `->` preferred, last `:` fallback.
_PLACE_LINE_RE = re.compile(
    r"^\s*(?P<finder>[^()\n]+?)\s*\((?P<game>[^()]+)\)\s*:\s*"
    r"(?P<middle>.+?)\s*\((?P<recipient>[^()]+)\)\s*,?\s*$"
)

# Indented detail line inside a slot's `Locations:` block.
_LOC_DETAIL_RE = re.compile(
    r"^\s+(?P<middle>.+?)\s*\((?P<recipient>[^()]+)\)\s*$"
)

# Known top-level section names that terminate the Locations / Playthrough
# section. Using an explicit list avoids matching slot headers like
# "Mario (Manual_SMO_archipelago):" as a terminator.
_KNOWN_SECTION_HEADERS = (
    "Playthrough", "Paths", "Hints", "Starting Items", "Locations",
    "Entrances", "Connections", "Hashes",
)
_SECTION_END_RE = re.compile(
    r"^(?:" + "|".join(re.escape(s) for s in _KNOWN_SECTION_HEADERS) + r"):\s*$",
    re.MULTILINE,
)


def _split_middle(middle: str) -> tuple[str, str] | None:
    """Split a playthrough/locations 'middle' into (location, item).

    Prefers ' -> ' (modern AP). Falls back to last ': ' (older AP / Locations
    section, where the location may itself contain colons).
    """
    if " -> " in middle:
        loc, item = middle.split(" -> ", 1)
        return loc.strip(), item.strip()
    if ": " in middle:
        loc, item = middle.rsplit(": ", 1)
        return loc.strip(), item.strip()
    return None


class SpoilerParseError(RuntimeError):
    pass


def parse_spoiler(path: str | Path) -> SpoilerData:
    """Parse an AP `_Spoiler.txt` and return its sphere + slot data.

    Raises SpoilerParseError on missing Playthrough block, empty Playthrough
    (means `output_spoiler` < 3 in host.yaml), or unparseable structure.
    """
    p = Path(path)
    if not p.exists():
        raise SpoilerParseError(f"spoiler not found: {p}")
    text = p.read_text(encoding="utf-8")

    slots = _parse_slot_headers(text)
    _populate_per_slot_locations(text, slots)
    spheres = _parse_playthrough(text)

    if not spheres:
        raise SpoilerParseError(
            f"spoiler {p.name} has no Playthrough block — regenerate with "
            "output_spoiler: 3 in host.yaml (the lower setting strips it)."
        )
    return SpoilerData(spheres=spheres, slots=slots)


def _parse_slot_headers(text: str) -> dict[str, SlotInfo]:
    """Scan the spoiler header for `Player N: <slot>` + `Game: <game>` pairs.

    AP header looks like:
        Player 1: Mario
        Game:                            Manual_SMO_archipelago
        ...

    Falls back to inferring slots from playthrough lines if the header section
    is missing (some custom spoiler writers omit it).
    """
    slots: dict[str, SlotInfo] = {}
    cur_slot: str | None = None
    for line in text.splitlines():
        m = re.match(r"^Player\s+\d+:\s*(?P<slot>.+?)\s*$", line)
        if m:
            cur_slot = m.group("slot").strip()
            continue
        m = re.match(r"^Game:\s*(?P<game>.+?)\s*$", line)
        if m and cur_slot is not None:
            slots[cur_slot] = SlotInfo(slot=cur_slot, game=m.group("game").strip())
            cur_slot = None
    return slots


def _populate_per_slot_locations(text: str, slots: dict[str, SlotInfo]) -> None:
    """Capture each slot's `Locations:` section.

    Format:
        Locations:

        Mario (Manual_SMO_archipelago):
            Cap: Frog-Jumping Above the Fog: Power Moon (Mario)
            ...

    Used by the sim to know each coplayer's full pool size so we can compute
    the SMO-bound ratio for the coplayer faucet.
    """
    loc_section_re = re.compile(r"^Locations:\s*$", re.MULTILINE)
    m = loc_section_re.search(text)
    if not m:
        return
    tail = text[m.end():]
    # Cut at the next top-level section heading (using a known-headers
    # whitelist so slot lines like "Mario (Manual_SMO_archipelago):" don't
    # accidentally terminate the section).
    next_section = _SECTION_END_RE.search(tail, pos=1)
    section = tail[: next_section.start()] if next_section else tail

    cur: SlotInfo | None = None
    for raw in section.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if not raw.startswith((" ", "\t")):
            mh = _SLOT_HEADER_RE.match(line)
            if mh:
                slot_name = mh.group("slot").strip()
                cur = slots.get(slot_name)
                if cur is None:
                    cur = SlotInfo(slot=slot_name, game=mh.group("game").strip())
                    slots[slot_name] = cur
                continue
        if cur is None:
            continue
        mp = _LOC_DETAIL_RE.match(raw)
        if mp:
            parts = _split_middle(mp.group("middle"))
            if parts:
                cur.locations.append((parts[0], parts[1], mp.group("recipient").strip()))


def _parse_playthrough(text: str) -> list[SpherePlacement]:
    pt_re = re.compile(r"^Playthrough:\s*$", re.MULTILINE)
    m = pt_re.search(text)
    if not m:
        return []
    tail = text[m.end():]
    nxt = _SECTION_END_RE.search(tail, pos=1)
    section = tail[: nxt.start()] if nxt else tail

    out: list[SpherePlacement] = []
    cur_sphere: int | None = None
    for raw in section.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m_sph = _SPHERE_OPEN_RE.match(line)
        if m_sph:
            cur_sphere = int(m_sph.group("n"))
            continue
        if line.strip() in ("{", "}"):
            continue
        m_place = _PLACE_LINE_RE.match(line)
        if m_place and cur_sphere is not None:
            parts = _split_middle(m_place.group("middle"))
            if not parts:
                continue
            location, item = parts
            out.append(SpherePlacement(
                sphere=cur_sphere,
                finder_slot=m_place.group("finder").strip(),
                finder_game=m_place.group("game").strip(),
                location=location,
                item=item,
                recipient_slot=m_place.group("recipient").strip(),
            ))
    return out
