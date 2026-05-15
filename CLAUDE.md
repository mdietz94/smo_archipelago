# CLAUDE.md — context for the next session

This file is a fast-load brief for picking up the project cold. Read it first, then `docs/architecture.md` and the plan file at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

## What we're building

A real Archipelago client for **Super Mario Odyssey on a modded Switch (FW 21.2, native SMO 1.0.0 install, Atmosphere CFW)**. Replaces the existing Manual checklist client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system tick-the-boxes app — with an in-game module that detects moons/captures/scenario events automatically, applies received items live, and enforces capture locks until the AP item arrives.

### Architecture (three tiers)

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Bridge (Python) ]  <--websocket-->  [ AP server ]
   exlaunch                              CommonContext                              archipelago.gg
   LunaKit headers                       Flask web tracker                          or self-host
   ImGui overlay (M8)                    Forked apworld
   HUD overlay (M3)
```

The PC bridge owns AP-protocol complexity (websocket + deflate + TLS + reconnect). Switch speaks a small line-delimited JSON protocol on port **17777**. Full wire format: `docs/wire-protocol.md`.

## Decisions already made (and why)

| Decision | Why |
|---|---|
| **PC bridge, not direct Switch→AP** | websocket+deflate+TLS+reconnect on Switch is months of work; bridge solves it in ~hundred lines via `CommonContext` |
| **Archipelago as git submodule, not pip install or vendored copy** | Their `setup.py` blocks pip; copying ~15 transitive files would drift fast. Submodule under `vendor/Archipelago/` is drift-proof and also enables seed generation in the same checkout |
| **Forked apworld, not vendored unchanged** | M8 will add automation-only features (deathlink, traps, hint system, progressive moon gating) the Manual world can't enforce |
| **Web tracker priority, in-game ImGui later** | User preference. Web tracker (M5) ships before in-game tracker (M8) |
| **LunaKit as soft dep (link headers), not fork** | LunaKit churns fast; submodule lets us pin without inheriting their bugs |
| **Target SMO 1.0.0** | Canonical version every public mod (lunakit, smo-online, smo-practice, OdysseyDecomp) targets. User has a native 1.0.0 install on a downgraded FW 21.2 Switch |
| **Bit-index capture table generated from apworld** | `scripts/sync_capture_table.py` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so Switch and bridge can't drift on cap-name → bit-index assignment |
| **Game name `Manual_SMO_archipelago`** | Distinct from Manual client's `Manual_SMO_mp3`. Seeds are intentionally incompatible |

## Current status — track by track

| Track | What it is | Status |
|---|---|---|
| **1 — Bridge runtime** | Python bridge can connect to AP server | DONE wiring, needs Archipelago submodule add |
| **2 — Switch dev toolchain** | devkitPro / CMake / Ninja installed on PC | **DONE** |
| **3 — Modded Switch + game dump** | Native SMO 1.0.0 install on FW 21.2 | **DONE.** Native 1.0.0 NSP + `main.nso` dump at `C:\Users\maxwe\Downloads\` (DO NOT commit — copyrighted). Keys at `C:\Users\maxwe\.switch\` |
| **4 — Symbol discovery (M0)** | Mangled symbols in `switch-mod/src/hooks/HookSymbols.hpp` | **DONE + VERIFIED.** All 8 symbols resolve in real 1.0.0 main.nso (`scripts/check_nso_symbols.py`). 3 byte-identical to lunakit's verified 1.0.0 hooks; 5 computed from OdysseyDecomp forward-decls. Runtime `nn::ro::LookupSymbol` will succeed |
| **5 — Ryujinx dev loop** | Build deploys to emulator, validates before Switch | **DONE.** `-DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx` post-build hook copies subsdk9+npdm+config into Ryujinx mods |
| **6 — Generate test seed** | Use forked apworld in Archipelago checkout to make a seed | Not started; needs Archipelago submodule add first |
| **7 — Real-Switch deploy** | Final validation after Ryujinx green | Ryujinx green (2026-05-15: HELLO observed end-to-end). Ready when desired |

## Plan milestones

`C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` is authoritative (FW 21.2 + SMO 1.0.0 simplification). The prior plan `there-is-a-super-peaceful-iverson.md` predates the downgrade and is archived for reference only. Summary:

- **M0**: toolchain + symbol map (Track 3 + Track 4) — **DONE**
- **M1**: bridge skeleton — **CODE COMPLETE** (19 tests pass, loopback smoke test green, web tracker JSON endpoint verified)
- **M2**: apworld parity fork — **CODE COMPLETE** (vendored `data/`, `creator: archipelago`)
- **M3**: Switch module skeleton — **RUNTIME VALIDATED** (2026-05-15, Ryujinx). Subsdk9 + main.npdm produced via lunakit stock template; all 8 hooks install via soft-install probe; `nn::socket` worker thread connects, sends HELLO, bridge logs `switch HELLO: mod=0.1.0 smo=1.0.0`. Inbound-item handler / replay / exponential backoff are code-complete but not yet exercised. Real-Switch deploy gated on user choice; Ryujinx loop is canonical for now.
- **M4**: read-only state mirroring — **DONE.** All 6 game-event hooks (MoonGet, CaptureStart, ScenarioFlag, SaveLoad, Ending, Death) emit raw SMO identifiers to the bridge. Bridge resolves via `shine_map.json` / `capture_map.json`. DeathLink outbound wired (inbound kill lands in M6). `Check` is now `char[64]` buffers + `FlatHashSet<4096>` for `locations_checked` (allocator NULL-deref workaround in our subsdk9 link). Validated in Ryujinx 2026-05-15.
- **M4.5**: state reconciliation across disconnects — **CODE COMPLETE.** Bridge accepts new `state_begin` / `state_chunk` / `state_end` snapshot from Switch on every (re)connect (transitively on save load via `requestRehello`); accumulates raw IDs by stage and dispatches each entry through the same `check` path live moon-get hooks use. `BridgeState.add_checked_location` dedupes by full ItemRef identity so replays are no-ops. Switch fixes outbound check drop bug in `pumpOnce` (peek-then-pop). 11 new bridge tests; switch-mod enumerate functions stubbed pending M5/M6 GameDataHolder traversal.
- **M5**: web tracker — **CODE COMPLETE** (Flask + SSE, served on :8000)
- **M5.5**: AP server live integration — **DONE 2026-05-15.** Forked apworld zipped to `vendor/Archipelago/custom_worlds/smo_archipelago.apworld` via `scripts/install_apworld.py`. Seed generation via `scripts/ap_generate.py` (thin wrapper that pre-sets `ModuleUpdate.update_ran = True` to suppress AP's auto-pip on world-specific deps). MultiServer wrapper at `scripts/ap_server.py`. Bridge ↔ local AP loopback validated end-to-end: `>> check Cap: Frog-Jumping Above the Fog` → bridge translates → `LocationChecks` to AP → AP sends `ReceivedItems` → bridge forwards `ItemMsg` to fake-Switch (all under 1s per round-trip). Bridge fix in `ap_client.py::_populate_datapackage_from_ctx` hydrates `self._dp` from CommonContext's `location_names`/`item_names` on `Connected` (CommonContext satisfies its own lookup from Archipelago's shipped `network_data_package.json` and never relays a `DataPackage` packet that our `on_package` could catch). Regression test `bridge/tests/test_ap_loopback.py` skips unless `SMOAP_LIVE_AP=1`; 43 existing tests still green. Test seed at `bridge/test_seeds/smo_loopback.yaml` (gitignored output at `bridge/test_seeds/out/`).
- **M5.7**: Ryujinx E2E — **DONE 2026-05-15.** First real moon traversed the whole stack: Mario collects "Our First Power Moon" in Ryujinx → `MoonGetHook` fires with `stage=WaterfallWorldHomeStage, obj=obj214` → `[pump] Send 102 bytes` → bridge resolves via `shine_map.json` → `LocationCheck id=14481151511` to AP → AP records check, places "Snow Kingdom Power Moon" item → `ReceivedItems` echoed → bridge forwards `ItemMsg` to mod (mod's inbound ring receives it; M6 application still stubbed). Three real bugs surfaced + fixed: (a) mod's `BRIDGE_HOST` was baked at the stale M3-era LAN IP (rebuilt with `-DBRIDGE_HOST=127.0.0.1` for Ryujinx-on-same-host); (b) `shine_map.json` seed entries used aspirational `MoonOurFirst`-style symbolic names but `ShineInfo::objectId` actually emits the placement-file ref `obj214` — confirmed via MoonFlow's public `ShineInfo` schema, replaced with 1 verified entry; (c) `ap_client.report_check` silently returned on `locations_checked` dedup, which combined with persistent `AP_*.apsave` from the M5.5 smoke test masked working pipeline as "moon arrived but nothing happened" — added explicit forwarding-vs-skip log lines. Diagnostic logging shipped permanently: `MoonGetHook` probe (`obj`/`scen`/`uid`), `ApClient::pumpOnce` `[pump]` traces, `ap_client.report_check` forwarding-distinction lines. These were load-bearing observability — every issue would have been silent without them.
- **M5.8 (planned)**: full moon-data extraction. `shine_map.json` currently has 1 of ~565 entries. Need to ingest SMO's romfs to produce the full `(stage_name, obj_id) → (kingdom, display_name)` table. See "Moon data extraction" section below.
- **M6**: item application (received items → GameDataHolder writes) — also lands snapshot enumerate bodies (`enumerateOwnedShines` / `enumerateOwnedCaptures`); same GameDataHolder traversal as `grantShine`
- **M7**: capture lock + goal detection
- **M8**: apworld extensions + in-game ImGui + polish

## Repository layout

```
C:\Users\maxwe\SMOArchipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    (after `git submodule add`)
  apworld/                       Forked manual_smo_mp3 → smo_archipelago
    smo_archipelago/             Full package; only `data/game.json` creator field changed
    README.md
  bridge/                        Python bridge — 43 tests pass (+1 live-AP skipped)
    smo_ap_bridge/
      __main__.py
      config.py                  TOML loader, CLI overrides, env var SMOAP_PASSWORD / SMOAP_AP_PATH
      protocol.py                Wire-format dataclasses (Switch ↔ Bridge), iter_lines, MAX_LINE_BYTES
      ap_client.py               CommonContext subclass; three-tier Archipelago path resolution
      switch_server.py           asyncio TCP server, line-JSON framing, replay on HELLO
      datapackage.py             AP id↔name + classifier (Moon/Capture/Kingdom/Shop/Other)
      state.py                   Thread-safe state mirror for tracker + replay
      tracker_web.py             Flask app on :8000, /api/snapshot
      logging_setup.py
    tests/                       43 passing (test_ap_loopback.py skips unless SMOAP_LIVE_AP=1)
    pyproject.toml
    requirements.txt
    config.example.toml
  switch-mod/                    exlaunch C++ module
    CMakeLists.txt               Builds subsdk9 from lunakit stock templates; no FW 22+ hacks
    src/
      main.cpp                   exl_main entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol}.{cpp,hpp}
      ap/capture_table.h         AUTO-GENERATED (42 cap names)
      hooks/HookSymbols.hpp      8 mangled symbols
      hooks/{MoonGet,CaptureStart,ScenarioFlag,SaveLoad,Ending}Hook.cpp
      game/{MoonApply,CaptureGate,KingdomUnlock}.{cpp,hpp}
      ui/ApHudOverlay.{cpp,hpp}
      util/{Json,Log}.{cpp,hpp}  Json reader implemented; rest stubs
    romfs/ap_config.json         Switch reads at runtime for bridge IP
    lunakit-vendor/              Vendored LunaKit submodule (toolchain + templates + libs)
  scripts/
    bridge_smoke_test.py         Fake-Switch end-to-end test
    sync_capture_table.py        items.json → capture_table.h (use this; ps1 also exists)
    sync_capture_table.ps1
  docs/
    architecture.md              Three-tier diagram, threading, responsibilities
    wire-protocol.md             14 message types with examples
    build-windows.md             Toolchain install
    install-switch.md            SD card layout, troubleshooting
  vendor/                        For submodules (Archipelago goes here)
  third_party/                   Local clones — gitignored
    SMO-manual-AP/               Reference clone of upstream Manual world
