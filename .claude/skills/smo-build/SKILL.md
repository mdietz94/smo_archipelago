---
name: smo-build
description: Build the SMO Switch mod (subsdk9 / switch-mod/) and deploy to Ryujinx or the real Switch. Use whenever the user asks to build, rebuild, recompile, or deploy the Switch module; whenever cmake, devkitPro, ninja, subsdk, or RYU_PATH come up; whenever a switch-mod/ C++ file changes and a build is needed; or whenever the user mentions the install_apworld worktree gotcha (DepositMsg / unknown message type from Switch). Covers the one-time capture_table.h generation, the Ryujinx-first iterate loop, the post-build deploy, the real-Switch deploy path, and the worktree apworld-install workaround.
---

# Building the SMO Switch mod

## Golden rule

**Ryujinx FIRST, real Switch never as the first test.** A failed Switch launch increments HOS's "title failed to launch" counter for SMO; enough failures shows "Corrupted data detected" prompts (recoverable in ~1 min via Settings → Data Management → Check for Corrupted Data, but a poor experience). Every subsdk build boots clean in Ryujinx before deploying to D:\.

Memory: `feedback_no_blind_switch_deploys.md`.

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
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja `
    -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake `
    -DBRIDGE_HOST=192.168.1.187
& "C:/Program Files/CMake/bin/cmake.exe" --build build
```

Defaults:
- `-DBRIDGE_HOST=192.168.1.187` — user's LAN IP. For Ryujinx-on-same-host runs, use `127.0.0.1` instead.
- Bridge port baked at compile time; the runtime `romfs/ap_config.json` SD-read path was abandoned (MountSdCardForDebug fails on retail/newer FW). Edit-and-rebuild is the only way.

**Do NOT add `-DRYU_PATH=...` unless the user explicitly asks.** The post-build hook auto-deploys subsdk9+npdm+ap_config.json into Ryujinx mods/, which clobbers parallel agents' state in another worktree. Memory: `feedback_no_auto_ryujinx_deploy.md`. Manual copy steps below.

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
xcopy /E /I /Y C:\Users\maxwe\Documents\smo_archipelago\switch-mod\sd-overlay\atmosphere D:\atmosphere
```

After deploy, eject the SD card programmatically (user prefers not to do it manually). Memory: `feedback_eject_sd_after_deploy.md`.

If a Switch deploy ever causes the corruption icon: Settings → Data Management → Software → Super Mario Odyssey → Check for Corrupted Data. NOT a reinstall.

## Worktree apworld-install gotcha (M6 phase D, 2026-05-17)

If you're working in `.claude/worktrees/<name>/` and rebuilt the Switch mod with a new wire-protocol message type (e.g. `DepositMsg`), `scripts/install_apworld.py` writes to **the worktree's** `vendor/Archipelago/custom_worlds/smo.apworld` — NOT the main checkout.

The user launches SMOClient from the **main checkout's** Launcher, so the Launcher loads the stale main-checkout `smo.apworld`. Symptom: bridge log shows `unknown message type from Switch: <type>` even though the mod is current.

**Fix every time you ship a wire-protocol change from a worktree**: after `python scripts/install_apworld.py` in the worktree, also overwrite the main checkout's zip:

```pwsh
Copy-Item -Force `
    C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\<name>\vendor\Archipelago\custom_worlds\smo.apworld `
    C:\Users\maxwe\Documents\smo_archipelago\vendor\Archipelago\custom_worlds\smo.apworld
```

## Subsdk slot

Module ships as `subsdk9` at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9` — the lunakit default. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## libnx extern "C" gotcha (build-time foot-gun)

`lunakit-vendor/src/lib/nx/kernel/svc.h` and `lib/nx/result.h` declare functions WITHOUT any `extern "C"` wrapper. The wrapper is in the umbrella `lib/nx/nx.h`. From C++ TUs, **always `#include "lib/nx/nx.h"`**, never the inner headers directly.

Including them direct gives C++ mangling at call sites (e.g. `_Z20svcOutputDebugStringPKcm`), the assembly stubs have C linkage, link succeeds, runtime gets unresolved-symbol from rtld, PC jumps to 0, process aborts. Critical bug we hit twice — recognize the symptom from runtime: `[rtld] unresolved _Z20svc...`.

## Fresh worktree setup (additional steps)

Fresh worktrees need three steps before cmake will succeed (memory: `project_fresh_worktree_setup.md`):

1. Init lunakit-vendor + exlaunch submodules: `git submodule update --init --recursive`
2. Copy `apworld/smo_archipelago/client/data/{shine_map,capture_map}.json` from main checkout (gitignored; per-machine generated).
3. Run `python scripts/sync_capture_table.py` (Step 0 above).

## SMO already inits nn::socket

Never call `nn::socket::Initialize` from subsdk9 — SMO did it first and a second call aborts. Memory: `project_smo_socket_already_inited.md`.
