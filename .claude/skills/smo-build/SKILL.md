---
name: smo-build
description: Build the SMO Switch mod (subsdk9 / switch-mod/, LibHakkun + OdysseyHeaders + sail) and deploy to Ryujinx or the real Switch. Use whenever the user asks to build, rebuild, recompile, or deploy the Switch module; whenever LLVM, cmake, ninja, sail, hakkun, subsdk, or RYU_PATH come up; whenever a switch-mod/ C++ file changes and a build is needed; or whenever the user mentions the install_apworld worktree gotcha (DepositMsg / unknown message type from Switch). Covers the one-time capture_table.h generation, the patch_hakkun + setup_sail wrappers, the Ryujinx-first iterate loop, the post-build deploy, the real-Switch deploy path, and the worktree apworld-install workaround.
---

# Building the SMO Switch mod

The Switch mod targets SMO 1.0.0 and is built on [LibHakkun](https://github.com/fruityloops1/LibHakkun) (subsdk runtime) + [OdysseyHeaders](https://github.com/MonsterDruide1/OdysseyHeaders) (SMO type layouts) + sail (the LibHakkun symbol-DB resolver). The exlaunch + lunakit-vendor toolchain it ran on through 2026-05-21 was retired in the Phase 6 cutover.

## Golden rule

**Ryujinx FIRST, real Switch never as the first test.** A failed Switch launch increments HOS's "title failed to launch" counter for SMO; enough failures shows "Corrupted data detected" prompts (recoverable in ~1 min via Settings → Data Management → Check for Corrupted Data, but a poor experience). Every subsdk build boots clean in Ryujinx before being copied to the SD card.

## Step 0 (one-time after fresh clone, or after items.json / locations.json / shine_map.json changes)

Both `switch-mod/src/ap/capture_table.h` and `switch-mod/src/ap/shine_table.h` are gitignored — on first build `CaptureGate.cpp` / `SaveLoadHook.cpp` fail with `../ap/<table>.h: No such file or directory` until you run:

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_capture_table.py
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_shine_table.py
```

Rerun:
- **`sync_capture_table.py`** whenever `apworld/smo_archipelago/data/items.json` or `client/data/capture_map.json` changes (cap-name → bit-index mapping; out-of-sync = wrong bit assignments).
- **`sync_shine_table.py`** whenever `apworld/smo_archipelago/data/locations.json` or `client/data/shine_map.json` changes (per-moon `(stage, obj_id, shine_uid, kingdom, name, progression)` table consumed by SaveLoadHook, MoonGetHook, and shine_lookup). When `shine_map.json` is absent the script emits an empty stub so the build still compiles, but Phase 2 pre-marking and Talkatoo% block silently no-op — extract first if you care about either.

Both tables are gitignored because they join apworld JSON with the gitignored extracted maps and reproduce the load-bearing shape of those maps for AP-pool moons/captures. See CLAUDE.md's "Never commit Nintendo IP" section for the full rationale.

## Step 1: build (~30s)

**You MUST pass `-DBRIDGE_HOST=<this-machine's-LAN-IP>` on every manual build.** `CMakeLists.txt` has no default — configure aborts with FATAL_ERROR if it's missing. The reason: when UDP discovery misses (firewall, broadcast-dropping router, slow SMOClient boot), the TCP fallback chain probes `127.0.0.1` and then this baked `BRIDGE_HOST`. A stale or made-up default silently produces a binary that can't reach the bridge on a real Switch.

Get this machine's LAN IP and build in one shot:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
$LAN_IP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and
    ($_.PrefixOrigin -eq 'Dhcp' -or $_.PrefixOrigin -eq 'Manual')
}).IPAddress
python scripts\build_switchmod.py -DBRIDGE_HOST=$LAN_IP
```

If the user has multiple NICs (Wi-Fi + Ethernet, VPN adapter, Hyper-V virtual switch), confirm which interface the Switch reaches the PC on before picking an address — the snippet above can return multiple results. Standard rule of thumb: pick the one whose `/24` matches the Switch's IP.

That single command:

1. Runs `scripts/patch_hakkun.py` to apply the 10 Windows-port patches to the pinned LibHakkun submodule (idempotent; reports "already applied" on re-runs).
2. Builds `sail.exe` (the host-side symbol-DB resolver) via `scripts/setup_sail_winpath.py` if it doesn't already exist (one-time per machine; uses msys2 mingw64 g++ since aarch64-clang can't link a host binary).
3. Configures + builds `switch-mod/` via Windows-native CMake + LLVM 19 + Ninja, producing `switch-mod/build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9`.

Other configurables (all optional, defaults baked in CMakeLists.txt):
- `-DBRIDGE_PORT=17777` — bridge TCP port.
- `-DDISCOVERY_PORT=17776` — UDP discovery probe port.
- `-DSMOAP_DEBUG_SD_LOG=ON` — boot-time SD-card log capture to `sd:/smo_ap.txt`.

The runtime `ApDiscovery` UDP probe chain (loopback → broadcast → unicast-fallback against the baked `BRIDGE_HOST`) is the primary path. The baked value is the TCP fallback when UDP misses; it MUST be correct or the fallback chain is useless.

**Don't reuse the cmake cache across machines / LAN changes.** CMake caches `BRIDGE_HOST`. If you copy a worktree to a new machine or your LAN IP changes, `rm -rf switch-mod/build` (or pass `-DBRIDGE_HOST=<new-ip>` to override) — otherwise the old IP stays baked.

## Prereqs (one-time per machine)

Each piece is installed from its upstream installer; the build wrapper sets PATH at run-time, so you don't need them on PATH globally:

- **LLVM 19** — `winget install LLVM.LLVM --version 19.1.7`. Installed at `C:\Program Files\LLVM\bin`. Used for the aarch64-target Switch build.
- **CMake** — installed at `C:\Program Files\CMake\bin`. Any recent version is fine.
- **Ninja** — `winget install Ninja-build.Ninja`. The wrapper finds it under the WinGet packages dir.
- **msys2 + mingw64 g++** — `winget install MSYS2.MSYS2`, then in a msys2 shell `pacman -S mingw-w64-x86_64-gcc`. Used only to build the host-side `sail.exe`.
- **Python 3.12+** — for the wrapper scripts.
- **Python pip packages** — `pip install --user pyelftools mmh3 lz4`. The LibHakkun README typo's the second one as "mmh"; the import is `mmh3`.

The build wrapper handles all the path quoting + env-var dance internally. Direct CMake invocation works too if you set PATH manually:

```pwsh
$env:PATH = "C:\Program Files\LLVM\bin;C:\Program Files\CMake\bin;<ninja>;C:\msys64\mingw64\bin;$env:PATH"
cmake -S switch-mod -B switch-mod\build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build switch-mod\build -j 8
```

But `python scripts\build_switchmod.py` is the canonical entry — it survives the upstream LibHakkun churn around `setup_sail.py` (which periodically wants to rebuild sail and rmtree the build dir mid-configure).

## Step 2: deploy to Ryujinx (manual, no -DRYU_PATH)

```pwsh
$RYU = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago"
New-Item -ItemType Directory -Force "$RYU\exefs" | Out-Null
Copy-Item -Force <worktree>\switch-mod\build\sd\atmosphere\contents\0100000000010000\exefs\subsdk9 "$RYU\exefs\subsdk9"
Copy-Item -Force <worktree>\switch-mod\build\sd\atmosphere\contents\0100000000010000\exefs\main.npdm "$RYU\exefs\main.npdm"
```

User boots SMO in Ryujinx manually (`cd C:\Users\maxwe\Documents\ryujinx-1.3.3 && .\Ryujinx.exe`, then double-click SUPER MARIO ODYSSEY).

**Do not auto-deploy from another worktree.** Auto-deploy hooks clobber a parallel agent's state. For build-verification ("does it compile?"), just check the build product exists. If a deploy IS needed this turn, ask first.

## Step 3: tail logs

```pwsh
# Ryujinx's log (catches everything: mod's [smoap …] svcOutputDebugString
# output, [rtld] unresolved symbols, guest stack traces with demangled names,
# register dumps):
Get-Content (Get-ChildItem "$env:APPDATA\Ryujinx\Logs\Ryujinx_*.log" | Sort LastWriteTime -Descending | Select -First 1) -Tail 80 -Wait

