# Spicy Meatball Overdrive

A real Archipelago client for **Super Mario Odyssey** on a modded Nintendo Switch.

Today the SMO Archipelago experience is a *Manual* client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system checklist where players tick boxes by hand. This project replaces the honor system with an in-game module that:

- Detects moons / captures / scenario events on Switch and reports them as AP location checks.
- Receives AP items (moons, captures, kingdoms, shop unlocks) and applies them to the live game.
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
| `apworld/smo_archipelago/tests/` | Unit + integration tests (113 pass, 55 skipped — live-AP gated). |
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

## Loopback dev setup (no Switch required)

This brings up the full SMOClient ↔ AP loop locally so you can validate AP-side wiring without booting Ryujinx or a Switch. Tested against Archipelago 0.6.7 on Windows 11 + Python 3.13.

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

## Quick start (typical user flow)

```pwsh
# 1. Install your forked apworld into Archipelago
.\.venv\Scripts\python scripts\install_apworld.py

# 2. Edit per-user settings in ~/.archipelago/host.yaml (optional —
#    defaults work for localhost AP)
# smo_options:
#   switch_listen_port: 17777
#   deathlink_default: false

# 3. Launch the client via the Archipelago Launcher
.\.venv\Scripts\python vendor\Archipelago\Launcher.py
# → click "SMO Client" in the GUI

# 4. On Switch:
#    - Build switch-mod (see docs/build-windows.md)
#    - Copy switch-mod/sd-overlay/ to SD card root
#    - Edit sd:/atmosphere/contents/0100000000010000/romfs/ap_config.json with the SMOClient IP
#    - Launch SMO
```

## Credits

- [empathy-mp3](https://github.com/empathy-mp3/SMO-manual-AP) — original SMO Manual AP world (apworld is forked from this).
- [Amethyst-szs](https://github.com/Amethyst-szs/smo-lunakit) — LunaKit SMO mod SDK.
- [shadowninja108](https://github.com/shadowninja108/exlaunch) — exlaunch.
- [ArchipelagoMW](https://github.com/ArchipelagoMW/Archipelago) — Archipelago.

## License

See [LICENSE](LICENSE).
