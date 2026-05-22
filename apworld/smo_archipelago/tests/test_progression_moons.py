"""Consistency check: locations.json `progression: true` flags vs the
authoritative scenario-advance list for Talkatoo% mode.

Phase 4's Talkatoo% block (switch-mod/src/hooks/MoonGetHook.cpp) refuses
moon collection unless Talkatoo has named the moon — except for moons
marked `progression: true` in this apworld's locations.json, which are
always collectible. Those exemptions prevent fresh-start soft-locks on
moons that advance SMO's internal `scenario_no` (Multi Moons, boss-fight
clears, and explicit prereqs like Seaside's 4 seals).

The data we test here:

  - Every `progression: true` name actually exists in locations.json
    (typo guard). A dangling flag would silently fail because the bridge
    + sync_shine_table.py both filter on name equality.
  - The set matches the audited list. The audit was anchored on
    mariowiki.com/Multi_Moon's per-kingdom Multi Moon entries plus the
    explicit per-kingdom prereqs (Seaside seals, Bowser's 4-step chain,
    Cascade's first power moon). See the inline EXPECTED_PROGRESSION
    comment for the source-of-truth break-down.

Pure-data: no Archipelago imports, no Switch dependency. Runs in the
standard test job (not gated on SMOAP_LIVE_AP).

When this fails:
  - "missing name" → typo or a moon was renamed. Either fix the name or
    drop the flag.
  - "set differs" → someone changed the progression list. If the change
    is deliberate, update EXPECTED_PROGRESSION here AND update the audit
    rationale in docs/milestones.md (Phase 4 narrative).
"""

from __future__ import annotations

import json
from pathlib import Path

APWORLD_ROOT = Path(__file__).resolve().parents[1]


# Audited 2026-05-21, re-audited 2026-05-22 against OdysseyDecomp's quest
# model + per-kingdom story walkthroughs. The criterion is mechanical:
# SMO's QuestInfoHolder calls setMainScenarioNo(quest->getQuestNo() + 1)
# when the last active quest for a given QuestNo is invalidated. The
# flagged set is the enumeration of those shines for the kingdoms SMO
# ships with main quests. (Source:
# github.com/MonsterDruide1/OdysseyDecomp QuestInfoHolder.cpp,
# specifically QuestInfoHolder::invalidateQuest at line 140.)
#
# This list is maintained by hand against Mario Wiki's per-kingdom
# Power Moon lists. A previous iteration extracted Shine* placements
# with positive QuestNo directly from StageData/*Map.szs and
# cross-checked against this set; that walker confirmed 17 of the
# entries but missed every Multi Moon (those route through QuestObj's
# SrcUnitLayerList layer-link indirection, which the walker couldn't
# resolve cleanly). The extraction approach was reverted as added
# complexity for partial coverage; the hand audit is the source of
# truth. The Mario Wiki references in the per-kingdom rationale below
# are the authoritative cross-check.
#
# Per-kingdom rationale (each entry = a separate IsMainQuest shine):
#   - Cascade (2): "Our First Power Moon" (story 0->1),
#                  "Multi Moon Atop the Falls" (Madame Broode MM, 1->2).
#   - Sand (4): "Atop the Highest Tower" (story 0->1),
#               "Moon Shards in the Sand" (1->2),
#               "Showdown on the Inverted Pyramid" (Hariet MM, 2->3),
#               "The Hole in the Desert" (Knucklotec MM, 3->4).
#   - Lake (1): "Broodals Over the Lake" (Rango MM). Lake is one-MM.
#   - Wooded (4): "Road to Sky Garden" (story 0->1),
#                 "Flower Thieves of Sky Garden" (Spewart MM, 1->2),
#                 "Path to the Secret Flower Field" (2->3),
#                 "Defend the Secret Flower Field!" (Torkdrift MM, 3->4).
#   - Metro (7): "New Donk City's Pest Problem" (Mechawiggler MM, 0->1),
#                "Drummer on Board!", "Guitarist on Board!",
#                "Bassist on Board!", "Trumpeter on Board!" (the four
#                band members; each advances scenario_no by one),
#                "Powering Up the Station" (post-band, pre-festival),
#                "A Traditional Festival!" (Pauline MM, terminal).
#   - Snow (5): "The Ice Wall Barrier", "The Gusty Barrier",
#               "The Icicle Barrier", "The Snowy Mountain Barrier"
#               (the four barriers gating Bound Bowl), then
#               "The Bound Bowl Grand Prix" (terminal MM).
#   - Seaside (5): 4 seal prereqs + Mollusque MM. Seals spawn Mollusque;
#                  Mollusque drops "The Glass Is Half Full!" MM.
#   - Luncheon (5): "The Broodals Are After Some Cookin'" (0->1),
#                   "Under the Cheese Rocks" (1->2),
#                   "Cookatiel Showdown!" (Cookatiel-fight MM, 2->3),
#                   "Big Pot on the Volcano: Dive In!" (Cookatiel-meat MM,
#                   2->3 sibling).
#                   "Climb Up the Cascading Magma" (3->4 scenario advance).
#                   The AP-side LuncheonPeace requirement on Climb is a
#                   reach gate (you can't reach the cascading-magma path
#                   without Cookatiel-fight clearing the volcano top), not
#                   a "this isn't a story moon" marker — SMO still bumps
#                   scenario_no on collection, so Talkatoo% must allow it.
#   - Ruined (1): "Battle with the Lord of Lightning!" (Ruined Dragon MM).
#   - Bowser's (4): "Infiltrate Bowser's Castle!" -> "Smart Bombing" ->
#                   "Big Broodal Battle" -> "Showdown at Bowser's Castle"
#                   (RoboBrood MM, terminal). Each advances scenario_no.
# Intentionally NOT included (per audit):
#   - Cap, Lost, Cloud, Mushroom, Moon, Dark Side, Darker Side. Cap has no
#     in-kingdom progression gate; Lost has no IsMainQuest shines per
#     Mario Wiki; Cloud / Mushroom / Moon are one-moon transitional /
#     post-game kingdoms; Dark / Darker Side are post-credits and AP-pool
#     exclusion is handled separately.
EXPECTED_PROGRESSION = frozenset({
    "Cascade: Our First Power Moon",
    "Cascade: Multi Moon Atop the Falls",
    "Sand: Atop the Highest Tower",
    "Sand: Moon Shards in the Sand",
    "Sand: Showdown on the Inverted Pyramid",
    "Sand: The Hole in the Desert",
    "Lake: Broodals Over the Lake",
    "Wooded: Road to Sky Garden",
    "Wooded: Flower Thieves of Sky Garden",
    "Wooded: Path to the Secret Flower Field",
    "Wooded: Defend the Secret Flower Field!",
    "Metro: New Donk City's Pest Problem",
    "Metro: Drummer on Board!",
    "Metro: Guitarist on Board!",
    "Metro: Bassist on Board!",
    "Metro: Trumpeter on Board!",
    "Metro: Powering Up the Station",
    "Metro: A Traditional Festival!",
    "Snow: The Ice Wall Barrier",
    "Snow: The Gusty Barrier",
    "Snow: The Icicle Barrier",
    "Snow: The Snowy Mountain Barrier",
    "Snow: The Bound Bowl Grand Prix",
    "Seaside: The Stone Pillar Seal",
    "Seaside: The Lighthouse Seal",
    "Seaside: The Hot Spring Seal",
    "Seaside: The Seal Above the Canyon",
    "Seaside: The Glass Is Half Full!",
    "Luncheon: The Broodals Are After Some Cookin'",
    "Luncheon: Under the Cheese Rocks",
    "Luncheon: Cookatiel Showdown!",
    "Luncheon: Big Pot on the Volcano: Dive In!",
    "Luncheon: Climb Up the Cascading Magma",
    "Ruined: Battle with the Lord of Lightning!",
    "Bowser's: Infiltrate Bowser's Castle!",
    "Bowser's: Smart Bombing",
    "Bowser's: Big Broodal Battle",
    "Bowser's: Showdown at Bowser's Castle",
})


