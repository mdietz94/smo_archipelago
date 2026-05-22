---
name: smo-extract-data
description: Extract shine_map.json and capture_map.json from an SMO 1.0.0 NSP or XCI — produces the per-machine, gitignored maps that the SMOClient uses to resolve raw SMO identifiers (stage + obj_id, hack_name) to display names. Use when the user asks to "extract", "regenerate", or work with "shine_map", "capture_map", an "NSP" or "XCI" file, "romfs", or "hactool"; or when a fresh clone / fresh worktree is missing `apworld/smo_archipelago/client/data/{shine_map,capture_map}.json` and moon collects silently drop. The extracted files are Nintendo-IP-sensitive and MUST stay gitignored.
---

# Game data extraction (M5.8 workflow)

One command after `git clone` produces both maps. NSPs and XCI cartridge dumps are both supported:

```pwsh
python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>
python scripts/extract_shine_map.py --xci <SMO_1.0.0.xci>
```

For example:
```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\extract_shine_map.py `
    --nsp "C:\Users\maxwe\Desktop\Roms\Switch\Super Mario Odyssey [0100000000010000][v0][Base].nsp"
```

The NSP path contains spaces and square brackets — always quote it.

Self-bootstraps a Python 3.12 venv with `oead` at `scripts/.extract-venv/` (no Python 3.13 wheel exists for oead), runs `hactool` to extract RomFS (~5 GB cache at `.romfs-cache/`), then walks the BYML + MSBT files. NSP unpacks via the PFS0 partition (`hactool -t pfs0`); XCI unpacks via the HFS0 secure partition (`hactool -t xci --securedir=`). Same NCA layout downstream — the largest NCA is the program NCA and its RomFS is what we walk.

## What gets produced

| File | Source | Entries |
|---|---|---|
| `apworld/smo_archipelago/client/data/shine_map.json` | `SystemData/ShineInfo.szs` × 17 BYML kingdom shine lists, joined to per-stage MSBT in `StageMessage.szs` under `ScenarioName_<ObjId>` | 775 moons |
| `apworld/smo_archipelago/client/data/capture_map.json` | `SystemData/HackObjList.szs` (130 internal HackNames), joined to `SystemMessage.szs/HackList.msbt` | 52 deduped captures |
| `*_review.json` | Diagnostics with the same strings — also gitignored | — |

All four files contain verbatim Nintendo USen strings and **MUST stay gitignored**. See CLAUDE.md "Never commit Nintendo IP". `.gitignore` already covers them; don't override with `git add -f`.

## Prerequisites

- SMO 1.0.0 NSP **or** XCI at a known path (user's NSP lives at `C:\Users\maxwe\Desktop\Roms\Switch\Super Mario Odyssey [0100000000010000][v0][Base].nsp`, copyrighted — never commit). Quote the path on the command line — it contains spaces and square brackets.
- `prod.keys` at `C:\Users\maxwe\.switch\` (hactool default location). Switch keys are themselves IP-sensitive.
- `title.keys` at the same location — **required for XCI** (cartridge dumps don't carry a ticket so hactool has to look up the titlekey by rights ID). NSPs ship the ticket inside the package; the extractor lifts it directly and `title.keys` is unused on that path.
- hactool on PATH (or in a known location — script auto-finds).
- Python 3.12 available on PATH (script bootstraps the venv from this).

## Cross-validation

100% of both apworld moons (436/436) and apworld captures (43/43) resolve. Out-of-apworld-scope SMO entries (339 moons, 7 captures) are emitted anyway so future apworld expansion picks them up automatically.

If extraction reports < 100% resolution, something has drifted — check the apworld typo list in M5.8 history first ([docs/milestones.md#m58](../../docs/milestones.md#m58)).

## Ground-truth conventions discovered during build

- **Moon MSBT lookup is in the per-shine StageName MSBT**, NOT the HomeStage MSBT — sub-stages like `PushBlockExStage` carry their own `ScenarioName_<obj>` entries.
- **Moon kingdom assignment comes from which BYML the shine came from** (HomeStage), not by the per-shine StageName prefix — those don't match for `*ExStage`/`*Zone` sub-stages.
- **Capture lookup is direct**: HackList.msbt label is the internal name, value is the English. No key construction needed.
- `pymsyt` only knows BotW's control-code set and chokes on SMO's control code 6. The script ships a ~150-line in-tree MSBT reader that generically skips all `0x0E.../0x0F...` sequences.
- The Japanese-internal → English capture mapping is NOT publicly published anywhere — lunakit/OdysseyDecomp use the internal names as code identifiers but never alongside English equivalents. Per the user's IP-safety stance, captures must be extracted at user-runtime (same as moons), not hand-coded.
- A small `CAPTURE_NAME_ALIASES` table in the extractor handles 6 cases where the apworld deliberately diverged from Nintendo's strings (collapsed multi-piece variants like `Picture Match Part (Mario)` → `Picture Match Part`, prefix renames like `Cheep Cheep (Snow Kingdom)` → `Snow Cheep Cheep`, casing like `Bowser statue` → `Bowser Statue`).

## Validating extraction

```pwsh
.\.venv\Scripts\python -m pytest -v `
    apworld\smo_archipelago\tests\test_shine_map_extraction.py
```

Nine tests validate schema/count/dedup/anchors for both maps. Auto-skip when files are absent.

## When to regenerate

- Fresh clone or fresh worktree (gitignored — won't be in the working tree).
- After an SMO update bumps the version (none expected — we target 1.0.0).
- Never on apworld edits — extraction reads SMO romfs, not apworld data.

## Channel A hard-dependency (M6 phase A.5)

The cutscene-label hook ([M6 phase A.5](../../docs/milestones.md#m6-phase-a5)) also needs these maps — without `shine_map.json` the client can resolve raw `stage + obj_id` but compose_moon_label returns None, cutscene shows vanilla. Future agents who think "I just want labels, not moon resolution" still need to run extraction.

Full prose workflow in [docs/extract-moon-data.md](../../docs/extract-moon-data.md).
