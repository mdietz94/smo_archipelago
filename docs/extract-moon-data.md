# Extracting game data (`shine_map.json` + `capture_map.json`) from your SMO 1.0.0 dump

The bridge needs two lookup tables to translate raw SMO identifiers into
Archipelago messages:

- **`shine_map.json`** — `(stage_name, object_id) → (kingdom, shine_id)` for
  every moon, so `MoonGetHook` fires become `LocationCheck` calls.
- **`capture_map.json`** — `hack_name → english_cap_name` for every capture,
  so the Switch's internal `Kuribo` becomes the apworld's `Goomba`.

Both live inside SMO's romfs and are copyrighted Nintendo content — we can't
ship them, so each user generates them locally on first run. The same one
command produces both.

NSP and XCI dumps are both supported. NSPs are simpler to set up (they
ship a `.tik` inside the package and decrypt with just `prod.keys`); XCI
cartridge dumps additionally need a populated `title.keys` because
cartridges don't carry a ticket.

## One command

From the repo root, for an NSP:

```pwsh
python scripts/extract_shine_map.py --nsp <path-to-SMO_1.0.0.nsp>
```

Or for an XCI cartridge dump:

```pwsh
python scripts/extract_shine_map.py --xci <path-to-SMO_1.0.0.xci>
```

That writes:

- `bridge/smo_ap_bridge/data/shine_map.json` — the moon lookup table (775 entries on 1.0.0).
- `bridge/smo_ap_bridge/data/shine_map_review.json` — moon diagnostic report.
- `bridge/smo_ap_bridge/data/capture_map.json` — capture lookup (52 entries: 42 apworld + 8 out-of-scope, deduped).
- `bridge/smo_ap_bridge/data/capture_map_review.json` — capture diagnostic report.
- `.romfs-cache/` — extracted RomFS (~5 GB; reused on subsequent runs).
- `scripts/.extract-venv/` — Python 3.12 venv with `oead` (created once).

All four output files are gitignored. Re-running the script is fast (~5 s)
since the romfs and venv caches survive.

## Prereqs

The script self-bootstraps a Python 3.12 venv with `oead` — but you need the
following on disk first:

| Item | Default location | How to get |
|---|---|---|
| Python 3.12 | on the `py -3.12` launcher | `winget install -e --id Python.Python.3.12` |
| `hactool.exe` | PATH or `C:\Users\maxwe\Desktop\Switch\hactool.exe` | https://github.com/SciresM/hactool/releases |
| `prod.keys` | `~/.switch/prod.keys` | Lockpick_RCM on a hackable Switch |
| `title.keys` (XCI only) | `~/.switch/title.keys` | Lockpick_RCM (same run as prod.keys) |
| SMO 1.0.0 NSP or XCI | `--nsp` / `--xci` argument | dump from your own cart |

If your paths differ, override:

```pwsh
python scripts/extract_shine_map.py `
    --nsp     D:\dumps\SMO_1.0.0.nsp `
    --keys    D:\switch\prod.keys `
    --hactool D:\tools\hactool.exe
```

Same shape for an XCI — just swap the flag:

```pwsh
python scripts/extract_shine_map.py `
    --xci     D:\dumps\SMO_1.0.0.xci `
    --keys    D:\switch\prod.keys `
    --hactool D:\tools\hactool.exe
```

For XCI dumps, make sure `title.keys` (next to `prod.keys`) contains
SMO's titlekey entry. If it doesn't, the extractor will fail with a
message telling you which rights ID is missing.

If you already have an unencrypted RomFS extracted (e.g. via Ryujinx's
"Dump RomFS" feature), skip hactool entirely:

```pwsh
python scripts/extract_shine_map.py --romfs <path-to-romfs-root>
```

## What the script does

1. **Bootstrap** (first run only, ~30 s): creates `scripts/.extract-venv/`
   from `py -3.12 -m venv`, installs `oead`, then re-execs itself in the venv.
2. **Romfs extract** (first run only, ~2 min): runs `hactool` twice — first
   to crack the dump into NCA contents (NSP → PFS0 partition, XCI → HFS0
   secure partition), then to extract RomFS from the largest NCA (the
   program NCA). Output lands in `.romfs-cache/`.
3. **Moons** — walks `SystemData/ShineInfo.szs` (Yaz0+SARC of 17 BYML files,
   one `ShineList_<HomeStage>.byml` per kingdom), then joins each shine's
   `ObjId` against the per-stage MSBT in
   `LocalizedData/USen/MessageData/StageMessage.szs` under key
   `ScenarioName_<ObjId>`. The MSBT must be the per-shine `StageName` MSBT,
   not the HomeStage MSBT — sub-stages like `PushBlockExStage` own their
   own messages.
