# Spicy Meatball Overdrive

A real Archipelago client for **Super Mario Odyssey** on a modded Nintendo Switch.

Today the SMO Archipelago experience is a *Manual* client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system checklist where players tick boxes by hand. This project replaces the honor system with an in-game module that:

- Detects moons / captures / scenario events on Switch and reports them as AP location checks.
- Receives AP items (moons, captures, kingdoms) and applies them to the live game.
- Enforces capture locks (cannot possess Frog/Yoshi/T-Rex/etc. until the AP item is received).
- Surfaces progress through a Kivy tracker tab and an in-game ImGui overlay (later).

## Architecture

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ SMOClient (Python, inside .apworld) ]  <--websocket-->  [ AP server ]
   exlaunch                              SMOContext(CommonContext)                                archipelago.gg
   LunaKit hooks                         Kivy GUI (Tracker + Connections tabs)                    or self-host
   ImGui overlay                         SwitchServer on :17777
```

The SMOClient is registered as the "SMO Client" component in Archipelago's Launcher; click it from the Launcher GUI and you get one process that simultaneously connects to the AP server (via the inherited `CommonContext` websocket plumbing) AND runs the LAN TCP server the Switch mod connects to. Wire format: [`docs/wire-protocol.md`](docs/wire-protocol.md).

Earlier revisions of this project shipped the client as a standalone `python -m smo_ap_bridge` process plus a Flask web tracker on :8000; both were merged into the in-apworld client (see the Phase 1-7 reshape plan at `~/.claude/plans/please-put-together-a-playful-thacker.md`).

## Project layout

| Path | Purpose |
| --- | --- |
| `apworld/smo_archipelago/` | Forked apworld + SMOClient. Generates seeds AND ships the Launcher button. |
| `apworld/smo_archipelago/client/` | Python client. Subclasses CommonContext, hosts the SwitchServer. |
| `apworld/smo_archipelago/tests/` | Unit + integration tests (227 pass, 41 skipped — live-AP / extraction / Windows-only-detect gated). |
| `switch-mod/` | exlaunch C++ module. Hooks SMO; produces `subsdk9` ELF. |
| `docs/` | Architecture, wire protocol, build, install, symbol catalog. |
| `scripts/` | install_apworld, ap_generate, ap_server, switch_smoke_test, sync_capture_table, extract_shine_map. |

## Status

Pre-alpha. Tracking against milestones M0-M8 — see [`docs/architecture.md`](docs/architecture.md) and the plan files at `~/.claude/plans/`.

| Milestone | What works |
| --- | --- |
| M0 | Toolchain + SMO 1.0.0 symbol map |
| M1 | Bridge skeleton |
| M2 | Apworld parity fork |
| M3 | Switch module skeleton |
| M4 | Read-only state mirroring (moons, captures) |
| M4.5 | State reconciliation across disconnects |
| M5 | Web tracker (since merged into the Kivy GUI's Tracker tab) |
| M5.5 | AP server live integration (PC-only loopback validated) |
| M5.7 | Ryujinx E2E |
| **M6** | **Item application — phase A (moons), A.5 (cutscene labels), B (captures) done** |
| M6.1 | Worker-thread allocator hardening |
| **Bridge merge** | **Bridge collapsed into the apworld as SMOClient; one process, one Kivy GUI** |
| M7 | Capture lock + goal |
| M8 | Apworld extensions, in-game ImGui, polish |

## First-time setup (typical user flow)

> ⚠️ **Requires SMO 1.0.0** on a modded Switch running **Atmosphere on
> firmware 21.x or earlier**. SMO 1.1.0+ won't work; downgrade with
> [Istador/odyssey-downgrade](https://github.com/Istador/odyssey-downgrade).
> **FW22+ is NOT supported** (homebrew lifecycle changes break our
> subsdk9 module).
>
> **Platform:** Windows only today. Linux/macOS aren't blocked by design,
> but the setup wizard and several scripts assume `%APPDATA%`,
> `C:/devkitPro`, and the Windows Python launcher.

The user-facing flow:

1. **Download `smo.apworld`** from the
   [Releases page](../../releases).
2. **Drop it into your Archipelago install's `custom_worlds/`** directory.
3. **Generate a multiworld with an SMO slot.** If you're new to AP:
   open the Archipelago Launcher → *Generate Template* → find the
   YAML labeled **Spicy Meatball Overdrive** in your `Players/`
   directory → set your `name` and any options → click *Generate*.
   Extract the per-player zip from `output/` — alongside the usual AP
   files you'll find a `<player>.smoap`.
4. **Double-click your `.smoap` file.** The Launcher routes it to
   **SMO Client** (that's how the entry appears in the Launcher's
   Clients list). On first run, SMO Client opens the setup wizard,
   which walks you through prereq checks → SMO NSP pick →
   moon/capture extraction → bridge PC IP → Switch-mod compile →
   deploy to SD card.

Detailed walkthrough including prerequisites:
[`docs/first-time-setup.md`](docs/first-time-setup.md).

## How the game plays differently from vanilla SMO

A few behaviors only make sense once you know what the mod is doing. The
short version: **moons and captures aren't yours until AP gives them to
you**, and the in-game UI is your honest indicator of what AP has sent.

### How do I know it is working?

The earliest in-game signal is **Cappy himself**. Shortly after you
acquire him in the Cap Kingdom intro, Cappy should pop a speech bubble
that reads *"Connected to Archipelago"* — that's the mod confirming the
Switch ↔ SMOClient ↔ AP-server chain is live. From that point on Cappy
will narrate item arrivals from other players (e.g. *"Got Frog from
P3!"*), and will also announce *"Disconnected from Archipelago"* if the
bridge drops, replaying anything you collected during the gap once it
reconnects.

If you never see the "Connected" bubble after Cappy joins you, check
SMOClient's Tracker tab and the AP-server log — the bridge isn't
reaching the Switch.

### Your Capture List is the source of truth for what you can capture

Cappy's in-game Capture List (the menu showing every hat-throw target
you've ever met) doubles as your **AP unlock list**. If a creature
appears there, AP has sent you its capture item and you can use it
freely. If it isn't there yet, you haven't been granted it.

You can still *try* to capture anything — see below.

### Captures you don't own snap back after ~4 seconds

Hat-toss onto a capture you have not unlocked and you'll briefly play
as the creature (Frog, Bullet Bill, T-Rex, ...) — then Mario gets yanked
back out and the enemy may despawn.

### Linear kingdom order is enforced at the two world-map forks

The apworld ships a linear kingdom chain, and the Switch mod backs it up
at SMO's two world-map fork points:

| Fork | You must collect first | Before you can go to |
| --- | --- | --- |
| After Sand Kingdom | 8 Lake Kingdom moons (AP credit) | Wooded Kingdom |
| After Metro Kingdom | 10 Snow Kingdom moons (AP credit) | Seaside Kingdom |

While a fork is gated, **both slots on the cutscene world-select will
show the prereq kingdom** (e.g. both "Lake") — pick either and you fly
to Lake. Once you have the required moons, the fork shows its real
options.

### Moons appear in the kingdom they're for

SMO's in-game moon counter (the HUD number and the Odyssey ship's fuel
gauge) only ever shows the **AP-credit balance for the kingdom you're
currently in**. Moons AP has sent you for other kingdoms are waiting
silently — fly to that kingdom and the counter will reflect them.

Practical consequences:

- Pre-existing moons from before you connected to AP won't show in the
  HUD counter. They still exist in the shine list, but they don't fund
  travel — only AP-granted moons do.
- The Odyssey ship will refuse to launch if your current-kingdom
  AP-credit balance is below the cost.
- The tracker tab in SMOClient (and the PopTracker pack, if you're
  using it) is the canonical view of what you have everywhere.

### Collecting a moon for a different kingdom: HUD blips down, then back up

When you pick up a local moon and AP routes it to another kingdom (or
to another player), you may see the HUD counter briefly tick down and
then bounce back. This is the AP-credit-only counter recomputing
around the deposit cycle, and it's **working correctly** — the
underlying balance is unchanged. Watch the moon-get cutscene's label:
it shows you exactly where the moon went (e.g. "Sent Snow Kingdom Power
Moon -> P3").

### Changing AP server or slot after setup

**Doesn't require a rebuild.** Just type `/connect <host>:<port> <slot>`
in SMOClient's command bar, or double-click a different `.smoap` file.
See [`docs/changing-servers.md`](docs/changing-servers.md) for the full
rebuild-vs-no-rebuild matrix.

### Changing bridge PC IP

**Does require a rebuild** (the IP is baked into the Switch module at
compile time — retail Switch firmware can't read runtime config from SD).
Type `/setup` in SMOClient to re-run the wizard.

## Loopback dev setup (no Switch required)

For project contributors. Brings up the full SMOClient ↔ AP loop locally
so you can validate AP-side wiring without booting Ryujinx or a Switch.
Tested against Archipelago 0.6.7 on Windows 11 + Python 3.13.

```pwsh
# 0. After fresh clone:
git submodule update --init --recursive

