"""Guards patch 13 in scripts/patch_hakkun.py — cmd.exe quote stripping.

Patches 3 and 12 quote the two Windows paths that sail's `compile()`
pastes into the single shell command it hands to `popen()`: the clang
binary and the `-o` output path. Patch 12's commit message asserted that
"quoting a space-free path is a no-op". Under `/bin/sh` it is. Under
`cmd.exe` — which is what `popen()` runs on Windows — it is not, and the
pair of patches broke *every* Windows build, spaces or no spaces:

    The filename, directory name, or volume label syntax is incorrect.
    clang compilation failed

`cmd /?` documents the rule. Quotes survive verbatim only when the `/c`
command line holds EXACTLY TWO quote characters (with whitespace and no
`&<>()@^|` between them, and an executable named between them).
Otherwise, if the line starts with a quote, cmd strips that leading
quote AND the last quote character on the line. Two quoted tokens means
four quotes, so the second branch fires and eats the compiler's OPENING
quote plus the `-o` path's CLOSING quote, stranding the inner two:

    C:\\...\\clang++.exe" ... -o "C:\\...\\build/fakesymbols.so ... -

cmd then reads `C:\\...\\clang++.exe"` as the program name and refuses it.

Patch 13 is the standard `cmd /c ""a" "b""` idiom: wrap the whole command
in one more quote pair so the pair cmd removes is ours. The wrapper is
`#ifdef _WIN32`-guarded because a POSIX shell parses the inner quoting
correctly and would mis-parse the wrapper.

These tests are toolchain-free: they run patch_hakkun against a synthetic
submodule tree holding an upstream-verbatim `fakelib.cpp`, then replay
cmd.exe's documented quote rules over the command string the patched
source builds.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "patch_hakkun.py"

# The real paths off the 2026-08-31 bug report (user name without a
# space) and off the patch 11/12 bug report (user name with one). Both
# must survive; the first is what patch 12 regressed.
_CLANG_NO_SPACE = r"C:\Users\ElmerLexmond\AppData\Local\SMOArchipelago\llvm\bin\clang++.exe"
_OUT_NO_SPACE = r"C:\Users\ElmerLexmond\AppData\Roaming\SMOArchipelago\bundled\switch_mod\build"
_CLANG_SPACE = r"C:\Program Files\LLVM\bin\clang++.exe"
_OUT_SPACE = r"C:\Users\Chris Haugh\AppData\Roaming\SMOArchipelago\bundled\switch_mod\build"

_MIDDLE = (
    " --target=aarch64-none-elf -march=armv8-a"
    " -mtune=cortex-a57 -nodefaultlibs -nostartfiles"
    " -Wno-unused-command-line-argument -o "
)
_TAIL = " -Wl,--shared -s -fuse-ld=lld -x assembler -"

# sail/src/fakelib.cpp's compile() verbatim at pin 9892726b.
_UPSTREAM_FAKELIB = '''#include "fakelib.h"

#include <cstdio>
#include <string>

namespace sail {
    static void compile(const char* outPath, const char* clangBinary, const char* language, const std::string& source, const std::string& flags, const char* filename) {
        std::string cmd = clangBinary;

        cmd.append(" -mtune=cortex-a57 -nodefaultlibs -nostartfiles -Wno-unused-command-line-argument -o ");

        cmd.append(outPath);
        cmd.append("/");
        cmd.append(filename);
        cmd.append(" ");
        cmd.append(flags);
        cmd.append(" -x ");
        cmd.append(language);
        cmd.append(" -");

        FILE* compilerPipe = popen(cmd.c_str(), "w");
    }
}
'''


def _fake_hakkun(tmp_path: Path) -> Path:
    """A minimal `switch-mod/` tree holding only fakelib.cpp. Every other
    patch target is absent — patch_file reports 'missing' for those and
    main() still returns 0."""
    fakelib = tmp_path / "switch-mod" / "sys" / "sail" / "src" / "fakelib.cpp"
    fakelib.parent.mkdir(parents=True)
    fakelib.write_text(_UPSTREAM_FAKELIB, encoding="utf-8")
    return fakelib


def _run_patcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Import patch_hakkun fresh against the synthetic tree and run main().

    The module resolves HAKKUN at import time from SMOAP_SWITCH_MOD_DIR,
    so the env var must be set before exec_module — and the module must be
    re-imported per call rather than cached."""
    monkeypatch.setenv("SMOAP_SWITCH_MOD_DIR", str(tmp_path / "switch-mod"))
    spec = importlib.util.spec_from_file_location("patch_hakkun_cmd_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


# ---------------------------------------------------------------------
# cmd.exe's documented /c quote handling, as a function.
# ---------------------------------------------------------------------

def _cmd_c_dequote(command: str) -> str:
    """Return the command line cmd.exe actually executes for `/c <command>`.

    Straight transcription of the two rules in `cmd /?`:

      1. Exactly two quote characters, no `&<>()@^|` between them, at
         least one whitespace character between them, and the text
         between them names an executable -> quotes preserved verbatim.
      2. Otherwise, if the first character is a quote, strip the leading
         quote and the LAST quote character on the line, keeping any text
         after it.

    Rule 1's "names an executable" check can't be evaluated off-Windows;
    the caller only ever passes lines whose first token is an .exe path,
    so we approximate it with an `.exe` suffix test. Nothing in these
    tests turns on the distinction: every rule-1 line is returned
    unchanged either way.
    """
    if command.count('"') == 2:
        first, last = command.index('"'), command.rindex('"')
        between = command[first + 1:last]
        if (not any(c in between for c in "&<>()@^|")
                and any(c.isspace() for c in between)
                and between.lower().endswith(".exe")):
            return command
    if command.startswith('"'):
        last = command.rindex('"')
        return command[1:last] + command[last + 1:]
    return command


def _program_token(command: str) -> str:
    """The program name cmd resolves: the first whitespace-delimited token,
    honouring a leading quoted span."""
    if command.startswith('"'):
        end = command.index('"', 1)
        return command[1:end]
    return command.split(" ", 1)[0]


def _sail_command(clang: str, out: str, *, wrapped: bool) -> str:
    """The exact string patched sail::compile() hands to popen(), with
    patch 3 + patch 12 quoting always on and patch 13's outer pair
    switchable so the tests can show the before/after."""
    cmd = f'"{clang}"{_MIDDLE}"{out}/fakesymbols.so"{_TAIL}'
    return f'"{cmd}"' if wrapped else cmd


# ---------------------------------------------------------------------
# The patch itself
# ---------------------------------------------------------------------

def test_patch_13_wraps_the_popen_command_on_windows_only(tmp_path, monkeypatch):
    fakelib = _fake_hakkun(tmp_path)
    assert _run_patcher(tmp_path, monkeypatch) == 0

    patched = fakelib.read_text(encoding="utf-8")
    assert "SMO_HAKKUN_PATCH_13" in patched

    # The wrapper must sit immediately before popen() — after every
    # append that builds the command — and must be _WIN32-guarded, since
    # /bin/sh parses the inner quoting correctly and mis-parses a wrapper.
    guarded = re.search(
        r"#ifdef _WIN32\n"
        r"\s*cmd\.insert\(cmd\.begin\(\), '\"'\);\n"
        r"\s*cmd\.push_back\('\"'\);\n"
        r"#endif\n"
        r'\s*FILE\* compilerPipe = popen\(cmd\.c_str\(\), "w"\);',
        patched,
    )
    assert guarded, f"patch 13 wrapper not found immediately before popen:\n{patched}"


def test_patch_13_is_idempotent(tmp_path, monkeypatch):
    fakelib = _fake_hakkun(tmp_path)
    assert _run_patcher(tmp_path, monkeypatch) == 0
    once = fakelib.read_text(encoding="utf-8")
    assert _run_patcher(tmp_path, monkeypatch) == 0
    assert fakelib.read_text(encoding="utf-8") == once
    assert once.count("SMO_HAKKUN_PATCH_13") == 1


def test_patch_13_lands_alongside_patches_3_and_12(tmp_path, monkeypatch):
    """Patch 13 only matters because 3 and 12 quote two tokens. If a pin
    bump ever drops one of those, this test says so before a user does."""
    fakelib = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)
    patched = fakelib.read_text(encoding="utf-8")
    for sentinel in ("SMO_HAKKUN_PATCH_3", "SMO_HAKKUN_PATCH_12", "SMO_HAKKUN_PATCH_13"):
        assert sentinel in patched


