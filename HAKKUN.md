# HAKKUN.md — exlaunch → Hakkun migration runbook

This is the operational runbook for migrating `switch-mod/` off exlaunch + lunakit-vendor and onto LibHakkun + OdysseyHeaders + sail. It is the successor to the de-risking spike plan at `C:/Users/maxwe/.claude/plans/hakkun-is-the-successor-drifting-wall.md` (which proved the migration is tractable) and the higher-level migration plan at `C:/Users/maxwe/.claude/plans/hakkun-cutover-the-old-tenant-packs-out.md` (which laid out the phasing and decisions).

This doc is **execution-oriented**: each phase has concrete commands, exact file paths, and a deliverable that gates the next phase.

**Estimated total: ~6.5 days.** Was 7 days under the original phase 3 (1 day) + phase 4 (2 days) split; the consolidated phase 3a (0.5 day) + phase 3b (3 days) better reflects the ap/ ↔ game/ ↔ hooks/ ↔ ui/ entanglement (see "Risk callout" in Locked-in decisions) without adding net time.

## Locked-in decisions (2026-05-20)

1. **Worktree-isolated.** Migration lives on branch `claude/hakkun-cutover` in worktree `.claude/worktrees/hakkun-migration/`. `main` stays shippable; the worktree merges in one PR when phase 6 passes.
2. **Stay on subsdk9 at cutover.** During phases 1–5 the new build emits **subsdk8** (so it can coexist with the production subsdk9 mod in Ryujinx + on SD); phase 6 flips `MODULE_BINARY` from `subsdk8` to `subsdk9` as part of the rename + replace.
3. **LibHakkun Windows-port patches: upstream-first, fork fallback.** Submodule pins upstream `github.com/fruityloops1/LibHakkun`. Patches are applied locally via `scripts/patch_hakkun.py` while upstream PRs are in flight. If a PR review stalls > 1 week, fork to `github.com/mdietz94/LibHakkun-smo` and re-pin.
4. **SMO 1.0.0 only at cutover.** Sail supports `@smo:100,101,110,120,130` in one binary, but offsets for 1.0.1+ are unresearched. Multi-version is a follow-up PR.
5. **Functional parity is the cutover gate.** Phase 5 must show, in order: (a) the loopback test passes against the new subsdk; (b) Ryujinx manual play — connect to AP server, collect ≥ 5 moons, receive ≥ 1 AP item, observe correct application, capture-lock denies on a non-received cap; (c) real-Switch FW22 — same manual-play sequence. Strict log-byte-equivalence is NOT required. (Production switch-mod's bridge connection has been confirmed working on real Switch FW22 with the current exlaunch build, so the new subsdk's behavior under (c) is the meaningful gate.)

**Risk callout (added 2026-05-20):** `switch-mod/src/ap/ApState.cpp` includes `game/{CaptureGate,KingdomUnlock,MoonApply}.hpp`, `hooks/DeathHook.hpp`, `ui/CappyMessenger.hpp`. `ApState::applyOnFrame()` drives MoonApply::grantMoon, CaptureGate::grantCapture, CappyMessenger::tryPump, DeathHook::synthFire on every inbound item. The ap/, game/, hooks/, ui/ source trees form one connected component of the include graph — there is no buildable intermediate state between "skeleton subsdk8" (phase 1/2) and "full runtime ported" (phase 3b done). The phase 3a + 3b split below reflects this: phase 3a ports only the layers that don't depend on the runtime (wire format, config, util) and lands as dead code; phase 3b lands the entire live runtime as one batch. Rollback granularity is "everything in phase 3b" — there is no partial-3b checkpoint.

## Spike artifacts you'll reuse

These already exist (gitignored under `third_party/`) and are the templates for phase 0–2:

- [third_party/hakkun-spike/](third_party/hakkun-spike/) — pinned LibHakkun snapshot we built against.
- [third_party/hakkun-example/](third_party/hakkun-example/) — full working SMO 1.0.0 Hakkun mod (subsdk4 = moonjump + HUD).
- [third_party/hakkun-example/build_winpath.py](third_party/hakkun-example/build_winpath.py) — Windows PATH-fixing CMake wrapper.
- [third_party/hakkun-example/setup_sail_winpath.py](third_party/hakkun-example/setup_sail_winpath.py) — sail host-compile wrapper.
- [third_party/hakkun-example/fix_symlinks.py](third_party/hakkun-example/fix_symlinks.py) — Git-on-Windows symlink-to-junction converter for OdysseyHeaders.
- [third_party/hakkun-example/syms/game/SmoApSymbols.sym](third_party/hakkun-example/syms/game/SmoApSymbols.sym) — all 37 mangled symbols from `HookSymbols.hpp` (verified by llvm-nm in Gate 6).
- [third_party/hakkun-example/config/npdm.json](third_party/hakkun-example/config/npdm.json) — NPDM template that works for SMO + bsd:u.
- [third_party/hakkun-example/config/VersionList.sym](third_party/hakkun-example/config/VersionList.sym) — SMO 1.0.0 build ID = `3ca12dfaaf9c82da064d1698df79cda1`.

The 10 Windows-port patches discovered during the spike (re-applied via `scripts/patch_hakkun.py` in phase 0):