```

## External paths (outside the repo)

| Path | Purpose |
|---|---|
| `C:\Users\maxwe\.switch\prod.keys` | Console keys (hactool default location). Also `dev.keys` |
| `D:\switch\` | User's microSD — DO NOT write large files here, it's the actual SD card |
| `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` | The authoritative plan (FW 21.2 + 1.0.0 simplification) |
| `C:\Users\maxwe\.claude\projects\C--Users-maxwe-SMOArchipelago\memory\` | Auto-memory directory |

## Dev loop — Ryujinx FIRST, real Switch never as the primary test

The user's HOS increments a "title failed to launch" counter for SMO every time the game crashes during startup. After enough failures HOS shows "Corrupted data detected" prompts. Cart data is never actually damaged (Atmosphere overlays are runtime), but recovery costs the user real time (Settings → Data Management → Check for Corrupted Data, ~1 min, OR an unnecessary 30+ min reinstall if they don't know about that menu). **Never deploy a freshly-changed subsdk9 to their Switch as the first test.**

The flow:

```pwsh
# 0. ONE-TIME (after fresh clone or `git pull` that touched apworld/data/items.json):
#    Generate switch-mod/src/ap/capture_table.h. The file is gitignored — the
#    build will fail with "../ap/capture_table.h: No such file or directory"
#    on the first compile of CaptureGate.cpp until you run this.
python C:\Users\maxwe\SMOArchipelago\scripts\sync_capture_table.py

