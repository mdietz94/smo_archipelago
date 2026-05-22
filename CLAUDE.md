# CLAUDE.md — context for the next session

This file is a fast-load brief for picking up the **Spicy Meatball Overdrive** project cold. The same project goes by several identifiers in different layers — keep them straight:

| Identifier | Value | Scope |
|---|---|---|
| AP-protocol game name | `Spicy Meatball Overdrive` | Wire-format `game` field in YAML seeds and AP `Connect` packets |
| Shipped apworld zip | `meatballs.apworld` | What lands in `vendor/Archipelago/custom_worlds/`; Archipelago imports it as `worlds.meatballs` |
| host.yaml settings key | `meatballs_options` | Derived by Archipelago from the zip stem `meatballs` |
| Per-player file extension | `.meatballsap` | Generated alongside the standard AP zip; SuffixIdentifier in the Component routes it to SMOClient |
| In-repo source folder | `apworld/smo_archipelago/` | Kept verbose to avoid churning every dev-workflow path reference; only the deployed artifact uses `meatballs` |
| Switch mod CMake project | `smo_archipelago` | Unrelated to the apworld; lives in `switch-mod/CMakeLists.txt` |

The "meatballs" spelling and the historical "smo" spelling both parse as **S**picy **M**eatball **O**verdrive. Rename history: 2026-05-16 dropped a prior framework-derived `<framework>_SMO_archipelago` AP identifier (we ship a real client with in-game enforcement) and shortened the deployed zip to `smo.apworld`; 2026-05-20 renamed the zip stem / module path / options key / file extension from `smo` → `meatballs` because the upstream `worlds.smo` slot was already claimed by another apworld using the `.apsmo` namespace, and rotated the apworld `creator` from `archipelago` → `maxdietz` at the same time (the latter shifts every item/location ID, but the zip-stem rename already forces seeds to regen so we cashed in the breakage in a single hop). Read this file first, then `docs/architecture.md` and the plan file at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

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
- `apworld/smo_archipelago/data/locations.json` and `items.json` — the community-curated location and capture names (478 locations + 67 item entries — 42 Captures + 25 Moon items + 27 post-metro items — as of 2026-05-22). Forked from the public [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) upstream. Edits are fine; bulk additions from a romfs dump are not — alignment with Nintendo's MSBT should happen one mismatch at a time, not as a wholesale copy.
- Functional identifiers like `WaterfallWorldHomeStage`, `obj214`, `ScenarioName_<ObjId>`, `ShineList`, kingdom internal names (`CapWorld`/`SkyWorld`/etc.). These appear in every public SMO modding project (lunakit, MoonFlow, OdysseyDecomp) and are functional, not expressive.
- The one M5.7 anchor entry (`"Our First Power Moon"`) appears in CLAUDE.md, the test suite, and docs as a known ground-truth datapoint. One name as a verifiable test fixture is fine; a list of names is not.

**Safe pattern**: anything that requires a user to run `scripts/extract_shine_map.py` to produce stays in the gitignore. If you find yourself wanting to commit a piece of data so the next agent has a richer starting point, instead document where to regenerate it — see `docs/extract-moon-data.md` for the model.

**If you've staged something questionable**: `git restore --staged <path>` to unstage, then either delete the file or add it to `.gitignore` before retrying. Never override `.gitignore` with `git add -f` for SMO content. When in doubt, ask the user.

## What we're building

A real Archipelago client for **Super Mario Odyssey on a modded Switch (FW 21.2 or FW 22, native SMO 1.0.0 install, Atmosphere CFW)**. Builds on the data layout from [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) (an earlier honor-system, tick-the-boxes-by-hand world) with an in-game module that detects moons/captures/scenario events automatically, applies received items live, and enforces capture locks until the AP item arrives.

### Architecture (two tiers)

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Client (Python, inside apworld) ]  <--websocket-->  [ AP server ]
   LibHakkun subsdk9                     SMOContext(CommonContext)                              archipelago.gg
   OdysseyHeaders                        Kivy GUI (Tracker + Connections tabs)                  or self-host
   sail (symbol DB)                      SwitchServer asyncio TCP on :17777
   ImGui debug overlay (Karla TTF)       Forked apworld machinery
   HUD overlay (M3)
