from typing import Optional
from BaseClasses import MultiWorld
from ..Locations import SMOLocation
from ..Items import SMOItem


# Peace categories gated by per-kingdom toggles. The kingdom toggle alone
# would suffice via categories.json::yaml_option, but the dispatcher below
# uses the table to special-case SHARED_PEACE_CATEGORY (OR of two toggles).
PEACE_CATEGORY_TO_OPTION = {
    "Cap Peace": "include_cap_peace_moons",
    "Cascade Peace": "include_cascade_peace_moons",
    "Sand Peace": "include_sand_peace_moons",
    "Lake Peace": "include_lake_peace_moons",
    "Wooded Peace": "include_wooded_peace_moons",
    "Lost Peace": "include_lost_peace_moons",
    "Metro Peace": "include_metro_peace_moons",
    "Snow Peace": "include_snow_peace_moons",
    "Seaside Peace": "include_seaside_peace_moons",
    "Luncheon Peace": "include_luncheon_peace_moons",
    "Bowser's Peace": "include_bowsers_peace_moons",
    "Cloud Peace": "include_cloud_peace_moons",
}
SHARED_PEACE_CATEGORY = "Snow/Seaside Peace"


# Use this if you want to override the default behavior of is_option_enabled
# Return True to enable the category, False to disable it, or None to use the default behavior
def before_is_category_enabled(multiworld: MultiWorld, player: int, category_name: str) -> Optional[bool]:
    from ..Helpers import is_option_enabled

    if category_name == SHARED_PEACE_CATEGORY:
        # "Secret Path to Lake Lamode!" and "Secret Path to the Steam Gardens!"
        # are reachable from either Snow or Seaside after that kingdom is at
        # peace -- include if EITHER per-kingdom toggle is on.
        return (
            is_option_enabled(multiworld, player, "include_snow_peace_moons")
            or is_option_enabled(multiworld, player, "include_seaside_peace_moons")
        )

    return None

# Use this if you want to override the default behavior of is_option_enabled
# Return True to enable the item, False to disable it, or None to use the default behavior
def before_is_item_enabled(multiworld: MultiWorld, player: int, item: SMOItem) -> Optional[bool]:
    return None

# Use this if you want to override the default behavior of is_option_enabled
# Return True to enable the location, False to disable it, or None to use the default behavior
def before_is_location_enabled(multiworld: MultiWorld, player: int, location: SMOLocation) -> Optional[bool]:
    return None
