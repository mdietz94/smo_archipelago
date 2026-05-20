---
name: smo-poptracker
description: Build, iterate on, or debug the SMO PopTracker pack — an independent logic-graph tracker that connects directly to AP's websocket alongside SMOClient. Use when the user mentions "PopTracker", "tracker pack", "build_poptracker_pack.py", "pack-src", PopTracker layouts/maps/widgets, or the Lua port of `Rules.py` in `poptracker/pack-src/scripts/logic.lua`. Covers the build command, the apworld→pack regeneration loop, the map+pins UI pattern, and the release-workflow integration.
---

# PopTracker pack

Independent logic-graph tracker that connects directly to AP's websocket alongside SMOClient. Generated from apworld data by [scripts/build_poptracker_pack.py](../../scripts/build_poptracker_pack.py) — single-file stdlib-only generator.

## Build

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\build_poptracker_pack.py
```

Output: `poptracker/build/smo-poptracker-v<version>.zip` (~27 KB, gitignored). Rebuild after any apworld change.

## Self-tests

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\build_poptracker_pack.py --self-test
```

20 internal tests (parser, translator, region-prereq flatten). All pass as of 2026-05-17.

## What the generator does

- Mirrors the id-allocation algorithm in [apworld/.../Game.py](../../apworld/smo_archipelago/Game.py) so AP location_ids in the pack match SMOClient's (verified: `Cap: Frog-Jumping Above the Fog`→`14481151500` and `Cascade: Our First Power Moon`→`14481151511`).
- Parses the apworld's `requires` mini-language (`|Name:N|`, `{Func(args)}`, `and`/`or`, paren grouping) and translates to PopTracker OR-of-AND access_rules.
- Per-region prereq chains flattened at build time via [regions.json](../../apworld/smo_archipelago/data/regions.json)'s `connects_to` graph.
- Per-category yaml-option gates pulled from [categories.json](../../apworld/smo_archipelago/data/categories.json).
- Lua ports of all ~30 functions in [Rules.py](../../apworld/smo_archipelago/hooks/Rules.py) live in [poptracker/pack-src/scripts/logic.lua](../../poptracker/pack-src/scripts/logic.lua), guarded on the same `capturesanity` check the Python uses.
- Yaml options + goal selection live in a Lua `OPTIONS` table populated by `Archipelago:AddClearHandler` from `slot_data` (`fill_slot_data` in [__init__.py](../../apworld/smo_archipelago/__init__.py) exports every non-common option). All 20 logic-affecting options snap into place automatically; defaults match apworld defaults so offline-mode is sane.

## UI pattern (critical to know before iterating)

PopTracker has NO built-in locations panel or location-tree widget. The documented widget set is `container/dock/array/tabbed/group/item/itemgrid/map/layout/recentpins/text/canvas`. **Locations are ONLY visible when placed as pins on a `map` widget.**

Pack ships a 740×560 dark-gray placeholder PNG (generated stdlib-only via `struct` + `zlib` in `make_solid_png`) with the 16 kingdom buckets pinned on a 4×4 grid (Cap top-left, Captures bottom-right, ordering loosely follows linear-chain progression). Each kingdom is one top-level location with all its moons as `sections` (the DBFZ reference pack uses this flat shape — nested `children + sections` is two levels deeper than PopTracker accepts and silently breaks the location panel). Click a pin → kingdom drawer with section list; sections color by access-rule state.

## What NOT to try (iteration history)

3 failed attempts before user-verified:

1. `tracker_default: {type: "locationtree"}` — invented widget type, broke main view entirely.
2. Kingdom-level layout grouping (`children` of locations holding nested sections) — too deep for PopTracker's location format; location panel silently broke.
3. Stripped layout to a `text` widget telling user to open View > Locations — that menu item doesn't exist; locations need maps to be visible at all.

Map+pins is the only approach that worked. Don't second-guess this.

## Release workflow

[release.yml](../../.github/workflows/release.yml) builds the zip alongside `meatballs.apworld` on every tagged release; both ship as GitHub release assets with their own sha256 checksums.

## Polish opportunities

Pack is functional but visually plain. Lowest-effort win: replace `make_solid_png` with a baked-in PNG of SMO world-map art. Bigger win: per-kingdom maps with moon pins at approximate in-world coordinates (would need coords sourced from M5.8 BYML walk; ShineInfo doesn't currently emit positions but could). Pack-generator rebuild stays a single command.

## Source layout

```
poptracker/
  pack-src/                  Hand-authored: manifest, init.lua, logic.lua
                             (Lua ports of Rules.py), autotracking.lua, layouts.
                             Map PNG + maps.json generated.
  build/                     Generated; gitignored.
```