# ---------------------------------------------------------------------
# The mechanism the patch exists for
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "clang,out,broken_token",
    [
        # No space anywhere: cmd hands clang++.exe a stray trailing quote.
        # This is the 2026-08-31 report — patch 12 shipped and every
        # space-free Windows install stopped building.
        (_CLANG_NO_SPACE, _OUT_NO_SPACE, _CLANG_NO_SPACE + '"'),
        # A space in the compiler path: its opening quote is gone too, so
        # the token now ends at the space instead.
        (_CLANG_SPACE, _OUT_SPACE, "C:\\Program"),
    ],
    ids=["no-spaces", "spaces"],
)
def test_unwrapped_command_is_corrupted_by_cmd(clang, out, broken_token):
    """The regression, reproduced: with patches 3 + 12 and no outer pair,
    cmd eats the compiler's opening quote and the -o path's closing one,
    so the program name it resolves is not the compiler. That is what
    produced 'The filename, directory name, or volume label syntax is
    incorrect.' at the sail PRE_LINK step."""
    executed = _cmd_c_dequote(_sail_command(clang, out, wrapped=False))
    assert _program_token(executed) != clang
    assert _program_token(executed) == broken_token
    # The -o path lost its closing quote in the same stroke.
    assert f'-o "{out}/fakesymbols.so"' not in executed


