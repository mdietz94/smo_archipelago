"""Channel A display-text formatting.

Composes the in-game cutscene label that replaces SMO's "Power Moon"
text when Mario collects a moon. The Switch hook (`MoonLabelHook`) calls
`al::setPaneStringFormat(layout, "TxtScenario", "%s", text)` with the
text this module produces.

`TxtScenario` is the same pane SMO uses for the moon name; SMO's own
font + width budget is the constraint, so we truncate by *bytes* (UTF-8)
rather than codepoints to be safe with the font texture. 30 bytes lines
up with the pane width for the SMO 1.0.0 stage-clear layout (empirically
matches the longest vanilla scenario names, e.g. "Smart Bombing").
"""

from __future__ import annotations

from .datapackage import ClassifiedItem
from .protocol import ItemKind

# Hard caps. Values intentionally short — the Switch buffer is 32 bytes
# (`char text[32]` in PendingMoonLabel) including the null terminator, so
# 30 chars is the practical max before the C++ side trims further.
MAX_MOON_LABEL_BYTES = 30
ELLIPSIS = "…"  # U+2026 (3 bytes UTF-8)

# Kingdom prefix shortcuts. Keeps "Sand Kingdom Power Moon" → "Sand Power
# Moon" rather than spending half the label on "Kingdom". The mapping
# matches apworld's canonical 17 kingdoms + "Mush" (Peach Tutorial).
# Unknown prefixes pass through unmodified so future apworld expansions
# don't silently break.
_KINGDOM_SHORT = {
    # Identity for short ones — listed so additions stay symmetric.
    "Cap": "Cap",
    "Cascade": "Cascade",
    "Sand": "Sand",
    "Lake": "Lake",
    "Wooded": "Wooded",
    "Cloud": "Cloud",
    "Lost": "Lost",
    "Metro": "Metro",
    "Snow": "Snow",
    "Seaside": "Seaside",
    "Luncheon": "Luncheon",
    "Ruined": "Ruined",
    "Bowser's": "Bowser's",
    "Bowser": "Bowser",
    "Moon": "Moon",
    "Mushroom": "Mushroom",
    "Dark Side": "Dark Side",
    "Darker Side": "Darker",  # one of the few worth abbreviating
}


def truncate_utf8(s: str, max_bytes: int = MAX_MOON_LABEL_BYTES) -> str:
    """Return `s` clipped to ≤ max_bytes UTF-8 bytes.

    When clipping is needed, the result ends with U+2026 (…). The ellipsis
    itself costs 3 bytes; if max_bytes < 3 the truncation degrades to a
    byte-exact prefix (no ellipsis room).

    Will never split a UTF-8 codepoint. Safe to feed directly into a
    null-terminated C buffer of size max_bytes + 1.
    """
    if not s:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s

    if max_bytes < 3:
        # Not enough room for ellipsis; just byte-trim.
        # Walk back to a codepoint boundary.
        cut = max_bytes
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        return encoded[:cut].decode("utf-8", errors="ignore")

    budget = max_bytes - len(ELLIPSIS.encode("utf-8"))
    cut = budget
    # Back up to a codepoint boundary (first byte of a UTF-8 sequence
    # has its top two bits != 0b10).
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="ignore") + ELLIPSIS


def _short_kingdom(name: str | None) -> str | None:
    if not name:
        return None
    return _KINGDOM_SHORT.get(name, name)


def _shorten_item_name(item: ClassifiedItem) -> str:
    """Compact human label for a classified item.

    Examples:
      ClassifiedItem(MOON, "Cascade Kingdom Power Moon", kingdom="Cascade",
                     shine_id="Power Moon")   → "Cascade Power Moon"
      ClassifiedItem(MOON, "Power Moon", kingdom=None, shine_id="Power Moon")
                                                → "Power Moon"
      ClassifiedItem(CAPTURE, "Goomba", cap="Goomba")  → "Goomba"
      ClassifiedItem(OTHER, "Sphynx's Treasure Vault")          → name as-is

    Falls back to `item.name` for anything unrecognized.
    """
    if item.kind == ItemKind.MOON:
        k = _short_kingdom(item.kingdom)
        body = item.shine_id or "Power Moon"
        if k:
            return f"{k} {body}"
        return body
    if item.kind == ItemKind.CAPTURE:
        return item.cap or item.name
    return item.name


def format_moon_label(
    item: ClassifiedItem,
    recipient_slot: str,
    me_slot: str | None,
    max_bytes: int = MAX_MOON_LABEL_BYTES,
) -> str:
    """Channel A text for the moon Mario just collected.

    The convention:
      * routes to me  → "Got <name>!"
      * routes to other → "Sent <name> -> <slot>"

    Recipient and own-slot are compared as strings. When `me_slot` is None
    (no auth yet, shouldn't happen by the time a moon is collected) the
    "routes to me" check is skipped — anything that's not labelled as
    routing to me reads as outgoing.

    The arrow is the ASCII `->` (not U+2192) because SMO's stage-clear
    font doesn't ship the Unicode arrow glyph; the fallback is a tofu box.
    """
    body = _shorten_item_name(item)
    if me_slot is not None and recipient_slot == me_slot:
        text = f"Got {body}!"
    else:
        text = f"Sent {body} -> {recipient_slot}"
    return truncate_utf8(text, max_bytes)
