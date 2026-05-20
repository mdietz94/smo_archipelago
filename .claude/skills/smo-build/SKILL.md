---
name: smo-build
description: Build the SMO Switch mod (subsdk9 / switch-mod/) and deploy to Ryujinx or the real Switch. Use whenever the user asks to build, rebuild, recompile, or deploy the Switch module; whenever cmake, devkitPro, ninja, subsdk, or RYU_PATH come up; whenever a switch-mod/ C++ file changes and a build is needed; or whenever the user mentions the install_apworld worktree gotcha (DepositMsg / unknown message type from Switch). Covers the one-time capture_table.h generation, the Ryujinx-first iterate loop, the post-build deploy, the real-Switch deploy path, and the worktree apworld-install workaround.
---

# Building the SMO Switch mod

## Golden rule

**Ryujinx FIRST, real Switch never as the first test.** A failed Switch launch increments HOS's "title failed to launch" counter for SMO; enough failures shows "Corrupted data detected" prompts (recoverable in ~1 min via Settings → Data Management → Check for Corrupted Data, but a poor experience). Every subsdk build boots clean in Ryujinx before being copied to the SD card.

## Step 0 (one-time after fresh clone or items.json change)

Generate `switch-mod/src/ap/capture_table.h`. The file is gitignored, so on first build `CaptureGate.cpp` fails with `../ap/capture_table.h: No such file or directory` until you run:

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_capture_table.py
```

Rerun this whenever `apworld/smo_archipelago/data/items.json` changes (the table maps cap-name → bit-index for the Switch mod; out-of-sync table = wrong bit assignments).

## Step 1: configure + build (~10s)

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\switch-mod
$env:DEVKITPRO = "C:/devkitPro"
# Auto-detect this machine's current LAN IP (interface with a default gateway, Up).
# DHCP can hand out a different address week-to-week, so re-detect every build rather
# than hardcoding. For Ryujinx-on-same-host runs, override with `$bridgeHost = "127.0.0.1"`.
$bridgeHost = (Get-NetIPConfiguration | Where-Object {
    $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up'
} | Select-Object -First 1).IPv4Address.IPAddress
if (-not $bridgeHost) { throw "Could not detect LAN IP — check network adapters." }
Write-Host "BRIDGE_HOST=$bridgeHost"
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja `
    -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake `
    -DBRIDGE_HOST=$bridgeHost
& "C:/Program Files/CMake/bin/cmake.exe" --build build
```

Defaults:
- `-DBRIDGE_HOST` — auto-detected from the active LAN adapter (the one with a default gateway). DHCP leases change, so let the snippet above resolve it fresh each build rather than baking a literal. For Ryujinx-on-same-host runs, set `$bridgeHost = "127.0.0.1"` before the cmake call.
- Bridge port baked at compile time; the runtime `romfs/ap_config.json` SD-read path was abandoned (MountSdCardForDebug fails on retail/newer FW). Edit-and-rebuild is the only way.
- The detected IP is baked into `subsdk9`; if your LAN IP changes after deploy, you must rebuild and re-deploy or the Switch mod will try to connect to a stale address.

**Do NOT add `-DRYU_PATH=...` unless the user explicitly asks.** The post-build hook auto-deploys subsdk9+npdm+ap_config.json into Ryujinx mods/, which clobbers parallel agents' state in another worktree. For build-verification ("does it compile?"), omit it. If a deploy IS needed for this turn, ask first. Manual copy steps below.

If Ninja isn't installed, swap `-G Ninja` for:
```
-G "Unix Makefiles" -DCMAKE_MAKE_PROGRAM=C:/devkitPro/msys2/usr/bin/make.exe
```
Same build product; verified end-to-end.

