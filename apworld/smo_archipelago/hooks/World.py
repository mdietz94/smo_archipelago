# Object classes from AP core, to represent an entire MultiWorld and this individual World that's part of it
from worlds.AutoWorld import World
from BaseClasses import MultiWorld, CollectionState

# Object classes from Manual -- extending AP core -- representing items and locations that are used in generation
from ..Items import ManualItem
from ..Locations import ManualLocation

# Raw JSON data from the Manual apworld, respectively:
#          data/game.json, data/items.json, data/locations.json, data/regions.json
#
from ..Data import game_table, item_table, location_table, region_table

# These helper methods allow you to determine if an option has been set, or what its value is, for any player in the multiworld
from ..Helpers import is_option_enabled, get_option_value

# calling logging.info("message") anywhere below in this file will output the message to both console and log file
import logging

from worlds.generic.Rules import set_rule

########################################################################################
## Order of method calls when the world generates:
##    1. create_regions - Creates regions and locations
##    2. create_items - Creates the item pool
##    3. set_rules - Creates rules for accessing regions and locations
##    4. generate_basic - Runs any post item pool options, like place item/category
##    5. pre_fill - Creates the victory location
##
## The create_item method is used by plando and start_inventory settings to create an item from an item name.
## The fill_slot_data method will be used to send data to the Manual client for later use, like deathlink.
########################################################################################



# Called before regions and locations are created. Not clear why you'd want this, but it's here. Victory location is included, but Victory event is not placed yet.
def before_create_regions(world: World, multiworld: MultiWorld, player: int):
    pass

# Called after regions and locations are created, in case you want to see or modify that information. Victory location is included.
def after_create_regions(world: World, multiworld: MultiWorld, player: int):
    # Use this hook to remove locations from the world
    include_post_peace_moons = is_option_enabled(multiworld, player, "include_post_peace_moons")
    capturesanity = is_option_enabled(multiworld, player, "capturesanity")
    coin_shops = is_option_enabled(multiworld, player, "coin_shops")
    regional_shops = is_option_enabled(multiworld, player, "regional_shops")
    action_rando = is_option_enabled(multiworld, player, "action_rando")
    generic_moons = is_option_enabled(multiworld, player, "generic_moons")
    include_post_metro_moons = is_option_enabled(multiworld, player, "include_post_metro_moons")


    locationNamesToRemove = []

    for location in world.location_table:
        if "Capture" in location.get("category", []) and not capturesanity:
            locationNamesToRemove.append(location["name"])
        elif "Coin" in location.get("category", []) and not coin_shops:
            locationNamesToRemove.append(location["name"])
        elif "Regional" in location.get("category", []) and not regional_shops:
            locationNamesToRemove.append(location["name"])
        elif "Action" in location.get("category", []) and not action_rando:
            locationNamesToRemove.append(location["name"])
        elif "post-metro" in location.get("category", []) and not include_post_metro_moons:
            locationNamesToRemove.append(location["name"])
        elif not include_post_peace_moons:
            if not set(["Sand Peace", "Lake Peace", "Wooded Peace", "Metro Peace", "Snow Peace", "Seaside Peace", "Snow/Seaside Peace", "Luncheon Peace", "Bowser's Peace"]).isdisjoint(location.get("category", [])):
                locationNamesToRemove.append(location["name"])

    # Add your code here to calculate which locations to remove

    for region in multiworld.regions:
        if region.player == player:
            for location in list(region.locations):
                if location.name in locationNamesToRemove:
                    region.locations.remove(location)
    if hasattr(multiworld, "clear_location_cache"):
        multiworld.clear_location_cache()

