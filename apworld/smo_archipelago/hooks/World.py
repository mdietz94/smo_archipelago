# Object classes from AP core, to represent an entire MultiWorld and this individual World that's part of it
from worlds.AutoWorld import World
from BaseClasses import ItemClassification, MultiWorld

# Item/Location subclasses extending AP core, used during generation
from ..Items import SMOItem
from ..Locations import SMOLocation

# Apworld data: game_table is the inlined dict in Data.py; the rest are loaded
# from data/items.json, data/locations.json, data/regions.json.
from ..Data import game_table, item_table, location_table, region_table

# These helper methods allow you to determine if an option has been set, or what its value is, for any player in the multiworld
from ..Helpers import is_location_enabled, get_option_value

# calling logging.info("message") anywhere below in this file will output the message to both console and log file
import logging


# Thresholds from KingdomMoons(K, N) clauses in data/regions.json. These are
# the per-kingdom effective-moon counts needed to leave that kingdom for the
# next, where each Multi-Moon = 3 effective and each Power Moon = 1. Mirrored
# here so after_create_items can demote surplus moons from progression to
# useful, freeing locations for toggle-driven location reductions to trim. The
# test_kingdom_moon_demotion sweep keeps this table in sync with regions.json.
KINGDOM_MOON_GATES = {
    "Cascade":  5,
    "Sand":    16,
    "Lake":     8,
    "Wooded":  16,
    "Lost":    10,
    "Metro":   20,
    "Snow":    10,
    "Seaside": 10,
    "Luncheon": 18,
    "Ruined":   3,
    "Bowser's": 8,
}

# Per-kingdom Moon-item-count cap, one Range option each. Mirrored to
# KINGDOM_MOON_GATES so every gated kingdom has a corresponding cap. The
# option floors (range_start in hooks/Options.py) are sized to leave the
# gate satisfiable under the MM-greedy trim strategy below; the option
# defaults (range_end == default) leave the pool identical to today.
# tests/test_kingdom_moon_count.py keeps the option floors/ends in sync
# with items.json and this table.
KINGDOM_MOON_COUNT_OPTIONS = {
    "Cascade":  "cascade_moon_count",
    "Sand":     "sand_moon_count",
    "Lake":     "lake_moon_count",
    "Wooded":   "wooded_moon_count",
    "Lost":     "lost_moon_count",
    "Metro":    "metro_moon_count",
    "Snow":     "snow_moon_count",
    "Seaside":  "seaside_moon_count",
    "Luncheon": "luncheon_moon_count",
    "Ruined":   "ruined_moon_count",
    "Bowser's": "bowsers_moon_count",
}

# Items dropped from the pool under the festival goal (Goal.option_festival).
# Post-Metro kingdoms are emptied of locations in create_regions, so their
# moon items have nowhere to land — adjust_filler_items would otherwise log
# "more items than locations" and start randomly trimming. The 15 captures
# below are exclusive to post-Metro kingdoms; the moon items cover every
# kingdom past Metro in the regions.json chain.
FESTIVAL_ITEMS_TO_DROP = frozenset({
    "Snow Kingdom Power Moon", "Snow Kingdom Multi-Moon",
    "Seaside Kingdom Power Moon", "Seaside Kingdom Multi-Moon",
    "Luncheon Kingdom Power Moon", "Luncheon Kingdom Multi-Moon",
    "Ruined Kingdom Power Moon", "Ruined Kingdom Multi-Moon",
    "Bowser's Kingdom Power Moon", "Bowser's Kingdom Multi-Moon",
    "Moon Kingdom Power Moon",
    "Ty-foo", "Shiverian Racer", "Snow Cheep Cheep", "Gushen",
    "Lava Bubble", "Volbonan", "Hammer Bro", "Meat", "Fire Piranha Plant",
    "Pokio", "Jizo", "Bowser Statue", "Parabones", "Banzai Bill",
    "Chargin' Chuck",
})

########################################################################################
## Order of method calls when the world generates:
##    1. create_regions - Creates regions and locations
##    2. create_items - Creates the item pool
##    3. set_rules - Creates rules for accessing regions and locations
##    4. generate_basic - Runs any post item pool options, like place item/category
##    5. pre_fill - Creates the victory location
##
## The create_item method is used by plando and start_inventory settings to create an item from an item name.
## The fill_slot_data method will be used to send data to the SMO client for later use, like deathlink.
########################################################################################



# Called before regions and locations are created. Victory location is included, but Victory event is not placed yet.
def before_create_regions(world: World, multiworld: MultiWorld, player: int):
    pass

