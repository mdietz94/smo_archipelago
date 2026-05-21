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

The module is built on LibHakkun + OdysseyHeaders + sail.

Prerequisites:

- **LLVM 19** — `winget install LLVM.LLVM --version 19.1.7`. Lands at `C:\Program Files\LLVM\bin`. The aarch64 target ships with LLVM out of the box.
- **CMake 3.24+** — `winget install Kitware.CMake`.
- **Ninja** — `winget install Ninja-build.Ninja`.
- **msys2 + mingw64 g++** — used only to build the host-side `sail.exe`. `winget install MSYS2.MSYS2`, then in a msys2 shell: `pacman -S mingw-w64-x86_64-gcc`. Installs at `C:\msys64\mingw64\bin`.
- **Python 3.12+** plus pip packages: `pip install --user pyelftools mmh3 lz4`.

Bootstrap (one time):

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
git submodule update --init --recursive switch-mod/sys switch-mod/lib/OdysseyHeaders
```

Configure & build:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
python scripts\build_switchmod.py
```

The wrapper script applies the 10 Windows-port patches to the pinned LibHakkun submodule (idempotent), builds `sail.exe` if needed, and runs the LLVM + ninja build.

Output: `switch-mod/build/sd/atmosphere/contents/0100000000010000/exefs/subsdk9` (and `main.npdm` next to it). Pass `-DBRIDGE_HOST=...` etc. through the wrapper to override the bridge target at configure time.

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