# 1. Build (~10s)
cd C:\Users\maxwe\SMOArchipelago\switch-mod
$env:DEVKITPRO = "C:/devkitPro"
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja `
    -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake `
    -DBRIDGE_HOST=192.168.1.187 `
    -DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx
& "C:/Program Files/CMake/bin/cmake.exe" --build build
# Post-build hook auto-deploys subsdk9+npdm+ap_config.json into
# %APPDATA%/Ryujinx/mods/contents/0100000000010000/smo-archipelago/
#
# Note: if Ninja isn't installed, swap `-G Ninja` for
#   `-G "Unix Makefiles" -DCMAKE_MAKE_PROGRAM=C:/devkitPro/msys2/usr/bin/make.exe`
# Same build product; verified end-to-end.

# 2. Boot SMO in Ryujinx. User does this manually:
#    cd C:\Users\maxwe\Documents\ryujinx-1.3.3 && .\Ryujinx.exe
#    (then double-click SUPER MARIO ODYSSEY in the game list)

# 3. After boot attempt, check for output:
type "C:\Users\maxwe\AppData\Roaming\Ryujinx\sdcard\atmosphere\contents\0100000000010000\smoap.log"
Get-Content (Get-ChildItem "$env:APPDATA\Ryujinx\Logs\Ryujinx_*.log" | Sort LastWriteTime -Descending | Select -First 1) -Tail 80
```

