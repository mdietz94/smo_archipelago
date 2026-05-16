# Spicy Meatball Overdrive apworld

Forked from [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) (`manual_smo_mp3`).

This fork registers as `Manual_SMO_archipelago` (vs upstream's `Manual_SMO_mp3`) so seeds generated for it are paired with the real-client bridge in `bridge/`. Item / location / rule data is unchanged from upstream as of the M2 fork (parity).

## Installing for generation

```
# In an Archipelago checkout:
ln -s /path/to/spicy-meatball-overdrive/apworld/smo_archipelago worlds/smo_archipelago
# or on Windows: mklink /D worlds\smo_archipelago C:\Users\maxwe\Documents\smo_archipelago\apworld\smo_archipelago
```

Generate a YAML, run `Generate.py`. The `.archipelago` it produces should be played with `python -m smo_ap_bridge` from `bridge/` — *not* the original Manual client.

## Differences from upstream (M2 = none)

| | upstream | this fork (M2) |
|---|---|---|
| Game name | `Manual_SMO_mp3` | `Manual_SMO_archipelago` |
| Item pool | identical | identical |
| Locations | identical | identical |
| Rules | identical | identical |

## Planned extensions (M8)

These options become possible once enforcement is real (server-verifiable, not honor system):

- **Progressive kingdom moon-count gating** — bridge enforces "you must have ≥ N moons before entering kingdom K" at AP-rules level rather than self-reported.
- **Hint system** — pull AP hints into the in-game overlay automatically.
- **DeathLink** — already declared in `data/game.json` (`death_link: true`); bridge wires it to SMO's death events.
- **Traps** — low-coin, no-jump, temporary capture-lock.
- **Goal selection** — Bowser vs Darker Side vs all-moons %.

## Sync from upstream

`scripts/sync_capture_table.ps1` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so the Switch and bridge cannot drift on capture-name → bit-index mapping.

To pull updated upstream data for parity, re-clone `empathy-mp3/SMO-manual-AP` into `third_party/` and copy `manual_smo_mp3/data/*.json` over `apworld/smo_archipelago/data/*.json`. Re-run `sync_capture_table.ps1`.
