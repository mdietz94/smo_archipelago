---
name: smo-build
description: Build the SMO Switch mod (subsdk9 / switch-mod/) and deploy to Ryujinx or the real Switch. Use whenever the user asks to build, rebuild, recompile, or deploy the Switch module; whenever cmake, LLVM, ninja, sail, subsdk, LibHakkun, OdysseyHeaders, or RYU_PATH come up; whenever a switch-mod/ C++ file changes and a build is needed; or whenever the user mentions the install_apworld worktree gotcha (DepositMsg / unknown message type from Switch). Covers the one-time capture_table.h generation, the Ryujinx-first iterate loop, the post-build deploy, the real-Switch deploy path, and the worktree apworld-install workaround.
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

## Step 1: build (~30s incremental, ~2min from cold)

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\build_switchmod.py
```

The script:
1. Applies LibHakkun Windows-port patches via `scripts/patch_hakkun.py` (idempotent — sentinels detect already-applied state).
2. Builds `sail` (LibHakkun's symbol-DB host binary) one time per machine via `scripts/setup_sail_winpath.py` if `switch-mod/sys/sail/build/sail.exe` is missing.
3. Runs CMake configure + ninja build with the Windows-native LLVM 19 + CMake + Ninja toolchain.

Toolchain paths the script expects (hardcoded; edit `scripts/build_switchmod.py` if your install differs):
- `C:\Program Files\LLVM\bin` — LLVM 19 (clang-cl + clang-tidy). Install via `winget install LLVM.LLVM --version 19.1.7`.
- `C:\Program Files\CMake\bin` — Windows-native CMake.
- `C:\Users\maxwe\AppData\Local\Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe` — Ninja.
- `C:\msys64\mingw64\bin` — mingw64 host g++ (used only by sail's host-compile step; the target build is LLVM-only).

Override `-DBRIDGE_HOST` / `-DBRIDGE_PORT` / `-DSMO_AP_MOD_VERSION` per-machine by passing them after the script name. The script forwards everything past `argv[1]` to CMake's configure:

```pwsh
python scripts\build_switchmod.py -DBRIDGE_HOST=127.0.0.1
```

For LAN auto-detection (DHCP can hand out a different address week-to-week, so re-detect every build rather than hardcoding):

```pwsh
$bridgeHost = (Get-NetIPConfiguration | Where-Object {
    $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up'
} | Select-Object -First 1).IPv4Address.IPAddress
if (-not $bridgeHost) { throw "Could not detect LAN IP — check network adapters." }
python scripts\build_switchmod.py -DBRIDGE_HOST=$bridgeHost
```

For Ryujinx-on-same-host runs, use `-DBRIDGE_HOST=127.0.0.1`. The detected IP is baked into `subsdk9`; if your LAN IP changes after deploy, rebuild and re-deploy or the Switch mod will try to connect to a stale address.

**Do NOT add CMake auto-deploy flags unless the user explicitly asks.** Build output is at `switch-mod/build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9`; manual copy via Step 2.

## Step 2: deploy to Ryujinx (manual)

```pwsh
$ryu = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago\exefs"
$src = "<worktree>\switch-mod\build\sd\atmosphere\contents\0100000000010000\exefs"
Copy-Item -Force $src\subsdk9   $ryu\subsdk9
Copy-Item -Force $src\main.npdm $ryu\main.npdm
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
# Replace <SD>: with whatever drive letter the SD card mounts as on this machine.
$src = "C:\Users\maxwe\Documents\smo_archipelago\switch-mod\build\sd\atmosphere"
xcopy /E /I /Y $src <SD>:\atmosphere
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

1. **Init `switch-mod/sys` (LibHakkun) + `switch-mod/lib/OdysseyHeaders` submodules**:
   ```pwsh
   git -C <worktree> submodule update --init --recursive switch-mod/sys switch-mod/lib/OdysseyHeaders
   ```
   The `--recursive` is required: LibHakkun nests `tools/senobi`, OdysseyHeaders nests NintendoSDK. Skipping → `scripts/patch_hakkun.py` exits with "switch-mod/sys not found".

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

Never call `nn::socket::Initialize` from subsdk9. SMO 1.0.0 calls it itself during process startup (before `GameSystem::init` returns); a second call hits an "already initialized" assertion inside `nn::socket::detail::InitializeCommon + 0x28c` and aborts the process. Confirmed by an Atmosphere crash report 2026-05-15 (under the legacy exlaunch build) showing `ApClient::initNetworking` → `nn::socket::Initialize` → `InitializeCommon` → `OnAssertionFailure` → `nn::svc::Break`. The Hakkun port wires `hk::socket::Socket::initialize<"bsd:u">` from `hkMain` *only* after explicitly initializing `sm::ServiceManager` (the spike's Gate 2 only took the function's address — non-lazy init was discovered during phase 3b). `Socket()`/`Connect()`/`Send()`/`Recv()`/`Poll()` all work against the library SMO already brought up; we run our own `bsd:u` service handle alongside SMO's. `nn::nifm::Initialize` and `SubmitNetworkRequestAndWait` are still safe (idempotent); resolved via sail-supplied `switch-mod/syms/nn/nifm.sym`.
