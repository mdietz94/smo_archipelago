---
name: smo-loopback-test
description: Run the AP loopback end-to-end test for SMO without booting SMO/Ryujinx — validates the whole Switch↔SMOClient↔AP server stack. Use when the user asks for "loopback", "AP loopback", "test seed", "ap_server", "ap_generate", or "switch_smoke_test"; when validating the bridge stack after a wire-protocol change; or when the user wants to verify SMOClient routing without booting the emulator. Covers seed generation, AP server hosting, SMOClient launch, fake-Switch driver, and the equivalent pytest path.
---

# AP loopback dev test

Validates the whole Switch↔Client↔AP stack without booting SMO. The fake-Switch driver simulates `MoonGetHook` traffic; the AP server is hosted locally; SMOClient is the real binary launched via Archipelago's Launcher.

## Prerequisites

- `.venv/Scripts/python` exists at the repo root (Archipelago's deps are a superset of what SMOClient needs; reuse it).
- `vendor/Archipelago/` submodule initialized (`git submodule update --init --recursive`).
- `apworld/smo_archipelago/client/data/{shine_map,capture_map}.json` present (gitignored; copy from main checkout if working in a worktree, or generate via the `smo-extract-data` skill).

### Bootstrapping a fresh dev venv

If `.venv/` is missing (fresh clone, new contributor), set one up.
Tested against Archipelago 0.6.7 on Windows 11 + Python 3.13:

```pwsh
python -m venv .venv
.\.venv\Scripts\python -m pip install pytest pytest-asyncio websockets
.\.venv\Scripts\python -m pip install "setuptools<81" PyYAML pathspec jellyfish `
    colorama platformdirs certifi orjson bsdiff4 schema typing_extensions `
    "websockets==13.1"
```

Archipelago's `setup.py` blocks `pip install`; this list is the minimum subset
needed to run `ap_generate.py`, `ap_server.py`, and the SMOClient Launcher entry.

## Step-by-step (3 panes)

```pwsh
# Build apworld zip (re-run after any apworld/client/__init__.py change)
.\.venv\Scripts\python scripts\install_apworld.py
```

If working in a `.claude/worktrees/<name>/` worktree AND the user launches SMOClient from the main checkout's Launcher, also `Copy-Item` the freshly-built zip to the main checkout's `vendor/Archipelago/custom_worlds/meatballs.apworld`. Symptom of skipping: `unknown message type from Switch: <type>` in bridge log. See the `smo-build` skill for the full Copy-Item form.

```pwsh
# Generate test seed (one-time per apworld change)
.\.venv\Scripts\python scripts\ap_generate.py `
    --player_files_path apworld\smo_archipelago\tests\seeds `
    --outputpath apworld\smo_archipelago\tests\seeds\out

# Unzip the .archipelago server file out of the player zip
.\.venv\Scripts\python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('apworld/smo_archipelago/tests/seeds/out') for z in glob.glob('apworld/smo_archipelago/tests/seeds/out/AP_*.zip')]"
```

```pwsh
# Pane A: host server
.\.venv\Scripts\python scripts\ap_server.py --port 38281 `
    apworld\smo_archipelago\tests\seeds\out\AP_*.archipelago
```

```pwsh
# Pane B: launch SMO Client — connects to localhost
.\.venv\Scripts\python vendor\Archipelago\Launcher.py "SMO Client" `
    --connect localhost:38281 --name Mario
```

```pwsh
# Pane C: drive a fake Switch
python scripts\switch_smoke_test.py
# Expect: each `>> check` mirrored by a `<< item` within ~1s
```

## Scripted path (pytest)

```pwsh
$env:SMOAP_LIVE_AP="1"
.\.venv\Scripts\python -m pytest -v `
    apworld\smo_archipelago\tests\test_ap_loopback.py
```

Test is gated on `SMOAP_LIVE_AP=1`; absent the var it auto-skips.

## Quick old-style smoke test

Switch-only, no AP server — just exercises the SwitchServer's recv/dispatch path:

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\switch_smoke_test.py
```

Run with SMOClient already up (no AP needed; the Client's SwitchServer accepts the fake-Switch connection on `:17777`).

## Connect behavior

SMOClient does NOT auto-dial AP on launch (default host is unset to avoid `archipelago.gg` "Connection refused" before the user configures anything), but Click-Connect dials AP immediately whether or not the Switch is up. The user can validate creds and watch items flow before booting SMO. The earlier SNI-style gate that parked the dial until HELLO was removed 2026-05-22 — see the "Eager AP dial" row in CLAUDE.md's decisions table.

For the loopback flow above, AP connection proceeds the moment Connect fires, regardless of when `switch_smoke_test.py` sends HELLO.

## Settings overrides

SMO Client listens on `0.0.0.0:17777` by default. Override via `~/.archipelago/host.yaml`:

```yaml
meatballs_options:
  switch_listen_host: "0.0.0.0"
  switch_listen_port: 17777
  shine_map_path: ""          # empty falls back to client/data/shine_map.json
  capture_map_path: ""
  deathlink_default: false
```

Or per-launch: `--switch-port 17777`. The host.yaml key is `meatballs_options` (derived from the shipped apworld zip stem `meatballs`), NOT from the AP game name `Spicy Meatball Overdrive`.

## Common gotchas

- **Stale `AP_*.apsave`** in the seeds/out/ dir can carry over locations_checked from a previous run; if `report_check` looks like it's working but no item arrives, delete the `.apsave` and re-run.
- **`AP-server KeyError on scout for missing locations`** — fix at [context.py](../apworld/smo_archipelago/client/context.py): warmup scopes to `ctx.missing_locations | ctx.checked_locations`, not the full datapackage. Otherwise a single not-in-this-slot location_id kills the websocket → client reconnect loop.
