# Object classes from AP that represent different types of options that you can create
from Options import FreeText, NumericOption, Toggle, DefaultOnToggle, Choice, TextChoice, Range, NamedRange

# These helper methods allow you to determine if an option has been set, or what its value is, for any player in the multiworld
from ..Helpers import is_option_enabled, get_option_value



####################################################################
# NOTE: At the time that options are created, Manual has no concept of the multiworld or its own world.
#       Options are defined before the world is even created.
#
# Example of creating your own option:
#
#   class MakeThePlayerOP(Toggle):
#       """Should the player be overpowered? Probably not, but you can choose for this to do... something!"""
#       display_name = "Make me OP"
#
#   options["make_op"] = MakeThePlayerOP
#
#
# Then, to see if the option is set, you can call is_option_enabled or get_option_value.
#####################################################################


# To add an option, use the before_options_defined hook below and something like this:
#   options["total_characters_to_win_with"] = TotalCharactersToWinWith
#
class IncludePostPeaceMoons(DefaultOnToggle):
    """Master toggle for all post-peace moons. Turning this off will remove every moon tagged with
    any per-kingdom Peace category, overriding the per-kingdom include_<kingdom>_peace_moons toggles.
    Leave on if you want per-kingdom control via the individual toggles."""
    display_name = "Include Post-Peace Moons"

class IncludeCapPeaceMoons(DefaultOnToggle):
    """Turn off to skip the small set of Cap Kingdom moons that are either only available after
    the kingdom's story is complete or are otherwise tedious to track down."""
    display_name = "Include Cap Kingdom Peace Moons"

class IncludeCascadePeaceMoons(DefaultOnToggle):
    """Turn off to skip Cascade Kingdom moons that are only available after the kingdom's story
    is complete or are otherwise tedious to track down."""
    display_name = "Include Cascade Kingdom Peace Moons"