# The item pool before starting items are processed, in case you want to see the raw item pool at that stage
def before_create_items_starting(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:
    return item_pool

# The item pool after starting items are processed but before filler is added, in case you want to see the raw item pool at that stage
def before_create_items_filler(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:

    generic_moons = is_option_enabled(multiworld, player, "generic_moons")
    generic_moon_count = get_option_value(multiworld, player, "generic_moon_count")

    # Use this hook to remove items from the item pool
    itemNamesToRemove = [] # List of item names

    if generic_moons:
        for i in range(463-generic_moon_count):
            itemNamesToRemove.append("Power Moon")

    # Add your code here to calculate which items to remove.
    #
    # Because multiple copies of an item can exist, you need to add an item name
    # to the list multiple times if you want to remove multiple copies of it.

    for itemName in itemNamesToRemove:
        item = next(i for i in item_pool if i.name == itemName)
        item_pool.remove(item)

    return item_pool

    # Some other useful hook options:

    ## Place an item at a specific location
    # location = next(l for l in multiworld.get_unfilled_locations(player=player) if l.name == "Location Name")
    # item_to_place = next(i for i in item_pool if i.name == "Item Name")
    # location.place_locked_item(item_to_place)
    # item_pool.remove(item_to_place)

# The complete item pool prior to being set for generation is provided here, in case you want to make changes to it
def after_create_items(item_pool: list, world: World, multiworld: MultiWorld, player: int) -> list:
    return item_pool

# Called before rules for accessing regions and locations are created. Not clear why you'd want this, but it's here.
def before_set_rules(world: World, multiworld: MultiWorld, player: int):
    pass

# Called after rules for accessing regions and locations are created, in case you want to see or modify that information.
def after_set_rules(world: World, multiworld: MultiWorld, player: int):
    capturesanity = get_option_value(multiworld, player, "capturesanity") or False
    action_rando = get_option_value(multiworld, player, "action_rando") or False

    def ground_pound_and_jump(state: CollectionState):
        if action_rando:
            return state.has_all(["Ground Pound", "Ground Pound Jump"], player)
        return True
    
    def wall_jump_and_dive(state: CollectionState):
        if action_rando:
            return state.has_all(["Wall Jump", "Dive"], player)
        return True

    def lake_entrance(state: CollectionState):
        if capturesanity and action_rando:
            return state.has("Uproot", player, 1) or ( ( (state.has_any(["Backward Somersault","Side Somersault"], player)) or ground_pound_and_jump(state) or wall_jump_and_dive(state) ) and state.has_all(["Cap Jump","Dive"], player) and  state.has_any(["Backward Somersault","Side Somersault","Long Jump","Triple Jump"], player))
        return True

    def wooded_entrance(state: CollectionState):
        if capturesanity and action_rando:
            return ( ( state.has_any(["Triple Jump", "Backward Somersault", "Side Somersault"],player) or ground_pound_and_jump(state) ) and state.has_all(["Wall Jump","Cap Jump"], player) and state.has_any(["Dive","Goomba"],player) ) or state.has_all(["Zipper","Swim"],player)
        elif action_rando and not capturesanity:
            return ( ( state.has_any(["Triple Jump", "Backward Somersault", "Side Somersault"],player) or ground_pound_and_jump(state) ) and state.has_all(["Wall Jump","Cap Jump"],player) ) or state.has("Swim",player,1)
        return True
    
    def metro_entrance(state: CollectionState):
        if capturesanity and action_rando:
            return state.has_any(["Wall Jump", "Tropical Wiggler"],player)
        return True    

    def snow_entrance(state: CollectionState):
        if capturesanity and action_rando:
            return state.has_any(["Swim", "Gushen", "Cheep Cheep"],player)
        return True
    
    def ruined_entrance(state: CollectionState):
        if capturesanity and action_rando:
            return state.has("Lava Bubble",player,1) or state.has_all(["Dive","Cap Jump"],player)
        return True

    if action_rando and capturesanity:
        # apply rule to entrances
        for exit_obj in multiworld.get_region("Lake Kingdom", player).entrances:
            set_rule(exit_obj, lake_entrance)
        for exit_obj in multiworld.get_region("Wooded Kingdom", player).entrances:
            set_rule(multiworld.get_entrance(exit_obj.name, player), wooded_entrance)
        for exit_obj in multiworld.get_region("Metro Kingdom", player).entrances:
            set_rule(multiworld.get_entrance(exit_obj.name, player), metro_entrance)
        for exit_obj in multiworld.get_region("Snow Kingdom", player).entrances:
            set_rule(multiworld.get_entrance(exit_obj.name, player), snow_entrance)
        for exit_obj in multiworld.get_region("Ruined Kingdom", player).entrances:
            set_rule(multiworld.get_entrance(exit_obj.name, player), ruined_entrance)
        # apply rule to exits
        for exit_obj in multiworld.get_region("Lake Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), lake_entrance)
        for exit_obj in multiworld.get_region("Wooded Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), wooded_entrance)
        for exit_obj in multiworld.get_region("Metro Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), metro_entrance)
        for exit_obj in multiworld.get_region("Snow Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), snow_entrance)
        for exit_obj in multiworld.get_region("Ruined Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), ruined_entrance)
    elif action_rando and not capturesanity:
        # apply rule to entrances
        for exit_obj in multiworld.get_region("Wooded Kingdom", player).entrances:
            set_rule(multiworld.get_entrance(exit_obj.name, player), wooded_entrance)
        # apply rule to exits
        for exit_obj in multiworld.get_region("Wooded Kingdom", player).exits:
            set_rule(multiworld.get_entrance(exit_obj.name, player), wooded_entrance)

    def Example_Rule(state: CollectionState) -> bool:
        # Calculated rules take a CollectionState object and return a boolean
        # True if the player can access the location
        # CollectionState is defined in BaseClasses
        return True

    ## Common functions:
    # location = world.get_location(location_name, player)
    # location.access_rule = Example_Rule

    ## Combine rules:
    # old_rule = location.access_rule
    # location.access_rule = lambda state: old_rule(state) and Example_Rule(state)
    # OR
    # location.access_rule = lambda state: old_rule(state) or Example_Rule(state)

# The item name to create is provided before the item is created, in case you want to make changes to it
def before_create_item(item_name: str, world: World, multiworld: MultiWorld, player: int) -> str:
    return item_name

# The item that was created is provided after creation, in case you want to modify the item
def after_create_item(item: ManualItem, world: World, multiworld: MultiWorld, player: int) -> ManualItem:
    return item

# This method is run towards the end of pre-generation, before the place_item options have been handled and before AP generation occurs
def before_generate_basic(world: World, multiworld: MultiWorld, player: int) -> list:
    pass

# This method is run at the very end of pre-generation, once the place_item options have been handled and before AP generation occurs
def after_generate_basic(world: World, multiworld: MultiWorld, player: int):
    pass

# This is called before slot data is set and provides an empty dict ({}), in case you want to modify it before Manual does
def before_fill_slot_data(slot_data: dict, world: World, multiworld: MultiWorld, player: int) -> dict:
    return slot_data

# This is called after slot data is set and provides the slot data at the time, in case you want to check and modify it after Manual is done with it
def after_fill_slot_data(slot_data: dict, world: World, multiworld: MultiWorld, player: int) -> dict:
    return slot_data

# This is called right at the end, in case you want to write stuff to the spoiler log
def before_write_spoiler(world: World, multiworld: MultiWorld, spoiler_handle) -> None:
    pass