Ryujinx's log is gold — it surfaces `[rtld]` unresolved symbols, guest stack traces with C++ demangled names, and guest register dumps. **Far** more useful than the Switch's binary erpts. Always iterate here.

Only after Ryujinx boots clean → propose deploying to the real Switch:

```pwsh
& "C:/Program Files/CMake/bin/cmake.exe" --install build  # populates sd-overlay/
xcopy /E /I /Y C:\Users\maxwe\SMOArchipelago\switch-mod\sd-overlay\atmosphere D:\atmosphere
```

If a Switch deploy ever causes the corruption icon: Settings → Data Management → Software → Super Mario Odyssey → Check for Corrupted Data. NOT a reinstall.

## Subsdk slot

Module ships as **`subsdk9`** at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9` — the lunakit default. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## Game dump (1.0.0)

User has a native SMO 1.0.0 NSP installed — no Atmosphere downgrade overlay. Local copies of `SMO_1.0.0.nsp` and the extracted `main.nso` (15.4 MB) live at `C:\Users\maxwe\Downloads\`. **Never commit these — copyrighted.** `.gitignore` covers `docs/main-*.nso` and the Downloads location is outside the repo.

For offline symbol verification: `bridge/.venv/Scripts/python scripts/check_nso_symbols.py C:\Users\maxwe\Downloads\main.nso`. The script decompresses the NSO segments (LZ4 block) and grep's the `.dynstr` table for the 8 mangled hook names. As of 2026-05-15 all 8 resolve.

## libnx extern "C" gotcha

Critical bug we hit twice. `lunakit-vendor/src/lib/nx/kernel/svc.h` and `lib/nx/result.h` declare functions WITHOUT any `extern "C"` wrapper. The wrapper is in the umbrella `lib/nx/nx.h`. From C++ TUs, **always `#include "lib/nx/nx.h"`**, never the inner headers directly. Including them direct gives C++ mangling at call sites (e.g. `_Z20svcOutputDebugStringPKcm`), the assembly stubs have C linkage, link succeeds, runtime gets unresolved-symbol from rtld, PC jumps to 0, process aborts.