```

The PC client (formerly the standalone "bridge" process) lives inside the apworld at `apworld/smo_archipelago/client/` and ships in the .apworld zip. Archipelago's Launcher auto-discovers it via the `Component("SMO Client", ...)` registration in the apworld's `__init__.py`. One process, one Kivy window, one install artifact.

The client owns AP-protocol complexity (websocket + deflate + TLS + reconnect, all inherited from `CommonContext`). Switch speaks a small line-delimited JSON protocol on port **17777**. Full wire format: `docs/wire-protocol.md`.

## Decisions already made (and why)

| Decision | Why |
|---|---|
| **PC bridge, not direct Switch→AP** | websocket+deflate+TLS+reconnect on Switch is months of work; bridge solves it in ~hundred lines via `CommonContext` |
| **Archipelago as git submodule, not pip install or vendored copy** | Their `setup.py` blocks pip; copying ~15 transitive files would drift fast. Submodule under `vendor/Archipelago/` is drift-proof and also enables seed generation in the same checkout |
| **Forked apworld, not vendored unchanged** | M8 will add automation-only features (deathlink, traps, hint system, progressive moon gating) the upstream honor-system world can't enforce |
| **Web tracker priority, in-game ImGui later** | User preference. Web tracker (M5) ships before in-game tracker (M8) |
| **LibHakkun + OdysseyHeaders + sail** | Hakkun is the actively maintained subsdk runtime (musl + LLVM libc++ + `HeapSourceDynamic` re-exporting SMO's allocator), OdysseyHeaders ships full SMO 1.0.0 type layouts, sail is its symbol-DB resolver. 5 Windows-port patches remain in `scripts/patch_hakkun.py` (the other 5 trampoline/sail patches got upstreamed and were retired with the 2026-05-22 LibHakkun pin bump — see [docs/milestones.md#m9](docs/milestones.md)). |
| **Target SMO 1.0.0** | Canonical version every public mod (smo-online, smo-practice, OdysseyDecomp, the Hakkun example) targets. Native 1.0.0 install on FW 21.2 or FW 22 (both validated). |
| **Bit-index capture table generated from apworld** | `scripts/sync_capture_table.py` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so Switch and bridge can't drift on cap-name → bit-index assignment |
| **Game name `Spicy Meatball Overdrive`, zip `meatballs.apworld`** | See identifier table in the preamble for the full mapping. The zip stem drives Archipelago's module name (`worlds.meatballs`) and the host.yaml settings key (`meatballs_options`). The in-repo source folder stayed `apworld/smo_archipelago/` to avoid churning dev-workflow path references. Rename provenance lives in the preamble paragraph. |
| **Eager AP dial — no Switch-presence gate (since 2026-05-22)** | SMOClient still doesn't auto-dial on launch (default host stays unset to avoid "Connection refused on default `archipelago.gg`"), but Click-Connect dials AP immediately whether or not the Switch is up. Lets the user validate creds and watch items flow into the log before booting SMO. The earlier SNI-style gate that parked the dial until the Switch HELLO'd was ripped 2026-05-22 — the only thing it bought us was a single combined "ready" indicator, at the cost of not being able to test creds without booting SMO. Items received while the Switch is offline stay in `BridgeState.received_items` and replay to the Switch on its eventual HELLO (the post-HELLO replay path was already wired for the "Switch reconnect mid-session" case). Tests: `apworld/smo_archipelago/tests/test_connect_gate.py` |

## Status

Shipped as v0.1.x-alpha (see `git tag`). M0–M7 complete, real-Switch deploy validated end-to-end, PopTracker pack ships alongside the apworld zip on every tagged release. M8 polish: **on-Switch ImGui debug overlay shipped 2026-05-22** (`switch-mod/src/ui/ApDebugConsole.cpp`) — via upstream LibHakkun's `Nvn`/`ImGui`/`DebugRenderer` addons; renders the discovery report + last ~200 log lines when SMOClient is unreachable for >5s, hides on TCP-up. Cappy speech-bubble notifications still ship alongside (connect/disconnect/save-load status). Per-classification moon recolor via `Shine::init` post-trampoline, M7 demo-end retime. **Phase 4 + Phase 5 (Talkatoo% mode)** shipped 2026-05-21 — Ryujinx-verified end-to-end; substitutes Talkatoo's speech bubble with AP-pool moon names, blocks collection of non-named moons, exempts 22 audited scenario-advancing moons. Phase 5 closed the soft-lock window via a post-fill greedy validator in [talkatoo_order.py](apworld/smo_archipelago/talkatoo_order.py) that computes a sphere-safe per-kingdom moon order whose every 3-wide prefix contains ≥1 reachable moon, ships it in `slot_data["talkatoo_order"]`, and the bridge advances a per-kingdom cursor as checks land. Gap #1 (bridge-side filter of progression moons out of the pool) also shipped; Gap #2 (named-set persistence across save+quit) is an explicit non-goal. Full context in [docs/handoff-talkatoo.md](docs/handoff-talkatoo.md). Per-milestone narratives (provenance for every wire-protocol decision, failed-iteration history): [docs/milestones.md](docs/milestones.md). Original implementation plan: `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

