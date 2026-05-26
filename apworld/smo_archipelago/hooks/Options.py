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

class Goal(Choice):
    """Choose your victory condition.

    mushroom_kingdom: full game. Collect moons, clear Bowser, and arrive in the
        Mushroom Kingdom (default).
    festival: shorter game ending at the New Donk City Festival. Drops every
        kingdom past Metro, every Metro moon except the seven on the festival
        story path (Mechawiggler, the four band members, Powering Up the
        Station, and A Traditional Festival! itself), and the 15 captures that
        only exist in post-Metro kingdoms."""
    display_name = "Goal"
    option_mushroom_kingdom = 0
    option_festival = 1
    default = 0

class TalkatooMode(Toggle):
    """Talkatoo% mode: Talkatoo's speech bubble names 3 of YOUR AP-pool moons from the current
    kingdom, refilling as you collect them. Composable with the other include_* / capturesanity
    toggles — those still define which moons enter the pool; this option only changes how
    Talkatoo points at them. Non-AP moons are pre-marked as collected on save load so the world
    physically contains only AP locations (NOTE: destructive to the save; reserve this mode for
    AP-only playthroughs)."""
    display_name = "Talkatoo Mode"


# Per-kingdom Moon-item-count caps.
#
# Each option caps how many of that kingdom's Power Moon + Multi-Moon items end up
# in the AP item pool. Reducing it does NOT remove any AP checks — the surplus
# kingdom-Moon items are dropped from the pool and `adjust_filler_items` tops up
# the freed slots with filler. Effect: the player still has the same number of
# checks in the kingdom, but receives fewer kingdom-flavored Moons (and so the
# in-game kingdom-progress meter caps out lower).
#
# Floor is the smallest count that still satisfies the kingdom's KingdomMoons(K, N)
# rule using the MM-greedy strategy in hooks/World.py::_trim_kingdom_moons_to_options
# (Multi-Moons are kept first since each is worth 3 effective moons; Power Moons
# are dropped first). `default = range_end` means everything-on behaves identically
# to today. tests/test_kingdom_moon_count.py keeps these values in sync with
# items.json + KINGDOM_MOON_GATES.

class CascadeMoonCount(Range):
    """Number of Cascade Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Cascade moons with filler — the AP checks
    themselves stay; the player just receives fewer Cascade-flavored moons. Floor
    is the smallest count that still satisfies the Cascade kingdom gate."""
    display_name = "Cascade Kingdom Moon Count"
    range_start = 3
    range_end = 20
    default = 20

class SandMoonCount(Range):
    """Number of Sand Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Sand moons with filler — the AP checks
    themselves stay; the player just receives fewer Sand-flavored moons. Floor
    is the smallest count that still satisfies the Sand kingdom gate."""
    display_name = "Sand Kingdom Moon Count"
    range_start = 12
    range_end = 62
    default = 62

class LakeMoonCount(Range):
    """Number of Lake Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Lake moons with filler — the AP checks
    themselves stay; the player just receives fewer Lake-flavored moons. Floor
    is the smallest count that still satisfies the Lake kingdom gate."""
    display_name = "Lake Kingdom Moon Count"
    range_start = 6
    range_end = 27
    default = 27

class WoodedMoonCount(Range):
    """Number of Wooded Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Wooded moons with filler — the AP checks
    themselves stay; the player just receives fewer Wooded-flavored moons. Floor
    is the smallest count that still satisfies the Wooded kingdom gate."""
    display_name = "Wooded Kingdom Moon Count"
    range_start = 12
    range_end = 50
    default = 50

class LostMoonCount(Range):
    """Number of Lost Kingdom Power Moons in the AP item pool. Reducing this
    replaces the dropped Lost moons with filler — the AP checks themselves stay;
    the player just receives fewer Lost-flavored moons. Floor is the smallest
    count that still satisfies the Lost kingdom gate (Lost has no Multi-Moon,
    so the floor equals the threshold directly)."""
    display_name = "Lost Kingdom Moon Count"
    range_start = 10
    range_end = 21
    default = 21

class MetroMoonCount(Range):
    """Number of Metro Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Metro moons with filler — the AP checks
    themselves stay; the player just receives fewer Metro-flavored moons. Floor
    is the smallest count that still satisfies the Metro kingdom gate."""
    display_name = "Metro Kingdom Moon Count"
    range_start = 16
    range_end = 53
    default = 53

class SnowMoonCount(Range):
    """Number of Snow Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Snow moons with filler — the AP checks
    themselves stay; the player just receives fewer Snow-flavored moons. Floor
    is the smallest count that still satisfies the Snow kingdom gate."""
    display_name = "Snow Kingdom Moon Count"
    range_start = 8
    range_end = 34
    default = 34

class SeasideMoonCount(Range):
    """Number of Seaside Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Seaside moons with filler — the AP checks
    themselves stay; the player just receives fewer Seaside-flavored moons. Floor
    is the smallest count that still satisfies the Seaside kingdom gate."""
    display_name = "Seaside Kingdom Moon Count"
    range_start = 8
    range_end = 50
    default = 50

class LuncheonMoonCount(Range):
    """Number of Luncheon Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Luncheon moons with filler — the AP checks
    themselves stay; the player just receives fewer Luncheon-flavored moons. Floor
    is the smallest count that still satisfies the Luncheon kingdom gate."""
    display_name = "Luncheon Kingdom Moon Count"
    range_start = 14
    range_end = 49
    default = 49

class RuinedMoonCount(Range):
    """Number of Ruined Kingdom Power Moons (and the Multi-Moon) in the AP item
    pool. Reducing this replaces the dropped Ruined moons with filler — the AP
    checks themselves stay; the player just receives fewer Ruined-flavored moons.
    Floor is the smallest count that still satisfies the Ruined kingdom gate
    (3 effective moons to repair the Odyssey and reach Bowser's Kingdom): one
    Multi-Moon alone clears it."""
    display_name = "Ruined Kingdom Moon Count"
    range_start = 1
    range_end = 4
    default = 4

class BowsersMoonCount(Range):
    """Number of Bowser's Kingdom Power Moons (and Multi-Moons) in the AP item pool.
    Reducing this replaces the dropped Bowser's moons with filler — the AP checks
    themselves stay; the player just receives fewer Bowser's-flavored moons. Floor
    is the smallest count that still satisfies the Bowser's kingdom gate."""
    display_name = "Bowser's Kingdom Moon Count"
    range_start = 6
    range_end = 38
    default = 38


# This is called before any options are defined, in case you want to define your own with a clean slate
def before_options_defined(options: dict) -> dict:
    options["goal"] = Goal
    options["capturesanity"] = Capturesanity
    options["talkatoo_mode"] = TalkatooMode
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
    # Per-kingdom Moon-item-count caps (only kingdoms with KingdomMoons(K, N) gates).
    options["cascade_moon_count"] = CascadeMoonCount
    options["sand_moon_count"] = SandMoonCount
    options["lake_moon_count"] = LakeMoonCount
    options["wooded_moon_count"] = WoodedMoonCount
    options["lost_moon_count"] = LostMoonCount
    options["metro_moon_count"] = MetroMoonCount
    options["snow_moon_count"] = SnowMoonCount
    options["seaside_moon_count"] = SeasideMoonCount
    options["luncheon_moon_count"] = LuncheonMoonCount
    options["ruined_moon_count"] = RuinedMoonCount
    options["bowsers_moon_count"] = BowsersMoonCount
    return options

# This is called after any options are defined, in case you want to see what options are defined or want to modify the defined options
def after_options_defined(options: dict) -> dict:
    return options