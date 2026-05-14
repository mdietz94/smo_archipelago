# SMO Archipelago

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
| M5 | Web tracker |
| M6 | Item application |
| M7 | Capture lock + goal |
| M8 | Apworld extensions, in-game ImGui, polish |

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
