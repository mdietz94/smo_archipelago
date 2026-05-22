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
# Probe the dev-checkout name first (`switch-mod`) and fall back to the
# bundled-apworld name (`switch_mod`). The apworld zip's contents must
# be valid Python module names — hyphens are illegal — so
# `install_apworld.py --bundle-mod` renames the dir to underscore-form
# when staging into `_setup/switch_mod/`. This probe lets the same
# wrapper drive both dev builds and end-user wizard builds.
SWITCH_MOD = next(
    (p for p in (
        os.path.join(REPO_ROOT, "switch-mod"),
        os.path.join(REPO_ROOT, "switch_mod"),
    ) if os.path.isdir(p)),
    os.path.join(REPO_ROOT, "switch-mod"),  # fallback used only for the error message
)
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
# Python interpreter dir. Hakkun's CMake shells out to bare `python` for
# elf2nso.py / build_npdm.py — those scripts import `lz4` (and the wizard
# pip-installs `lz4 pyelftools mmh3 --user` into a specific Python). PATH
# must put that Python first so cmake's `python` resolves to the one that
# has the packages — not whatever 3.x is first on the user's system PATH.
# Default: the dir of the interpreter running this script, which is the
# wizard's vendored Python 3.12 in wizard mode (py -3.12 was used by the
# wizard's _python_invoker) and the dev's venv in source-checkout mode.
PYTHON_BIN = os.environ.get("SMOAP_PYTHON_BIN", os.path.dirname(sys.executable))


def ensure_hakkun_patched() -> None:
    """Apply Windows-port patches to the LibHakkun submodule.

    Idempotent — re-running is cheap. The patches must land BEFORE sail
    builds (patch 1 touches sail's CMakeLists.txt; patch 2 fixes sail's
    Windows wchar_t bug; patch 3 quotes the host clang path).
    """
    patch_script = os.path.join(REPO_ROOT, "scripts", "patch_hakkun.py")
    # Hand patch_hakkun.py the resolved switch-mod path explicitly so the
    # dev-checkout vs bundled-apworld layout difference is settled in one
    # place (here). Without this, patch_hakkun.py's own REPO_ROOT-relative
    # probe would have to duplicate the same fallback logic.
    patch_env = os.environ.copy()
    patch_env["SMOAP_SWITCH_MOD_DIR"] = SWITCH_MOD
    result = subprocess.run([sys.executable, patch_script], env=patch_env)
    if result.returncode != 0:
        sys.exit("[build] patch_hakkun.py failed")


def ensure_libstd_downloaded() -> None:
    """Pre-download LibHakkun's aarch64 stdlib (musl libc + LLVM libc++ +
    compiler-rt) before cmake configures.

    Upstream `sys/cmake/toolchain.cmake` notices a missing `lib/std/*.a`
    and invokes `python3 sys/tools/setup_libcxx_prepackaged.py` to fetch
    the tarball. On Windows, bare `python3` typically resolves to the
    Microsoft Store stub at `%LOCALAPPDATA%\\Microsoft\\WindowsApps\\
    python3.exe`, which exits silently. cmake's `execute_process` captures
    the result var but never checks it, so configure proceeds with
    `STDLIB_FOUND=FALSE` and the clang link fails much later with cryptic
    "no such file" errors against the empty `lib/std/*.a` paths.

    Run the same script ourselves with the real Python interpreter
    (`sys.executable` — same one the wizard's _python_invoker resolves)
    BEFORE cmake gets a crack at it. The script's `subprocess.run(['curl',
    ...])` doesn't `check=True` either, so we verify the .a files actually
    landed afterward and surface a clear error if not (curl missing,
    network blocked, etc.).
    """
    lib_std = os.path.join(SWITCH_MOD, "lib", "std")
    required = (
        "libc.a", "libc++.a", "libc++abi.a", "libm.a",
        "libunwind.a", "libclang_rt.builtins-aarch64.a",
    )
    if all(os.path.exists(os.path.join(lib_std, name)) for name in required):
        return

    script = os.path.join(SWITCH_MOD, "sys", "tools", "setup_libcxx_prepackaged.py")
    if not os.path.exists(script):
        sys.exit(f"[build] {script} missing — sys submodule not checked out?")

    print(f"[build] lib/std/*.a missing — pre-running setup_libcxx_prepackaged.py")
    # cwd MUST be SWITCH_MOD: the script curls the tarball into cwd and
    # `tarfile.extractall('.')` from cwd. Anything else and the libs land
    # in the wrong place. sys.executable sidesteps the broken `python3`
    # PATH lookup (Microsoft Store stub on Windows).
    result = subprocess.run([sys.executable, script], cwd=SWITCH_MOD)
    if result.returncode != 0:
        sys.exit(f"[build] setup_libcxx_prepackaged.py exited {result.returncode}")

    missing = [n for n in required if not os.path.exists(os.path.join(lib_std, n))]
    if missing:
        sys.exit(
            f"[build] setup_libcxx_prepackaged.py returned 0 but did not "
            f"produce {missing} under {lib_std}. The script's `curl` step "
            f"may have failed silently (no `check=True` upstream). Check "
            f"network connectivity and that `curl` is on PATH."
        )