4. **Captures** — walks `SystemData/HackObjList.szs` (~130 `HackName`
   strings, the internal SMO names like `Kuribo`), then joins against
   `LocalizedData/USen/MessageData/SystemMessage.szs/HackList.msbt` where
   the label *is* the internal name and the value is the English display
   name (`Kuribo → Goomba`). A small alias table in the script handles
   cases where the apworld deliberately diverged from Nintendo (e.g.
   apworld collapses Nintendo's per-piece `Picture Match Part (Mario)` /
   `Picture Match Part (Goomba)` etc. into one randomizable item).
5. **Cross-validate against apworld**: every extracted moon name should
   appear in `apworld/.../locations.json`; every extracted capture name
   should appear in `apworld/.../items.json` (Capture category). Mismatches
   are logged in the review files; misses are still emitted (the bridge
   handles unknowns gracefully).

The MSBT parser is in-tree (no `pymsyt` — that tool only knows BotW's control
codes and chokes on SMO's). Moon names are plain text, so generically
stripping all `0x0E…` / `0x0F…` control sequences is enough.

## Expected output

```
== moons ==
raw shines:           775
resolved entries:     775  -> bridge/smo_ap_bridge/data/shine_map.json
  msbt misses:        0
  unknown home_stage: 0
  duplicate keys:     0
apworld moons:        436
  name mismatches:    339 (out-of-apworld-scope; still emitted)
  apworld unhit:      0

== captures ==
raw HackObjList:      130
emitted entries:      52  -> bridge/smo_ap_bridge/data/capture_map.json
  no MSBT match:      77
apworld captures:     42
  apworld matched:    44
  apworld unhit:      0
  out-of-scope hacks: 8 (still emitted)
```

- **`apworld unhit: 0`** for both is the success criterion: every moon /
  capture the apworld asks about can be resolved live.
- **Moons `name mismatches: 339`** is expected — SMO has 775 moons but the
  apworld randomizes 436. The other 339 are story moons, racing-cup moons,
  Peach cameos, Dark/Darker Side, etc.
- **Captures `no MSBT match: 77`** is expected — `HackObjList.byml` includes
  many internal/debug objects (`PukupukuRebuild`, `Bee`, `BigStatuePossessed`,
  …) that have no user-facing display name.
- **Captures `out-of-scope hacks: 8`** is expected — Frog, Yoshi, T-Rex,
  Chain Chomp variants, Spark Pylon, and Tostarena letters: SMO captures
  the apworld doesn't randomize.
- **Captures `apworld matched: 44 > 42`** because the alias table maps two
  Nintendo per-kingdom variants (e.g. `Puzzle Part (Lake Kingdom)` and
  `Puzzle Part (Metro Kingdom)`) onto the single apworld `Puzzle Part`
  entry.

## Troubleshooting

**`Python 3.12 not available via py -3.12`**

`winget install -e --id Python.Python.3.12`. The script needs 3.12 because
`oead` doesn't have prebuilt wheels for 3.13/3.14 yet.

**`hactool failed (exit N)`**

Most often a stale or wrong-version `prod.keys`. Re-dump with Lockpick_RCM
and confirm it has `header_key`, `key_area_key_application_*`, etc.
`hactool --disablekeywarns` is already passed; if you get key warnings
without `--disablekeywarns` they may be informational only.

**`hactool could not decrypt the dump — your title.keys is missing the entry for SMO's rights ID`** (XCI path)

XCI cartridge dumps don't carry a ticket inside the package, so the
extractor relies on `title.keys` to look up SMO's titlekey. The default
location is alongside `prod.keys` (e.g. `%USERPROFILE%\.switch\title.keys`).
Re-dump with Lockpick_RCM — its default run produces both `prod.keys`
and `title.keys` together — and rerun the extractor. (NSPs ship their
own `.tik` so this error doesn't apply to the `--nsp` path.)

**`apworld unhit > 0`**

A new SMO build (1.0.1, 1.1.0, etc.) shifted a moon name. Diff the unhit
list against the extracted candidates; the right fix is usually to update
`apworld/smo_archipelago/data/locations.json` to match Nintendo's MSBT
(MSBT is canonical — that's the string the player sees in-game).

**`raw shines < 775` on SMO 1.0.0**

Indicates one of the 17 `ShineList_<HomeStage>.byml` BYML files is missing
or malformed. Re-extract the romfs from a known-good NSP.

## Validating the result

```pwsh
cd bridge
.\.venv\Scripts\python -m pytest tests/test_shine_map_extraction.py -v
```

Nine tests check schema, count, dedup, and a small set of anchor lookups for
both maps. The capture tests deliberately use only 3 spot-check name pairs
to avoid bulk transcription of Nintendo's internal→English table; the bulk
of capture validation is structural (every emitted hack resolves, no
duplicates) rather than name-by-name.

They auto-skip when the corresponding map file is missing (fresh checkout / CI).