# Called after regions and locations are created, in case you want to see or modify that information. Victory location is included.
def after_create_regions(world: World, multiworld: MultiWorld, player: int):
    # Every location whose category set resolves "disabled" via the generic
    # category/yaml_option machinery (see Helpers.is_location_enabled and the
    # before_is_category_enabled hook below) is removed here. Adding a new
    # toggle is now a pure data edit: tag the category in categories.json with
    # a yaml_option, tag affected locations, and you're done.
    locationNamesToRemove = [
        location["name"]
        for location in world.location_table
        if not is_location_enabled(multiworld, player, location)
    ]

    for region in multiworld.regions:
        if region.player == player:
            for location in list(region.locations):
                if location.name in locationNamesToRemove:
                    region.locations.remove(location)
    if hasattr(multiworld, "clear_location_cache"):
        multiworld.clear_location_cache()

def _demote_surplus_kingdom_moons(item_pool: list) -> None:
    """Demote surplus per-kingdom progression moons to useful in place.

    Items.json marks every kingdom moon as `progression: true`, but each
    gated kingdom's KingdomMoons(K, N) rule only needs N effective moons
    reachable. The surplus contributes nothing to reachability but blocks
    adjust_filler_items from trimming the pool when toggles drop the
    location count — it pops fillers/traps/useful but never progression.
    """
    for kingdom, threshold in KINGDOM_MOON_GATES.items():
        pm_name = f"{kingdom} Kingdom Power Moon"
        mm_name = f"{kingdom} Kingdom Multi-Moon"
        prog_mms = [it for it in item_pool
                    if it.name == mm_name and it.classification == ItemClassification.progression]
        prog_pms = [it for it in item_pool
                    if it.name == pm_name and it.classification == ItemClassification.progression]
        # MMs first since each is worth 3 effective; minimize kept count.
        mms_kept = min(len(prog_mms), threshold // 3)
        pms_kept = min(len(prog_pms), max(0, threshold - 3 * mms_kept))
        for it in prog_mms[mms_kept:]:
            it.classification = ItemClassification.useful
        for it in prog_pms[pms_kept:]:
            it.classification = ItemClassification.useful


# The item pool before starting items are processed, in case you want to see the raw item pool at that stage
def before_create_items_starting(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:
    # Under the festival goal, post-Metro locations are removed in
    # create_regions, so post-Metro kingdoms' moons and the 15 captures
    # exclusive to those kingdoms have nowhere to land. Drop them now.
    #
    # Metro Kingdom Power/Multi-Moons also get reclassified to filler:
    # festival is reached from inside Metro, so nothing downstream consumes
    # a `KingdomMoons(Metro, N)` gate, and leaving them as progression
    # forces adjust_filler_items to leave the surplus in the pool (it pops
    # filler/trap/useful, never progression). With them as filler, the
    # pool trims down cleanly to the smaller location count.
    if get_option_value(multiworld, player, "goal") == 1:
        item_pool[:] = [it for it in item_pool if it.name not in FESTIVAL_ITEMS_TO_DROP]
        for it in item_pool:
            if it.name in ("Metro Kingdom Power Moon", "Metro Kingdom Multi-Moon"):
                it.classification = ItemClassification.filler
    return item_pool

def _trim_kingdom_moons_to_options(item_pool: list, multiworld: MultiWorld, player: int) -> None:
    """Drop surplus per-kingdom Moon items down to the option-configured cap.

    Power Moons are dropped first so Multi-Moons (worth 3 effective each toward
    the KingdomMoons(K, N) gate) are preserved; this keeps the demotion in
    _demote_surplus_kingdom_moons able to select a gate-satisfying progression
    subset even at the option floor. adjust_filler_items in __init__.py refills
    the freed pool slots with filler — same total check count, just fewer
    kingdom-flavored Moon items received.
    """
    for kingdom, opt_name in KINGDOM_MOON_COUNT_OPTIONS.items():
        target = get_option_value(multiworld, player, opt_name)
        pm_name = f"{kingdom} Kingdom Power Moon"
        mm_name = f"{kingdom} Kingdom Multi-Moon"
        pm_indices = [i for i, it in enumerate(item_pool) if it.name == pm_name]
        mm_indices = [i for i, it in enumerate(item_pool) if it.name == mm_name]
        current = len(pm_indices) + len(mm_indices)
        if current <= target:
            continue
        to_drop = current - target
        # Drop PMs first; only dip into MMs once PMs are exhausted. Pop in
        # descending index order so earlier pops don't shift the later ones.
        pms_to_drop = pm_indices[: min(to_drop, len(pm_indices))]
        mms_to_drop = mm_indices[: max(0, to_drop - len(pm_indices))]
        for i in sorted(pms_to_drop + mms_to_drop, reverse=True):
            item_pool.pop(i)


# The item pool after starting items are processed but before filler is added, in case you want to see the raw item pool at that stage
def before_create_items_filler(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:
    # Apply the per-kingdom moon-count caps before adjust_filler_items runs
    # in create_items: the trim leaves locations > items, which then triggers
    # adjust_filler_items' top-up branch (filler / traps). Runs before
    # after_create_items so _demote_surplus_kingdom_moons sees the trimmed pool.
    _trim_kingdom_moons_to_options(item_pool, multiworld, player)
    return item_pool

# The complete item pool prior to being set for generation is provided here, in case you want to make changes to it
def after_create_items(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:
    # See _demote_surplus_kingdom_moons for the why. Runs in every mode so
    # the default goal still benefits from the demotion (which is what the
    # `all_off` peace-toggle scenarios rely on).
    _demote_surplus_kingdom_moons(item_pool)
    return item_pool

# Called before rules for accessing regions and locations are created.
def before_set_rules(world: World, multiworld: MultiWorld, player: int):
    pass

# Locations whose Multi Moon / Power Moon can become PERMANENTLY UNOBTAINABLE on
# SMO 1.0.0 via documented sequence-break tricks. Marked `filler_only: true` in
# locations.json so the AP fill never places a progression item there — a player
# who hits the skip wouldn't be able to send the check, and a soft-lock would be
# unrecoverable on 1.0.0.
#
# Why these two (both Cascade Kingdom, 1.0.0-only):
#  - "Our First Power Moon": First Moon Skip (smo.wiki/First_Moon_Skip) — on
#    1.0.0 the Madame Broode loading zone is active before the first moon
#    spawns. Defeating her without first collecting it permanently invalidates
#    the moon's cutscene; trying to collect it later crashes the game and the
#    moon never registers in the save.
#  - "Multi Moon Atop the Falls": Broode Skip (smo.wiki/Broode_Skip) — collect
#    5 regular Power Moons via the 2P warp-painting trick and you can leave
#    Cascade without ever fighting Madame Broode. On 1.0.0 the Multi Moon is
#    then unobtainable for the rest of the save (1.0.1+ auto-awards it on
#    return; we target 1.0.0).
#
# Every other kingdom: per Mario Wiki Missable_content and the 1.0.0 / 1.0.1
# patch notes, no other moon is permanently missable in normal play or via
# documented 1.0.0 skips. The Cookatiel-fight / Big-Pot pair in Luncheon shares
# QuestNo 2->3 (only one collection advances scenario_no), but both moons stay
# physically collectible in either order, so neither is missable.
def _apply_filler_only_rules(world: World, multiworld: MultiWorld, player: int) -> None:
    filler_only_names = {
        loc["name"] for loc in world.location_table
        if loc.get("filler_only", False)
    }
    if not filler_only_names:
        return
    from worlds.generic.Rules import add_item_rule
    for region in multiworld.regions:
        if region.player != player:
            continue
        for location in region.locations:
            if location.name in filler_only_names:
                add_item_rule(location, lambda item: not item.advancement)


# Called after rules for accessing regions and locations are created, in case you want to see or modify that information.
def after_set_rules(world: World, multiworld: MultiWorld, player: int):
    _apply_filler_only_rules(world, multiworld, player)

# The item name to create is provided before the item is created, in case you want to make changes to it
def before_create_item(item_name: str, world: World, multiworld: MultiWorld, player: int) -> str:
    return item_name

# The item that was created is provided after creation, in case you want to modify the item
def after_create_item(item: SMOItem, world: World, multiworld: MultiWorld, player: int) -> SMOItem:
    return item

# This method is run towards the end of pre-generation, before the place_item options have been handled and before AP generation occurs
def before_generate_basic(world: World, multiworld: MultiWorld, player: int) -> list:
    pass

# This method is run at the very end of pre-generation, once the place_item options have been handled and before AP generation occurs
def after_generate_basic(world: World, multiworld: MultiWorld, player: int):
    pass

# This is called before slot data is set and provides an empty dict ({}), in case you want to modify it before the world fills it
def before_fill_slot_data(slot_data: dict, world: World, multiworld: MultiWorld, player: int) -> dict:
    return slot_data

# This is called after slot data is set and provides the slot data at the time, in case you want to check and modify it after the world fills it
def after_fill_slot_data(slot_data: dict, world: World, multiworld: MultiWorld, player: int) -> dict:
    # When talkatoo_mode is on, ship a per-kingdom sphere-safe ordered list
    # of AP-pool moon shine_ids so the bridge can keep a per-kingdom cursor
    # + window of 3. Without this, fresh-start seeds can soft-lock when all
    # 3 Talkatoo picks in a kingdom are gated behind items not yet received.
    if not slot_data.get("talkatoo_mode"):
        return slot_data
    from ..talkatoo_order import build_talkatoo_order
    # locations.json's `progression: true` flag is the canonical source.
    # The Switch's MoonGetHook bypasses the Talkatoo block for these via
    # isProgressionShine — they're never gated by the cursor-window, so
    # don't include them in the ordered list.
    progression_names = {
        loc["name"] for loc in location_table
        if loc.get("progression", False)
    }
    slot_data["talkatoo_order"] = build_talkatoo_order(
        world, multiworld, player, progression_names)
    return slot_data

# This is called right at the end, in case you want to write stuff to the spoiler log
def before_write_spoiler(world: World, multiworld: MultiWorld, spoiler_handle) -> None:
    pass
