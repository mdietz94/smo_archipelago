# Spicy Meatball Overdrive apworld

The Archipelago world for **Super Mario Odyssey**. Forked from [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP); the data layout (items / locations / regions / categories) descends from that fork.

This world registers as `Spicy Meatball Overdrive` so seeds generated for it are paired with the real Switch client. Item / location / rule data started at parity with the upstream M2 baseline; subsequent milestones added per-kingdom toggles, AP-credit moon counters, capture lock enforcement, and the SMO Client itself.

## Installing for generation

```
# In an Archipelago checkout:
ln -s /path/to/spicy-meatball-overdrive/apworld/smo_archipelago worlds/smo_archipelago
# or on Windows: mklink /D worlds\smo_archipelago C:\Users\maxwe\Documents\smo_archipelago\apworld\smo_archipelago
```

Generate a YAML, run `Generate.py`. The `.archipelago` it produces is played with SMO Client, launched from the Archipelago Launcher (or by double-clicking the `.meatballsap` file shipped in each player's zip).

## Planned extensions (M8)

These options become possible once enforcement is real (server-verifiable, not honor system):

- **Progressive kingdom moon-count gating** — bridge enforces "you must have ≥ N moons before entering kingdom K" at AP-rules level rather than self-reported.
- **Hint system** — pull AP hints into the in-game overlay automatically.
- **DeathLink** — already declared on the inlined `game_table` in `Data.py` (`death_link: True`); bridge wires it to SMO's death events.
- **Traps** — low-coin, no-jump, temporary capture-lock.
- **Goal selection** — Bowser vs Darker Side vs all-moons %.

## Sync from upstream

`scripts/sync_capture_table.ps1` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so the Switch and bridge cannot drift on capture-name → bit-index mapping.

To pull updated upstream data for parity, re-clone the upstream into `third_party/` and copy its `data/*.json` over `apworld/smo_archipelago/data/*.json`. Re-run `sync_capture_table.ps1`.