class IncludeSandPeaceMoons(DefaultOnToggle):
    """Turn off to skip Sand Kingdom moons that are only available after the kingdom's story
    is complete (Tostarena moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Sand Kingdom Peace Moons"

class IncludeLakePeaceMoons(DefaultOnToggle):
    """Turn off to skip Lake Kingdom moons that are only available after the kingdom's story
    is complete (Lake Lamode moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Lake Kingdom Peace Moons"

class IncludeWoodedPeaceMoons(DefaultOnToggle):
    """Turn off to skip Wooded Kingdom moons that are only available after the kingdom's story
    is complete (Steam Gardens moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Wooded Kingdom Peace Moons"

class IncludeLostPeaceMoons(DefaultOnToggle):
    """Turn off to skip Lost Kingdom moons that are only available after the kingdom's story
    is complete or are otherwise tedious to track down."""
    display_name = "Include Lost Kingdom Peace Moons"

class IncludeMetroPeaceMoons(DefaultOnToggle):
    """Turn off to skip Metro Kingdom moons that are only available after the kingdom's story
    (the New Donk City festival) is complete or are otherwise tedious to track down."""
    display_name = "Include Metro Kingdom Peace Moons"

class IncludeSnowPeaceMoons(DefaultOnToggle):
    """Turn off to skip Snow Kingdom moons that are only available after the kingdom's story
    is complete (Shiveria moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Snow Kingdom Peace Moons"

class IncludeSeasidePeaceMoons(DefaultOnToggle):
    """Turn off to skip Seaside Kingdom moons that are only available after the kingdom's story
    is complete (Bubblaine moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Seaside Kingdom Peace Moons"

class IncludeLuncheonPeaceMoons(DefaultOnToggle):
    """Turn off to skip Luncheon Kingdom moons that are only available after the kingdom's story
    is complete (Mount Volbono moon-rock state) or are otherwise tedious to track down."""
    display_name = "Include Luncheon Kingdom Peace Moons"

class IncludeBowsersPeaceMoons(DefaultOnToggle):
    """Turn off to skip Bowser's Kingdom moons that are only available after the kingdom's story
    is complete or are otherwise tedious to track down."""
    display_name = "Include Bowser's Kingdom Peace Moons"

class IncludeCloudPeaceMoons(DefaultOnToggle):
    """Turn off to skip Cloud Kingdom side moons (e.g. Picture Match) that are tedious to track down."""
    display_name = "Include Cloud Kingdom Peace Moons"

class IncludeDeepWoodsMoons(DefaultOnToggle):
    """Turn off to skip the Wooded Kingdom Deep Woods moons (the foggy secret area):
    Rolling Rock / Glowing / Hard Rock in Deep Woods, By the Babbling Brook,
    Past the Peculiar Pipes, A Treasure Made from Coins, Beneath the Roots of the Moving Tree,
    Deep Woods Treasure Trap, Exploring for Treasure, Wandering in the Fog, Nut Hidden in the Fog."""
    display_name = "Include Deep Woods Moons"

class IncludeMinigameMoons(DefaultOnToggle):
    """Turn off to skip RNG / minigame moons across kingdoms: Sand/Metro/Luncheon Kingdom Slots,
    Sand Quiz, Ocean Quiz, Sphynx's Treasure Vault, Beach Volleyball pair, Jump-Rope pair,
    Roulette Tower pair."""
    display_name = "Include Minigame Moons"

class IncludeHintArtMoons(DefaultOnToggle):
    """Turn off to skip moons that require interpreting Hint Art murals from other kingdoms:
    every 'Found with X Kingdom Art' moon plus Sand: Walking the Desert!."""
    display_name = "Include Hint Art Moons"

class IncludeTouristMoons(DefaultOnToggle):
    """Turn off to skip the 'A Tourist in the X Kingdom' moons that require chained visits
    to a list of other kingdoms in sequence."""
    display_name = "Include Tourist Moons"

class IncludeLongCourseMoons(DefaultOnToggle):
    """Turn off to skip long obstacle-course / precision-platforming moons: Lake Jump-Grab-Climb,
    Wooded Flooding Pipeway / Elevator / Flower Road, Sand Strange Neighborhood, Luncheon Spinning
    Athletics / Fork Flickin', Seaside Narrow Valley / Stretch, Bowser's Dashing Clouds."""
    display_name = "Include Long Course Moons"

class IncludePrecisionCaptureMoons(DefaultOnToggle):
    """Turn off to skip moons that hinge on tedious precise control of a specific capture:
    Sand Bullet Bill Maze pair, Sand Invisible/Transparent Maze pair, Sand Jaxi Driver / Stunt,
    Metro Sharpshooting Under Siege, Metro RC Car Pro!, Bowser's Jizo cluster, Bowser's Pokio
    'Poking' cluster."""
    display_name = "Include Precision Capture Moons"

class Capturesanity(Toggle):
    """Shuffle all captures into the pool.
    Captures found in Cap or Cascade on the first visit are considered to be given for free and will not grant checks."""
    display_name = "Capturesanity"

class CoinShops(Toggle):
    """Shuffles all clothing that can be purchased with regular coins. Shop Moons are always shuffled."""
    display_name = "Coin Shops"

class RegionalShops(Toggle):
    """Shuffles all clothing, souvenirs, and stickers that can be purchased with regional coins."""
    display_name = "Regional Shops"

class IncludePostMetroMoons(DefaultOnToggle):
    """Turning this off will remove every location and item that isn't relevant before Metro Kingdom. Mostly for the Festival goal.
    This removes roughly 200 locations, depending on your settings."""
    display_name = "Include Post-Metro Moons"

# This is called before any manual options are defined, in case you want to define your own with a clean slate or let Manual define over them
def before_options_defined(options: dict) -> dict:
    options["include_post_peace_moons"] = IncludePostPeaceMoons
    options["capturesanity"] = Capturesanity
    options["coin_shops"] = CoinShops
    options["regional_shops"] = RegionalShops
    options["include_post_metro_moons"] = IncludePostMetroMoons
    # Per-kingdom Peace toggles
    options["include_cap_peace_moons"] = IncludeCapPeaceMoons
    options["include_cascade_peace_moons"] = IncludeCascadePeaceMoons
    options["include_sand_peace_moons"] = IncludeSandPeaceMoons
    options["include_lake_peace_moons"] = IncludeLakePeaceMoons
    options["include_wooded_peace_moons"] = IncludeWoodedPeaceMoons
    options["include_lost_peace_moons"] = IncludeLostPeaceMoons
    options["include_metro_peace_moons"] = IncludeMetroPeaceMoons
    options["include_snow_peace_moons"] = IncludeSnowPeaceMoons
    options["include_seaside_peace_moons"] = IncludeSeasidePeaceMoons
    options["include_luncheon_peace_moons"] = IncludeLuncheonPeaceMoons
    options["include_bowsers_peace_moons"] = IncludeBowsersPeaceMoons
    options["include_cloud_peace_moons"] = IncludeCloudPeaceMoons
    # Per-area annoying cluster toggles
    options["include_deep_woods_moons"] = IncludeDeepWoodsMoons
    options["include_minigame_moons"] = IncludeMinigameMoons
    options["include_hint_art_moons"] = IncludeHintArtMoons
    options["include_tourist_moons"] = IncludeTouristMoons
    options["include_long_course_moons"] = IncludeLongCourseMoons
    options["include_precision_capture_moons"] = IncludePrecisionCaptureMoons
    return options

# This is called after any manual options are defined, in case you want to see what options are defined or want to modify the defined options
def after_options_defined(options: dict) -> dict:
    return options