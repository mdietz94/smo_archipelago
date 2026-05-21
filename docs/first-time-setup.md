# First-time setup

This page describes everything a brand-new SMO Archipelago player needs to
do once on their machine. After this, joining a multiworld is the same as
with any other Archipelago client: open **SMO Client** from the Archipelago
Launcher and connect to the AP server.

> **Platform:** Windows only today. Linux and macOS aren't blocked by
> design, but the setup wizard and several scripts assume `%APPDATA%`,
> `C:\Program Files\LLVM`, `C:\msys64`, the Windows Python launcher
> (`py -3.12`), and similar Windows-specific paths. No one has tested the
> flow on other platforms.

## Hard requirements

Before you start, confirm all three:

| Requirement | Why | What to do if you don't have it |
|---|---|---|
| **Super Mario Odyssey 1.0.0** | Every public SMO mod (smo-online, OdysseyDecomp, the Hakkun example, ours) targets the original 1.0.0 release. 1.1.0+ have different symbol offsets, struct layouts, and patched behaviors — our module won't load on them. | If you're on 1.1.0, 1.2.0, or 1.3.0, downgrade to 1.0.0 using [Istador/odyssey-downgrade](https://github.com/Istador/odyssey-downgrade). Follow that tool's README — it's a one-time process that removes the update overlay so the cartridge / base NSP runs as 1.0.0. |
| **Switch firmware 21.x or 22, OR an emulator** | Both FW 21.x (the historical target) and FW 22 boot the same `subsdk9` overlay cleanly under Atmosphere — validated end-to-end on real hardware via the 2026-05-20 Hakkun real-Switch spike and the post-cutover boot of the current build. An emulator loads the same overlay as a mod and is fully supported. |  |
| **Atmosphere CFW** running on a supported firmware (real Switch only) | The mod ships as an Atmosphere overlay (`exefs/subsdk9`). | Follow one of the community guides — [NH Switch Guide](https://nh-server.github.io/switch-guide/) is the canonical starting point. |

## What you'll end up with

- `meatballs.apworld` installed in your Archipelago install's `custom_worlds/`
- Moon + capture name tables extracted from your own SMO 1.0.0 NSP or XCI,
  sitting in `%APPDATA%/SMOArchipelago/data/`
- A compiled Switch module (`subsdk9` + `main.npdm` + `ap_config.json`)
  sitting in `%APPDATA%/SMOArchipelago/build/`
- That module copied to **either** your modded Switch's SD card **or**
  Ryujinx's mods directory (your choice; you can re-run setup and pick the
  other one later)

You only need to run this once per machine. **Changing AP server or slot
does NOT require re-running setup** — once setup is done, you can join any
multiworld by opening SMO Client from the Archipelago Launcher and typing
its host/port and slot name into the Connect bar. See
[changing servers](changing-servers.md) for details.

## Prerequisites

The wizard checks for all of these before doing anything and links you to
install pages for whatever's missing. Best to install them up-front to avoid
back-and-forth:

| Prerequisite | Used for | How to get it |
|---|---|---|
| **Archipelago** | The framework SMO Client runs inside | https://github.com/ArchipelagoMW/Archipelago/releases |
| **Python 3.12** | The moon/capture extractor (`oead` has no Python 3.13+ wheel) | https://www.python.org/downloads/release/python-3120/#files |
| **LLVM 19** | Cross-compiler (aarch64-target) for the Switch module | `winget install LLVM.LLVM --version 19.1.7` |
| **CMake 3.24+** | Build orchestrator | `winget install Kitware.CMake` |
| **Ninja** | Build backend | `winget install Ninja-build.Ninja` |
| **msys2 + mingw64 g++** | Host compiler used only to build the host-side sail symbol-DB tool | `winget install MSYS2.MSYS2`, then in a msys2 shell: `pacman -S mingw-w64-x86_64-gcc` |
| **hactool** | Extracts RomFS from your SMO dump | https://github.com/SciresM/hactool/releases |
| **prod.keys** (Switch console keys) | hactool needs them to decrypt the dump | Dump with Lockpick_RCM → place at `%USERPROFILE%\.switch\prod.keys` |
| **title.keys** (XCI only) | NSPs ship a ticket inside the package; XCI cartridge dumps don't, so hactool needs the SMO titlekey from `title.keys` | Dump with Lockpick_RCM (same run as prod.keys) → place at `%USERPROFILE%\.switch\title.keys` |
| **Your SMO 1.0.0 NSP or XCI** | Source of moon + capture names | Your legally-dumped copy. **Not** a patched version — 1.0.0 only. NSP and XCI are both supported. |
| **A modded Switch OR an emulator** | Where SMO actually runs | Atmosphere CFW on a modded Switch (FW 21.x or earlier), or an emulator |

> ⚠️ **Why so many tools?** SMO Archipelago is "play your own Switch", not
> "play an emulated ROM". The mod that talks to AP runs inside SMO on the
> Switch itself, which means it has to be cross-compiled per-user.
> Pre-built binaries can't ship because they'd incorporate Nintendo SDK
> derivations. The wizard automates as much of the build as it can, but
> the toolchain itself has to live on your machine.

## The flow

Setup is independent of any specific multiworld. Do it once; afterwards you
can join any SMO Archipelago multiworld by opening SMO Client and connecting
to it, exactly like every other AP client.

1. **Download `meatballs.apworld`** from the
   [Releases page](https://github.com/mdietz94/smo_archipelago/releases).
2. **Drop it into Archipelago's `custom_worlds/`** directory. On Windows the
   path is typically `%LOCALAPPDATA%\Archipelago\custom_worlds\` or
   wherever you installed Archipelago.
3. **Open the Archipelago Launcher and click "SMO Client"** in the Clients
   list. The SMO Client window opens.
4. **Type `/setup` in the SMO Client command bar.** The setup wizard opens
   in a fresh window. This is also how you re-run the wizard later
   (apworld update, switching deploy targets) — SMO Client never
   auto-spawns the wizard.
5. **Walk the wizard.** Seven pages, in order:
   1. Welcome — read the overview.
   2. Prerequisites — wizard checks the table above; click "Install..." for
      anything missing, install it, click "Re-check".
   3. SMO dump picker — browse to your SMO 1.0.0 NSP or XCI.
   4. Extract maps — wizard runs the extractor (~30s the first time
      because it sets up a Python 3.12 venv with `oead`, then faster on
      re-runs). Outputs land in `%APPDATA%/SMOArchipelago/data/`.
   5. Build Switch module — wizard configures and runs the cross-compile.
      The PC's LAN IP is auto-detected and baked into the mod as a
      fallback; runtime UDP discovery is the primary path that lets the
      Switch find your PC even if the LAN IP changes later. Takes about
      a minute end to end.
   6. Deploy target — usually **Real Switch (SD card)**:
      - **SD card:** wizard auto-detects mounted drives with an
        `atmosphere/` directory; pick yours or browse to it. Files land
        at `<drive>:\atmosphere\contents\0100000000010000\`. Eject the
        SD card and plug it into your modded Switch.
      - **Custom folder:** writes the same `atmosphere/contents/...`
        subtree under a folder of your choice — useful if you sync your
        SD card through DBI, Goldleaf, a network share, or UMS later.
      - **Ryujinx:** if you happen to already have Ryujinx set up
        locally, it's a supported target — the wizard copies to
        `%APPDATA%/Ryujinx/mods/contents/...`. (Ryujinx itself is no
        longer publicly distributed; the wizard works with whichever
        copy you already have.)
   7. Done — wizard closes and returns control to SMO Client.
6. **Boot SMO.** On your Switch (or in Ryujinx, if that's where you
   deployed) — the mod loads on game start. It dials your PC every
   couple seconds until SMO Client is listening (port 17777 by default);
   the SMO Client window flips from "waiting for Switch" to "ready" the
   moment HELLO arrives.
7. **Join a multiworld.** Generate or join a seed and connect SMO Client
   to it the same way you would for any other Archipelago game. The
   apworld is named **Spicy Meatball Overdrive** in the Launcher's
   *Generate Template* output. If you're new to Archipelago in general,
   see AP's
   [Setting up a YAML](https://archipelago.gg/tutorial/Archipelago/setup/en)
   tutorial.

## After setup

Joining additional multiworlds works exactly like every other Archipelago
client — open SMO Client from the Archipelago Launcher and connect. The
wizard does not need to run again unless you upgrade to a new SMO
Archipelago release (or, rarely, if your network blocks the runtime UDP
discovery and the baked-in fallback IP no longer matches your PC). Run
`/setup` from the SMO Client command bar in those cases.

## Troubleshooting

### "Prerequisite missing" but I installed it

Open a fresh terminal — `cmake`, `ninja`, `clang` (from LLVM), `g++` (from
msys2 mingw64), and `hactool` are PATH-based, and a shell that was open
before you installed them won't see them. Re-launch the wizard from the
Archipelago Launcher and click "Re-check".

### Extraction fails with "oead build failed"

Confirm you actually have Python 3.12 (not 3.13). The wizard's prereq check
finds it via `py -3.12` on Windows; if you have Python 3.13 installed but
not 3.12, `oead` has no wheel for 3.13 and pip will try to build it from
source (slow + fragile). Install Python 3.12 from
https://www.python.org/downloads/release/python-3120/#files alongside any
other Pythons.

### Build fails with "clang.exe: command not found" or "sail not found"

LLVM 19 isn't on PATH or sail's host build didn't run. The build wrapper
(`scripts/build_switchmod.py`) sets PATH itself for the LLVM + ninja
invocation and also builds `sail.exe` on first run via msys2 mingw64 g++.
If you get a missing-compiler error: re-check that `winget install
LLVM.LLVM --version 19.1.7` and `winget install MSYS2.MSYS2` (plus
`pacman -S mingw-w64-x86_64-gcc` inside the msys2 shell) both completed.

### Switch boots SMO but the mod doesn't load

Check the mod log on the SD card / Ryujinx sd folder at
`atmosphere/contents/0100000000010000/smoap.log`. Most often the cause is
your PC's firewall blocking inbound TCP 17777 or UDP 17776 (the discovery
probe port). The mod tries discovery first (UDP probe on loopback, then
LAN broadcast); if discovery fails, it falls back to the IP baked in at
setup time. If your LAN IP has changed AND discovery is broken (e.g.
firewall is dropping UDP), re-run `/setup` to rebuild with the current
fallback IP.

### "Wizard launched but window doesn't show up"

SMO Client can take a few seconds to spin up on a cold start. If nothing
appears after 30s, check the Archipelago Launcher's log window for
errors — most likely a missing dependency (re-run Archipelago's installer).

## Reset / re-run

If anything goes wrong and you want a clean slate:

```pwsh
# Delete all wizard outputs. Next time you open SMO Client (from the
# Archipelago Launcher), the wizard will pop on its own; you can also
# trigger it explicitly with /setup from the command bar.
Remove-Item -Recurse -Force "$env:APPDATA\SMOArchipelago"
```

Or, from inside a running SMO Client, type `/setup` in the command bar —
that spawns the wizard in a fresh window without wiping anything; the
wizard's pages remember what's already been done so you can re-run only
the steps you actually changed (e.g. only the Build and Deploy pages if
you're rebuilding to refresh the fallback IP after UDP discovery turned
out to be blocked).