# 1. Dev venv + deps (the legacy bridge/.venv from before the merge is fine
#    to reuse if you have one; Archipelago's deps are a superset of what we need)
python -m venv .venv
.\.venv\Scripts\python -m pip install pytest pytest-asyncio websockets
.\.venv\Scripts\python -m pip install "setuptools<81" PyYAML pathspec jellyfish `
    colorama platformdirs certifi orjson bsdiff4 schema typing_extensions `
    "websockets==13.1"

# 2. Build the apworld zip + capture-table header
.\.venv\Scripts\python scripts\install_apworld.py
python scripts\sync_capture_table.py

# 3. Generate a test seed (single-slot, items_handling=7)
.\.venv\Scripts\python scripts\ap_generate.py `
    --player_files_path apworld\smo_archipelago\tests\seeds `
    --outputpath apworld\smo_archipelago\tests\seeds\out
# Unzip the .archipelago out of the .zip
.\.venv\Scripts\python -c "import zipfile, glob; \
    [zipfile.ZipFile(z).extractall('apworld/smo_archipelago/tests/seeds/out') for z in glob.glob('apworld/smo_archipelago/tests/seeds/out/AP_*.zip')]"

# 4. Host AP locally + launch SMOClient + drive checks (3 panes)
# Pane A
.\.venv\Scripts\python scripts\ap_server.py --port 38281 `
    apworld\smo_archipelago\tests\seeds\out\AP_*.archipelago
# Pane B — launch SMOClient (either via Launcher button or headless)
.\.venv\Scripts\python vendor\Archipelago\Launcher.py "SMO Client" `
    --connect localhost:38281 --name Mario
# Pane C — drive a fake Switch
python scripts\switch_smoke_test.py
# Expect: each `>> check` is mirrored by a `<< item` from AP within ~1s.

# Or run the regression test that scripts all of the above:
$env:SMOAP_LIVE_AP="1"
.\.venv\Scripts\python -m pytest -v apworld\smo_archipelago\tests\test_ap_loopback.py
```

## Credits

- [empathy-mp3](https://github.com/empathy-mp3/SMO-manual-AP) — original SMO Manual AP world (apworld is forked from this).
- [Amethyst-szs](https://github.com/Amethyst-szs/smo-lunakit) — LunaKit SMO mod SDK.
- [shadowninja108](https://github.com/shadowninja108/exlaunch) — exlaunch.
- [ArchipelagoMW](https://github.com/ArchipelagoMW/Archipelago) — Archipelago.

## License

See [LICENSE](LICENSE).
