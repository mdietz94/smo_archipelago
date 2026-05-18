# CLAUDE.md — context for the next session

This file is a fast-load brief for picking up the **Spicy Meatball Overdrive** project cold. Three identifiers all refer to the same thing but spell it differently — keep them straight:

| Identifier | Value | Scope |
|---|---|---|
| AP-protocol game name | `Spicy Meatball Overdrive` | Wire-format `game` field in YAML seeds and AP `Connect` packets |
| Shipped apworld zip | `smo.apworld` | What lands in `vendor/Archipelago/custom_worlds/`; Archipelago imports it as `worlds.smo` |
| host.yaml settings key | `smo_options` | Derived by Archipelago from the zip stem `smo` |
| In-repo source folder | `apworld/smo_archipelago/` | Kept verbose to avoid churning every dev-workflow path reference; only the deployed artifact uses `smo` |
| Switch mod CMake project | `smo_archipelago` | Unrelated to the apworld; lives in `switch-mod/CMakeLists.txt` |

All four "smo" spellings parse as **S**picy **M**eatball **O**verdrive. The 2026-05-16 rename pass dropped the `Manual_SMO_archipelago` AP identifier (Manual-framework prefix was redundant once we shipped a real client) and shortened the deployed zip to `smo.apworld` (the `_archipelago` suffix was redundant when the parent dir is literally `custom_worlds/`). Read this file first, then `docs/architecture.md` and the plan file at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

## ⚠️ CRITICAL: Never commit Nintendo IP

This repository is open-source and built on a careful line: **functional identifiers and reference apworld names are okay; bulk-extracted Nintendo content is not.** A misstep here exposes the user to DMCA risk. Before any commit, audit `git status` + `git diff` and refuse to stage anything from this list:

**Must NEVER be committed (already gitignored — keep it that way):**
- `apworld/smo_archipelago/client/data/shine_map.json` — full extracted (stage, obj_id) → display-name table. Generated per-machine by `scripts/extract_shine_map.py`. ~775 verbatim Nintendo USen strings.
- `apworld/smo_archipelago/client/data/capture_map.json` — `hack_name → english_name` table. ~52 verbatim Nintendo USen strings.
- `apworld/smo_archipelago/client/data/shine_map_review.json` and `capture_map_review.json` — diagnostics that include the same strings.
- `.romfs-cache/` — extracted RomFS (~5 GB of Nintendo assets).
- `scripts/.extract-venv/` — local Python 3.12 venv (not IP, but big and machine-specific).
- `docs/main-*.nso`, `*.nsp`, `*.nca`, `*.byml`, `*.szs`, `*.msbt` — any raw Nintendo binary.
- `prod.keys` / `dev.keys` / `title.keys` — Switch keys are themselves IP-sensitive.
- Any moon-name list, capture list, or stage list of more than ~5 entries pasted into a doc, comment, or commit message as illustrative content — bulk transcription is the same exposure as the file.

**Generally OK (already in the repo, established by upstream forks):**
- `apworld/smo_archipelago/data/locations.json` and `items.json` — the community-curated location and capture names (currently 482 locations + 42 captures after the shop / outfit / trap purge). Forked from the public [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) Manual world. Edits are fine; bulk additions from a romfs dump are not — alignment with Nintendo's MSBT should happen one mismatch at a time, not as a wholesale copy.
- Functional identifiers like `WaterfallWorldHomeStage`, `obj214`, `ScenarioName_<ObjId>`, `ShineList`, kingdom internal names (`CapWorld`/`SkyWorld`/etc.). These appear in every public SMO modding project (lunakit, MoonFlow, OdysseyDecomp) and are functional, not expressive.
- The one M5.7 anchor entry (`"Our First Power Moon"`) appears in CLAUDE.md, the test suite, and docs as a known ground-truth datapoint. One name as a verifiable test fixture is fine; a list of names is not.

**Safe pattern**: anything that requires a user to run `scripts/extract_shine_map.py` to produce stays in the gitignore. If you find yourself wanting to commit a piece of data so the next agent has a richer starting point, instead document where to regenerate it — see `docs/extract-moon-data.md` for the model.

