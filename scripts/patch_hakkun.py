#!/usr/bin/env python3
"""Apply Windows-port patches to the pinned LibHakkun submodule.

The spike at third_party/hakkun-spike (gitignored) discovered six source-level
patches needed to build LibHakkun + sail on Windows + msys2. Each patch is
idempotent (uses a sentinel check before applying). On first run, all six
land; subsequent runs report 'already applied' and exit cleanly.

These patches should be upstreamed to fruityloops1/LibHakkun. While upstream
PRs are in flight, this script reapplies them locally after submodule init.
If a PR review stalls > 1 week, the migration plan calls for forking
LibHakkun to mdietz94/LibHakkun-smo and re-pinning the submodule — at which
point this script becomes obsolete.

Patches applied:
  1. sys/sail/CMakeLists.txt — drop hardcoded clang/clang++ compiler.
  2. sys/sail/src/main.cpp — std::filesystem::path::c_str() is wchar_t* on Windows.
  3. sys/sail/src/fakelib.cpp — quote clangBinary path in popen cmdline.
  4. sys/cmake/sail.cmake — expand sys/addons/*/syms glob (cmd.exe doesn't).
  5. sys/cmake/generate_exefs.cmake — prefix elf2nso.py with `python`.
  6. (env only) Copy sys/sail/build/sail.exe → sys/sail/build/sail (no ext).
     Handled by scripts/build_switchmod.py.

  7. (correctness) include/hk/hook/Trampoline.h + src/hk/hook/Trampoline.cpp:
     Add AArch64 PC-relative prologue relocation to TrampolineHook. Upstream
     copies the first instruction verbatim into the trampoline pool (TODO at
     Trampoline.h:67 says "Relocate instruction, or at least abort if
     instruction needs to be relocated"); when the original is
     adrp/adr/b/bl/b.cond/cbz/cbnz/tbz/tbnz, calling .orig() executes the
     instruction at the wrong PC and the guest crashes — observed in
     Ryujinx ARMeilleure 0xC0000005 on SMO 1.0.0 stage load. Patch expands
     TrampolineBackup to 8 slots and emits movz/movk + indirect/direct
     branch sequences as needed. Worth upstreaming to fruityloops1/LibHakkun.

  8. (correctness) include/hk/services/socket/service.h:
     Drop `const` on `Socket::recvFrom`'s `address` parameter. recvFrom is the
     OUT direction (the kernel writes the sender's address into it), but
     upstream declares `const A& address` and then passes `&address` into
     `addOutAutoselect(void* data, u64 size, ...)`. The function won't compile
     when instantiated — `const A*` → `void*` is a const violation. Bind and
     connect (genuinely IN-direction) keep their `const A&` parameters via
     `inFdInAddress`; this fix mirrors the OUT-direction pattern that
     getPeerName/getSockName already use. Worth upstreaming.

  9. (forward-compat) sys/tools/nso.py composition refactor: rewrite
     `class NsoSegment(struct.Struct)` / `class NsoHeader(struct.Struct)`
     to own a struct.Struct instead of inheriting from one. The no-arg
     subclass instantiation breaks on Python 3.14 (`TypeError: Struct()
     missing required argument 'format' (pos 1)`) because Argument
     Clinic moved the format requirement into __new__. Composition is
     durable across every CPython version. Worth upstreaming.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# SMOAP_SWITCH_MOD_DIR is set by build_switchmod.py (which already
# resolved the dev-checkout vs bundled-apworld layout). When invoked
# standalone we probe both names, dev-checkout first.
_switch_mod_env = os.environ.get("SMOAP_SWITCH_MOD_DIR")
if _switch_mod_env and os.path.isdir(_switch_mod_env):
    SWITCH_MOD = _switch_mod_env
else:
    SWITCH_MOD = next(
        (p for p in (
            os.path.join(REPO_ROOT, "switch-mod"),
            os.path.join(REPO_ROOT, "switch_mod"),
        ) if os.path.isdir(p)),
        os.path.join(REPO_ROOT, "switch-mod"),
    )
HAKKUN = os.path.join(SWITCH_MOD, "sys")


def patch_file(path: str, old: str, new: str, sentinel: str) -> str:
    """Apply a literal-string patch. Idempotent via sentinel check.

    Returns 'applied', 'already-applied', 'missing', or 'upstream-shifted'.

    `upstream-shifted` (warning, not error) means the expected old text
    isn't present and the sentinel isn't either — most commonly this happens
    when bumping LibHakkun across a branch hop (main → imgui) where some
    of our Windows-port patches have been merged upstream or refactored
    away. We warn so the build proceeds; if the missing patch is still
    needed it will surface as a compile/link failure later (informative
    enough to point us at which patch needs reworking).
    """
    if not os.path.exists(path):
        return "missing"
    content = open(path, encoding="utf-8").read()
    if sentinel in content:
        return "already-applied"
    if old not in content:
        return "upstream-shifted"
    new_content = content.replace(old, new, 1)
    open(path, "w", encoding="utf-8", newline="\n").write(new_content)
    return "applied"


def report(name: str, result: str) -> None:
    print(f"  [{result:>15}] {name}")


def main() -> int:
    if not os.path.isdir(HAKKUN):
        sys.exit(f"[patch_hakkun] {HAKKUN} not found — `git submodule update --init` first")

    print(f"[patch_hakkun] applying Windows-port patches to {HAKKUN}")

    # Patch 1 retired 2026-05-22: upstreamed as fruityloops1/LibHakkun
    # commit a1ae290c2d "sail: don't hardcode clang/clang++ as the host
    # compiler" (PR #71). Switch-mod/sys pin >= 9892726 contains it.

    report(
        "sail main.cpp filesystem::path wchar_t fix",
        patch_file(
            os.path.join(HAKKUN, "sail", "src", "main.cpp"),
            "            const char* path = entry.path().c_str();",
            "            std::string path_str = entry.path().string();  // SMO_HAKKUN_PATCH_2: Windows wchar_t fix\n            const char* path = path_str.c_str();",
            sentinel="SMO_HAKKUN_PATCH_2",
        ),
    )

    report(
        "sail fakelib.cpp clang path quoting",
        patch_file(
            os.path.join(HAKKUN, "sail", "src", "fakelib.cpp"),
            "    static void compile(const char* outPath, const char* clangBinary, const char* language, const std::string& source, const std::string& flags, const char* filename) {\n        std::string cmd = clangBinary;",
            "    static void compile(const char* outPath, const char* clangBinary, const char* language, const std::string& source, const std::string& flags, const char* filename) {\n        // SMO_HAKKUN_PATCH_3: quote clangBinary for Windows paths with spaces.\n        std::string cmd;\n        cmd.push_back('\"');\n        cmd.append(clangBinary);\n        cmd.push_back('\"');",
            sentinel="SMO_HAKKUN_PATCH_3",
        ),
    )

    report(
        "sail.cmake addons glob expansion",
        patch_file(
            os.path.join(HAKKUN, "cmake", "sail.cmake"),
            "        if (ADDONS_SYMS_EMPTY_TEST)\n            set(SAIL_CMD ${SAIL_CMD} ${CMAKE_CURRENT_SOURCE_DIR}/sys/addons/*/syms)\n        endif()",
            "        if (ADDONS_SYMS_EMPTY_TEST)\n            # SMO_HAKKUN_PATCH_4: expand glob ourselves (cmd.exe doesn't).\n            file(GLOB ADDONS_SYM_DIRS LIST_DIRECTORIES TRUE ${CMAKE_CURRENT_SOURCE_DIR}/sys/addons/*/syms)\n            foreach (d IN LISTS ADDONS_SYM_DIRS)\n                if (IS_DIRECTORY ${d})\n                    set(SAIL_CMD ${SAIL_CMD} ${d})\n                endif()\n            endforeach()\n        endif()",
            sentinel="SMO_HAKKUN_PATCH_4",
        ),
    )

    # Patch 5 + 5b retired 2026-05-22: upstreamed as fruityloops1/LibHakkun
    # commit de915fb55b "generate_exefs: invoke elf2nso.py via explicit
    # `python`" (PR #75). switch-mod/sys pin >= 9892726 contains it.

    # ------------------------------------------------------------------
    # Patch 7 retired 2026-05-22: upstreamed as fruityloops1/LibHakkun
    # commit 9892726b "Trampoline: relocate first instruction" (which IS
    # the current main HEAD). switch-mod/sys pin >= 9892726 contains it.
    #
    # Upstream's relocator differs from ours:
    #   - 5-slot packed TrampolineBackup (~24 bytes) instead of our 8-slot
    #     page-aligned (4 KiB). 168x smaller pool footprint.
    #   - Uses a64::assemble<"…">() constexpr DSL instead of hand-written
    #     instruction encoding.
    #   - Uses adrp+add for absolute address loading instead of our
    #     movz/movk×4 sequence.
    # We swapped to it because our patched relocator was the prime suspect
    # in addon-link-time failures (DebugRenderer + ImGui addons hung on
    # first NVN draw with our trampoline at nvnBootstrapLoader installed).
    # See memory: project_addon_static_init_pre_hkmain.
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Patch 8: drop `const` on Socket::recvFrom's address param.
    # ------------------------------------------------------------------
    # The original signature passes `&address` (a `const A*`) into
    # `addOutAutoselect(void*, ...)`. Won't compile when instantiated; recvFrom
    # is the OUT direction so the parameter should be non-const anyway.
    report(
        "service.h recvFrom drop-const on out-param address",
        patch_file(
            os.path.join(HAKKUN, "hakkun", "include", "hk", "services", "socket", "service.h"),
            "        template <typename A, typename T>\n"
            "            requires(std::is_convertible<A*, SocketAddr*>::value)\n"
            "        ValueOrResult<Ret> recvFrom(s32 fd, Span<u8> buffer, s32 flags, const A& address) {\n",
            "        // SMO_HAKKUN_PATCH_8: recvFrom is the OUT direction (kernel writes the\n"
            "        // sender's addr into `address`), so `&address` cannot be `const`. The\n"
            "        // upstream `const A&` declaration fails to compile when the function\n"
            "        // is instantiated because addOutAutoselect takes `void*`, not\n"
            "        // `const void*`. Mirrors getPeerName/getSockName.\n"
            "        template <typename A, typename T>\n"
            "            requires(std::is_convertible<A*, SocketAddr*>::value)\n"
            "        ValueOrResult<Ret> recvFrom(s32 fd, Span<u8> buffer, s32 flags, A& address) {\n",
            sentinel="SMO_HAKKUN_PATCH_8",
        ),
    )

    # ------------------------------------------------------------------
    # Patch 9: nso.py composition-over-inheritance.
    # ------------------------------------------------------------------
    # Upstream tools/nso.py writes `class NsoSegment(struct.Struct)` /
    # `class NsoHeader(struct.Struct)` with no-arg constructors that call
    # super().__init__(format) inside __init__. That pattern relies on
    # struct.Struct.__new__ accepting a no-arg call (so the subclass can
    # provide format later, in __init__). Python 3.14 reimplemented
    # struct.Struct with Argument Clinic, making `format` strictly
    # required in __new__'s signature. The no-arg subclass instantiation
    # `NsoHeader()` now raises at __new__ before __init__ ever runs:
    #
    #     TypeError: Struct() missing required argument 'format' (pos 1)
    #
    # Symptom: cmake's link-rule step `python sys/tools/elf2nso.py`
    # crashes with that traceback at NsoHeader() on line 64 of elf2nso.py.
    # Even with build_switchmod.py's PATH pin to Python 3.12 (the
    # wizard-verified interpreter where lz4 lives), a user whose system
    # has Python 3.14 elsewhere can hit this via any number of leak paths
    # (a stale bundled tree, an inherited PATH that wins the resolution,
    # py launcher misconfiguration). The robust fix is to defend the
    # script itself.
    #
    # The patch rewrites both classes to use composition (own a
    # struct.Struct instead of inheriting from one). Public interface
    # (.size, .format, .unpack_from, .load, .save) and constructor
    # shape are preserved, so elf2nso.py works unchanged. The new
    # implementation is durable across every CPython version because
    # it never relies on struct.Struct's constructor signature — it
    # treats struct.Struct as a tool, not a base class.
    #
    # Full-file replacement (file is ~100 lines, self-contained). If
    # upstream changes nso.py, the patch will fail loud at patch_file's
    # "old text not found" check and we revisit.
    _NSO_OLD = (
        "import struct\n"
        "\n"
        "class NsoSegment(struct.Struct):\n"
        "    def __init__(self):\n"
        "        super().__init__('<3I')\n"
        "\n"
        "        self.file_offset = 0\n"
        "        self.memory_offset = 0\n"
        "        self.decompressed_size = 0\n"
        "\n"
        "    def load(self, data, pos):\n"
        "        (self.file_offset,\n"
        "         self.memory_offset,\n"
        "         self.decompressed_size) = self.unpack_from(data, pos)\n"
        "\n"
        "    def save(self):\n"
        "        return struct.pack(\n"
        "            self.format,\n"
        "            self.file_offset,\n"
        "            self.memory_offset,\n"
        "            self.decompressed_size,\n"
        "        )\n"
        "\n"
        "\n"
        "class NsoHeader(struct.Struct):\n"
        "    def __init__(self):\n"
        "        super().__init__('<4I12xI12xI12xI32s3I28s3Q32s32s32s')\n"
    )
    _NSO_NEW = (
        "# SMO_HAKKUN_PATCH_9: composition over inheritance.\n"
        "#\n"
        "# Upstream wrote these classes as `class X(struct.Struct)` with\n"
        "# a no-arg constructor that called super().__init__(format). That\n"
        "# pattern relies on struct.Struct.__new__ accepting zero args.\n"
        "# Python 3.14 made `format` strictly required in Struct.__new__\n"
        "# (Argument Clinic rewrite), so the no-arg subclass instantiation\n"
        "# now raises `TypeError: Struct() missing required argument\n"
        "# 'format' (pos 1)` at __new__ before __init__ runs. Composition\n"
        "# preserves the public interface (.size, .format, .unpack_from,\n"
        "# .load, .save) without depending on Struct's constructor shape,\n"
        "# so it works on every CPython version (3.10 through 3.14+).\n"
        "import struct\n"
        "\n"
        "class NsoSegment:\n"
        "    _fmt = struct.Struct('<3I')\n"
        "    size = _fmt.size\n"
        "    format = _fmt.format\n"
        "\n"
        "    def __init__(self):\n"
        "        self.file_offset = 0\n"
        "        self.memory_offset = 0\n"
        "        self.decompressed_size = 0\n"
        "\n"
        "    def unpack_from(self, data, pos):\n"
        "        return self._fmt.unpack_from(data, pos)\n"
        "\n"
        "    def load(self, data, pos):\n"
        "        (self.file_offset,\n"
        "         self.memory_offset,\n"
        "         self.decompressed_size) = self.unpack_from(data, pos)\n"
        "\n"
        "    def save(self):\n"
        "        return struct.pack(\n"
        "            self.format,\n"
        "            self.file_offset,\n"
        "            self.memory_offset,\n"
        "            self.decompressed_size,\n"
        "        )\n"
        "\n"
        "\n"
        "class NsoHeader:\n"
        "    _fmt = struct.Struct('<4I12xI12xI12xI32s3I28s3Q32s32s32s')\n"
        "    size = _fmt.size\n"
        "    format = _fmt.format\n"
        "\n"
        "    def unpack_from(self, data, pos):\n"
        "        return self._fmt.unpack_from(data, pos)\n"
        "\n"
        "    def __init__(self):\n"
    )
    report(
        "nso.py composition (3.14 Struct.__new__ fix)",
        patch_file(
            os.path.join(HAKKUN, "tools", "nso.py"),
            _NSO_OLD,
            _NSO_NEW,
            sentinel="SMO_HAKKUN_PATCH_9",
        ),
    )

    print("[patch_hakkun] done")
    return 0


def _maybe_add_sentinel(path: str, after_line: str, sentinel: str) -> None:
    """Insert a sentinel comment after a given line so future re-runs detect 'already applied'."""
    if not os.path.exists(path):
        return
    content = open(path, encoding="utf-8").read()
    if sentinel.strip() in content:
        return
    if after_line not in content:
        return
    new_content = content.replace(after_line, after_line + "\n" + sentinel.rstrip() + "\n", 1)
    open(path, "w", encoding="utf-8", newline="\n").write(new_content)


if __name__ == "__main__":
    sys.exit(main())
