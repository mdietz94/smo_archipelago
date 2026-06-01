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

# Force stdout/stderr to UTF-8 before any print runs. Several of the
# diagnostic strings below contain em-dashes (U+2014), which raise
# UnicodeEncodeError under Windows code pages that lack them (cp932 on
# JP locale, cp949 on KR, cp936 on simplified Chinese). The wizard's
# `_stream_subprocess` already plumbs PYTHONIOENCODING=utf-8 into the
# env when it spawns us, but this guard keeps standalone invocations
# (devs running `python scripts/build_switchmod.py` directly) from
# crashing on the same locale. Python 3.7+ exposes `.reconfigure`.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
# Propagate UTF-8 to every child Python we spawn below (patch_hakkun.py,
# setup_libcxx_prepackaged.py, setup_sail_winpath.py). Without this, each
# child inherits the JP/KR/CN locale and crashes on its own em-dashes.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

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

    `sys/cmake/sail.cmake` looks for the binary at
    `switch-mod/sys/sail/build/sail` (no .exe). If it doesn't exist we
    shell out to `scripts/setup_sail_winpath.py`, which builds sail via
    cmake + ninja with mingw64 g++. After the build we copy sail.exe to
    `sail` (no extension) because that's the literal filename baked into
    sail.cmake's `SAIL_BIN` variable.

    Pin note: when sys is at LibHakkun main HEAD (9892726+) sail lives at
    `sys/sail/`. The 2026-05-22 imgui-dev-branch pin (e92ac56) briefly
    relocated it to `sys/hakkun/sail/`; that bump was reverted because
    the imgui branch lacked features (devkitPro-free NPDM, the upstream
    trampoline relocator) that main has. If you re-pin to a branch that
    moves sail again, audit this script + setup_sail_winpath.py together.
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
                f"produce {sail_exe}. The cmake/ninja subprocess in that "
                f"wrapper can leave the dir empty on silent toolchain "
                f"failure (mingw g++ missing, ninja not on PATH). Check "
                f"the build log above."
            )

    if not os.path.exists(sail_noext) and os.path.exists(sail_exe):
        shutil.copy2(sail_exe, sail_noext)


def _ensure_python3_on_path(env: dict) -> None:
    """Ensure ``python3.exe`` is reachable in the cmake subprocess PATH.

    LibHakkun's toolchain.cmake runs bare ``python3
    sys/tools/setup_libcxx_prepackaged.py`` to unpack lib/std/*.a at
    configure time.  On Windows, standard CPython installs ship
    ``python.exe`` but NOT ``python3.exe``.  The Microsoft Store stub
    that answers ``python3`` silently exits without writing anything, so
    ``lib/std/`` is never populated and the cmake compiler-check link
    fails with missing *.a.

    ``ensure_libstd_downloaded()`` above already pre-runs the download
    with ``sys.executable`` before cmake gets a crack at it, but this
    shim is a second line of defence for any other ``python3`` calls
    cmake may make (and for re-configure runs where the libs already
    exist and ``ensure_libstd_downloaded`` returns early).

    If ``python3.exe`` still isn't present in ``PYTHON_BIN``, we create
    the shim in ``%LOCALAPPDATA%/SMOArchipelago/bin/`` (always writable)
    and prepend that directory so it shadows any Store stub on the
    inherited PATH.
    """
    if sys.platform != "win32":
        return
    if os.path.isfile(os.path.join(PYTHON_BIN, "python3.exe")):
        return  # shim already in place — nothing to do

    py_exe = os.path.join(PYTHON_BIN, "python.exe")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if not local_appdata or not os.path.isfile(py_exe):
        return
    shim_dir = os.path.join(local_appdata, "SMOArchipelago", "bin")
    shim = os.path.join(shim_dir, "python3.exe")
    if not os.path.isfile(shim):
        try:
            os.makedirs(shim_dir, exist_ok=True)
            shutil.copy2(py_exe, shim)
        except OSError:
            return  # cmake will surface a clear error about python3
    path_val = env.get("PATH", "")
    if shim_dir not in path_val.split(os.pathsep):
        env["PATH"] = shim_dir + os.pathsep + path_val


def configure_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([PYTHON_BIN, LLVM_BIN, CMAKE_BIN, NINJA_BIN, MINGW_BIN, env.get("PATH", "")])
    env["CMAKE_GENERATOR"] = "Ninja"
    _ensure_python3_on_path(env)
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