1. `winget install LLVM.LLVM --version 19.1.7` (must be on PATH or invoked via wrapper).
2. `pip install --user pyelftools mmh3 lz4` (README typo says "mmh"; actual import is `mmh3`).
3. `git submodule update --init --recursive` is mandatory after submodule add.
4. In `lib/OdysseyHeaders/`, ten `include/` symlinks land as text-files on Git-for-Windows; convert each to a directory junction.
5. In `sys/sail/CMakeLists.txt`, delete the `set(CMAKE_C_COMPILER clang)` / `set(CMAKE_CXX_COMPILER clang++)` lines — they fight CMake's compiler detection. Use mingw64 g++ as host compiler instead.
6. In `sys/sail/src/main.cpp:36`, `entry.path().c_str()` returns `wchar_t*` on MSVC/MinGW. Use `entry.path().string().c_str()`.
7. In `sys/sail/src/fakelib.cpp:13`, quote `clangBinary` in the `popen` cmdline (Windows path may contain spaces, `cmd.exe` splits on unquoted spaces).
8. In `sys/cmake/sail.cmake:42`, the literal `sys/addons/*/syms` is passed verbatim to sail; expand the glob with `file(GLOB ADDONS_SYM_DIRS ...)` first.
9. In `sys/cmake/generate_exefs.cmake:16,21`, prefix `python ${PROJECT_SOURCE_DIR}/sys/tools/elf2nso.py` (Windows doesn't always have `.py` as executable).
10. Copy `sys/sail/build/sail.exe` → `sys/sail/build/sail` (no-extension); `sail.cmake` checks for the no-extension path before invoking `setup_sail.py`.

Patches 5–10 are local source edits; `scripts/patch_hakkun.py` applies them. Patches 1–4 are environment setup; the build wrapper handles them.

## Phase 0 — Worktree + submodules + Windows-port wrappers  *(0.5 day)*

**Goal:** Migration worktree is ready, LibHakkun + OdysseyHeaders are submoduled, Windows-port patches script exists, build wrappers exist.

### Commands

```pwsh
# 0.1 Create the migration worktree off main.
cd C:\Users\maxwe\Documents\smo_archipelago
git worktree add .claude\worktrees\hakkun-migration -b claude/hakkun-cutover main

# 0.2 Add submodules. Pin commit hashes the spike validated against.
cd .claude\worktrees\hakkun-migration
git submodule add https://github.com/fruityloops1/LibHakkun.git switch-mod\hakkun
git submodule add https://github.com/MonsterDruide1/OdysseyHeaders.git switch-mod\odyssey-headers
git submodule update --init --recursive switch-mod\hakkun switch-mod\odyssey-headers

# 0.3 Pin the OdysseyHeaders nested submodule for NintendoSDK (the example used it).
# OdysseyHeaders has a NintendoSDK submodule that needs init'ing.
git submodule update --init --recursive switch-mod\odyssey-headers

# 0.4 Apply Windows-port patches.
python scripts\patch_hakkun.py

# 0.5 Validate: build a Hello-World subsdk that does nothing.
python scripts\build_switchmod_hk.py
# Expected: switch-mod-hk\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk8 produced.
```

### Files to add in phase 0

| Path | Source | Purpose |
|---|---|---|
| `scripts/patch_hakkun.py` | New | Re-applies patches 5–10 to `switch-mod/hakkun/` after submodule init. Idempotent. |
| `scripts/build_switchmod_hk.py` | Port of [third_party/hakkun-example/build_winpath.py](third_party/hakkun-example/build_winpath.py) | One-call build: ensures LLVM + Ninja + CMake on PATH, runs cmake config + build, post-processes outputs. |
| `scripts/setup_sail_winpath.py` | Port of [third_party/hakkun-example/setup_sail_winpath.py](third_party/hakkun-example/setup_sail_winpath.py) | One-time sail host-binary compile (mingw64 g++). |
| `scripts/fix_hakkun_symlinks.py` | Port of [third_party/hakkun-example/fix_symlinks.py](third_party/hakkun-example/fix_symlinks.py) | Convert OdysseyHeaders text-symlinks to directory junctions. |

### Done when

- `git submodule status` shows `switch-mod/hakkun` and `switch-mod/odyssey-headers` pinned.
- `python scripts/patch_hakkun.py` reports all 6 patches applied or already applied.
- The 3 wrapper scripts are in `scripts/`.

## Phase 1 — Skeleton build  *(0.5 day)*

**Goal:** A `switch-mod-hk/` tree builds a no-op subsdk8 .nso that loads in Ryujinx without crashing SMO. Proves the toolchain end-to-end.

### Commands

```pwsh
# 1.1 Initialize switch-mod-hk/ from the spike template.
# (Files listed below; create them one at a time.)

# 1.2 Build.
python scripts\build_switchmod_hk.py
# Expected output: switch-mod-hk\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk8

# 1.3 Deploy to Ryujinx alongside production subsdk9 (different mod folder).
$dst = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago-hk\exefs"
New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item -Force switch-mod-hk\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk8 $dst\subsdk8

# 1.4 Boot SMO in Ryujinx. Expect: clean boot, title screen, normal gameplay.
#     Both subsdks load (production subsdk9 in smo-archipelago, new subsdk8 in smo-archipelago-hk).
#     The new subsdk8 does nothing — hkMain is empty.
```

### Files to add in phase 1

| Path | Content |
|---|---|
| `switch-mod-hk/CMakeLists.txt` | Adapted from [hakkun-example CMakeLists.txt](third_party/hakkun-example/CMakeLists.txt). `PROJECT_NAME` = `smo_archipelago_hk`. Includes `src/*.cpp`. |
| `switch-mod-hk/config/config.cmake` | `TITLE_ID 0x0100000000010000`, `MODULE_NAME smo_archipelago_hk`, `MODULE_BINARY subsdk8` (NB: subsdk8 during phases 1–5; flipped to subsdk9 at phase 6 cutover). `HAKKUN_ADDONS HeapSourceDynamic` only (no Nvn/DebugRenderer in production; those were spike-only). `USE_SAIL TRUE`. |
| `switch-mod-hk/config/npdm.json` | Copy from [hakkun-example npdm.json](third_party/hakkun-example/config/npdm.json) verbatim. |
| `switch-mod-hk/config/VersionList.sym` | `@smo = main` + `100 = 3ca12dfaaf9c82da064d1698df79cda1`. |
| `switch-mod-hk/syms/.gitkeep` | Placeholder; phase 2 populates. |
| `switch-mod-hk/src/main.cpp` | `extern "C" void hkMain() {}` — empty until phase 4. |

### Done when

- Build succeeds; `subsdk8` artifact is ~10–20 KiB.
- Ryujinx boots SMO with the new subsdk8 in `mods/contents/0100000000010000/smo-archipelago-hk/exefs/subsdk8` and the existing production subsdk9 unchanged in `mods/contents/0100000000010000/smo-archipelago/exefs/subsdk9`. No `[rtld]` errors in the Ryujinx log.

## Phase 2 — Sail symbol DB  *(1 day)*

**Goal:** All 37 mangled symbols from [switch-mod/src/hooks/HookSymbols.hpp](switch-mod/src/hooks/HookSymbols.hpp) are in sail `.sym` files. `llvm-nm --dynamic` confirms all 37 appear in `fakesymbols.so`.

### Commands

```pwsh
# 2.1 Copy the spike's sail file verbatim — already proven in Gate 6.
Copy-Item third_party\hakkun-example\syms\game\SmoApSymbols.sym `
    switch-mod-hk\syms\game\SmoApSymbols.sym

# 2.2 Rebuild.
python scripts\build_switchmod_hk.py

# 2.3 Validate.
& "C:\Program Files\LLVM\bin\llvm-nm.exe" --dynamic `
    switch-mod-hk\build\fakesymbols.so | Select-String "_Z" | Measure-Object -Line
# Expected: at least 37 lines (the 37 SMO symbols + any sail-self symbols).
```

### Done when

- llvm-nm shows all 37 names from `SmoApSymbols.sym` in `fakesymbols.so`.
- Build is still clean (no link errors from `main.cpp` referencing missing symbols — there's nothing to reference yet).

## Phase 3a — Pure-plumbing port  *(0.5 day)*

**Goal:** Port the wire-format, config, frame-bridge, and util layers — the parts of switch-mod that touch *neither* Hakkun's runtime primitives *nor* SMO's game state. Lands independently because nothing references these files until phase 3b — they survive the link via `--gc-sections` (the skeleton from phase 1 already has this property).

### Per-file mapping (one-to-one ports, mostly verbatim)

| Old file (`switch-mod/src/`) | New file (`switch-mod-hk/src/`) | Notes |
|---|---|---|
| `util/Json.{cpp,hpp}` | `util/Json.{cpp,hpp}` | Verbatim copy — no nn::/exlaunch/lunakit deps. |
| `util/Log.{cpp,hpp}` | `util/Log.{cpp,hpp}` | Swap `svcOutputDebugString` → `hk::svc::OutputDebugString`. Drop the `SMOAP_DEBUG_SD_LOG` compile-gate (exlaunch-era diagnostic; no Hakkun port for `nn::fs::*`). Retain `markFsReady` / `drainPendingToFile` as no-op stubs for source compat. |
| `ap/ApConfig.{cpp,hpp}` | `ap/ApConfig.{cpp,hpp}` | Verbatim — pure config struct, still compile-time `-DBRIDGE_HOST=...`. |
| `ap/ApProtocol.{cpp,hpp}` | `ap/ApProtocol.{cpp,hpp}` | Verbatim — wire format is byte-equivalent contract. The `ApProtocol.hpp` allocator comment can be relaxed but isn't load-bearing. |
| `ap/ApFrameBridge.{cpp,hpp}` | `ap/ApFrameBridge.{cpp,hpp}` | Verbatim — references `ApState::instance()` but only via the singleton accessor (header-only declaration is enough at compile time; link succeeds once phase 3b lands ApState.cpp). |

**Important: the phase 3a build relies on `--gc-sections` dropping all the new translation units** because `main.cpp`'s `hkMain()` is still empty (per phase 1). Do not reference any of the new files from `main.cpp` until phase 3b. The phase 1 skeleton already verifies the gc-sections behavior.

### Commands

```pwsh
# 3a.1 Create directories and copy the five file pairs.
New-Item -ItemType Directory -Force switch-mod-hk\src\util | Out-Null
New-Item -ItemType Directory -Force switch-mod-hk\src\ap   | Out-Null
Copy-Item switch-mod\src\util\Json.cpp       switch-mod-hk\src\util\Json.cpp
Copy-Item switch-mod\src\util\Json.hpp       switch-mod-hk\src\util\Json.hpp
Copy-Item switch-mod\src\util\Log.cpp        switch-mod-hk\src\util\Log.cpp
Copy-Item switch-mod\src\util\Log.hpp        switch-mod-hk\src\util\Log.hpp
Copy-Item switch-mod\src\ap\ApConfig.cpp     switch-mod-hk\src\ap\ApConfig.cpp
Copy-Item switch-mod\src\ap\ApConfig.hpp     switch-mod-hk\src\ap\ApConfig.hpp
Copy-Item switch-mod\src\ap\ApProtocol.cpp   switch-mod-hk\src\ap\ApProtocol.cpp
Copy-Item switch-mod\src\ap\ApProtocol.hpp   switch-mod-hk\src\ap\ApProtocol.hpp
Copy-Item switch-mod\src\ap\ApFrameBridge.cpp switch-mod-hk\src\ap\ApFrameBridge.cpp
Copy-Item switch-mod\src\ap\ApFrameBridge.hpp switch-mod-hk\src\ap\ApFrameBridge.hpp

# 3a.2 Edit util/Log.cpp: swap svcOutputDebugString → hk::svc::OutputDebugString,
# remove SMOAP_DEBUG_SD_LOG block, stub out markFsReady / drainPendingToFile.

# 3a.3 Build.
python scripts\build_switchmod_hk.py
```

### Done when

- The 5 new pairs of files exist under `switch-mod-hk/src/{util,ap}/`.
- `python scripts/build_switchmod_hk.py` still produces a clean subsdk8 (.nso size unchanged from phase 2 — gc-sections drops the dead code).

## Phase 3b — Switch-side runtime port  *(3 days — was phases 3+4 combined)*

**Goal:** Rewrite the entire live runtime — `ApState`, `ApClient`, all 14 hook files, the `game/` state-machine modules, and the `ui/` (`CappyMessenger`, `ApHudOverlay`) — against Hakkun primitives in one batch. This phase must land as a single coherent change because the include graph forms one connected component (see "Risk callout" in Locked-in decisions).

### Sub-deliverables, in suggested order

1. **Headers first** *(0.25 day)* — copy and adjust `ap/ApState.hpp`, `ap/ApClient.hpp`, `game/{CaptureGate,KingdomUnlock,KingdomOrderGate,MoonApply,ShineInfoLayout}.hpp`, `hooks/*.hpp`, `ui/{ApHudOverlay,CappyMessenger}.hpp`. Headers compile against each other once the include graph is consistent — no implementation code yet.

2. **Hook trampolines (26 hooks across 14 files)** *(1 day)* — port one .cpp file at a time, swapping `HOOK_DEFINE_TRAMPOLINE` → `HkTrampoline<...>` + `installAtSym<"...">()` in `hkMain`. [spike_gate5.cpp](third_party/hakkun-example/src/spike_gate5.cpp) is the canonical template; per-hook estimate 5–10 min mechanical. Plus the 1 inline-at-offset hook (`CreditsStartHook`) refactored to a trampoline on the enclosing `StaffRollScene::init` (Strategy B from spike Gate 3 — symbol candidate `_ZN15StaffRollScene4initERKN2al13ActorInitInfoE`; verify against main.nso before commit; if false-positive on credits-from-menu, add a guard or fall back to Strategy A naked-trampoline @ 0x4C54A4 via `writeBranchLinkAtMainOffset`).

   Per-file inventory (was the phase-4 table):

   | File | Old macro × count | Notes |
   |---|---|---|
   | `AddHackDictionaryHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `AddPayShineHook.cpp` | TRAMPOLINE × 2 | mechanical |
   | `CappyMessageHook.cpp` | TRAMPOLINE × 4 | mechanical |
   | `CaptureStartHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `CreditsStartHook.cpp` | INLINE @ 0x4C54A4 | Strategy B refactor (see above) |
   | `DeathHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `MoonGetHook.cpp` | TRAMPOLINE × 2 | mechanical |
   | `MoonLabelHook.cpp` | TRAMPOLINE × 4 | mechanical |
   | `SaveLoadHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `ScenarioFlagHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `ShineAppearanceHook.cpp` | TRAMPOLINE × 1 | already trampoline on `Shine::init`, not inline |
   | `ShineNumByWorldGetHook.cpp` | TRAMPOLINE × 1 | mechanical |
   | `ShineNumGetHook.cpp` | TRAMPOLINE × 2 | mechanical |
   | `WorldMapSelectHook.cpp` | TRAMPOLINE × 5 | mechanical |

3. **Game state modules + UI** *(0.5 day)* — port `game/{CaptureGate,KingdomUnlock,KingdomOrderGate,MoonApply}.cpp` and `ui/{CappyMessenger,ApHudOverlay}.cpp`. These are SMO-internal logic + occasional `nn::ro::LookupSymbol` calls → `hk::ro::lookupSymbol`.

4. **ApState.cpp** *(0.25 day)* — swap `nn::os::GetSystemTick` for `hk::svc::getSystemTick`. May now use `std::set` / `std::vector` / `std::string` freely (spike Gate 4 cleared this), but `FlatHashSet` is no longer *required* as a workaround — actually retiring it is deferred to phase 7 polish, not load-bearing for parity.

5. **ApClient.cpp (~600 lines, but the apworld's current ApClient.cpp is ~1,124 lines)** *(1 day)* — the major port. `nn::socket::*` → `hk::socket::Socket::instance()->*`; the manual sockaddr workaround dies (use `hk::socket::SocketAddrIpv4::parse(host, port)`); worker thread uses `hk::os::Thread` + raw page-aligned stack; `nn::nifm::*` is replaced or wrapped per Open question 2 below.

6. **`main.cpp` wiring** *(stub time)* — install all 26 trampolines + the 1 enclosing-function trampoline in `hkMain()`, plus `hk::socket::Socket::initialize<"bsd:u">(...)` and the worker thread spawn.

### Files to remove (deferred until phase 6, but listed here for the audit trail)

- `switch-mod/src/util/FlatHashSet.hpp` — obsolete after the libstdc++ allocator restriction lifts (phase 7 retirement, not load-bearing).
- M6.1 hardening in `switch-mod/src/util/Log.cpp` (`snprintf`-to-stack-char-array pattern) — obsolete; can use `std::string` on worker thread.
- `switch-mod/src/hooks/HookSymbols.hpp` — sail .sym replaces it.
- `switch-mod/src/hooks/SoftInstall.hpp` — `installAtSym<"...">()` IS the soft install equivalent.

### Open questions to lock in before starting phase 3b

These don't change the plan structure but shape execution. Spelled out in full in the proposal doc; condensed here:

1. **`hk::socket::Socket` vs SMO's own `nn::socket::Initialize`.** Production switch-mod relies on SMO already calling `nn::socket::Initialize` (per [CLAUDE.md SMO-already-inits-socket note](CLAUDE.md)). The spike used `hk::socket::Socket::initialize<"bsd:u">` from `hkMain` without conflict — but the spike's hkMain didn't exchange data, just took the function address. **Recommended:** match the spike Gate 2 build (call `hk::socket::Socket::initialize<"bsd:u">` from `hkMain`); probe in phase 3b for conflicts; if any surface, mirror lunakit's pattern of replace-hooking SMO's `nn::socket::Initialize` to no-op.
2. **`nn::nifm::*` mapping.** `ApClient` calls `nn::nifm::Initialize` + `SubmitNetworkRequestAndWait` + `IsNetworkAvailable`. Hakkun's surface for `nifm` is undocumented in the spike. **Recommended:** keep the `nn::nifm` calls by adding `nifm` symbols to a new `switch-mod-hk/syms/nn/nifm.sym` file (sail resolves and links against SMO's dynsym). Minimal change.
3. **`BAKE_SYMBOLS` choice.** Phase 2's sail `.sym` block uses `BAKE_SYMBOLS = FALSE` (per `switch-mod-hk/config/config.cmake`); names are stored as strings and resolved by `hk::ro::lookupSymbol` at module load. The alternative — `BAKE_SYMBOLS = TRUE` — replaces strings with murmur hashes for smaller binary. **Recommended:** no change (debugging easier with unbaked names; size isn't a constraint).

### Commands

```pwsh
# 3b.N (after each sub-deliverable, or in one shot at the end):
python scripts\build_switchmod_hk.py
$dst = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago-hk\exefs"
Copy-Item -Force switch-mod-hk\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk8 $dst\subsdk8
```

### Done when

- Build is clean — `scripts/build_switchmod_hk.py` produces subsdk8.
- Sail resolves every symbol referenced by hooks; `llvm-nm --dynamic build/fakesymbols.so` still shows all 37 SMO symbols.
- Loopback test ([smo-loopback-test skill](.claude/skills/smo-loopback-test/SKILL.md)) passes: bridge sees HELLO, scout pre-warm runs, fake moon collection routes through, capture-lock deny rejection fires.
- No `[rtld]` errors at boot.

## Phase 5 — End-to-end validation  *(1 day)*

**Goal:** Functional parity with production. Three gates, sequential.

### 5.1 Ryujinx loopback test  *(~30 min)*

```pwsh
# Use the smo-loopback-test skill canonical flow.
# Probe: bridge sees HELLO, scout pre-warm runs, fake moon collections route through,
# capture-lock-deny rejection fires. Same observed wire-protocol output as production.
```

### 5.2 Ryujinx manual play  *(~1 hour)*

```pwsh
# Boot Ryujinx, run apworld AP server, connect SMOClient.
# Collect ≥5 moons in Cap Kingdom; verify each appears as an AP location check on the server.
# Receive an AP item (most easily: a Moon); verify it applies to the running game.
# Verify capture-lock denies a Frog capture if the AP Frog item hasn't been received.
# Verify CreditsStartHook fires on real game-end (not on credits-from-menu).
```

### 5.3 Real-Switch FW22  *(~1 hour)*

```pwsh
# Deploy: copy subsdk8 (still subsdk8, not yet subsdk9) to SD card alongside production subsdk9.
# Copy switch-mod-hk\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk8
#  to D:\atmosphere\contents\0100000000010000\exefs\subsdk8  (confirm SD drive letter first)
# Boot SMO on real Switch. Same play sequence as 5.2.
```

### Done when

All three gates pass. If 5.3 reveals a divergence Ryujinx didn't show: stop, diagnose, fix, re-validate.

## Phase 6 — Cutover  *(0.5 day)*

**Goal:** `switch-mod-hk/` becomes `switch-mod/`. `subsdk8` flips to `subsdk9`. CI, skills, and docs are updated. exlaunch + lunakit-vendor submodules are gone.

### Commands

```pwsh
# 6.1 In the migration worktree:
cd .claude\worktrees\hakkun-migration

# 6.2 Move the old switch-mod aside, then rename the new one into place.
git mv switch-mod switch-mod-old
git mv switch-mod-hk switch-mod

# 6.3 Flip MODULE_BINARY from subsdk8 to subsdk9 in switch-mod\config\config.cmake.
# Edit by hand or via sed-equivalent.

# 6.4 Remove the old switch-mod submodules.
git submodule deinit switch-mod-old\lunakit-vendor
git submodule deinit switch-mod-old\exlaunch
git rm -rf switch-mod-old

# 6.5 Rebuild to confirm the rename + subsdk9 flip works.
python scripts\build_switchmod_hk.py
# Expected output: switch-mod\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk9
```

### Files to update outside switch-mod/

| Path | Change |
|---|---|
| `.github/workflows/release.yml` | Build step now calls `python scripts/build_switchmod_hk.py`. Output path `switch-mod/build/...` for the .nso. Drop devkitPro / lunakit setup steps; add LLVM 19 install + LibHakkun submodule init. |
| `.github/workflows/test.yml` | If `smo-host-tests` C++ tests reference lunakit headers, update to LibHakkun equivalents. |
| `.claude/skills/smo-build/SKILL.md` | Full rewrite. Toolchain (LLVM 19, prepackaged libc++ via Hakkun setup), build command (`scripts/build_switchmod_hk.py`), deploy path (still subsdk9 in Ryujinx mods folder). |
| `.claude/skills/smo-symbol-discovery/SKILL.md` | Sail-based now: add to `.sym` file → build → verify by `llvm-nm --dynamic fakesymbols.so`. `check_nso_symbols.py` retired. |
| `.claude/skills/smo-host-tests/SKILL.md` | Minor — LibHakkun headers replace lunakit. |
| `.claude/skills/smo-loopback-test/SKILL.md` | No change (bridge-side, implementation-agnostic). |
| `.claude/skills/smo-extract-data/SKILL.md` | No change. |
| `.claude/skills/smo-poptracker/SKILL.md` | No change. |
| `CLAUDE.md` | Architecture section, Decisions table, Repository layout. Annotate M6.1 in Pattern Invariants: "*was* an issue under exlaunch; Hakkun retired it 2026-05-20." |
| `docs/architecture.md` | Switch-side stack swap. |
| `docs/build-windows.md` | LLVM 19 + mingw64 + prepackaged libc++. |
| `docs/install-switch.md` | subsdk filename unchanged (still subsdk9). |
| `docs/milestones.md` | Append M9 entry: "exlaunch → Hakkun migration, 2026-05-DD." |
| `docs/release-process.md` | Build command in the release flow. |
| `scripts/check_nso_symbols.py` | DELETE. |

### Memory annotations

| Memory | Annotation |
|---|---|
| [project_subsdk9_no_thread_local.md](memory/project_subsdk9_no_thread_local.md) | Add: "This was an exlaunch-era pattern. Retired post-Hakkun migration 2026-05-DD — Hakkun's musl + LLVM libc++ + HeapSourceDynamic does NOT have this restriction." |
| [project_nintendo_sockaddr_layout.md](memory/project_nintendo_sockaddr_layout.md) | Add: "Retired post-Hakkun migration. `hk::socket::SocketAddrIpv4` encapsulates the layout; manual sockaddr construction no longer happens in our code." |

### Done when

- `git submodule status | grep -E "lunakit|exlaunch"` returns nothing.
- `grep -r "exlaunch\|lunakit" switch-mod/src` returns nothing.
- CI workflow runs green on a push to the migration branch.
- The smo-loopback-test passes against the new subsdk9.
- A real-Switch FW22 manual play session collects ≥ 5 moons via AP location checks.
- CLAUDE.md no longer mentions exlaunch or lunakit-vendor as current dependencies (historical references in `docs/milestones.md` are fine).
- The two memories above are annotated.

## Phase 7 — Optional polish  *(deferred PR)*

Not in scope for the cutover. Tracked here for the follow-up:

- **Multi-version SMO support** — add `@smo:101,110,120,130` blocks to `VersionList.sym` and per-version `.sym` blocks for the 37 symbols.
- **In-game tracker overlay (M8 deferred)** — `hk::gfx::DebugRenderer` via the Hakkun addon.
- **Drop legacy `Log.cpp` workarounds** — `std::string` + `std::format` on the worker thread.

## Rollback

If phase 5 reveals a > 1-day blocker:
1. Don't merge. The migration branch stays a branch; main is unchanged.
2. File the symptom (creport, log, HUD state) for follow-up.
3. Either fix on the migration branch, or shelve. Shelving costs nothing — spike artifacts + this runbook stay in the repo.

## Working-state checkpoint commits

Each row is a known-good rollback target. The phase 3b row covers the full ap/ + game/ + hooks/ + ui/ runtime port — there is no buildable intermediate state inside phase 3b (see Risk callout).

| Phase | Commit (will populate as we land) | Artifact at this checkpoint |
|---|---|---|
| 0 | `5612a20` | Port wrappers + patch_hakkun.py |
| 1 | `5a537af` | Skeleton subsdk8 (~13 KiB) builds from `switch-mod-hk/` |
| 2 | `1f1399d` | Sail resolves all 37 SMO 1.0.0 hooks; build still clean |
| 2.5 | `c119228` | `build_switchmod_hk.py` auto-applies patches |
| 3a | _TBD_ | Pure-plumbing files copied (`util/Json,Log` + `ap/{ApConfig,ApProtocol,ApFrameBridge}`); subsdk8 size unchanged (gc-sections drops dead code) |
| 3b | _TBD_ | Full runtime ported; loopback test green; sail still resolves 37 symbols |
| 5 | _TBD_ | All three validation gates pass (loopback, Ryujinx manual, real-Switch FW22) |
| 6 | _TBD_ | Cutover: switch-mod-hk → switch-mod, subsdk8 → subsdk9, lunakit/exlaunch removed |

## Glossary

- **Sail** — LibHakkun's symbol DB / resolver. Reads `.sym` files at build time, emits `symboldb.o` + `fakesymbols.so` + `datablocks.o` into the build dir, links them into the .nso. At module load, looks up each symbol against the main module's dynsym and patches in the address.
- **HeapSourceDynamic** — LibHakkun addon that re-exports `operator new` / `malloc` / `free` from the host process (SMO) to subsdk code. This is what makes worker-thread `std::vector::push_back` safe (Gate 4).
- **HkTrampoline** — LibHakkun's trampoline-hook primitive. File-scope `HkTrampoline<Ret, Args...>` variable + lambda body that calls `hook.orig(args)` for the original behavior, installed via `installAtSym<"mangled_name">()`.
- **subsdk8 vs subsdk9** — Atmosphère exefs slot. Phases 1–5 use subsdk8 (avoids collision with production subsdk9). Phase 6 flips to subsdk9.
- **fakesymbols.so** — Sail-generated synthetic library. Each .sym entry becomes a stub. The link uses the stubs; at runtime hk::ro resolves real addresses.
