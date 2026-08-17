#!/usr/bin/env python3
"""Apply local patches to the pinned LibHakkun submodule.

Mostly Windows-port fixes (the original motivation), plus a few
platform-independent ones for upstream drift the pin can't dodge —
see patches 9 and 10.

The spike at third_party/hakkun-spike (gitignored) discovered six source-level
patches needed to build LibHakkun + sail on Windows + msys2. Each patch is
idempotent (uses a sentinel check before applying). On first run, all six
land; subsequent runs report 'already applied' and exit cleanly.

These patches should be upstreamed to fruityloops1/LibHakkun. While upstream
PRs are in flight, this script reapplies them locally after submodule init.
If a PR review stalls > 1 week, the migration plan calls for forking
LibHakkun to mdietz94/LibHakkun-smo and re-pinning the submodule — at which
point this script becomes obsolete.

Patches applied (paths are inside switch-mod/sys/, the LibHakkun submodule):
  2. sail/src/main.cpp — std::filesystem::path::c_str() is wchar_t* on Windows.
  3. sail/src/fakelib.cpp — quote clangBinary path in popen cmdline.
  4. cmake/sail.cmake — expand sys/addons/*/syms glob (cmd.exe doesn't).
  6. (env only) Copy sail/build/sail.exe → sail/build/sail (no ext).
     Handled by scripts/build_switchmod.py.

Retired at the current pin (9892726b, LibHakkun main HEAD as of 2026-05-22):
  1.  sail CMakeLists clang/clang++ removal  → fruityloops1/LibHakkun PR #71 (a1ae290c2d)
  5.  generate_exefs.cmake python prefix     → PR #75 (de915fb55b)
  5b. generate_exefs.cmake non-baked variant → PR #75 (de915fb55b)
  7.  AArch64 prologue relocator             → upstream commit 9892726b "Trampoline: relocate first instruction"
These re-activate only if the pin ever rolls back to a tree that predates them.

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

 10. (blocking) sys/tools/setup_libcxx_prepackaged.py release-host move:
     LibHakkun moved its releases off GitHub to Codeberg and removed the
     old GitHub ones, so the baked-in
     `github.com/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0-3/`
     URL now 404s and no from-scratch build (dev checkout, setup wizard,
     release CI) can fetch the prepackaged aarch64 stdlib. 10a repoints at
     `codeberg.org/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0/`
     (same two tarballs; the Codeberg tag drops the `-3` suffix). 10b makes
     the curl step fail loudly instead of saving an HTTP error body under
     the tarball's name. Worth upstreaming.
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

    # Patch 1 retired at pin 9892726b — upstreamed as fruityloops1/LibHakkun
    # commit a1ae290c2d "sail: don't hardcode clang/clang++ as the host
    # compiler" (PR #71). Re-activate if the pin rolls back to a tree
    # that predates it.

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

    # Patch 5 + 5b retired at pin 9892726b — upstreamed as fruityloops1/LibHakkun
    # commit de915fb55b "generate_exefs: invoke elf2nso.py via explicit `python`"
    # (PR #75). Re-activate if the pin rolls back to a tree that predates it.

    # Patch 7 retired at pin 9892726b — upstream's relocator IS the pin
    # itself ("Trampoline: relocate first instruction"). Structurally
    # different from the relocator we used to ship locally (upstream uses
    # a 5-slot packed TrampolineBackup + constexpr a64::assemble<> DSL vs
    # our 8-slot page-aligned + hand-encoded), functionally equivalent.
    # Re-activate if the pin rolls back to a tree that predates it.


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

    # ------------------------------------------------------------------
    # Patch 10: setup_libcxx_prepackaged.py — GitHub → Codeberg releases.
    # ------------------------------------------------------------------
    # LibHakkun moved its release hosting to Codeberg and deleted the
    # GitHub releases (reported 2026-08-11). The download URL baked into
    # sys/tools/setup_libcxx_prepackaged.py,
    #
    #   https://github.com/fruityloops1/LibHakkun/releases/download/
    #       stdlib-19.1.0-3/stdlib-19.1.0_clang_19.1.7.tar.xz
    #
    # now 404s. That script is how lib/std/*.a (musl libc + LLVM libc++ +
    # libunwind + compiler-rt for aarch64) gets on disk, so an unpatched
    # tree cannot build at all: every from-scratch build path hits it —
    # a dev checkout, the setup wizard on an end-user machine, and the
    # release CI switch-mod job.
    #
    # Codeberg hosts both tarballs (aarch64 and aarch32) under the tag
    # `stdlib-19.1.0` — same filenames, no `-3` suffix on the tag:
    #
    #   https://codeberg.org/fruityloops1/LibHakkun/releases/download/
    #       stdlib-19.1.0/stdlib-19.1.0_clang_19.1.7.tar.xz
    #
    # Patched as two independent replacements so that if upstream fixes
    # (or re-shuffles) one of the two lines, the other still applies.
    report(
        "setup_libcxx_prepackaged.py Codeberg release URL",
        patch_file(
            os.path.join(HAKKUN, "tools", "setup_libcxx_prepackaged.py"),
            'prepackaged_source = "https://github.com/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0-3/" + prepackaged_source_tar_name',
            "# SMO_HAKKUN_PATCH_10A: LibHakkun moved its releases to Codeberg and\n"
            "# removed them from GitHub, so the old GitHub URL 404s. Codeberg\n"
            "# serves the same two tarballs under the tag `stdlib-19.1.0` (the\n"
            "# `-3` suffix is GitHub-only).\n"
            'prepackaged_source = "https://codeberg.org/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0/" + prepackaged_source_tar_name',
            sentinel="SMO_HAKKUN_PATCH_10A",
        ),
    )

    # Upstream's curl call passes neither `-f` nor `check=True`. On an HTTP
    # error curl happily writes the error body to `stdlib-*.tar.xz` and
    # exits 0, so the failure surfaces two steps later as an opaque
    # `tarfile.ReadError` — or, if the tar step is skipped, as a link
    # failure against absent lib/std/*.a. `-f` makes curl exit non-zero on
    # 4xx/5xx; the explicit check turns that into a message that names the
    # URL and points at this script, so the next host move is a one-line
    # fix rather than an investigation.
    report(
        "setup_libcxx_prepackaged.py curl error checking",
        patch_file(
            os.path.join(HAKKUN, "tools", "setup_libcxx_prepackaged.py"),
            "    subprocess.run(['curl', '-O', '-L', prepackaged_source])",
            "    # SMO_HAKKUN_PATCH_10B: fail loudly. Without -f, curl saves HTTP\n"
            "    # error bodies under the tarball's name and exits 0, turning a\n"
            "    # dead download URL into a confusing tarfile.ReadError.\n"
            "    result = subprocess.run(\n"
            "        ['curl', '-f', '-S', '-O', '-L', '--retry', '3', prepackaged_source])\n"
            "    if result.returncode != 0 or not os.path.exists(prepackaged_source_tar_name):\n"
            "        raise SystemExit(\n"
            "            f'Failed to download the prepackaged stdlib from '\n"
            "            f'{prepackaged_source} (curl exit {result.returncode}).\\n'\n"
            "            f'If LibHakkun moved its release hosting again, update the '\n"
            "            f'URL in scripts/patch_hakkun.py (patch 10a).')",
            sentinel="SMO_HAKKUN_PATCH_10B",
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
