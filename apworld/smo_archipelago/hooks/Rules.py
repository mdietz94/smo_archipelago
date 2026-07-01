from typing import Optional
from worlds.AutoWorld import World
from ..Helpers import clamp, get_items_with_value, is_option_enabled
from BaseClasses import MultiWorld, CollectionState

import re

def SandPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do sand peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| and |Knucklotec's Fist|"
    return True

def LakePeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do lake peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Zipper|"
    return True

def SwimOrCheepCheep(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """swim or cheep cheep"""
    return True

def SwimOrCapJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """swim or cap jump"""
    return True

def CheepCheepOrGroundPound(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """cheep cheep or ground pound"""
    return True

def WoodedPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get world peace in wooded kingdom"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Uproot| and |Sherm|"
    return True

def ShermOrLongJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do sherm or long jump"""
    return True

def PostNightMetro(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get post-night metro moons"""
    capturesanity = is_option_enabled(multiworld, player, "capturesanity")
    if capturesanity:
        return "|Sherm|"
    return True

def PostTrumpeter(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get the trumpeter in metro kingdom"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm|"
    return True

def MetroPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get metro peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and |Manhole|"
    return True

def FromTheTopOfTheTower(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player jump frm the top of metro tower"""
    return True

def WallJumpOrPole(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do wall jump or pole"""
    return True

def TyfooOrScaleATallWall(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do tyfoo or scale a tall wall"""
    return True

def SnowPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get snow peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Ty-foo| and |Shiverian Racer|"
    return True

def SeasidePeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do seaside Peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Gushen|"
    return True

def SnowSeasidePeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do snow or seaside Peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "(|Ty-foo| and |Shiverian Racer|) or |Gushen|"
    return True

def PostEarlyLuncheon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get more moons in luncheon than the very first ones"""
    return True

def ClimbToTheMeat(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player climb to the meat"""
    return True

def LuncheonPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get snow peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Meat| and |Lava Bubble|"
    return True

def JumpHigh(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player jump high"""
    return True

def ScaleAWall(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player scale a wall"""
    return True

def ScaleAWallNoTripleJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player scale a wall without triple jump"""
    return True

def NiceFrame(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player knock down the nice frame (and get the other nearby moon)"""
    return True

def BowserPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get bowser peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Pokio|"
    return True

def DifficultMode(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do 'difficult' things"""
    return is_option_enabled(multiworld, player, "difficult_mode")

def LakeDifficult(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player make the 'difficult' triple jump at the start of lake or do lake peace
    (separate def cause it would be repetitive in locations.json)"""
    return "{LakePeace()} OR {DifficultMode()}"

def WoodedDifficult(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player make the 'difficult' long jump at the first uproot section or have uproot
    (separate def cause it would be repetitive in locations.json)"""
    return "{DifficultMode()} OR {OptOne(Uproot)}"

def KingdomMoons(world: World, multiworld: MultiWorld, state: CollectionState, player: int, kingdom: str, n) -> str:
    """N effective Power Moons FROM A SPECIFIC KINGDOM.

    Models SMO's in-game Odyssey-power leave-threshold for that kingdom:
    the player must have enough moons FROM THAT KINGDOM to advance the
    Odyssey out of it. Generic moon-count isn't sufficient because (a)
    there's no generic Power Moon item in the pool (the M6 cleanup
    dropped it), and (b) the natural progression is that each kingdom
    contributes its own moons to the Odyssey-power counter as the player
    explores it.

    Multi-Moon items count as 3 effective moons each. Power Moon items
    count as 1. Returns an OR-chain over the valid (MM_count, PM_count)
    combinations the player's pool can support, e.g.
    `KingdomMoons(Sand, 16)` ->
        (|Sand Kingdom Power Moon:16|
         OR (|Sand Kingdom Multi-Moon| AND |Sand Kingdom Power Moon:13|)
         OR (|Sand Kingdom Multi-Moon:2| AND |Sand Kingdom Power Moon:10|))

    Used in regions.json to gate kingdom-entry transitions for the linear
    chain Sand -> Lake -> Wooded -> Lost and Metro -> Snow -> Seaside ->
    Luncheon. Each kingdom's `requires` calls `KingdomMoons(<previous>, N)`
    where N is the vanilla Odyssey-power threshold to leave the previous
    kingdom for the next (per the pre-rebase regions.json's per-kingdom
    moon clauses).
    """
    kingdom = kingdom.strip()
    try:
        n = int(str(n).strip())
    except (ValueError, TypeError):
        return False
    if n <= 0:
        return True

    items_counts = world.get_item_counts()
    pm_name = f"{kingdom} Kingdom Power Moon"
    mm_name = f"{kingdom} Kingdom Multi-Moon"
    pm_pool = items_counts.get(pm_name, 0)
    mm_pool = items_counts.get(mm_name, 0)

    clauses = []
    for mm in range(mm_pool + 1):
        pm_needed = max(0, n - 3 * mm)
        if pm_needed > pm_pool:
            continue  # this combo can't satisfy N from the available pool
        sub = []
        if mm == 1:
            sub.append(f"|{mm_name}|")
        elif mm > 1:
            sub.append(f"|{mm_name}:{mm}|")
        if pm_needed > 0:
            sub.append(f"|{pm_name}:{pm_needed}|")
        if not sub:
            return True  # 0-need: any state satisfies
        clauses.append(sub[0] if len(sub) == 1 else "(" + " AND ".join(sub) + ")")

    if not clauses:
        return False  # pool too small to satisfy N from this kingdom alone
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"

def RegionalCap(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in cap"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Paragoomba|"
    return True

def RegionalCascade(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in cascade"""
    return True

def RegionalSand(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in sand"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| and |Knucklotec's Fist| and |Mini Rocket| and |Goomba|"
    return True

def RegionalLake(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in lake"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Zipper|"
    return True

def RegionalWooded(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in wooded"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and |Uproot| and |Boulder|"
    return True

def RegionalLost(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in lost"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Wall Jump|"
    return True

def RegionalMetro(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in metro"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Manhole| and |Mini Rocket|"
    return True

def RegionalSnow(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in snow"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Ty-foo| and |Goomba|"
    return True

def RegionalSeaside(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in snow"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Gushen|"
    return True

def RegionalLuncheon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in luncheon"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Volbonan| and |Meat| and |Lava Bubble|"
    return True

def RegionalBowser(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in bowser"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Pokio|"
    return True

def RegionalMoon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in bowser"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Parabones| and |Tropical Wiggler| and |Banzai Bill| and |Sherm|"
    return True

def Meat(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get meat moon"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Meat|"
    return True

def UprootOrFireBro(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """uproot or fire bro"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Uproot| or |Fire Bro|"
    return True

def Lighthouse(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get to the lighthouse"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Gushen| or |Cheep Cheep|"
    return True

def ItemValue(world: World, multiworld: MultiWorld, state: CollectionState, player: int, args: str):
    """When passed a string with this format: 'valueName:int',
    this function will check if the player has collect at least 'int' valueName worth of items\n
    eg. {ItemValue(Coins:12)} will check if the player has collect at least 12 coins worth of items
    """

    args_list = args.split(":")
    if not len(args_list) == 2 or not args_list[1].isnumeric():
        raise Exception(f"ItemValue needs a number after : so it looks something like 'ItemValue({args_list[0]}:12)'")
    args_list[0] = args_list[0].lower().strip()
    args_list[1] = int(args_list[1].strip())

    if not hasattr(world, 'item_values_cache'): #Cache made for optimization purposes
        world.item_values_cache = {}

    if not world.item_values_cache.get(player, {}):
        world.item_values_cache[player] = {
            'state': {},
            'count': {},
            }

    if (args_list[0] not in world.item_values_cache[player].get('count', {}).keys()
            or world.item_values_cache[player].get('state') != dict(state.prog_items[player])):
        #Run First Time or if state changed since last check
        existing_item_values = get_items_with_value(world, multiworld, args_list[0])
        total_Count = 0
        for name, value in existing_item_values.items():
            count = state.count(name, player)
            if count > 0:
                total_Count += count * value
        world.item_values_cache[player]['count'][args_list[0]] = total_Count
        world.item_values_cache[player]['state'] = dict(state.prog_items[player]) #save the current gotten items to check later if its the same
    return world.item_values_cache[player]['count'][args_list[0]] >= args_list[1]


# Two useful functions to make require work if an item is disabled instead of making it inaccessible
def OptOne(world: World, multiworld: MultiWorld, state: CollectionState, player: int, item: str, items_counts: Optional[dict] = None):
    """Check if the passed item (with or without ||) is enabled, then this returns |item:count|
    where count is clamped to the maximum number of said item in the itempool.\n
    Eg. requires: "{OptOne(|DisabledItem|)} and |other items|" become "|DisabledItem:0| and |other items|" if the item is disabled.
    """
    if item == "":
        return "" #Skip this function if item is left blank
    if not items_counts:
        items_counts = world.get_item_counts()

    require_type = 'item'

    if '@' in item[:2]:
        require_type = 'category'

    item = item.lstrip('|@$').rstrip('|')

    item_parts = item.split(":")
    item_name = item
    item_count = '1'

    if len(item_parts) > 1:
        item_name = item_parts[0]
        item_count = item_parts[1]

    if require_type == 'category':
        if item_count.isnumeric():
            #Only loop if we can use the result to clamp
            category_items = [item for item in world.item_name_to_item.values() if "category" in item and item_name in item["category"]]
            category_items_counts = sum([items_counts.get(category_item["name"], 0) for category_item in category_items])
            item_count = clamp(int(item_count), 0, category_items_counts)
        return f"|@{item_name}:{item_count}|"
    elif require_type == 'item':
        if item_count.isnumeric():
            item_current_count = items_counts.get(item_name, 0)
            item_count = clamp(int(item_count), 0, item_current_count)
        return f"|{item_name}:{item_count}|"

# OptAll check the passed require string and loop every item to check if they're enabled,
def OptAll(world: World, multiworld: MultiWorld, state: CollectionState, player: int, requires: str):
    """Check the passed require string and loop every item to check if they're enabled,
    then returns the require string with items counts adjusted using OptOne\n
    eg. requires: "{OptAll(|DisabledItem| and |@CategoryWithModifedCount:10|)} and |other items|"
    become "|DisabledItem:0| and |@CategoryWithModifedCount:2| and |other items|" """
    requires_list = requires

    items_counts = world.get_item_counts()

    functions = {}
    if requires_list == "":
        return True
    for item in re.findall(r'\{(\w+)\(([^)]*)\)\}', requires_list):
        #so this function doesn't try to get item from other functions, in theory.
        func_name = item[0]
        functions[func_name] = item[1]
        requires_list = requires_list.replace("{" + func_name + "(" + item[1] + ")}", "{" + func_name + "(temp)}")
    # parse user written statement into list of each item
    for item in re.findall(r'\|[^|]+\|', requires):
        itemScanned = OptOne(world, multiworld, state, player, item, items_counts)
        requires_list = requires_list.replace(item, itemScanned)

    for function in functions:
        requires_list = requires_list.replace("{" + function + "(temp)}", "{" + func_name + "(" + functions[func_name] + ")}")
    return requires_list

# Rule to expose the can_reach_location core function
def canReachLocation(world: World, multiworld: MultiWorld, state: CollectionState, player: int, location: str):
    """Can the player reach the given location?"""
    if state.can_reach_location(location, player):
        return True
    return False