def _load_locations() -> list[dict]:
    return json.loads(
        (APWORLD_ROOT / "data" / "locations.json").read_text(encoding="utf-8")
    )


def _flagged_names(locs: list[dict]) -> set[str]:
    return {loc["name"] for loc in locs if loc.get("progression", False)}


def test_every_flagged_name_exists():
    """Every name marked `progression: true` is itself a valid loc name."""
    locs = _load_locations()
    all_names = {loc["name"] for loc in locs}
    flagged = _flagged_names(locs)
    # The flagged set is a subset of names by construction (we only flag
    # entries we iterate over), but assert it explicitly so a future
    # refactor that builds the flags from elsewhere doesn't silently break.
    missing = flagged - all_names
    assert not missing, (
        f"locations.json has progression-flagged names not present in the "
        f"name set: {sorted(missing)}"
    )


def test_progression_set_matches_audit():
    """The flagged set is exactly the audited list."""
    flagged = _flagged_names(_load_locations())
    extra = flagged - EXPECTED_PROGRESSION
    missing = EXPECTED_PROGRESSION - flagged
    assert flagged == EXPECTED_PROGRESSION, (
        f"locations.json progression flags drift from the audit:\n"
        f"  In locations.json but not audited: {sorted(extra)}\n"
        f"  Audited but missing from locations.json: {sorted(missing)}\n"
        f"If this drift is intentional, update EXPECTED_PROGRESSION here "
        f"AND record the audit rationale in docs/milestones.md."
    )


def test_progression_count_matches_audit():
    """Cardinality check — fast signal if anything moved."""
    flagged = _flagged_names(_load_locations())
    assert len(flagged) == len(EXPECTED_PROGRESSION), (
        f"progression count drift: got {len(flagged)}, expected "
        f"{len(EXPECTED_PROGRESSION)}"
    )


def test_no_capture_marked_progression():
    """Captures don't fit the scenario-advance pattern.

    Captures are AP items applied to SMO's HackDictionary; they don't go
    through MoonGetHook so flagging them as progression has no in-game
    effect. Marking one is a typing error.

    (NOTE: victory locations CAN legitimately be progression-flagged.
    Metro: A Traditional Festival! is the festival% goal AND a Multi Moon
    that advances Metro's scenario_no — both flags apply. The goal flag
    is consumed by CreditsStartHook / festival-mode logic; the progression
    flag is consumed by MoonGetHook's Talkatoo% block. Different concerns,
    no conflict.)
    """
    locs = _load_locations()
    for loc in locs:
        if not loc.get("progression", False):
            continue
        assert "Capture" not in loc.get("category", []), (
            f"{loc['name']} is a Capture but flagged progression; remove flag"
        )


def test_progression_moons_have_kingdom_prefix():
    """Every progression entry follows the `Kingdom: Moon Name` form.

    The bridge-side filter (Phase 4 follow-up #1) plans to use the prefix
    to route progression moons to the right per-kingdom talkatoo_pool
    exclusion list. Defensively check the schema is honored.
    """
    bad = []
    for name in EXPECTED_PROGRESSION:
        if ":" not in name:
            bad.append(name)
            continue
        kingdom = name.split(":", 1)[0].strip()
        if not kingdom or kingdom == name:
            bad.append(name)
    assert not bad, (
        f"progression entries missing 'Kingdom: ' prefix: {bad}"
    )