def ensure_sail_built() -> None:
    """Sail is a Windows-native host binary built once per machine.

    `sail.cmake` looks for the binary at `switch-mod/hakkun/sys/sail/build/sail`
    (no .exe). If it doesn't exist, cmake re-runs setup_sail.py during
    configure, which rmtree's the build dir — meaning even a freshly-built
    sail.exe disappears on the next configure unless the no-extension copy
    exists.

    The cmake fallback in `sys/cmake/sail.cmake:18` invokes bare `python3
    sys/tools/setup_sail.py` with RESULT_VARIABLE captured-but-unchecked
    — same shape as the libstd bug, silent on the Microsoft Store stub.
    Pre-running here with sys.executable sidesteps that, and the
    postcondition check below mirrors `ensure_libstd_downloaded()`: the
    upstream setup_sail.py script doesn't use `check=True` on its own
    cmake/ninja subprocess calls, so a returncode of 0 plus an empty
    output dir is a real failure mode worth surfacing explicitly.
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
        if not os.path.exists(sail_exe):
            sys.exit(
                f"[build] setup_sail_winpath.py returned 0 but did not "
                f"produce {sail_exe}. The upstream setup_sail.py invokes "
                f"cmake/ninja without check=True, so a silent toolchain "
                f"failure (mingw g++ missing, generator mismatch) can "
                f"leave the dir empty. Check the build log above."
            )

    if not os.path.exists(sail_noext) and os.path.exists(sail_exe):
        shutil.copy2(sail_exe, sail_noext)


def configure_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([PYTHON_BIN, LLVM_BIN, CMAKE_BIN, NINJA_BIN, MINGW_BIN, env.get("PATH", "")])
    env["CMAKE_GENERATOR"] = "Ninja"
    return env


def main() -> int:
    if not os.path.isdir(SWITCH_MOD):
        sys.exit(f"[build] {SWITCH_MOD} does not exist — phase 1 hasn't run yet")

    ensure_hakkun_patched()
    ensure_libstd_downloaded()
    ensure_sail_built()

    env = configure_env()
    cmake = os.path.join(CMAKE_BIN, "cmake.exe")

    # Clean reconfigure each call — incremental is unreliable across sail
    # re-runs that wipe the build dir.
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)

    # Extra cmake args (after the script name) are forwarded to the configure
    # call. Pass at minimum `-DBRIDGE_HOST=<PC LAN IP>` (no default; CMake
    # aborts if missing). ApDiscovery uses that IP's /24 as the unicast sweep
    # range — the actual SMOClient might be on a neighbouring octet after a
    # DHCP renumber, the sweep covers that.
    #   python scripts/build_switchmod.py -DBRIDGE_HOST=192.168.1.42
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