@pytest.mark.parametrize(
    "clang,out",
    [(_CLANG_NO_SPACE, _OUT_NO_SPACE), (_CLANG_SPACE, _OUT_SPACE)],
    ids=["no-spaces", "spaces"],
)
def test_wrapped_command_survives_cmd(clang, out):
    """With patch 13's outer pair, cmd removes ours and hands clang a
    correctly quoted line — compiler path and -o path both intact,
    whether or not the user name has a space in it."""
    executed = _cmd_c_dequote(_sail_command(clang, out, wrapped=True))
    assert _program_token(executed) == clang
    assert f'-o "{out}/fakesymbols.so"' in executed
    assert executed.endswith(_TAIL)
    # Nothing of ours is left behind.
    assert not executed.startswith('""')
    assert not executed.endswith('"')


def test_posix_command_is_left_unwrapped():
    """The `#ifdef _WIN32` guard is load-bearing in the other direction:
    /bin/sh splits the inner quoting correctly on its own, and the outer
    pair would fuse the whole command into one argv entry."""
    import shlex

    unwrapped = _sail_command(_CLANG_SPACE, _OUT_SPACE, wrapped=False).replace("\\", "/")
    argv = shlex.split(unwrapped)
    assert argv[0] == _CLANG_SPACE.replace("\\", "/")
    assert argv[-1] == "-"

    wrapped = _sail_command(_CLANG_SPACE, _OUT_SPACE, wrapped=True).replace("\\", "/")
    assert len(shlex.split(wrapped)) < len(argv)


# ---------------------------------------------------------------------
# How the patch reaches a machine that already built sail once
# ---------------------------------------------------------------------

def _build_switchmod():
    spec = importlib.util.spec_from_file_location(
        "build_switchmod_under_test", _REPO_ROOT / "scripts" / "build_switchmod.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sail_tree(tmp_path: Path, *, source_mtime: float, binary_mtime: float) -> tuple[Path, Path]:
    """A `sys/sail/` tree with one source and one built binary, mtimes set
    explicitly so the test doesn't race the filesystem's timestamp
    granularity."""
    sail = tmp_path / "sys" / "sail"
    src = sail / "src" / "fakelib.cpp"
    src.parent.mkdir(parents=True)
    src.write_text("// source\n", encoding="utf-8")
    binary = sail / "build" / "sail.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ")
    import os

    os.utime(src, (source_mtime, source_mtime))
    os.utime(binary, (binary_mtime, binary_mtime))
    return sail, binary


def test_sail_binary_is_stale_when_a_source_is_newer(tmp_path):
    """patch_hakkun rewrites fakelib.cpp in place, bumping its mtime. Without
    this check `ensure_sail_built` would keep the pre-patch sail.exe and
    patch 13 would never take effect on a machine that already built once
    — which is every machine that hit the bug."""
    mod = _build_switchmod()
    sail, binary = _sail_tree(tmp_path, source_mtime=2000, binary_mtime=1000)
    assert mod._sail_binary_is_stale(str(binary), str(sail)) is True


def test_sail_binary_is_fresh_when_sources_are_older(tmp_path):
    mod = _build_switchmod()
    sail, binary = _sail_tree(tmp_path, source_mtime=1000, binary_mtime=2000)
    assert mod._sail_binary_is_stale(str(binary), str(sail)) is False


def test_sail_build_dir_does_not_make_the_binary_look_stale(tmp_path):
    """cmake's own artefacts land in `sys/sail/build/` and are newer than
    the sources by construction — counting them would rebuild sail on
    every single invocation."""
    mod = _build_switchmod()
    sail, binary = _sail_tree(tmp_path, source_mtime=1000, binary_mtime=2000)
    import os

    artefact = sail / "build" / "CMakeCache.txt"
    artefact.write_text("stale-looking but irrelevant\n", encoding="utf-8")
    os.utime(artefact, (3000, 3000))
    assert mod._sail_binary_is_stale(str(binary), str(sail)) is False


def test_missing_sail_binary_counts_as_stale(tmp_path):
    mod = _build_switchmod()
    sail, binary = _sail_tree(tmp_path, source_mtime=1000, binary_mtime=2000)
    binary.unlink()
    assert mod._sail_binary_is_stale(str(binary), str(sail)) is True


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