# SMOClient's bridge-forwarded view (once the Switch connects).
# Path is wherever Archipelago's Utils.init_logging writes — for in-tree
# dev that's vendor/Archipelago/logs/SMOClient.txt; for an end-user
# install, under the Archipelago install dir's logs/.
Get-Content "vendor\Archipelago\logs\SMOClient.txt" -Wait -Tail 80
```

Ryujinx's log is gold — `[rtld]` unresolved-symbol lines, guest stack traces with C++ demangled names, register dumps. **Far** more useful than the Switch's binary erpts. Always iterate here.

**On real Switch**: Atmosphere's `lm` does NOT redirect `svcOutputDebugString`
output into a file on the SD card (older docs that pointed at
`sd:/atmosphere/contents/<TID>/smoap.log` were wrong — no such file is
written). For on-device boot-time capture without Ryujinx, configure with
`-DSMOAP_DEBUG_SD_LOG=ON`; the mod will dump its first ~5s of log output
to `sd:/smo_ap.txt` once at drawMain frame ~300. Bridge-side logs (once
SMOClient connects) remain the primary source for everything past boot.

## Step 4: real-Switch deploy (only after Ryujinx clean)

```pwsh
# build_switchmod.py already emits the SD overlay layout at
# switch-mod/build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9
# Replace <SD>: with whatever drive letter the SD card mounts as on this machine.
xcopy /E /I /Y C:\Users\maxwe\Documents\smo_archipelago\switch-mod\build\sd\atmosphere <SD>:\atmosphere
```

Confirm the drive letter with the user before copying — it varies per machine (the maintainer's is `D:`, others may differ). Don't guess. After the copy, optionally eject the same drive so the user can pull it without remembering:

```pwsh
# Only run with a drive letter the user has confirmed is the SD card.
(New-Object -comObject Shell.Application).Namespace(17).ParseName("<SD>:").InvokeVerb("Eject")
```

**Never run `Eject` against a drive letter you didn't confirm with the user this turn** — the wrong letter ejects whatever is there (external HDD, thumb drive, ...).

If a Switch deploy ever causes the corruption icon: Settings → Data Management → Software → Super Mario Odyssey → Check for Corrupted Data. NOT a reinstall.

## Worktree apworld-install gotcha (M6 phase D, 2026-05-17)

If you're working in `.claude/worktrees/<name>/` and rebuilt the Switch mod with a new wire-protocol message type (e.g. `DepositMsg`), `scripts/install_apworld.py` writes to **the worktree's** `vendor/Archipelago/custom_worlds/meatballs.apworld` — NOT the main checkout.

The user launches SMOClient from the **main checkout's** Launcher, so the Launcher loads the stale main-checkout `meatballs.apworld`. Symptom: bridge log shows `unknown message type from Switch: <type>` even though the mod is current.

**Fix every time you ship a wire-protocol change from a worktree**: after `python scripts/install_apworld.py` in the worktree, also overwrite the main checkout's zip:

```pwsh
Copy-Item -Force `
    C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\<name>\vendor\Archipelago\custom_worlds\meatballs.apworld `
    C:\Users\maxwe\Documents\smo_archipelago\vendor\Archipelago\custom_worlds\meatballs.apworld
