"""Shop moon label mapping for the bridge -> Switch `shop_labels` wire message.

SMO's Crazy Cap shops sell one Power Moon per kingdom for purple coins
(the AP location named "<Kingdom>: Shopping in <City>"). The Switch's
ShopItemMessageHook patches the two `al::getSystemMessageString` BL sites
inside `ShopLayoutInfo::updateItemPartsData` and substitutes the displayed
text for a slot when (file_name, key) is in the table this module builds.

The (file_name, key) for each shop slot is a SMO 1.0.0 constant — observed
empirically: the Switch logs each unique pair on first sighting via
SMOAP_LOG_INFO ("[shop-discovery] file='X' key='Y'"). Populate the dict
below from those logs the first time you walk into a Crazy Cap.

While the table is empty the wire-protocol plumbing still works end-to-end
(the Switch's lookupShopLabel returns null on miss, the hook falls through
to vanilla al::getSystemMessageString, every vanilla shop moon still
displays its original Nintendo name). Filling the table is a one-line
edit per kingdom and takes effect on the next HELLO replay.
"""

from __future__ import annotations

# AP location names use the "<Kingdom>: Shopping in <City>" convention — see
# apworld/smo_archipelago/data/locations.json. The kingdoms with shops, in
# in-game traversal order:
#   Cap, Cascade, Sand, Lake, Wooded, Lost, Metro, Snow, Seaside,
#   Luncheon, Bowser's.
# (Cloud, Ruined, Moon, Dark Side, Darker Side, Mushroom — no shops.)
#
# Shape: {AP location name: (file_name, key)}.
#
# Both file_name and key MUST match what SMO actually passes to
# al::getSystemMessageString at the patched BL sites — see the
# [shop-discovery] log lines. Until populated, the entry is "" / "" and
# the bridge skips it.
# Pattern observed 2026-05-26 via [shop-discovery] log in Cap Kingdom:
# the shop's purple-coin moon slot calls
# `al::getSystemMessageString("ItemMoon", "Moon<KingdomInternalCode>")`.
# The kingdom-internal codes match SMO's `<X>World` stage prefixes minus
# "World" (CapWorld → Cap, WaterfallWorld → Waterfall, ...). Cap is
# verified; the rest are educated guesses from those codes.
#
# Any miss will show as a fresh `[shop-discovery] file='ItemMoon' key='Moon<X>'`
# line on the next visit to that kingdom's shop — update the tuple here and
# the next HELLO replay (save-reload or AP reconnect) takes effect.
SHOP_LOCATION_TO_FILEKEY: dict[str, tuple[str, str]] = {
    "Cap: Shopping in Bonneton":             ("ItemMoon", "MoonCap"),
    "Cascade: Shopping in Fossil Falls":     ("ItemMoon", "MoonWaterfall"),
    "Sand: Shopping in Tostarena":           ("ItemMoon", "MoonSand"),
    "Lake: Shopping in Lake Lamode":         ("ItemMoon", "MoonLake"),
    "Wooded: Shopping in Steam Gardens":     ("ItemMoon", "MoonForest"),
    "Lost: Shopping on Forgotten Isle":      ("ItemMoon", "MoonClash"),
    "Metro: Shopping in New Donk City":      ("ItemMoon", "MoonCity"),
    "Snow: Shopping in Shiveria":            ("ItemMoon", "MoonSnow"),
    "Seaside: Shopping in Bubblaine":        ("ItemMoon", "MoonSea"),
    "Luncheon: Shopping in Mount Volbono":   ("ItemMoon", "MoonLava"),
    "Bowser's: Shopping at Bowser's Castle": ("ItemMoon", "MoonSky"),
}


def has_any_populated_keys() -> bool:
    """True iff any kingdom has its (file_name, key) tuple populated.

    Used by context.py to gate the [shop-labels] log line — when the table
    is still all-empty there's no point announcing "0 labels ready", the
    user is presumably about to populate it from the discovery logs.
    """
    return any(f and k for (f, k) in SHOP_LOCATION_TO_FILEKEY.values())