## nn::fs SD mount

`sd:/...` paths in nn::fs are NOT accessible by default in our process. SMO doesn't mount the SD via the Nintendo SDK API (its asset path goes through `sead::FileDeviceMgr` to RomFS). To use `nn::fs::OpenFile("sd:/...")` we must call `nn::fs::MountSdCardForDebug("sd")` once. LunaKit does this by hooking `sead::FileDeviceMgr` ctor. We do it inline in our `GameSystemInitHook::Callback` (plus a fallback in `DrawMainHook` first-call). Without this, `nn::fs::CreateFile` aborts via internal `GetFreeSpaceSize` because "sd:" is unmounted.

## How to run the bridge

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
.\.venv\Scripts\python -m pytest                            # 43 tests pass (1 skipped: live-AP)
.\.venv\Scripts\python -m smo_ap_bridge --no-web-tracker    # without web tracker
.\.venv\Scripts\python -m smo_ap_bridge --config config.local.toml --web-tracker  # full
```

Bridge listens on `0.0.0.0:17777` (Switch TCP) and `0.0.0.0:8000` (web tracker). AP-side connection requires `vendor/Archipelago/` submodule with deps installed (see "Loopback dev setup" in README).

## AP loopback (recommended pre-Ryujinx test)

Validates the whole bridge↔AP stack without booting SMO. After fresh clone:

```pwsh
# Build apworld zip
bridge/.venv/Scripts/python scripts/install_apworld.py

# Generate test seed (one-time per apworld change)
bridge/.venv/Scripts/python scripts/ap_generate.py `
    --player_files_path bridge/test_seeds --outputpath bridge/test_seeds/out

# Unzip the .archipelago server file out of the player zip
bridge/.venv/Scripts/python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('bridge/test_seeds/out') for z in glob.glob('bridge/test_seeds/out/AP_*.zip')]"

# Host server (pane A)
bridge/.venv/Scripts/python scripts/ap_server.py --port 38281 bridge/test_seeds/out/AP_*.archipelago

# Bridge (pane B) — needs bridge/config.local.toml with host=localhost slot=Mario
bridge/.venv/Scripts/python -m smo_ap_bridge --config bridge/config.local.toml

# Drive checks (pane C)
python scripts/bridge_smoke_test.py
# Expect: each `>> check` mirrored by a `<< item` within ~1s

# Or scripted via pytest:
$env:SMOAP_LIVE_AP="1"; bridge/.venv/Scripts/python -m pytest -v bridge/tests/test_ap_loopback.py
```

