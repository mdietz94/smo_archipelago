#!/usr/bin/env python3
"""Build switch-mod/ (LibHakkun-based subsdk9) with Windows-native CMake + LLVM + Ninja.

Wraps the CMake invocation so it works on Windows out of the box — Hakkun
upstream assumes Linux paths in several places (msys2 cmake on PATH first
breaks the build; sail binary lacks .exe extension; setup_libcxx output
location resolves wrong from a bash cwd; etc.). This wrapper handles all of
that.

Run from anywhere; this script always operates against switch-mod/ next to
itself in the repo.
"""

import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWITCH_MOD = os.path.join(REPO_ROOT, "switch-mod")
BUILD_DIR = os.path.join(SWITCH_MOD, "build")

# Windows-native binary directories. Each can be overridden via the matching
# SMOAP_* env var; the defaults match a dev machine that installed everything
# via winget + msys2 by hand. The wizard sets these env vars to point at the
# portable installs under %LOCALAPPDATA%\SMOArchipelago\{llvm,winlibs}\.
# LLVM 19 is ABI-pinned by LibHakkun's libc++ headers.
CMAKE_BIN = os.environ.get("SMOAP_CMAKE_BIN", r"C:\Program Files\CMake\bin")
LLVM_BIN = os.environ.get("SMOAP_LLVM_BIN", r"C:\Program Files\LLVM\bin")
NINJA_BIN = os.environ.get(
    "SMOAP_NINJA_BIN",
    r"C:\Users\maxwe\AppData\Local\Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe",
)
# Host C++ compiler (gcc/g++) for sail. The wizard points this at the
# WinLibs portable install; the default keeps a hand-installed msys2
# working for repo devs.
MINGW_BIN = os.environ.get("SMOAP_MINGW_BIN", r"C:\msys64\mingw64\bin")


def ensure_hakkun_patched() -> None:
    """Apply Windows-port patches to the LibHakkun submodule.

    Idempotent — re-running is cheap. The patches must land BEFORE sail
    builds (patch 1 touches sail's CMakeLists.txt; patch 2 fixes sail's
    Windows wchar_t bug; patch 3 quotes the host clang path).
    """
    patch_script = os.path.join(REPO_ROOT, "scripts", "patch_hakkun.py")
    result = subprocess.run([sys.executable, patch_script])
    if result.returncode != 0:
        sys.exit("[build] patch_hakkun.py failed")


def ensure_sail_built() -> None:
    """Sail is a Windows-native host binary built once per machine.

    `sail.cmake` looks for the binary at `switch-mod/hakkun/sys/sail/build/sail`
    (no .exe). If it doesn't exist, cmake re-runs setup_sail.py during
    configure, which rmtree's the build dir — meaning even a freshly-built
    sail.exe disappears on the next configure unless the no-extension copy
    exists.
    """
    sail_dir = os.path.join(SWITCH_MOD, "sys", "sail", "build")
    sail_exe = os.path.join(sail_dir, "sail.exe")
    sail_noext = os.path.join(sail_dir, "sail")

    if not os.path.exists(sail_exe):
        print(f"[build] sail not yet built — running setup_sail_winpath.py")
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "setup_sail_winpath.py")],
            cwd=SWITCH_MOD,
        )
        if result.returncode != 0:
            sys.exit("[build] sail build failed")

    if not os.path.exists(sail_noext) and os.path.exists(sail_exe):
        shutil.copy2(sail_exe, sail_noext)


def configure_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([LLVM_BIN, CMAKE_BIN, NINJA_BIN, MINGW_BIN, env.get("PATH", "")])
    env["CMAKE_GENERATOR"] = "Ninja"
    return env


def main() -> int:
    if not os.path.isdir(SWITCH_MOD):
        sys.exit(f"[build] {SWITCH_MOD} does not exist — phase 1 hasn't run yet")

    ensure_hakkun_patched()
    ensure_sail_built()

    env = configure_env()
    cmake = os.path.join(CMAKE_BIN, "cmake.exe")

    # Clean reconfigure each call — incremental is unreliable across sail
    # re-runs that wipe the build dir.
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)

    # Extra cmake args (after the script name) are forwarded to the configure
    # call. Use this to override BRIDGE_HOST / BRIDGE_PORT / SMO_AP_MOD_VERSION
    # without editing CMakeLists.txt. Example:
    #   python scripts/build_switchmod.py -DBRIDGE_HOST=127.0.0.1
    cmake_extra = sys.argv[1:]
    cfg = subprocess.run(
        [cmake, "-S", SWITCH_MOD, "-B", BUILD_DIR, "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", *cmake_extra],
        env=env,
    )
    if cfg.returncode != 0:
        return cfg.returncode

    bld = subprocess.run([cmake, "--build", BUILD_DIR, "-j", "8"], env=env)
    return bld.returncode


if __name__ == "__main__":
    sys.exit(main())