```

## Subsdk slot

Module ships as `subsdk9` at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9`. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## Fresh worktree setup (additional steps)

A fresh `.claude/worktrees/<name>/` (or `git worktree add`) is missing three pieces the build needs. Do all three up-front, in this order — each fails differently and step 3 reads files written by step 2:

1. **Init `switch-mod/sys` (LibHakkun) + `switch-mod/lib/OdysseyHeaders` submodules** (the wrapper script applies the Windows-port patches before the first compile):
   ```pwsh
   git -C <worktree> submodule update --init --recursive switch-mod/sys switch-mod/lib/OdysseyHeaders
   ```
   Skipping → cmake configure fails on missing `sys/cmake/toolchain.cmake`.

2. **Copy generated data files** (gitignored, per-machine — extracted from a Nintendo NSP via the `smo-extract-data` skill; copy from main checkout is faster):
   ```pwsh
   Copy-Item C:/Users/maxwe/Documents/smo_archipelago/apworld/smo_archipelago/client/data/*.json `
             <worktree>/apworld/smo_archipelago/client/data/
   ```
   Skipping is **silent at build time** but corrupts runtime: `sync_capture_table.py` (step 3) falls back to identity-only `kCaptureHackNames` (every entry equals `kCaptureNames`), so M7 hack-name lookups for SMO-internal names like `Kuribo` / `TRex` fail-open and the capture-lock gate doesn't deny. `sync_shine_table.py` (also step 3) falls back to an empty `kShineTable`, which silently disables Phase 2 pre-marking and Talkatoo% block.

3. **Run `sync_capture_table.py` + `sync_shine_table.py`** (Step 0 above; both read the maps from step 2 to populate the joined tables):
   ```pwsh
   python <worktree>/scripts/sync_capture_table.py
   python <worktree>/scripts/sync_shine_table.py
   ```
   Skipping either → first compile of `CaptureGate.cpp` / `SaveLoadHook.cpp` fails with `../ap/<table>.h: No such file or directory`.

## SMO already inits nn::socket — open a parallel hk::socket client

Never call `nn::socket::Initialize` from subsdk9. SMO 1.0.0 calls it itself during process startup (before `GameSystem::init` returns); a second call hits an "already initialized" assertion inside `nn::socket::detail::InitializeCommon + 0x28c` and aborts the process. Confirmed by an Atmosphere crash report 2026-05-15.

In the Hakkun build, we don't touch `nn::socket` at all. `ApClient` opens its **own** `hk::socket::Socket` client against `bsd:u` (parallel to SMO's), with its own `sm:` handle + transfer-memory pool. That isolates our reconnect loop and selectable behavior from whatever the game does with sockets internally.

## Hakkun internals you'll trip over

### `hk::socket::Socket` template quirks

Two `hk::socket::Socket` signatures don't deduce cleanly and need a workaround at every call site (or an upstream patch). Both bit us during the phase 3b ApClient port:

1. **`Socket::connect` has a phantom 2nd template parameter** that template-deduction cannot infer:
   ```cpp
   template <typename A, typename B>
       requires(std::is_convertible<A*, SocketAddr*>::value)
   ValueOrResult<Ret> connect(s32 fd, const A& address);
   ```
   `B` is never referenced in the signature or body. Calls fail with "no matching member function." Workaround: explicit args, e.g. `sock->connect<SocketAddrIpv4, int>(fd, addr)`.

2. **Templated `setSockOpt(fd, lvl, opt, const T&)` fails to compile inside its own body.** The convenience overload wraps to `Span<const u8>(&opt, sizeof(T))`, but `&opt` is `const T*` (e.g. `const s32*`) and `Span<const u8>`'s constructor wants `const u8*` — no implicit pointer conversion. Workaround: call the explicit Span-taking overload with `reinterpret_cast<const u8*>(&opt)`.

These are landmines from API drafts that compile-tested only with templated-stub callers. If you find similar template-deduction surprises elsewhere in `hk/services/socket/service.h`, consider promoting all three to a `patch_hakkun.py` patch (patches 5–10 there are the model).

### `HkTrampoline` AArch64 PC-relative relocation (already patched)

Upstream `hk::hook::TrampolineHook::installAtOffset` does NOT relocate PC-relative instructions when saving the original prologue — calling `.orig()` on any hooked function whose first instruction is `adrp`/`adr`/`b`/`bl`/`b.cond`/`cbz`/`tbz`/`ldr-literal` corrupts execution. Crash signature in Ryujinx: ARMeilleure host throws `0xC0000005` in `Translator.Execute`, no guest creport. **The fix is shipped** in `scripts/patch_hakkun.py` patches 7a/b/c: the relocator decodes adrp/adr → movz/movk, b/bl → range-checked imm26 or movz+blr, conditional branches → inverted-skip + long-jump. `TrampolineBackup` reserves 8 instruction slots per entry, page-aligned (0x1000) so nested trampolines stay on different ARMeilleure JIT translation blocks. Upstream-PR-ready against fruityloops1/LibHakkun. If you ever bisect a "boot clean, crash mid-frame" regression: comment out hook installs in `main.cpp` to find which prologue Hakkun isn't relocating; the highest-frequency suspects are hooks targeting `Shine::init`, `CappyMessage*`, and other per-spawn functions.
