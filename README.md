# Spicy Meatball Overdrive

A real Archipelago client for **Super Mario Odyssey** on a modded Nintendo Switch.

Today the SMO Archipelago experience is a *Manual* client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system checklist where players tick boxes by hand. This project replaces the honor system with an in-game module that:

- Detects moons / captures / scenario events on Switch and reports them as AP location checks.
- Receives AP items (moons, captures, kingdoms, shop unlocks) and applies them to the live game.
- Enforces capture locks (cannot possess Frog/Yoshi/T-Rex/etc. until the AP item is received).
- Surfaces progress through a web tracker (priority) and an in-game ImGui overlay (later).

## Architecture

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Bridge (Python) ]  <--websocket-->  [ AP server ]
   exlaunch                                CommonContext                          archipelago.gg
   LunaKit hooks                           web tracker (Flask)                   or self-host
   ImGui overlay                           forked apworld
```

The PC bridge owns AP-protocol complexity (websocket, deflate, TLS, reconnect). The Switch speaks a small line-delimited JSON protocol over a single TCP socket.

## Project layout

| Path | Purpose |
| --- | --- |
| `apworld/smo_archipelago/` | Forked from `manual_smo_mp3`. Generates seeds. |
| `bridge/` | Python bridge. Connects to AP server, serves Switch + web tracker. |
| `switch-mod/` | exlaunch C++ module. Hooks SMO; produces `subsdk9` ELF. |
| `docs/` | Architecture, wire protocol, build, install, symbol catalog. |
| `scripts/` | Build helpers (apworld sync, capture-table sync, release packaging). |

## Status

Pre-alpha. Tracking against milestones M0-M8 — see [`docs/architecture.md`](docs/architecture.md) and the plan file at `~/.claude/plans/`.

| Milestone | What works |
| --- | --- |
| M0 | Toolchain + SMO 1.0.0 symbol map |
| M1 | Bridge skeleton |
| M2 | Apworld parity fork |
| M3 | Switch module skeleton |
| M4 | Read-only state mirroring (moons, captures) |
| M4.5 | State reconciliation across disconnects |
| M5 | Web tracker |
| **M5.5** | **AP server live integration (PC-only loopback validated)** |
| M5.7 | Ryujinx E2E |
| M6 | Item application |
| M7 | Capture lock + goal |
| M8 | Apworld extensions, in-game ImGui, polish |

## Loopback dev setup (no Switch required)

This brings up the full bridge ↔ AP loop locally so you can validate AP-side wiring without booting Ryujinx or a Switch. Tested against Archipelago 0.6.7 on Windows 11 + Python 3.13.

```pwsh
# 0. After fresh clone:
git submodule update --init --recursive

# 1. Bridge venv + deps
python -m venv bridge/.venv
bridge/.venv/Scripts/python -m pip install -r bridge/requirements.txt
bridge/.venv/Scripts/python -m pip install pytest pytest-asyncio
# Archipelago needs these for network/multiserver — kivy/Pymem/dolphin-* are NOT needed
bridge/.venv/Scripts/python -m pip install "setuptools<81" PyYAML pathspec jellyfish `
    colorama platformdirs certifi orjson bsdiff4 schema typing_extensions `
    "websockets==13.1"

# 2. Build the apworld zip + capture-table header
bridge/.venv/Scripts/python scripts/install_apworld.py
python scripts/sync_capture_table.py

# 3. Generate a test seed (single-slot, items_handling=7)
bridge/.venv/Scripts/python scripts/ap_generate.py `
    --player_files_path bridge/test_seeds `
    --outputpath bridge/test_seeds/out
# Unzip the .archipelago out of the .zip
bridge/.venv/Scripts/python -c "import zipfile, glob; \
    [zipfile.ZipFile(z).extractall('bridge/test_seeds/out') for z in glob.glob('bridge/test_seeds/out/AP_*.zip')]"

# 4. Host AP locally + run the bridge + drive checks (3 panes)
# Pane A
bridge/.venv/Scripts/python scripts/ap_server.py --port 38281 bridge/test_seeds/out/AP_*.archipelago
# Pane B
copy bridge\config.example.toml bridge\config.local.toml  # then edit host=localhost slot=Mario
bridge/.venv/Scripts/python -m smo_ap_bridge --config bridge/config.local.toml --log-level INFO
# Pane C
python scripts/bridge_smoke_test.py
# Expect: each `>> check` is mirrored by a `<< item` from AP within ~1s.

# Or run the regression test that scripts all of the above:
SMOAP_LIVE_AP=1 bridge/.venv/Scripts/python -m pytest -v bridge/tests/test_ap_loopback.py
```

## Quick start (when M3+ is done)

```pwsh
# PC
cd bridge
copy config.example.toml config.toml   # edit slot/server
python -m smo_ap_bridge --config config.toml --web-tracker
# Open http://localhost:8000 for tracker

# Switch
# 1. Build switch-mod (see docs/build-windows.md)
# 2. Copy switch-mod/sd-overlay/ to SD card root
# 3. Edit sd:/atmosphere/contents/0100000000010000/romfs/ap_config.json with bridge IP
# 4. Launch SMO
```

## Credits

- [empathy-mp3](https://github.com/empathy-mp3/SMO-manual-AP) — original SMO Manual AP world (apworld is forked from this).
- [Amethyst-szs](https://github.com/Amethyst-szs/smo-lunakit) — LunaKit SMO mod SDK.
- [shadowninja108](https://github.com/shadowninja108/exlaunch) — exlaunch.
- [ArchipelagoMW](https://github.com/ArchipelagoMW/Archipelago) — Archipelago.

## License

See [LICENSE](LICENSE).