**If you've staged something questionable**: `git restore --staged <path>` to unstage, then either delete the file or add it to `.gitignore` before retrying. Never override `.gitignore` with `git add -f` for SMO content. When in doubt, ask the user.

## What we're building

A real Archipelago client for **Super Mario Odyssey on a modded Switch (FW 21.2, native SMO 1.0.0 install, Atmosphere CFW)**. Replaces the existing Manual checklist client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system tick-the-boxes app — with an in-game module that detects moons/captures/scenario events automatically, applies received items live, and enforces capture locks until the AP item arrives.

### Architecture (two tiers)

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Client (Python, inside apworld) ]  <--websocket-->  [ AP server ]
   exlaunch                              SMOContext(CommonContext)                              archipelago.gg
   LunaKit headers                       Kivy GUI (Tracker + Connections tabs)                  or self-host
   ImGui overlay (M8)                    SwitchServer asyncio TCP on :17777
   HUD overlay (M3)                      Forked apworld machinery
```

The PC client (formerly the standalone "bridge" process) lives inside the apworld at `apworld/smo_archipelago/client/` and ships in the .apworld zip. Archipelago's Launcher auto-discovers it via the `Component("SMO Client", ...)` registration in the apworld's `__init__.py`. One process, one Kivy window, one install artifact.

The client owns AP-protocol complexity (websocket + deflate + TLS + reconnect, all inherited from `CommonContext`). Switch speaks a small line-delimited JSON protocol on port **17777**. Full wire format: `docs/wire-protocol.md`.

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
| **Game name `Spicy Meatball Overdrive`, zip `smo.apworld`** | Renamed 2026-05-16. AP-protocol name dropped the Manual-framework `Manual_SMO_archipelago` prefix (we ship a real client now, not a Manual world). Deployed zip shortened from `smo_archipelago.apworld` to `smo.apworld` — Archipelago derives the module name from the zip stem, so the world imports as `worlds.smo` and the host.yaml settings key is `smo_options`. The in-repo source folder stayed `apworld/smo_archipelago/` to avoid churning every dev-workflow path reference; see the identifier table in the preamble |
| **Two-stage connect gate (SNI-style)** | SMOClient never auto-dials AP on launch. Clicking Connect (or `/connect` / `--connect`) parks the request until the Switch HELLOs; `SMOContext.connect()` overrides `CommonContext.connect` to dial AP from the Switch-ready callback. State tracked as `disconnected → waiting_for_switch → connected`. Mirrors SNIClient (user-cited gold standard); pre-fix, the default `archipelago.gg` host produced "Connection refused" the moment the user opened the Launcher button. Any new AP-dial path (auto-reconnect, scripted launch) must route through `SMOContext.connect()` — never `asyncio.create_task(server_loop(ctx))` directly. `disconnect()` clears the pending state so a stale dial doesn't fire on the next Switch reconnect. Tests: `apworld/smo_archipelago/tests/test_connect_gate.py` |

## Current status — track by track

| Track | What it is | Status |
|---|---|---|
| **1 — Bridge runtime** | Python bridge can connect to AP server | DONE wiring, needs Archipelago submodule add |
| **2 — Switch dev toolchain** | devkitPro / CMake / Ninja installed on PC | **DONE** |
| **3 — Modded Switch + game dump** | Native SMO 1.0.0 install on FW 21.2 | **DONE.** Native 1.0.0 NSP + `main.nso` dump at `C:\Users\maxwe\Downloads\` (DO NOT commit — copyrighted). Keys at `C:\Users\maxwe\.switch\` |
| **4 — Symbol discovery (M0)** | Mangled symbols in `switch-mod/src/hooks/HookSymbols.hpp` | **DONE + VERIFIED.** All 8 symbols resolve in real 1.0.0 main.nso (`scripts/check_nso_symbols.py`). 3 byte-identical to lunakit's verified 1.0.0 hooks; 5 computed from OdysseyDecomp forward-decls. Runtime `nn::ro::LookupSymbol` will succeed |
| **5 — Ryujinx dev loop** | Build deploys to emulator, validates before Switch | **DONE.** Build skill (`smo-build`) covers the manual deploy flow |
| **6 — Generate test seed** | Use forked apworld in Archipelago checkout to make a seed | DONE. See `smo-loopback-test` skill |
| **7 — Real-Switch deploy** | Final validation after Ryujinx green | Ryujinx green (2026-05-15: HELLO observed end-to-end). Ready when desired |

## Plan milestones

`C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` is the authoritative plan (FW 21.2 + SMO 1.0.0 simplification). One-line status per milestone; deep narratives in [docs/milestones.md](docs/milestones.md).

| Milestone | Status | Details |
|---|---|---|
| M0 — toolchain + symbol map | DONE | [docs/milestones.md#m0](docs/milestones.md#m0) |
| M1 — bridge skeleton | CODE COMPLETE | [docs/milestones.md#m1](docs/milestones.md#m1) |
| M2 — apworld parity fork | CODE COMPLETE | [docs/milestones.md#m2](docs/milestones.md#m2) |
| M3 — Switch module skeleton | RUNTIME VALIDATED 2026-05-15 (Ryujinx) | [docs/milestones.md#m3](docs/milestones.md#m3) |
| M4 — read-only state mirroring | DONE | [docs/milestones.md#m4](docs/milestones.md#m4) |
| M4.5 — disconnect state reconciliation | CODE COMPLETE | [docs/milestones.md#m45](docs/milestones.md#m45) |
| M4.6 — inbound DeathLink | DONE 2026-05-15 | [docs/milestones.md#m46](docs/milestones.md#m46) |
| M5 — web tracker | CODE COMPLETE | [docs/milestones.md#m5](docs/milestones.md#m5) |
| M5.5 — AP server live integration | DONE 2026-05-15 | [docs/milestones.md#m55](docs/milestones.md#m55) |
| M5.7 — Ryujinx E2E first moon | DONE 2026-05-15 | [docs/milestones.md#m57](docs/milestones.md#m57) |
| M5.8 — moon + capture extraction | DONE 2026-05-15 | [docs/milestones.md#m58](docs/milestones.md#m58) |
| M6 phase A — AP-credit HUD substitution | DONE 2026-05-15 | [docs/milestones.md#m6-phase-a](docs/milestones.md#m6-phase-a) |
| M6 phase A.5 — moon-get cutscene label | DONE 2026-05-16 (Ryujinx-verified) | [docs/milestones.md#m6-phase-a5](docs/milestones.md#m6-phase-a5) |
| M6 phase B — capture grant | DONE 2026-05-16 | [docs/milestones.md#m6-phase-b](docs/milestones.md#m6-phase-b) |
| M6.1 — worker-thread allocator hardening | DONE 2026-05-16 | [docs/milestones.md#m61](docs/milestones.md#m61) |
| M6 phase C — snapshot enumerate | DEFERRED (kingdom-unlock half dropped 2026-05-18) | [docs/milestones.md#m6-phase-c-deferred](docs/milestones.md#m6-phase-c-deferred) |
| M6 phase D — moon-deposit debit | DONE 2026-05-17 (Ryujinx-verified) | [docs/milestones.md#m6-phase-d](docs/milestones.md#m6-phase-d) |
| M7 Path A — kingdom-order gate | DONE 2026-05-17 (Ryujinx-verified) | [docs/milestones.md#m7-path-a--kingdom-order-gate](docs/milestones.md#m7-path-a--kingdom-order-gate) |
| M7 phase A — capture lock | DONE 2026-05-16 | [docs/milestones.md#m7-phase-a--capture-lock](docs/milestones.md#m7-phase-a--capture-lock) |
| M7 phase B — goal detection | DEFERRED (needs playtest) | [docs/milestones.md#m7-phase-b-deferred](docs/milestones.md#m7-phase-b-deferred) |
| M8 — apworld extensions + ImGui + polish | NOT STARTED | [docs/milestones.md#m8](docs/milestones.md#m8) |
| PopTracker pack | DONE 2026-05-17 (user-verified) | [docs/milestones.md#poptracker-pack](docs/milestones.md#poptracker-pack) |

Pattern invariants worth knowing even without reading the milestone narratives:

- **M6.1 — libstdc++ allocator NULL-derefs in subsdk9** (worker thread is NOT a safe haven — proven 2026-05-16). Any thread that hits `std::set`, `std::vector<T>::push_back` (incl. `<bool>`), `std::string` growth past SSO (~15 chars), `std::to_string`, or `std::mutex` construction NULL-derefs inside `nn::os::GetTlsValue` reading slot 0. Cause: most likely libstdc++'s allocator reaches for a `nn::os::TlsSlot` our init never `AllocateTlsSlot`'d. Use instead: `FlatHashSet<N>` (open-addressing, `uint64_t[N]`) for dedupe, `char[N]` + `copyFixedFieldN` / `readIntoField<N>` for variable strings, `LineBuffer` (caller-owned `char[8 KiB]`) for encode output, `snprintf` to stack `char[24]` for int→string, fixed `T[N]` + count for vectors, release-store-publish atomics (the `pending_moon_label` pattern) for cross-thread handoff instead of mutexes. Strings ≤ 15 chars (SSO, no heap) are OK on any thread. Long-term fix would be an early `nn::os::AllocateTlsSlot` + libnx heap-init in `exl_main`; not investigated.
- **M6 phase D**: when sending the post-HELLO item replay, **skip Moon items** — `OutstandingMsg` carries authoritative per-kingdom balance, re-sending Moons double-counts. See [docs/milestones.md#m6-phase-d](docs/milestones.md#m6-phase-d).
- **M7 Path A**: future "lie to the game" hooks need the three-layer pattern (UI query → cinematic state → stage commit) — catch upstream of the visible state change, not just at commit. See [docs/milestones.md#m7-path-a--kingdom-order-gate](docs/milestones.md#m7-path-a--kingdom-order-gate).

## Repository layout

```
C:\Users\maxwe\Documents\smo_archipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    Submodules (vendor/Archipelago, lunakit-vendor)
  .claude/skills/                Project skills (smo-build, smo-loopback-test, ...)
  apworld/smo_archipelago/       Forked manual_smo_mp3 → smo_archipelago apworld + client
    __init__.py                  World class + SMOSettings + "SMO Client" Component reg
    data/                        items.json / locations.json / regions.json / categories.json
    hooks/                       Manual-framework hook surfaces (Rules, Options, World, ...)
    Data.py, Game.py, ...        Manual framework boilerplate
    ManualClient.py              Vestigial — NOT Launcher-registered; kept because the Manual
                                 framework references it. The active client is client/main.py.
    client/                      Python client (replaces the old standalone `bridge/`)
      __init__.py                Empty / lightweight; never pulls Kivy
      main.py                    Launcher entry point; `def launch(*args)` invoked via Component
      context.py                 SMOContext(CommonContext) + SMOClientCommandProcessor
      gui.py                     SmoManager(GameManager) — Kivy UI; imported lazily inside run_gui
      switch_server.py           asyncio TCP server on :17777; replay on HELLO
      protocol.py, state.py      Wire-format dataclasses + thread-safe state mirror
      datapackage.py, maps.py    AP id↔name + classifier + ShineMap / CaptureMap
      scout_cache.py, display.py Channel A: LocationScouts pre-fetch + label formatting
      commands.py                Pure `parse_command` for the /-commands in context.py
      config.py, logging_setup.py  Legacy TOML overlay (kept for back-compat) + log config
      data/                      shine_map.json + capture_map.json (gitignored; regenerated)
    tests/                       120 passing (11 skipped: live-AP gated on SMOAP_LIVE_AP=1
                                 + extraction tests need shine/capture maps present)
      pyproject.toml             Self-contained pytest config (importmode=importlib)
      conftest.py                Inserts apworld/smo_archipelago/ into sys.path
      seeds/                     Loopback test seeds (smo_loopback.yaml + gitignored out/)
  switch-mod/                    exlaunch C++ module — unchanged by the client merge
    CMakeLists.txt               Builds subsdk9 from lunakit stock templates
    src/
      main.cpp                   exl_main entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol}.{cpp,hpp}
      ap/capture_table.h         AUTO-GENERATED (42 cap names) — run sync_capture_table.py
      hooks/HookSymbols.hpp      Mangled SMO 1.0.0 symbols
      hooks/{MoonGet,CaptureStart,ScenarioFlag,SaveLoad,Ending,MoonLabel,WorldMapSelect}Hook.cpp
      game/{MoonApply,CaptureGate,KingdomUnlock,KingdomOrderGate}.{cpp,hpp}
      ui/ApHudOverlay.{cpp,hpp}
      util/{Json,Log}.{cpp,hpp}
    romfs/ap_config.json         INFORMATIONAL ONLY — bridge IP/port are baked in at
                                 compile time via CMake -DBRIDGE_HOST/-DBRIDGE_PORT. The
                                 runtime SD-read path was abandoned (MountSdCardForDebug
                                 fails on retail/newer FW). See ApConfig.cpp:1-8. Editing
                                 this JSON on the SD does NOTHING — rebuild instead.
    lunakit-vendor/              Vendored LunaKit submodule
  scripts/
    switch_smoke_test.py         Fake-Switch end-to-end test
    sync_capture_table.py        items.json → capture_table.h
    extract_shine_map.py         M5.8: NSP → romfs → shine_map.json + capture_map.json
    install_apworld.py           Zips apworld/smo_archipelago/ → vendor/.../custom_worlds/
    ap_generate.py, ap_server.py Archipelago Generate/MultiServer wrappers (auto-pip suppressed)
    build_poptracker_pack.py     PopTracker pack generator
    check_nso_symbols.py         Offline symbol-resolution check against main.nso
    .extract-venv/               Auto-created Python 3.12 venv (gitignored)
  docs/
    architecture.md              Two-tier diagram, threading, responsibilities
    wire-protocol.md             14 message types with examples
    build-windows.md             Toolchain install
    extract-moon-data.md         How to generate shine_map.json + capture_map.json
    install-switch.md            SD card layout, troubleshooting
    milestones.md                Deep per-milestone narrative — linked from status table above
  vendor/                        For submodules (Archipelago goes here)
  third_party/                   Local clones — gitignored
    SMO-manual-AP/               Reference clone of upstream Manual world
  poptracker/
    pack-src/                    Hand-authored: manifest, init.lua, logic.lua (Lua ports of
                                 Rules.py), autotracking.lua, layouts.
    build/                       Generated; gitignored.