Quick old-style smoke test (Switch-only, no AP server):
```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\bridge_smoke_test.py
```

## What's next

**M5.7 — Ryujinx E2E** (next milestone): the AP loopback above, but with the real M3/M4 mod running in Ryujinx instead of `bridge_smoke_test.py`. Steps:

1. Boot SMO 1.0.0 in Ryujinx with our `subsdk9` deployed. Confirm `smoap.log` appears under `%APPDATA%/Ryujinx/sdcard/atmosphere/contents/0100000000010000/`. Ryujinx's game library must hold the 1.0.0 version (re-import if it still has 1.3.0).
2. Once `smoap.log` is non-empty, the soft-install logs will name any symbols that didn't resolve. Fall back to delta-polling per-hook if needed (most likely candidate: `setGotShine` if inlined).
3. Verify worker thread reaches `connect()` (LAN IP from `BRIDGE_HOST` cmake var). Bridge running on PC should log `switch HELLO`.
4. With AP server already running (per the loopback recipe above), collect a moon in-game and confirm `LocationChecks` is logged at the AP server and the bridge logs `ItemMsg` going back down (it'll be a no-op on the Switch until M6, but the round-trip should be observable).

**Then on real Switch** (only after Ryujinx green):
5. `cmake --install build` → `xcopy /E /I /Y switch-mod\sd-overlay\atmosphere D:\atmosphere`.
6. Boot SMO. Same `smoap.log` should appear under SD's `atmosphere/contents/0100000000010000/`.

**After M5.7 — M6 implementation**: fill in-game item application — `game/MoonApply::grantShine` (idempotent GameDataHolder write), `game/CaptureGate::captureBlocked` (bitset gate on `PlayerHackKeeper::startHack`), and the snapshot enumerate bodies (`enumerateOwnedShines` / `enumerateOwnedCaptures`).

## Adding new hook targets

8 symbols in `switch-mod/src/hooks/HookSymbols.hpp`. 3 come verbatim from lunakit's `src/program/main.cpp` `InstallAtSymbol(...)` calls; that's the canonical 1.0.0 source. For symbols lunakit doesn't hook, forward-declare the signature from `MonsterDruide1/OdysseyDecomp` (a 1.0.0 decompilation) and pass through `aarch64-none-elf-g++ -c` + `nm` to get the mangled name — Itanium ABI mangling is deterministic from the signature, so forward decls are sufficient. If a function turns out to be inlined on 1.0.0, fall back to delta-polling the relevant field from `drawMain` (one-frame latency, zero symbol dependency).

## User collaboration style (from memory)

- **No blind Switch deploys.** Every subsdk build must boot clean in Ryujinx first. Failed Switch launches trigger HOS corruption flag → painful recovery. Memory: `feedback_no_blind_switch_deploys.md`.
- **Show output before fix**: when the user reports an error from a tool, wait for the full output before committing to a remediation tool call. Memory: `feedback_show_output_first.md`.
- **Atmosphere `enable_log_manager` is broken** on the user's HATS 1.11.1 pack — enabling it crashes Atmosphere at boot. Don't suggest. Memory: `feedback_atmosphere_log_manager_broken.md`.
- User has Switch homebrew experience (Goldleaf, prod.keys from Lockpick_RCM, FW 21.2 downgraded Switch with native SMO 1.0.0 install, Ryujinx 1.3.3 installed at `C:\Users\maxwe\Documents\ryujinx-1.3.3` with firmware + game imported).
- Windows 11, PowerShell-default shell, Python 3.14.3 installed.
- D: drive is the microSD card — never write large files there.
- PowerShell execution policy is restrictive (`-ExecutionPolicy Bypass` is denied). Use Python alternatives for scripts.

## Test commands worth knowing

```pwsh
# Bridge tests (Python)
cd C:\Users\maxwe\SMOArchipelago\bridge
python -m pytest -v

# Switch-module host tests (C++ via MSVC). Requires Visual Studio 2022 BuildTools.
cmd /c C:\Users\maxwe\AppData\Local\Temp\build_test_protocol.bat
C:\Users\maxwe\AppData\Local\Temp\build\test_protocol.exe
cmd /c C:\Users\maxwe\AppData\Local\Temp\build_test_json.bat
C:\Users\maxwe\AppData\Local\Temp\build\test_json.exe

# Switch-module cross build (devkitA64 + Windows CMake; not msys2 cmake)
cd C:\Users\maxwe\SMOArchipelago\switch-mod
$env:DEVKITPRO = "C:/devkitPro"
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake
& "C:/Program Files/CMake/bin/cmake.exe" --build build
& "C:/Program Files/CMake/bin/cmake.exe" --install build  # populates sd-overlay/

# Regenerate capture table after apworld change
python C:\Users\maxwe\SMOArchipelago\scripts\sync_capture_table.py

# Loopback smoke test (with bridge running separately)
python C:\Users\maxwe\SMOArchipelago\scripts\bridge_smoke_test.py
```

**Critical cross-build gotcha**: msys2 cmake (`/c/devkitPro/msys2/usr/bin/cmake`) inside Git Bash CANNOT find DEVKITPRO (it expects `/opt/devkitpro` mount which Git Bash doesn't have). Use the Windows CMake at `C:/Program Files/CMake/bin/cmake.exe` with `DEVKITPRO=C:/devkitPro` env var.

The build also needs `set_source_files_properties(... PROPERTIES COMPILE_FLAGS "-fpermissive")` on lunakit's vendored sources because devkitA64 GCC 15 rejects const-T `std::construct_at` in lunakit's `typed_storage.hpp`. Already wired in our CMakeLists.

## Moon data extraction (M5.8 plan context)

`shine_map.json` currently has 1 of ~565 entries — populated by hand after M5.7's first end-to-end Ryujinx run. Next milestone bulk-fills it from SMO's romfs. Everything in this section is research notes for that planning session — no code has been written yet.

### What we need

A full mapping of `(stage_name, obj_id) → (kingdom_prefix, english_display_name)` for every moon, so the bridge can resolve any `MoonGetHook` fire without manual labeling. ~565 entries to match `apworld/smo_archipelago/data/locations.json`'s moon-location count.

### Why nothing public has this

It lives in SMO's romfs, which is Nintendo IP. Every tool that uses the data (MoonFlow, OdysseyEditor) reads it at runtime from the user's dump. The English-name-by-kingdom lists in `empathy-mp3/SMO-manual-AP`, our forked apworld, and `rampantepsilon/smorando` were extracted by humans for those projects but the `(stage, obj_id) → name` join isn't published anywhere we searched (see the M5.7 chapter; we searched ~10 directions including GitHub, smo.wiki, GBATemp).

### Where the data lives

Inside SMO's romfs:

- **`StageData/<Stage>ShineList.byml`** — per-stage list of every shine with metadata. Each entry has `StageName`, `ScenarioName`, `ObjId` (e.g. `"obj214"`), `UniqueId` (int), `MainScenarioNo`, `HintIdx`, `ProgressBitFlag`, `IsAchievement`, `IsGrand`, `IsMoonRock`, `Trans` (Vec3 world position). Field names confirmed via [MoonFlow ShineInfo.cs](https://github.com/Amethyst-szs/MoonFlow/blob/stable/MoonFlow.Project/DB/Info/ShineInfo.cs).
- **`LocalizedData/USen/MessageData/StageMessage_<Stage>.szs` (or similar)** — message archive (SARC of MSBT files). Display names keyed by `"ScenarioName_" + ObjId` (e.g. `"ScenarioName_obj214"`). Lookup convention confirmed via MoonFlow source: see `ShineInfo.LookupDisplayName()`.

### Tools available (all open source)

- **[zeldamods/byml-v2](https://github.com/zeldamods/byml-v2)** — mature Python BYML parser/writer; full SMO support. Pip-installable.
- **[Amethyst-szs/MoonFlow](https://github.com/Amethyst-szs/MoonFlow)** — C# Godot tool that already does this exact join. Could be run headless to dump or used as reference implementation. Already in our submodule tree via lunakit linkage (separate repo but same author).
- **MSBT/SARC parsers** — several Python options exist; can crib from MoonFlow's `Nindot` library if Python options don't pan out.
- **hactool / hactoolnet** — extract romfs from the user's `SMO_1.0.0.nsp` at `C:\Users\maxwe\Downloads\` (keys at `C:\Users\maxwe\.switch\prod.keys`).

### Verified anchor entry

Our one ground-truth datapoint from M5.7:
- `(WaterfallWorldHomeStage, "obj214")` → kingdom `Cascade`, display name `"Our First Power Moon"`
- The mod logged `scen=ScenarioName_obj214` from `ShineInfo` field at offset 0x130 — confirming the `"ScenarioName_" + ObjId` MSBT lookup key for this entry.

Any extraction tool must produce this row identically.

### Cross-validation against apworld

Every extracted display name must match an entry in `apworld/smo_archipelago/data/locations.json` (565 strings of form `"<Kingdom>: <Moon name>"`). The extracted JSON is junk if it produces names that don't appear there — that signals a stage/scenario the upstream Manual world excluded, or a translation mismatch. Mismatches are the main risk surface in the design.

### Likely shape of the output script

`scripts/extract_shine_map.py` (TBD):
1. Take a romfs path argument (user extracts NSP → romfs separately).
2. Walk `StageData/*ShineList.byml` via `byml-v2`.
3. Walk `LocalizedData/USen/MessageData/*` for MSBT message tables.
4. Join `(StageName, ObjId) ↔ msbt[ScenarioName + "_" + ObjId]`.
5. Cross-reference against `apworld/.../locations.json` to assign kingdom prefix (and drop / warn on names not present in the apworld).
6. Emit `bridge/smo_ap_bridge/data/shine_map.json` covering all moons.

Open design questions for the planning session:
- Romfs extraction: ship a one-liner using hactoolnet (cli, MIT-licensed) in our `scripts/`, or document a manual step?
- Whether to also extract capture metadata (the apworld omits tutorial captures, so the capture map likely stays partial — but the same mechanism could help if we later extend the apworld).
- How to handle locale: USen is the canonical English. Could let users override for non-English play.
- Whether the extraction script lives in `scripts/` (one-off) or `bridge/smo_ap_bridge/` (callable from a bridge subcommand for end-users with their own dump).

## Known unknowns / risks

1. **`PlayerHackKeeper::startHack` may not be a single chokepoint** — capture entry can split across multiple functions per cap-type. Secondary read-only check on `CapTargetInfo::isCaptureTarget` from the frame pump if the trampoline misses cases.
2. **Synthetic moon grant** must not retrigger our own hook — `ApState::synthetic_grant_this_frame` guard exists, plus belt-and-braces dedupe by `locations_checked` hash set.
3. **`Game.py` game-name guard**: bridge should compare `game_name` against `RoomInfo` at startup to catch seed mis-pairing. Not yet implemented; M4 todo.
4. **DemoPeachWedding hook fires for the wedding cutscene** which is the canonical SMO ending. If 1.0.0 names that demo differently (unlikely given OdysseyDecomp targets 1.0.0), the symbol won't resolve and we'd fall back to hooking a `setMainScenarioNo` call with the post-Bowser scenario value.

## What's definitely NOT done

- Ryujinx + real-Switch boot validation — M3 module compiles + links cleanly but hasn't been loaded yet (next step)
- AP-side connection in bridge never tested against a real server (needs Archipelago submodule + generated seed)
- M4-M7 hook callback bodies — the 5 game-event hooks are empty trampolines that just call `Orig`. Real moon detection / capture lock / goal trigger lands in M4-M7
- On-screen status overlay — deferred to M8 per user Q&A; M3 ships heartbeat-to-lm-log instead (web tracker is the canonical source of truth)
- HELLO `cap_table_hash` field is empty — populated in M4 once we hash the generated `capture_table.h`
