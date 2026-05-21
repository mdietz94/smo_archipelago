# Build the bridge and Switch module on Windows

Tested on Windows 11 with PowerShell. Most of the bridge instructions also work on macOS / Linux unchanged.

## Bridge (Python)

Prerequisites:

- Python 3.11 or newer (3.12 recommended). `winget install Python.Python.3.12`
- Git. `winget install Git.Git`

Setup:

```pwsh
# 1. Clone Archipelago into vendor/ as a submodule.
#    (Archipelago refuses pip install — its setup.py blocks it. We add it as
#    a submodule and the bridge auto-finds it via sys.path injection.)
cd C:\Users\maxwe\Documents\smo_archipelago
git submodule add https://github.com/ArchipelagoMW/Archipelago.git vendor/Archipelago
git submodule update --init --recursive

# 2. Install bridge requirements
cd bridge
python -m pip install -r requirements.txt

# 3. Configure
copy config.example.toml config.toml
# Edit config.toml: ap.host, ap.slot, ap.password if any
```

If you want Archipelago somewhere else (existing checkout you keep up to date), point the bridge at it via the `--archipelago` CLI flag, the `SMOAP_AP_PATH` env var, or `bridge.archipelago_path` in `config.toml`. The bridge resolves in that order of precedence.

Run:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
python -m smo_ap_bridge --config config.toml --web-tracker
```

Open <http://localhost:8000> for the tracker UI.

## Switch module (C++)

The module is a Hakkun-based subsdk9 (post-Hakkun-cutover, 2026-05-21). Build via the wrapper script `scripts/build_switchmod.py`.

Prerequisites:

- LLVM 19 (not 20 — LibHakkun pins ABI to libc++ 19). `winget install LLVM.LLVM --version 19.1.7`. Verify:
  ```pwsh
  & "C:\Program Files\LLVM\bin\clang.exe" --version
  ```
- CMake 3.24 or newer. `winget install Kitware.CMake`
- Ninja. `winget install Ninja-build.Ninja`
- msys2 with mingw64 g++ (for sail's host-binary compile). `winget install MSYS2.MSYS2`, then `pacman -S mingw-w64-x86_64-gcc`.
- Python 3.11 or newer with `pyelftools`, `mmh3`, `lz4`: `pip install pyelftools mmh3 lz4`.

Bootstrap (one time per worktree):

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
git submodule update --init --recursive switch-mod/sys switch-mod/lib/OdysseyHeaders
```

`switch-mod/sys` is LibHakkun (github.com/fruityloops1/LibHakkun); `switch-mod/lib/OdysseyHeaders` is MonsterDruide1's vendored SMO 1.0.0 forward-decl headers. `--recursive` is required to pull LibHakkun's nested `tools/senobi` submodule.

Configure & build:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
python scripts\build_switchmod.py
```

The script:
1. Applies LibHakkun's Windows-port patches via `scripts/patch_hakkun.py` (idempotent).
2. Builds sail once per machine if `switch-mod/sys/sail/build/sail.exe` is missing.
3. Runs CMake configure + ninja build with the LLVM 19 + CMake + Ninja toolchain on PATH.

Output: `switch-mod/build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9` + `main.npdm`.

Override `-DBRIDGE_HOST` / `-DBRIDGE_PORT` per-machine by passing them after the script name (forwarded to CMake configure):

```pwsh
python scripts\build_switchmod.py -DBRIDGE_HOST=127.0.0.1
```

## Sync the capture table

After updating `apworld/smo_archipelago/data/items.json`, regenerate the bit-index table the Switch uses:

```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_capture_table.py
```

(There's also a PowerShell version `sync_capture_table.ps1` if your execution policy permits it.)

## Tests

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
python -m pip install pytest pytest-asyncio
python -m pytest -v
```

## Loopback smoke test (no Switch needed)

In one shell:
```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
python -m smo_ap_bridge --no-web-tracker
```

In another shell:
```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\bridge_smoke_test.py
```

The fake-Switch script connects, sends `hello`, and emits a synthetic `check` every 5s. Bridge logs should show the handshake and your bridge's AP-server status.