**Critical cross-build gotcha**: msys2 cmake (`/c/devkitPro/msys2/usr/bin/cmake`) inside Git Bash CANNOT find DEVKITPRO (it expects `/opt/devkitpro` mount which Git Bash doesn't have). Use the Windows CMake at `C:/Program Files/CMake/bin/cmake.exe` with `DEVKITPRO=C:/devkitPro` env var.

The build needs `set_source_files_properties(... PROPERTIES COMPILE_FLAGS "-fpermissive")` on lunakit's vendored sources because devkitA64 GCC 15 rejects const-T `std::construct_at` in lunakit's `typed_storage.hpp`. Already wired in CMakeLists.

## Step 2: deploy to Ryujinx (manual, no -DRYU_PATH)

```pwsh
$RYU = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago"
Copy-Item -Force <worktree>\switch-mod\build\subsdk9  $RYU\exefs\subsdk9
Copy-Item -Force <worktree>\switch-mod\build\main.npdm $RYU\exefs\main.npdm
```

User boots SMO in Ryujinx manually (`cd C:\Users\maxwe\Documents\ryujinx-1.3.3 && .\Ryujinx.exe`, then double-click SUPER MARIO ODYSSEY).

## Step 3: tail logs

```pwsh
# Mod's smoap.log (most useful — its own structured output):
Get-Content "$env:APPDATA\Ryujinx\sdcard\atmosphere\contents\0100000000010000\smoap.log" -Wait -Tail 80

# Ryujinx's own log (catches [rtld] unresolved symbols + guest stack traces with demangled names):
Get-Content (Get-ChildItem "$env:APPDATA\Ryujinx\Logs\Ryujinx_*.log" | Sort LastWriteTime -Descending | Select -First 1) -Tail 80
```

Ryujinx's log is gold — `[rtld]` unresolved-symbol lines, guest stack traces with C++ demangled names, register dumps. **Far** more useful than the Switch's binary erpts. Always iterate here.

## Step 4: real-Switch deploy (only after Ryujinx clean)

```pwsh
& "C:/Program Files/CMake/bin/cmake.exe" --install build  # populates sd-overlay/
# Replace <SD>: with whatever drive letter the SD card mounts as on this machine.
xcopy /E /I /Y C:\Users\maxwe\Documents\smo_archipelago\switch-mod\sd-overlay\atmosphere <SD>:\atmosphere
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

Module ships as `subsdk9` at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9` — the lunakit default. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## libnx extern "C" gotcha (build-time foot-gun)

`lunakit-vendor/src/lib/nx/kernel/svc.h` and `lib/nx/result.h` declare functions WITHOUT any `extern "C"` wrapper. The wrapper is in the umbrella `lib/nx/nx.h`. From C++ TUs, **always `#include "lib/nx/nx.h"`**, never the inner headers directly.

Including them direct gives C++ mangling at call sites (e.g. `_Z20svcOutputDebugStringPKcm`), the assembly stubs have C linkage, link succeeds, runtime gets unresolved-symbol from rtld, PC jumps to 0, process aborts. Critical bug we hit twice — recognize the symptom from runtime: `[rtld] unresolved _Z20svc...`.

## Fresh worktree setup (additional steps)

A fresh `.claude/worktrees/<name>/` (or `git worktree add`) is missing three pieces the build needs. Do all three up-front, in this order — each fails differently and step 3 reads files written by step 2:

1. **Init `lunakit-vendor` + `exlaunch` submodules**:
   ```pwsh
   git -C <worktree> submodule update --init switch-mod/lunakit-vendor switch-mod/exlaunch
   ```
   Skipping → `cmake configure: Could not find toolchain file: lunakit-vendor/cmake/toolchain.cmake`.

2. **Copy generated data files** (gitignored, per-machine — extracted from a Nintendo NSP via the `smo-extract-data` skill; copy from main checkout is faster):
   ```pwsh
   Copy-Item C:/Users/maxwe/Documents/smo_archipelago/apworld/smo_archipelago/client/data/*.json `
             <worktree>/apworld/smo_archipelago/client/data/
   ```
   Skipping is **silent at build time** but corrupts runtime: `sync_capture_table.py` (step 3) falls back to identity-only `kCaptureHackNames` (every entry equals `kCaptureNames`), so M7 hack-name lookups for SMO-internal names like `Kuribo` / `TRex` fail-open and the capture-lock gate doesn't deny.

3. **Run `sync_capture_table.py`** (Step 0 above; reads `capture_map.json` from step 2 to populate the diverged hack-name array):
   ```pwsh
   python <worktree>/scripts/sync_capture_table.py
   ```
   Skipping → first compile of `CaptureGate.cpp` fails with `../ap/capture_table.h: No such file or directory`.

## SMO already inits nn::socket

Never call `nn::socket::Initialize` from subsdk9. SMO 1.0.0 calls it itself during process startup (before `GameSystem::init` returns); a second call hits an "already initialized" assertion inside `nn::socket::detail::InitializeCommon + 0x28c` and aborts the process. Confirmed by an Atmosphere crash report 2026-05-15 showing `ApClient::initNetworking` → `nn::socket::Initialize` → `InitializeCommon` → `OnAssertionFailure` → `nn::svc::Break`. Lunakit independently confirms by installing `DisableSocketInit::InstallAtSymbol("_ZN2nn6socket10InitializeEPvmmi")` as a no-op replace-hook — they suppress SMO's call so they can run their own pool. We take the opposite approach: keep SMO's init, skip ours. `Socket()`/`Connect()`/`Send()`/`Recv()`/`Select()` all work against the library SMO already brought up; no pool of our own needed. `nn::nifm::Initialize` and `SubmitNetworkRequestAndWait` are still safe (idempotent). If a larger socket pool is ever needed, mirror lunakit's pattern: replace-hook SMO's `Initialize` with a no-op, then call our own — don't double-init.