```

## External paths (outside the repo)

| Path | Purpose |
|---|---|
| `C:\Users\maxwe\.switch\prod.keys` | Console keys (hactool default location). Also `dev.keys` |
| `D:\switch\` | User's microSD — DO NOT write large files here, it's the actual SD card |
| `C:\Users\maxwe\Downloads\SMO_1.0.0.nsp` + `main.nso` | Game dump (copyrighted — never commit) |
| `C:\Users\maxwe\AppData\Roaming\Ryujinx\` | Ryujinx install + mods + logs |
| `C:\Users\maxwe\Documents\ryujinx-1.3.3\` | Ryujinx executable |
| `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` | The authoritative plan (FW 21.2 + 1.0.0 simplification) |
| `C:\Users\maxwe\.claude\projects\C--Users-maxwe-Documents-smo-archipelago\memory\` | Auto-memory directory |

## Skills

Project skills live in `.claude/skills/`. They auto-load when triggered by their description keywords:

- **smo-build** — build switch-mod, deploy to Ryujinx/Switch, capture_table sync, libnx + worktree gotchas, fresh-worktree setup, the SMO-already-inits-socket rule.
- **smo-loopback-test** — AP loopback E2E without booting SMO (3-pane setup + scripted pytest path).
- **smo-host-tests** — C++ host tests (test_json, test_protocol) via msys2 mingw64 g++.
- **smo-symbol-discovery** — add new hook targets; OdysseyDecomp forward-decls + aarch64 mangling + check_nso_symbols.py verification.
- **smo-extract-data** — regenerate `shine_map.json` + `capture_map.json` from a 1.0.0 NSP.
- **smo-poptracker** — build / iterate / debug the PopTracker pack.

For anything not covered by a skill, [docs/milestones.md](docs/milestones.md) is the deep-dive: it captures pattern decisions (Channel-A scout pre-warm, the three-layer hook pattern from M7 Path A, the worker-thread allocator hardening from M6.1) that successor work tends to need.

## Known unknowns / risks

1. **`PlayerHackKeeper::startHack` may not be a single chokepoint** — capture entry can split across multiple functions per cap-type. Secondary read-only check on `CapTargetInfo::isCaptureTarget` from the frame pump if the trampoline misses cases.
2. **Synthetic moon grant** must not retrigger our own hook — `ApState::synthetic_grant_this_frame` guard exists, plus belt-and-braces dedupe by `locations_checked` hash set.
3. **`Game.py` game-name guard**: bridge should compare `game_name` against `RoomInfo` at startup to catch seed mis-pairing. Not yet implemented.
4. **DemoPeachWedding hook fires for the wedding cutscene** which is the canonical SMO ending. If 1.0.0 names that demo differently (unlikely given OdysseyDecomp targets 1.0.0), the symbol won't resolve and we'd fall back to hooking a `setMainScenarioNo` call with the post-Bowser scenario value.

## What's definitely NOT done

- On-screen status overlay — deferred to M8 per user Q&A; M3 ships heartbeat-to-lm-log instead (web tracker is the canonical source of truth).
- HELLO `cap_table_hash` field is empty — populated in M4 once we hash the generated `capture_table.h`.
- **AP-credit HUD overlay (M8)**: M6 phase A hooks `getCurrentShineNum`/`getGotShineNum` to return AP-credit-only counts (not orig+credit). The natural HUD shows our AP count — visually weird: a locally collected moon does NOT bump the counter even though the shine appears in the shine list. A dedicated ImGui-style AP overlay (à la lunakit devgui) belongs in M8 to surface AP credit info in a clearer, separate UI element. Hooks lying about the natural counter is a stopgap.
- **`getGotShineNum` doesn't fire in normal gameplay**: M6 phase A playtest showed the per-kingdom counter hook never fires when Mario plays in Cascade. SMO's natural per-kingdom counter reads from a different code path. The hook is harmless (returns AP credit when called); if a future code path does call it the credit lands correctly. Kingdom-progression gating ended up handled by M7 Path A's substitution hooks rather than an explicit AP-driven unlock — the `unlockWorld` fallback was dropped 2026-05-18 along with the rest of the kingdom-unlock plumbing.
- **Cleaner M7 uncapture animation (M8 polish)**: M7 phase A uses `PlayerHackKeeper::forceKillHack` which despawns the captured enemy actor. The visual is jarring on big captures like T-Rex. Researched alternatives (`rs::endHack`, `PlayerHackKeeper::endHack`) carry non-trivial risk; see [docs/milestones.md#m7-phase-a--capture-lock](docs/milestones.md#m7-phase-a--capture-lock).
- **2D moons aren't recolored by item type yet.** `ShineAppearanceHook` inline-patches the 3D moon actor only; the 2D variant bypasses the patched offsets. Symmetric inline patches on the 2D shine init path would close the gap.

## What's next

**M7 phase B — goal detection playtest.** `EndingHook` is wired on `DemoPeachWedding::makeActorAlive` since M3 and is supposed to set `ApState::goal_sent` + ship an AP `StatusUpdate{ClientGoal}`. Validation requires actually defeating Bowser in Ryujinx; expected to be a small task if the symbol resolves and the demo fires once-per-defeat as designed.

**M6 phase C — snapshot enumerate** is the natural next big-ticket item if M7B turns out clean. The kingdom-unlock half of phase C was dropped 2026-05-18 (M7 Path A's substitution hooks cover gating without needing AP-driven `unlockWorld` writes). What remains: `enumerateOwnedShines` / `enumerateOwnedCaptures` to populate the M4.5 reconciliation stream the bridge already consumes — stubs are in `CaptureGate.cpp` / `MoonApply.cpp` and just need GameDataHolder traversal bodies.

**M6.6 — Cappy bubble (Channel B)**: items arriving outside the moon-get cutscene window need a UI surface. Three UI candidates to spike — see [docs/milestones.md#m66-deferred-next-milestone](docs/milestones.md#m66-deferred-next-milestone).

## Test commands worth knowing (Python)

```pwsh
# Client tests — 120 pass + 11 skip (live-AP / extraction gated)
cd C:\Users\maxwe\Documents\smo_archipelago
.\bridge\.venv\Scripts\python -m pytest apworld\smo_archipelago\tests\ -v
```

For switch-mod C++ host tests, use the `smo-host-tests` skill. For the cross-build, use the `smo-build` skill.
