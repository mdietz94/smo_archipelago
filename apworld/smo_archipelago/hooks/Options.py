# Option classes for the SMO apworld. New options are registered in
# before_options_defined() at the bottom of this file. Options are defined
# before the world itself is created, so they can't reference per-player state
# at class-definition time — read it via is_option_enabled / get_option_value
# from a hook that runs later.
from Options import FreeText, NumericOption, Toggle, DefaultOnToggle, Choice, TextChoice, Range, NamedRange
from ..Helpers import is_option_enabled, get_option_value


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
    is complete (Tostarena moon-rock state) or are otherwise tedious to track down.
    Removes ~22 locations. Set together with multiple other 'include_...' toggles set to false,
    generation may fail."""
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
    is complete (Shiveria moon-rock state) or are otherwise tedious to track down.
    Removes ~18 locations. Set together with multiple other 'include_...' toggles set to false,
    generation may fail."""
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
    Roulette Tower pair.
    Removes ~12 locations. Set together with multiple other 'include_...' toggles set to false,
    generation may fail."""
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
    Athletics / Fork Flickin', Seaside Narrow Valley / Stretch, Bowser's Dashing Clouds.
    Removes ~20 locations. Set together with multiple other 'include_...' toggles set to false,
    generation may fail."""
    display_name = "Include Long Course Moons"

class IncludePrecisionCaptureMoons(DefaultOnToggle):
    """Turn off to skip moons that hinge on tedious precise control of a specific capture:
    Sand Bullet Bill Maze pair, Sand Invisible/Transparent Maze pair, Sand Jaxi Driver / Stunt,
    Metro Sharpshooting Under Siege, Metro RC Car Pro!, Bowser's Jizo cluster, Bowser's Pokio
    'Poking' cluster.
    Removes ~15 locations. Set together with multiple other 'include_...' toggles set to false,
    generation may fail."""
    display_name = "Include Precision Capture Moons"

class Capturesanity(Toggle):
    """Shuffle all captures into the pool.
    Each 'Capture: X' location only grants its check once you've received the matching X capture
    item — capturing an enemy you haven't unlocked yanks Mario back out and grants no credit."""
    display_name = "Capturesanity"

# This is called before any options are defined, in case you want to define your own with a clean slate
def before_options_defined(options: dict) -> dict:
    options["capturesanity"] = Capturesanity
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

# This is called after any options are defined, in case you want to see what options are defined or want to modify the defined options
def after_options_defined(options: dict) -> dict:
    return options