Pattern invariants worth knowing even without reading the milestone narratives:

- **Subsdk pre-orig init ordering (load-bearing, 2026-05-22)**: any subsdk init that allocates from `al::getStationedHeap()` MUST happen pre-orig in `gameSystemInit`, before SMO's engine has fragmented the heap. The ImGui overlay's `ImGuiBackendNvn::tryInitialize()` was deferred-to-first-draw in seven earlier attempts and silently hung the first `drawMain.orig` — no log, no crash report. Fix is the FIRST statement in our `gameSystemInit` lambda: `smoap::ui::initDebugConsole();` (carves a 2 MiB ExpHeap + wires allocator + calls `tryInitialize`). Mirrors Kgamer77/SMOO-Plus-Hakkun's `imgui::setup()` placement. See memory `imgui-addon-pre-orig-setup` for the three ranked first-principles theories of WHY (heap fragmentation #1, addon state-machine ordering #2, ARMeilleure translation block #3) and the full list of fixes that DIDN'T work.
- **M6 phase D**: when sending the post-HELLO item replay, **skip Moon items** — `OutstandingMsg` carries authoritative per-kingdom balance, re-sending Moons double-counts. See [docs/milestones.md#m6-phase-d](docs/milestones.md#m6-phase-d).
- **M7 Path A**: future "lie to the game" hooks need the three-layer pattern (UI query → cinematic state → stage commit) — catch upstream of the visible state change, not just at commit. See [docs/milestones.md#m7-path-a--kingdom-order-gate](docs/milestones.md#m7-path-a--kingdom-order-gate).
- **Phase 4 (Talkatoo% block)**: SMO's Shine actor has FIVE entry points into `GameDataFunction::setGotShine` (`Shine::get`, `getDirect`, `getDirectWithDemo`, `receiveMsg`, `exeWaitRequestDemo`). Hooking any single one misses 4/5 collection paths. The universal chokepoint is `GameDataFile::setGotShine(ShineInfo*)` — already hooked since M4 as `MoonGetHook`. Anything that wants to gate moon collection lives in that one trampoline. See [docs/milestones.md#phase-4--talkatoo-mode](docs/milestones.md#phase-4--talkatoo-mode).
- **Wire-format fixed-buffer patterns** (`FlatHashSet`, `LineBuffer`, fixed `char[N]` fields) are vestigial M6.1 workarounds from the pre-Hakkun libstdc++ allocator NULL-deref. Hakkun's musl + libc++ + `HeapSourceDynamic` removed the constraint, but the shapes are committed contracts — don't rewrite them unless retiring the wire format. Backstory: [docs/milestones.md#m61](docs/milestones.md).

## Repository layout

```
C:\Users\maxwe\Documents\smo_archipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    Submodules (vendor/Archipelago, switch-mod/sys,
                                 switch-mod/lib/OdysseyHeaders, switch-mod/lib/imgui)
  .claude/skills/                Project skills (smo-build, smo-loopback-test, ...)
  apworld/smo_archipelago/       The apworld + Python client
    __init__.py                  World class + SMOSettings + "SMO Client" Component reg
    data/                        categories.json / items.json / locations.json
                                 / meta.json / regions.json (game-level config
                                 lives in Data.py, not a JSON file)
    hooks/                       Generation hook surfaces (Rules, Options, World, ...)
    Data.py, Game.py, ...        World boilerplate (item/location/region tables, etc.)
    _setup/                      One-download setup wizard (Kivy) — first-time toolchain +
                                 deploy + extract, surfaces in Archipelago Launcher.
                                 wizard.py is the Kivy front-end; wizard_cli.py is a
                                 headless JSON-event orchestrator the wizard delegates
                                 to. Split sub-modules: audit, build, deploy, installers,
                                 launcher_errors, net, prereqs, smoap_file.
    client/                      Python client (replaces the old standalone `bridge/`)
      __init__.py                Empty / lightweight; never pulls Kivy
      main.py                    Launcher entry point; `def launch(*args)` invoked via Component
      context.py                 SMOContext(CommonContext) + SMOClientCommandProcessor
      gui.py                     SmoManager(GameManager) — Kivy UI; imported lazily inside run_gui
      switch_server.py           asyncio TCP server on :17777; replay on HELLO
      discovery.py               UDP bridge-discovery responder (the other side of ApDiscovery)
      protocol.py, state.py      Wire-format dataclasses + thread-safe state mirror
      datapackage.py, maps.py    AP id↔name + classifier + ShineMap / CaptureMap
      scout_cache.py, display.py Channel A: LocationScouts pre-fetch + label formatting
      commands.py                Pure `parse_command` for the /-commands in context.py
      config.py, logging_setup.py  Legacy TOML overlay (kept for back-compat) + log config
      net_util.py                detect_lan_ip helper shared by client + wizard
      setup_state.py             Pure helpers that locate wizard-produced map files
                                 (kept in client/ so SMOClient never imports _setup/)
      data/                      shine_map.json + capture_map.json (gitignored; regenerated)
    tests/                       50 test files (`ls apworld/smo_archipelago/tests/test_*.py | wc -l`).
                                 Live-AP tests gated on SMOAP_LIVE_AP=1; extraction tests
                                 skip when shine/capture maps absent. Run via the command
                                 at the bottom of this file for current pass/skip numbers.
      pyproject.toml             Self-contained pytest config (importmode=importlib)
      conftest.py                Inserts apworld/smo_archipelago/ into sys.path
      seeds/                     Loopback test seeds (smo_loopback.yaml + gitignored out/)
  switch-mod/                    LibHakkun C++ module (subsdk9)
    CMakeLists.txt               Builds subsdk9 via the Hakkun + sail CMake includes
    config/{config.cmake,npdm.json,VersionList.sym}
                                 Module-binary slot (subsdk9), title id, NPDM
                                 capabilities, SMO 1.0.0 build-id pin
    sys/                         LibHakkun submodule (musl + LLVM libc++ + HeapSourceDynamic
                                 addon + sail; Windows-port patches applied by
                                 scripts/patch_hakkun.py at build time)
    lib/OdysseyHeaders/          OdysseyHeaders submodule — SMO 1.0.0 type layouts
                                 (al::, agl::, game::, nn::, sead::, ...)
    lib/imgui/                   Dear ImGui submodule pinned at v1.92.8 — backs the
                                 on-Switch ApDebugConsole via LibHakkun's ImGui addon.
    syms/                        sail symbol DB
      game/SmoApSymbols.sym      All mangled SMO function + vtable symbols we hook
                                 (~47 entries; `grep -c '^_Z' switch-mod/syms/game/SmoApSymbols.sym`).
      nn/nifm.sym                nn::nifm symbols (Initialize / SubmitNetworkRequestAndWait /
                                 IsNetworkAvailable / GetCurrentPrimaryIpAddress) resolved
                                 against SMO's dynsym.
      nn/socket.sym              nn::socket symbols (Initialize, GetLastErrno) — uses SMO's
                                 socket session via a no-op trampoline; see commit 89632a7.
      nvn.sym                    NVN bootstrap symbol (`nvnBootstrapLoader`) for the ImGui
                                 NVN backend; tagged `@sdk = nnSdk` so sail resolves it
                                 against nnSdk instead of RedStar.nss.
    src/
      main.cpp                   hkMain entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol,ApDiscovery}.{cpp,hpp}
                                 ApClient owns a parallel hk::socket::Socket client
                                 against bsd:u (separate from SMO's nn::socket); ApDiscovery
                                 runs the UDP probe chain — loopback (Ryujinx, 250ms) then
                                 unicast sweep across BRIDGE_HOST's /24 (real-Switch, 1s).
                                 The broadcast step was retired 2026-05-22 (travel routers
                                 + mesh extenders + IGMP-snooping switches silently drop it).
      ap/capture_table.h         AUTO-GENERATED (42 cap names) — run sync_capture_table.py
      ap/shine_table.h           AUTO-GENERATED (436 moons) — run sync_shine_table.py
      ap/shine_lookup.hpp        Linear-scan helpers over shine_table.h (Phase 4)
      hooks/HookSymbols.hpp      C++ string constants mirroring syms/*.sym; used by
                                 HkTrampoline<>::installAtSym<> and hk::ro::lookupSymbol.
                                 Must stay in sync with the .sym files.
      hooks/*.cpp                One file per hook target. Covers moon get/label, capture
                                 start/lock, scenario flag, save load, world-map select,
                                 addPayShine debit, addHackDictionary gating, Cappy message
                                 routing, shine appearance, death-link, credits-roll goal,
                                 Talkatoo% speech substitution.
      game/{MoonApply,CaptureGate,KingdomUnlock,KingdomOrderGate}.{cpp,hpp}
                                 KingdomUnlock retains the kingdom name ↔ bit ↔ worldId
                                 tables M6-D + M7-A depend on, despite its now-legacy name.
      ui/ApHudOverlay.{cpp,hpp}  Heartbeat-mode HUD (kept for debug logging surface).
      ui/ApDebugConsole.{cpp,hpp}  On-Switch ImGui debug overlay. Init MUST be the FIRST
                                 statement in `gameSystemInit` (pre-orig) — see the
                                 pre-orig invariant in the Status section above.
      ui/EmbeddedFontKarla.hpp   Karla-Regular.ttf (OFL 1.1, ~17 KB) as a byte-array
                                 header — atlas swap replaces ProggyClean for crisp text.
      ui/CappyMessenger.{cpp,hpp}  In-game speech-bubble notifications via SMO's CappyMessenger
                                 (used by M6-C reconciliation, M7-A lock messaging, etc.).
                                 Settle gate now requires BOTH a frame-counter threshold
                                 AND a wallclock interval (post-Hakkun Ryujinx JIT timing
                                 bug — see M9 in docs/milestones.md).
      util/{Json,Log}.{cpp,hpp}
    tests/                       Host-runnable C++ tests (test_json, test_protocol,
                                 test_cappy_messenger, test_shine_lookup). Same SMOAP_HOST_TEST
                                 guards as before. Run via smo-host-tests skill.
    romfs/ap_config.json         INFORMATIONAL ONLY — bridge IP/port are baked in at
                                 compile time via CMake -DBRIDGE_HOST/-DBRIDGE_PORT.
                                 Runtime UDP discovery (ApDiscovery) overrides this at
                                 connect time when SMOClient is reachable on the LAN.
  scripts/
    switch_smoke_test.py         Fake-Switch end-to-end test
    sync_capture_table.py        items.json → capture_table.h
    extract_shine_map.py         M5.8: NSP → romfs → shine_map.json + capture_map.json
    install_apworld.py           Zips apworld/smo_archipelago/ → vendor/.../custom_worlds/
    ap_generate.py, ap_server.py Archipelago Generate/MultiServer wrappers (auto-pip suppressed)
    build_poptracker_pack.py     PopTracker pack generator
    build_switchmod.py           One-shot Switch-mod build wrapper (LLVM 19 + sail +
                                 LibHakkun Windows-port patches; see smo-build skill)
    patch_hakkun.py              Applies the 5 remaining Windows-port patches to the
                                 pinned LibHakkun submodule (idempotent; 5 trampoline
                                 + sail patches were upstreamed and retired 2026-05-22)
    setup_imgui_addons.py        Copies LibHakkun's Nvn/ImGui/DebugRenderer addon sources
                                 into the build tree alongside Dear ImGui (run from
                                 build_switchmod.py before configure)
    setup_sail_winpath.py        One-time sail host-binary compile via msys2 mingw64
    fix_hakkun_symlinks.py       Stub for converting OdysseyHeaders symlinks (no-op
                                 in current layout; ready for future use)
    sync_shine_table.py          Generates switch-mod/src/ap/shine_table.h from
                                 apworld locations × shine_map.json
    .extract-venv/               Auto-created Python 3.12 venv (gitignored)
  docs/
    architecture.md              Two-tier diagram, threading, responsibilities
    wire-protocol.md             Wire-format reference
    build-windows.md             Toolchain install
    extract-moon-data.md         How to generate shine_map.json + capture_map.json
    install-switch.md            SD card layout, troubleshooting
    first-time-setup.md          End-user setup walkthrough (paired with the wizard)
    release-process.md           Tag → CI release workflow notes
    changing-servers.md          End-user server-switch flow
    milestones.md                Deep per-milestone narrative — provenance for every
                                 decision that lives load-bearing in current code.
    TALKATOO.md                  Talkatoo% mode design notes.
    handoff-talkatoo.md          Talkatoo% Phase 4/5 handoff notes — Gap #1 + Gap #3
                                 shipped 2026-05-22; Gap #2 reframed as explicit non-goal.
  .github/workflows/             release.yml (tag-triggered), test.yml (CI), dependabot.yaml
  vendor/                        For submodules (Archipelago goes here)
  third_party/                   Local clones — gitignored (may be empty in fresh checkouts)
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
| `<SMO 1.0.0 NSP>` | User-supplied game dump (copyrighted — never commit, path not stored in repo). `main.nso` is not retained locally; re-extract via `python scripts\extract_shine_map.py --nsp <SMO 1.0.0 NSP>` when needed (see `.claude/skills/smo-symbol-discovery/SKILL.md`). |
| `C:\Users\maxwe\AppData\Roaming\Ryujinx\` | Ryujinx install + mods + logs |
| `C:\Users\maxwe\Documents\ryujinx-1.3.3\` | Ryujinx executable |
| `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` | The authoritative plan (FW 21.2 + 1.0.0 simplification) |
| `C:\Users\maxwe\.claude\projects\C--Users-maxwe-Documents-smo-archipelago\memory\` | Auto-memory directory |

## Skills

Project skills live in `.claude/skills/`. They auto-load when triggered by their description keywords:

- **smo-build** — build switch-mod via scripts/build_switchmod.py (LLVM 19 + sail + LibHakkun patches), deploy to Ryujinx/Switch, capture_table sync, worktree gotchas, fresh-worktree setup, the SMO-already-inits-socket rule.
- **smo-loopback-test** — AP loopback E2E without booting SMO (3-pane setup + scripted pytest path).
- **smo-host-tests** — 4 C++ host tests (test_json, test_protocol, test_cappy_messenger, test_shine_lookup) via msys2 mingw64 g++ + the ApState::nowMs stub pattern.
- **smo-symbol-discovery** — add new hook targets; OdysseyDecomp forward-decls + aarch64 mangling + sail .sym + llvm-nm `fakesymbols.so` verification.
- **smo-extract-data** — regenerate `shine_map.json` + `capture_map.json` from a 1.0.0 NSP.
- **smo-poptracker** — build / iterate / debug the PopTracker pack.

For anything not covered by a skill, [docs/milestones.md](docs/milestones.md) is the deep-dive: it captures pattern decisions (Channel-A scout pre-warm, the three-layer hook pattern from M7 Path A, the worker-thread allocator hardening from M6.1) that successor work tends to need.

## Known unknowns / risks for new work

1. **`PlayerHackKeeper::startHack` may not be a single chokepoint** — capture entry can split across multiple functions per cap-type. Secondary read-only check on `CapTargetInfo::isCaptureTarget` from the frame pump if the trampoline misses cases.
2. **Synthetic moon grant** must not retrigger our own hook — `ApState::synthetic_grant_this_frame` guard exists, plus belt-and-braces dedupe by `locations_checked` hash set.
3. **Goal-detection wiring (load-bearing, easy to break by accident).** Vanilla SMO awards NO Power Moon for clearing the main game — Mario is simply deposited in Mushroom Kingdom after the wedding cutscene, with nothing to collect. Four earlier attempts got this wrong: (a) `DemoPeachWedding::makeActorAlive` fires in Bowser's Kingdom too (the actor is a generic "Peach in wedding dress" per OdysseyDecomp); (b) hanging the bridge-side trigger on the "Defeat Bowser and Escape the Moon" location actually fires on the *Darker Side* completion moon, not the main ending; (c) "first Mushroom Kingdom arrival" via `WorldMapSelectHook::markVisitedFromStage` AND (d) "current kingdom resolves to Mushroom" via `ShineNumGetHook` both false-positive on the hidden Luncheon portrait warp (a painting in CookingWorld teleports Mario to PeachWorld pre-game-clear). The shipped fix is `CreditsStartHook` ([switch-mod/src/hooks/CreditsStartHook.cpp](switch-mod/src/hooks/CreditsStartHook.cpp)): a `HOOK_DEFINE_INLINE` patch at offset `0x4C54A4` (BL inside `StaffRollScene::init`, the credits-only scene class — verified by Kgamer77/SuperMarioOdysseyArchipelago, MIT) calls `reportGoal()` gated by `ApState::goal_sent`. The credits scene only initializes when the post-wedding cutscene plays — never on portrait warp, Darker Side, or save load. The apworld's `victory: true` location is "Arrive in the Mushroom Kingdom" (naming retained for back-compat; the trigger is now credits-roll). Don't re-introduce a Mushroom-arrival, moon-check, or `DemoPeachWedding` trigger here.

## Partial / deferred work for a future iteration

- **HELLO `cap_table_hash` field** is empty — would close the Switch↔apworld cap-table drift detection loop. Hash the generated `capture_table.h` and compare on connect.
- **`getGotShineNum` semantics quirk.** Per OdysseyDecomp the int param is `file_id` (save slot, default -1), not a world id — the function returns global lifetime collected from that slot, and SMO's per-kingdom HUD uses a different (inlined field-access) path. Our hook returns `sumAllKingdomCredits()` so AP credit lands correctly in save-slot summary contexts. Kingdom-progression gating ended up handled by M7 Path A's substitution hooks rather than an explicit AP-driven unlock; `unlockWorld`/`ItemKind::Kingdom` were dropped 2026-05-18.
- **Dedicated AP-credit overlay.** M6 phase A's `getCurrentShineNum`/`getGotShineNum` hooks return AP-credit-only counts so the natural HUD shows AP credit — visually weird because a locally collected moon doesn't bump the counter even though the shine appears in the shine list. Cappy speech bubbles smoothed most of this, but a dedicated ImGui-style AP overlay (à la lunakit devgui) would be cleaner.
- **Talkatoo% Phase 4 + Phase 5 (shipped 2026-05-21).** Phase 4 (named-set + collection block + Multi Moon exemption) and Phase 5 (sphere-safe ordering) both shipped — Ryujinx-verified, see [Phase 4 in docs/milestones.md](docs/milestones.md#phase-4--talkatoo-mode) and [docs/handoff-talkatoo.md](docs/handoff-talkatoo.md). Gap #1 (bridge-side filter of `progression: true` moons out of `talkatoo_pool` so Talkatoo never "spends" a hint slot on a Multi Moon) shipped in [client/context.py](apworld/smo_archipelago/client/context.py) `_derive_and_push_talkatoo_pool`, backed by a `DataPackage.is_progression_location` query. Gap #3 (sphere-safe ordering — apworld-side validator producing a per-kingdom permutation guaranteeing ≥1 of any 3-window is reachable from prior items, so fresh-start seeds can't soft-lock on Capture/Cap-gated AP-pool moons) shipped as [talkatoo_order.py](apworld/smo_archipelago/talkatoo_order.py)'s greedy random-tiebreak validator, wired into `after_fill_slot_data` in [hooks/World.py](apworld/smo_archipelago/hooks/World.py), consumed by the bridge via a per-kingdom cursor in [client/context.py](apworld/smo_archipelago/client/context.py). Notable deviation from the original sketch: the validator runs with state = precollected + every advancement item from this slot's non-pool locations (representing "player has just entered this kingdom with the items they earned to get here"); precollected-only failed loud on Bowser's 34 AP-pool moons. The Multi Moon exemption shipped as the `progression: true` schema in locations.json → `bool progression` column in shine_table.h → `isProgressionShine(stage, obj)` short-circuit in [MoonGetHook.cpp](switch-mod/src/hooks/MoonGetHook.cpp). The audited list is 22 moons (Mario Wiki Multi_Moon authoritative + Cascade story opener + Seaside seal chain + Bowser's 4-step chain); guarded by [tests/test_progression_moons.py](apworld/smo_archipelago/tests/test_progression_moons.py). Gap #2 (named-set persistence across save+quit) is an explicit non-goal, not a deferred TODO — having to re-talk to Talkatoo after save+quit is the intended UX (it's a small "did I really name this?" check that the player wanted to keep). Don't implement persistence here unless the design decision changes.

## Test commands worth knowing (Python)

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
.\.venv\Scripts\python -m pytest apworld\smo_archipelago\tests\ -v
```

The repo-root `.venv` lives in the main checkout (not in worktrees) — Archipelago's deps are a superset of what SMOClient needs. For switch-mod C++ host tests, use the `smo-host-tests` skill. For the cross-build, use the `smo-build` skill.
