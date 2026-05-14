from typing import Optional
from worlds.AutoWorld import World
from ..Helpers import clamp, get_items_with_value, is_option_enabled
from BaseClasses import MultiWorld, CollectionState

import re

def BulletBillSkip(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do certain jumps without a bullet bill (or with a bullet bill)"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| or (|Dive| and |Cap Jump| and |Long Jump|)"
    return True

def BulletBillSmallSkip(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do certain small jumps without a bullet bill (or with a bullet bill)"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| or (|Dive| and |Cap Jump|)"
    return True

def BulletBillMaze(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do the bullet bill maze without a bullet bill (or with a bullet bill)"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| or (|Dive| and (|Wall Jump| or |Triple Jump|))"
    return True

def SandPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do sand peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| and |Knucklotec's Fist|"
    return True

def IntoTheLake(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player enter the lake kingdom lake"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump| and |Cap Jump| and (|Dive| or |Goomba|)) or (|Zipper| and |Swim|)"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump|) or |Swim|"
    return True

def LakePeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do lake peace"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "(((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump| and |Cap Jump| and (|Dive| or |Goomba|)) or (|Zipper| and |Swim|)) and (|Swim| or |Cheep Cheep|)"
    if is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump|) or |Swim|"
    return True

def SwimOrCheepCheep(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """swim or cheep cheep"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Swim| or |Cheep Cheep|"
    return True

def SwimOrCapJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """swim or cap jump"""
    action_rando = is_option_enabled(multiworld, player, "action_rando")
    if action_rando:
        return "|Cap Jump| or |Swim|"
    return True

def CheepCheepOrGroundPound(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """cheep cheep or ground pound"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Ground Pound| or |Cheep Cheep|"
    return True

def MazeSkip(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get past the maze in wooded kingdom"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Uproot| or ( ( ( |Ground Pound Jump| and |Ground Pound| ) or |Backward Somersault| or |Side Somersault| or ( |Wall Jump| and |Dive| ) or |Triple Jump| ) and ( |Long Jump| or |Triple Jump| or |Backward Somersault| or |Side Somersault| ) and |Cap Jump| and |Dive| )"
    return True

def WoodedPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get world peace in wooded kingdom"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Uproot| and |Sherm|"
    return True

def ShermOrLongJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do sherm or long jump"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| or |Long Jump|"
    return True

def PostNightMetro(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get post-night metro moons"""
    capturesanity = is_option_enabled(multiworld, player, "capturesanity")
    if capturesanity:
        return "|Sherm|"
    return True

def PostTrumpeter(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get the trumpeter in metro kingdom"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and (|Long Jump| or |Pole| or |Roll| or |Ground Pound Jump| or |Dive| or |Triple Jump|)"
    if not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm|"
    return True

def MetroPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get metro peace"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and |Manhole| and (|Long Jump| or |Pole| or |Roll| or |Ground Pound Jump| or |Dive| or |Triple Jump|)"
    if not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and |Manhole|"
    return True

def FromTheTopOfTheTower(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player jump frm the top of metro tower"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "(|Long Jump| or (|Pole| and |Motor scooter|) or |Roll| or |Ground Pound Jump| or |Dive| or |Triple Jump|)"
    if is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "(|Long Jump| or |Motor scooter| or |Roll| or |Ground Pound Jump| or |Dive| or |Triple Jump|)"
    return True

def WallJumpOrPole(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do wall jump or pole"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Wall Jump| or |Pole|"
    return True

def TyfooOrScaleATallWall(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player do tyfoo or scale a tall wall"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Ty-foo| or ((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump| and |Cap Jump| and |Dive|)"
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
        return "((|Ty-foo| and |Shiverian Racer| and (|Power Moon:95| or (|Seaside Kingdom Power Moon:10| OR (|Seaside Kingdom Multi-Moon| AND |Seaside Kingdom Power Moon:7|))) or (|Gushen| and (|Snow Kingdom Power Moon:10| OR (|Snow Kingdom Multi-Moon| AND |Snow Kingdom Power Moon:7|)))))"
    return True

def PostEarlyLuncheon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get more moons in luncheon than the very first ones"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Lava Bubble| or (|Dive| and |Cap Jump|)"
    return True

def ClimbToTheMeat(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player climb to the meat"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Volbonan| or (|Dive| and |Wall Jump| and (|Triple Jump| or |Ground Pound Jump| or |Backward Somersault| or |Side Somersault|))"
    return True

def LuncheonPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get snow peace"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Meat| and |Lava Bubble| and (|Volbonan| or (|Dive| and |Wall Jump| and (|Triple Jump| or |Ground Pound Jump| or |Backward Somersault| or |Side Somersault|)))"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Meat| and |Lava Bubble|"
    return True

def JumpHigh(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player jump high"""
    if is_option_enabled(multiworld, player, "action_rando"):
        return "(|Ground Pound Jump| and |Ground Pound|) or |Backward Somersault| or |Side Somersault| or |Triple Jump|"
    return True

def ScaleAWall(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player scale a wall"""
    if is_option_enabled(multiworld, player, "action_rando"):
        return "|Triple Jump| or (|Wall Jump| and |Dive|) or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|"
    return True

def ScaleAWallNoTripleJump(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player scale a wall without triple jump"""
    if is_option_enabled(multiworld, player, "action_rando"):
        return "(|Wall Jump| and |Dive|) or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|"
    return True

def NiceFrame(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player knock down the nice frame (and get the other nearby moon)"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Lakitu| or |Long Jump| or |Roll|"
    return True

def ParabonesSkip(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player skip the parabones (or use it)"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Parabones| or (|Long Jump| and |Cap Jump| and |Dive|)"
    return True

def BowserPeace(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get bowser peace"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Pokio|"
    return True

def RegionalCap(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in cap"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Paragoomba|"
    return True

def RegionalCascade(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in cascade"""
    return "|Power Moon:5| or (|Cascade Kingdom Power Moon:5| OR ( |Cascade Kingdom Multi-Moon| AND |Cascade Kingdom Power Moon:2| ))"

def RegionalSand(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in sand"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Jaxi| and |Bullet Bill| and |Knucklotec's Fist| and |Mini Rocket| and |Goomba|"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Bullet Bill| and |Knucklotec's Fist| and |Mini Rocket| and |Goomba|"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "|Jaxi|"
    return True

def RegionalLake(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in lake"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Swim| and |Zipper| and (|Cheep Cheep| or |Swim|) and (((|Triple Jump| or (|Ground Pound| and |Ground Pound Jump|) or |Backward Somersault| or |Side Somersault|) and |Wall Jump| and |Cap Jump| and (|Dive| or |Goomba|)) or (|Zipper| and |Swim|))"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "|Swim|"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Zipper|"
    return True

def RegionalWooded(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in wooded"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hold/Throw| and |Sherm| and |Uproot| and |Boulder|"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hold/Throw|"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Sherm| and |Uproot| and |Boulder|"
    return True

def RegionalLost(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in lost"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Wall Jump| and |Glydon| and |Tropical Wiggler|"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "|Glydon| and |Tropical Wiggler|"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Wall Jump|"
    return True

def RegionalMetro(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in metro"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "((|Long Jump| and |Dive| and |Cap Jump|) or (|Pole| and |Wall Jump|)) and (|Long Jump| or |Pole| or |Roll| or |Ground Pound Jump| or |Dive| or |Triple Jump|) and |Manhole| and |Mini Rocket|"
    elif is_option_enabled(multiworld, player, "action_rando") and not is_option_enabled(multiworld, player, "capturesanity"):
        return "((|Long Jump| and |Dive| and |Cap Jump|) or |Wall Jump|)"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Manhole| and |Mini Rocket|"
    return True

def RegionalSnow(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in snow"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Ty-foo| and |Goomba|"
    return True

def RegionalSeaside(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in snow"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Gushen| and (|Cheep Cheep| or |Swim|)"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Gushen|"
    return True

def RegionalLuncheon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in luncheon"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Volbonan| and |Meat| and |Lava Bubble|"
    return True

def RegionalBowser(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in bowser"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Pokio| and (|Lakitu| or |Long Jump| or |Roll|)"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Pokio|"
    return True

def RegionalMoon(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get regional coins in bowser"""
    if is_option_enabled(multiworld, player, "capturesanity"):
        return "|Parabones| and |Tropical Wiggler| and |Banzai Bill| and |Sherm|"
    return True

def Meat(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """can the player get meat moon"""
    if is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
        return "|Hammer Bro| and |Meat| and (|Volbonan| or (|Dive| and |Wall Jump| and (|Triple Jump| or |Ground Pound Jump| or |Backward Somersault| or |Side Somersault|)))"
    elif not is_option_enabled(multiworld, player, "action_rando") and is_option_enabled(multiworld, player, "capturesanity"):
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

def Sombrero(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Sombrero| and |Poncho|"
    return True

def Explorer(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Explorer Hat| and |Explorer Outfit|"
    return True

def Builder(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Builder Helmet| and |Builder Outfit|"
    return True

def Snowsuit(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Snow Hood| and |Snow Suit|"
    return True

def Resort(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Resort Hat| and |Resort Outfit|"
    return True

def Chef(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Chef Hat| and |Chef Suit|"
    return True

def Samurai(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops"):
        return "|Samurai Helmet| and |Samurai Armor|"
    return True

def Boxers(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "coin_shops"):
        return "|Boxer Shorts|"
    return True

def Swimwear(world: World, multiworld: MultiWorld, state: CollectionState, player: int):
    """clothes"""
    if is_option_enabled(multiworld, player, "regional_shops") and is_option_enabled(multiworld, player, "coin_shops"):
        return "(|Swim Goggles| and |Swimwear|) or |Boxer Shorts|"
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
