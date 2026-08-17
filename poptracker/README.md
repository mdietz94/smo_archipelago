# SMO Archipelago — PopTracker pack

A [PopTracker](https://poptracker.github.io/) pack for [Spicy Meatball
Overdrive](../README.md), the Super Mario Odyssey Archipelago client.
Connects directly to an AP server via PopTracker's built-in autotracker
so you can see live which checks are reachable given the items you've
collected, without keeping SMOClient open.

## Install

1. Grab the latest `smo-poptracker-v<version>.zip` from this repo's
   `poptracker/build/` directory (or build it yourself — see below).
2. Extract it into PopTracker's packs directory:
   - Windows: `%APPDATA%\PopTracker\packs\`
   - Linux: `~/.config/PopTracker/packs/`
   - macOS: `~/Library/Application Support/PopTracker/packs/`
3. Launch PopTracker → Load Pack → **Spicy Meatball Overdrive** → choose
   the **Archipelago** variant.
4. PopTracker's AP connection dialog appears: enter the same
   `host:port` and slot name you used in SMOClient. The pack snaps
   your YAML options into place from `slot_data` automatically.

You can run the pack against the same AP server the SMO mod is already
connected to — it's a separate read-only client, doesn't interfere.

## What the pack shows

The main viewport is a 4×4 grid of pins, one per kingdom bucket
(Cap, Cascade, Sand, Lake / Wooded, Cloud, Lost, Metro / Snow, Seaside,
Luncheon, Ruined / Bowser's, Moon, Mushroom, Captures). Hover a pin to
see the kingdom name; click to open its drawer of moon sections. Each
section colors live based on whether you can reach it given items
collected so far.

- **All 482 location checks** are pinned under their kingdom, with
  per-section access rules translated from the apworld's `requires`
  strings + the linear-chain region prereqs in `regions.json`.
- **Per-kingdom Moon credits** (PowerMoon × 1 + Multi-Moon × 3) are
  tracked as composite items the access-rule helper reads via
  `has_kingdom_moons(kingdom, n)` — the same accounting the in-game
  HUD uses for kingdom-progression gating.
- **All 41 captures** are toggle items updated automatically by the AP
  autotracker.
- **All 20 logic-affecting YAML options + goal selection** sync
  automatically from `slot_data` on connect into a Lua `OPTIONS` table
  the access rules read from — no manual configuration. Defaults match
  apworld defaults so the pack also works reasonably offline (open with
  the AP variant but skip the connect dialog).

## Building

The pack is generated from the apworld's data (`items.json`,
`locations.json`, `regions.json`, `categories.json`) by a single Python
script. Re-run after any apworld change so the tracker doesn't drift.

```pwsh
# From the repo root:
python scripts/build_poptracker_pack.py            # build to poptracker/build/smo-poptracker/
python scripts/build_poptracker_pack.py --zip      # also produce the release zip
python scripts/build_poptracker_pack.py --self-test  # parser/translator sanity check
```

Build outputs land in `poptracker/build/` (gitignored). Drop the
`smo-poptracker-v<version>.zip` into your PopTracker `packs/` directory
and reload to pick up changes.

## Pack source layout

- [pack-src/](pack-src/) — hand-authored bits that don't derive from
  the apworld: manifest, init script, logic helpers (Lua ports of
  `Rules.py`), autotracker glue, layouts.
- [pack-src/scripts/logic.lua](pack-src/scripts/logic.lua) — one Lua
  function per rule in
  [apworld/smo_archipelago/hooks/Rules.py](../apworld/smo_archipelago/hooks/Rules.py).
  Edit when `Rules.py` changes; the generator re-emits everything
  else automatically.
- [pack-src/scripts/autotracking.lua](pack-src/scripts/autotracking.lua) —
  registers Archipelago handlers; resets state on `onClear`, bumps
  items on `onItem`, marks checks on `onLocation`, syncs YAML options
  from `slot_data`.
- [build/](build/) — generated; gitignored.

## Limitations (v1)

- **The map is a placeholder** — a 740×560 dark-gray rectangle with 16
  uniform pins on a 4×4 grid. PopTracker requires a `map` widget for
  locations to be visible at all (its widget set doesn't include any
  location-tree or list widget), so the placeholder ships to keep the
  pack functional. Replacing it with a hand-drawn SMO-world overview is
  a v2 candidate. In-game screenshots would be Nintendo IP and can't
  ship from this repo.
- **`{ItemValue(coins:N)}`** is wired but always returns false — no
  location in the current apworld uses it, so this is a no-op until
  the coin-value tracker lands. Future apworld changes will surface as
  out-of-logic until [logic.lua](pack-src/scripts/logic.lua) `item_value`
  gets a real implementation.

## Adding an option

If the apworld gains a new YAML option that affects logic:

1. Add the default for the option to the `OPTIONS` table at the top of
   [pack-src/scripts/logic.lua](pack-src/scripts/logic.lua). The Archipelago
   `onClear` handler in
   [autotracking.lua](pack-src/scripts/autotracking.lua) snaps any keys
   present in `slot_data` into this table.
2. If the option gates `requires` strings (not just category visibility),
   add a Lua function in `logic.lua` that consults `OPTIONS[<key>]` and
   have `_func_to_dnf` in the generator emit `$<func>` references.
3. Rebuild with `--zip` and reload the pack.

## See also

- Top-level project [README](../README.md) and
  [CLAUDE.md](../CLAUDE.md).
- PopTracker
  [PACKS.md](https://github.com/black-sliver/PopTracker/blob/master/doc/PACKS.md)
  and
  [AUTOTRACKING.md](https://github.com/black-sliver/PopTracker/blob/master/doc/AUTOTRACKING.md)
  for format details.
