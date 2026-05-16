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

The module compiles cleanly via the lunakit toolchain. Build steps:

Prerequisites:

- devkitPro / devkitA64 — install via the Windows installer at <https://devkitpro.org/wiki/Getting_Started>.
  - The installer sets `DEVKITPRO`, `DEVKITA64`, `DEVKITARM` env vars. Verify in a fresh shell:
    ```pwsh
    echo $env:DEVKITPRO
    & "$env:DEVKITPRO\devkitA64\bin\aarch64-none-elf-gcc.exe" --version
    ```
- CMake 3.24 or newer. `winget install Kitware.CMake`
- Ninja. `winget install Ninja-build.Ninja`
- Ghidra 11.x — only needed for M0 symbol discovery. <https://ghidra-sre.org>

Bootstrap (one time):

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\switch-mod
git submodule add https://github.com/shadowninja108/exlaunch.git exlaunch
git submodule add https://github.com/Amethyst-szs/smo-lunakit.git lunakit-vendor
git submodule update --init --recursive
```

Configure & build:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\switch-mod
cmake -S . -B build -G Ninja `
      -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake `
      -DSMO_VERSION=1.0.0
cmake --build build
cmake --install build      # populates ../sd-overlay/
```

Output: `switch-mod/sd-overlay/atmosphere/contents/0100000000010000/exefs/subsdk9` and `romfs/ap_config.json`.

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
