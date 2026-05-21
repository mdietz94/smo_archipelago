#!/usr/bin/env python3
"""Build sail (LibHakkun's symbol-DB host binary) on Windows.

Sail is a C++23 program that runs on the *host* (not the Switch). LibHakkun's
upstream `tools/setup_sail.py` assumes Linux. On Windows we need:

  - Windows-native CMake (not msys2 cmake which uses POSIX path resolution).
  - mingw64 g++ as host compiler (the LLVM clang we use for the Switch target
    is configured for aarch64-none-elf and can't link a Windows host binary
    without a full MSVC SDK).
  - `-DCMAKE_C/CXX_COMPILER` overrides because sail/CMakeLists.txt sets
    `clang`/`clang++` after `project()`.
  - Patched sail sources to handle Windows `std::filesystem::path` returning
    wchar_t* on Windows, and to quote the clangBinary path in popen.

This script runs the upstream setup_sail.py with the right PATH + env.
Patches to the sail source are applied by scripts/patch_hakkun.py — run that
first if sail fails to compile.
"""

import os
import subprocess
import sys

# Each binary dir is overridable via the matching SMOAP_* env var so the
# wizard can point MINGW_BIN at the WinLibs portable install. Defaults match
# a winget + msys2 hand-install so repo devs keep working unchanged.
CMAKE_BIN = os.environ.get("SMOAP_CMAKE_BIN", r"C:\Program Files\CMake\bin")
NINJA_BIN = os.environ.get(
    "SMOAP_NINJA_BIN",
    r"C:\Users\maxwe\AppData\Local\Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe",
)
MINGW_BIN = os.environ.get("SMOAP_MINGW_BIN", r"C:\msys64\mingw64\bin")

# The script runs from switch-mod/ (cwd contains `hakkun/` submodule).
SWITCH_MOD = os.getcwd()
HAKKUN_SETUP = os.path.join(SWITCH_MOD, "sys", "tools", "setup_sail.py")

if not os.path.exists(HAKKUN_SETUP):
    sys.exit(f"[setup_sail] upstream setup_sail.py not found at {HAKKUN_SETUP}; is the hakkun submodule checked out?")

env = os.environ.copy()
env["PATH"] = os.pathsep.join([MINGW_BIN, CMAKE_BIN, NINJA_BIN, env.get("PATH", "")])
env["CMAKE_GENERATOR"] = "Ninja"
env["CC"] = "gcc"
env["CXX"] = "g++"

# Upstream setup_sail.py uses os.getcwd() as the root, then builds at
# {root}/sys/sail/build. We must invoke it with cwd = directory that *contains*
# a `sys/sail/` subdir. In our layout, switch-mod/ contains `sys/` (the
# LibHakkun submodule), and LibHakkun's setup_sail expects `cwd/sys/sail/`,
# so cwd = SWITCH_MOD works because SWITCH_MOD/sys is LibHakkun's `sail/`
# parent. Wait — LibHakkun's own setup_sail.py does
# `project_dir = f'{root_dir}/sys/sail'`, which from SWITCH_MOD gives
# `switch-mod/sys/sail` — which is correct.
HAKKUN_ROOT = SWITCH_MOD

result = subprocess.run([sys.executable, HAKKUN_SETUP], env=env, cwd=HAKKUN_ROOT)
sys.exit(result.returncode)
