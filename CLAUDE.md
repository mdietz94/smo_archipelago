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
- `apworld/smo_archipelago/data/locations.json` and `items.json` — the community-curated location and capture names (~479 locations + 42 captures after the shop / outfit / trap / Bowser-softlock purges; `grep -c '"name"'` for the current count). Forked from the public [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) upstream. Edits are fine; bulk additions from a romfs dump are not — alignment with Nintendo's MSBT should happen one mismatch at a time, not as a wholesale copy.
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
   ImGui overlay (M8)                    Forked apworld machinery
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
| **LibHakkun + OdysseyHeaders + sail (since 2026-05-21 cutover)** | Hakkun is the actively maintained subsdk runtime (musl + LLVM libc++ + HeapSourceDynamic re-exporting SMO's allocator), OdysseyHeaders ships full SMO 1.0.0 type layouts, sail is its symbol-DB resolver. Replaced the exlaunch + lunakit-vendor toolchain we used through M0–M7. Pre-cutover details and the 5 real bugs the migration surfaced live in [docs/milestones.md#m9](docs/milestones.md). 10 Windows-port patches to upstream LibHakkun ride in `scripts/patch_hakkun.py` (upstream-PR-ready) |
| **Target SMO 1.0.0** | Canonical version every public mod (smo-online, smo-practice, OdysseyDecomp, the Hakkun example) targets. User has a native 1.0.0 install on FW 21.2 or FW 22 (both validated; the 2026-05-20 Hakkun real-Switch spike falsified the prior "FW 22 unsupported" claim) |
| **Bit-index capture table generated from apworld** | `scripts/sync_capture_table.py` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so Switch and bridge can't drift on cap-name → bit-index assignment |
| **Game name `Spicy Meatball Overdrive`, zip `meatballs.apworld`** | AP-protocol name set 2026-05-16 (dropped a prior framework-derived prefix; we ship a real client with in-game enforcement). Deployed zip renamed `smo_archipelago.apworld` → `smo.apworld` (2026-05-16) → `meatballs.apworld` (2026-05-20). The 2026-05-20 hop moved us off the `worlds.smo` slot that an existing upstream apworld already owns under the `.apsmo` namespace. Archipelago derives the module name from the zip stem, so the world imports as `worlds.meatballs` and the host.yaml settings key is `meatballs_options`. The per-player file extension is `.meatballsap` (was `.smoap`). The in-repo source folder stayed `apworld/smo_archipelago/` to avoid churning every dev-workflow path reference; see the identifier table in the preamble |
| **Two-stage connect gate (SNI-style)** | SMOClient never auto-dials AP on launch. Clicking Connect (or `/connect` / `--connect`) parks the request until the Switch HELLOs; `SMOContext.connect()` overrides `CommonContext.connect` to dial AP from the Switch-ready callback. State tracked as `disconnected → waiting_for_switch → connected`. Mirrors SNIClient (user-cited gold standard); pre-fix, the default `archipelago.gg` host produced "Connection refused" the moment the user opened the Launcher button. Any new AP-dial path (auto-reconnect, scripted launch) must route through `SMOContext.connect()` — never `asyncio.create_task(server_loop(ctx))` directly. `disconnect()` clears the pending state so a stale dial doesn't fire on the next Switch reconnect. Tests: `apworld/smo_archipelago/tests/test_connect_gate.py` |

## Status

Shipped as v0.1.x-alpha (see `git tag`). All planned milestones (M0 through M7) are complete and a real-Switch deploy has been validated end-to-end. The PopTracker pack ships alongside the apworld zip on every tagged release. M8 polish is partial — Cappy speech-bubble notifications shipped in place of an ImGui overlay, per-classification moon recolor now covers all three shine variants (3D / 2D ShineDot / ShineGrand) via a `Shine::init` post-trampoline that writes the tint directly into the body material, and the M7 deny path was retimed 2026-05-20 to gate on `PlayerHackKeeper::isActiveHackStartDemo` (releases the moment the dive-in cinematic ends, no fixed delay) with `tryEscapeHack` for inanimate captures (no actor despawn). **Phase 4 (Talkatoo% mode)** shipped 2026-05-21 — Ryujinx-verified end-to-end; substitutes Talkatoo's speech bubble with AP-pool moon names, blocks collection of non-named moons (Mario sees a "Blocked by Talkatoo!" cutscene label and the moon respawns on save-reload), and exempts 22 audited scenario-advancing moons (Multi Moons + explicit prereqs) so kingdom progression isn't gated by Talkatoo's pick. Two follow-up gaps tracked in [docs/handoff-talkatoo.md](docs/handoff-talkatoo.md). Deep per-milestone narratives — including the exact provenance of every wire-protocol decision and the failed-iteration history — live in [docs/milestones.md](docs/milestones.md). The original implementation plan at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` is retained for historical reference.

Pattern invariants worth knowing even without reading the milestone narratives:

- **M6.1 — libstdc++ allocator NULL-derefs in subsdk9** (**retired post-Hakkun cutover 2026-05-21** — kept for historical context). Under the exlaunch + lunakit-vendor build, any worker-thread allocation past SSO (~15 chars) NULL-derefed inside `nn::os::GetTlsValue` because libstdc++'s allocator reached for a `nn::os::TlsSlot` exlaunch never `AllocateTlsSlot`'d. Hakkun's musl + LLVM libc++ + `HeapSourceDynamic` addon (which re-exports `operator new` / `malloc` / `free` from SMO's own thread-safe allocator) lifts the restriction entirely. `std::string` / `std::set` / `std::vector` / `std::mutex` are safe on any thread under the current build. The fixed-buffer / `FlatHashSet` / `LineBuffer` / `snprintf-to-stack-char[]` patterns from M6.1 are vestigial workarounds — they still ship because the wire-format shapes are committed contracts and rewriting the hooks isn't load-bearing for parity. Phase 7 polish PR may retire them.
- **M6 phase D**: when sending the post-HELLO item replay, **skip Moon items** — `OutstandingMsg` carries authoritative per-kingdom balance, re-sending Moons double-counts. See [docs/milestones.md#m6-phase-d](docs/milestones.md#m6-phase-d).
- **M7 Path A**: future "lie to the game" hooks need the three-layer pattern (UI query → cinematic state → stage commit) — catch upstream of the visible state change, not just at commit. See [docs/milestones.md#m7-path-a--kingdom-order-gate](docs/milestones.md#m7-path-a--kingdom-order-gate).
- **Phase 4 (Talkatoo% block)**: SMO's Shine actor has FIVE entry points into `GameDataFunction::setGotShine` (`Shine::get`, `getDirect`, `getDirectWithDemo`, `receiveMsg`, `exeWaitRequestDemo`). Hooking any single one misses 4/5 collection paths. The universal chokepoint is `GameDataFile::setGotShine(ShineInfo*)` — already hooked since M4 as `MoonGetHook`. Anything that wants to gate moon collection lives in that one trampoline. See [docs/milestones.md#phase-4--talkatoo-mode](docs/milestones.md#phase-4--talkatoo-mode).

## Repository layout

```
C:\Users\maxwe\Documents\smo_archipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    Submodules (vendor/Archipelago, switch-mod/sys, switch-mod/lib/OdysseyHeaders)
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
    tests/                       30 test files; ~150 passing with a handful skipped (live-AP
                                 gated on SMOAP_LIVE_AP=1 + extraction tests need shine/capture
                                 maps present). Run them via the command at the bottom of this
                                 file for current pass/skip numbers.
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
    syms/                        sail symbol DB
      game/SmoApSymbols.sym      All mangled SMO function + vtable symbols we hook
                                 (~50 entries — `grep -c '^_Z' for the current count)
      nn/nifm.sym                nn::nifm::{Initialize,SubmitNetworkRequestAndWait,
                                 IsNetworkAvailable} resolved against SMO's dynsym
    src/
      main.cpp                   hkMain entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol,ApDiscovery}.{cpp,hpp}
                                 ApClient owns a parallel hk::socket::Socket client
                                 against bsd:u (separate from SMO's nn::socket); ApDiscovery
                                 runs the UDP probe chain (loopback / broadcast / unicast
                                 fallback) before TCP connect.
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
    patch_hakkun.py              Applies the 10 Windows-port patches to the pinned
                                 LibHakkun submodule (idempotent)
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
<!-- 2026-05-20: M7 uncapture polish resolved — demo-end gate + tryEscapeHack
     split for the 7 inanimate caps eliminated the fixed-delay timer tables
     and the actor-despawn pop on those caps. See docs/milestones.md M7
     phase A 2026-05-20 update. forceKillHack is still used for caps with
     active intro state machines (T-Rex et al) since that's the failure
     mode endHack/tryEscapeHack can't safely handle. -->

- **`getGotShineNum` semantics quirk.** Per OdysseyDecomp the int param is `file_id` (save slot, default -1), not a world id — the function returns global lifetime collected from that slot, and SMO's per-kingdom HUD uses a different (inlined field-access) path. Our hook returns `sumAllKingdomCredits()` so AP credit lands correctly in save-slot summary contexts. Kingdom-progression gating ended up handled by M7 Path A's substitution hooks rather than an explicit AP-driven unlock; `unlockWorld`/`ItemKind::Kingdom` were dropped 2026-05-18.
- **Dedicated AP-credit overlay.** M6 phase A's `getCurrentShineNum`/`getGotShineNum` hooks return AP-credit-only counts so the natural HUD shows AP credit — visually weird because a locally collected moon doesn't bump the counter even though the shine appears in the shine list. Cappy speech bubbles smoothed most of this, but a dedicated ImGui-style AP overlay (à la lunakit devgui) would be cleaner.
- **Talkatoo% Phase 4 follow-ups.** Phase 4 (named-set + collection block + Multi Moon exemption) shipped 2026-05-21 — Ryujinx-verified, see [Phase 4 in docs/milestones.md](docs/milestones.md#phase-4--talkatoo-mode). Two known gaps remain, both spelled out in [docs/handoff-talkatoo.md](docs/handoff-talkatoo.md): **(#1)** bridge-side filter of `progression: true` moons out of `talkatoo_pool` — Talkatoo currently *can* "spend" a hint slot on a Multi Moon (wasted but not broken; `isProgressionShine` lets the player collect it anyway). ~30 min in [client/context.py](apworld/smo_archipelago/client/context.py). **(#3)** Phase 5 sphere-safe ordering — apworld-side validator that produces a per-kingdom permutation guaranteeing ≥1 of any 3-window is reachable from prior items. Without it, fresh-start seeds risk soft-lock on Capture/Cap-gated AP-pool moons even with the Multi Moon exemption. ~1–2 days. The Multi Moon exemption itself (decision 2026-05-20 to always allow + filter from pool) shipped as the `progression: true` schema in locations.json → `bool progression` column in shine_table.h → `isProgressionShine(stage, obj)` short-circuit in [MoonGetHook.cpp](switch-mod/src/hooks/MoonGetHook.cpp). The audited list is 22 moons (Mario Wiki Multi_Moon authoritative + Cascade story opener + Seaside seal chain + Bowser's 4-step chain); guarded by [tests/test_progression_moons.py](apworld/smo_archipelago/tests/test_progression_moons.py).

## Test commands worth knowing (Python)

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
.\bridge\.venv\Scripts\python -m pytest apworld\smo_archipelago\tests\ -v
```

The pre-merge `bridge/.venv` lives in the main checkout (not in worktrees) — Archipelago's deps are a superset of what SMOClient needs. For switch-mod C++ host tests, use the `smo-host-tests` skill. For the cross-build, use the `smo-build` skill.
