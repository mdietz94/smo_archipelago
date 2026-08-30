"""Guards patches 11 + 12 in scripts/patch_hakkun.py — spaces in paths.

The setup wizard stages the bundled switch_mod tree under
`%APPDATA%/SMOArchipelago/bundled/`, which on Windows expands to
`C:/Users/<user name>/AppData/Roaming/...`. Any user name with a space
in it ("Chris Haugh") puts a space in every absolute path the Switch-mod
build touches, and two places in the pinned LibHakkun submodule paste
those paths into a *shell command string* without quoting:

  1. `sys/cmake/toolchain.cmake` folds the six aarch64 stdlib include
     dirs into `CMAKE_C_FLAGS`/`CMAKE_CXX_FLAGS` and the six static libs
     into `CMAKE_EXE_LINKER_FLAGS` as space-separated strings. The build
     dies at `project()` — before one source file compiles — with clang
     reading each path tail as a bogus input file:

         clang: error: no such file or directory:
           'Haugh/AppData/Roaming/SMOArchipelago/.../musl/obj/include'

  2. `sail/src/fakelib.cpp` builds one popen() command line. Patch 3
     already quotes the clang binary; patch 12 quotes the `-o` output
     path, which is the CMake binary dir — under the same spaced root.

Everything else in the build survives spaces on its own: CMake escapes
`target_link_options` and non-VERBATIM `add_custom_command` arguments
per-platform, and every wrapper in `scripts/` invokes subprocesses with
argument lists rather than shell strings.

These tests are network- and toolchain-free: they run patch_hakkun
against a synthetic submodule tree holding upstream-verbatim copies of
the two files, then assert the patched text quotes a spaced path in a
way that survives shell splitting.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "patch_hakkun.py"

_SPACED = "/home/user/Chris Haugh/smo_archipelago/switch-mod/lib/std/musl/include"

# The relevant excerpt of sys/cmake/toolchain.cmake verbatim at pin
# 9892726b. Kept inline because patch_hakkun rewrites the submodule copy
# in place — after one build there is no pristine copy left to diff.
_UPSTREAM_TOOLCHAIN = '''set(DEFAULTINCLUDES_F "")
foreach(item IN LISTS DEFAULTINCLUDES)
    set(DEFAULTINCLUDES_F "${DEFAULTINCLUDES_F} -isystem ${item}")
endforeach()
set(DEFAULTLIBS_F "")
foreach(item IN LISTS DEFAULTLIBS)
    set(DEFAULTLIBS_F "${DEFAULTLIBS_F} ${item}")
endforeach()

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${ARCH_FLAGS} ${DEFAULTINCLUDES_F}")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} ${DEFAULTLIBS_F}")
'''

# sail/src/fakelib.cpp's compile() verbatim at the same pin, already
# carrying patch 3 (which lands first and is not what these tests guard).
_UPSTREAM_FAKELIB = '''namespace sail {
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


def _fake_hakkun(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal `switch-mod/` tree holding just the two files
    patches 11 + 12 target. Every other patch target is absent —
    patch_file reports 'missing' for those and main() still returns 0."""
    root = tmp_path / "switch-mod" / "sys"
    toolchain = root / "cmake" / "toolchain.cmake"
    toolchain.parent.mkdir(parents=True)
    toolchain.write_text(_UPSTREAM_TOOLCHAIN, encoding="utf-8")

    fakelib = root / "sail" / "src" / "fakelib.cpp"
    fakelib.parent.mkdir(parents=True)
    fakelib.write_text(_UPSTREAM_FAKELIB, encoding="utf-8")
    return toolchain, fakelib


def _run_patcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Import patch_hakkun fresh against the synthetic tree and run main().

    The module resolves HAKKUN at import time from SMOAP_SWITCH_MOD_DIR,
    so the env var must be set before exec_module — and the module must
    be re-imported per call rather than cached."""
    monkeypatch.setenv("SMOAP_SWITCH_MOD_DIR", str(tmp_path / "switch-mod"))
    spec = importlib.util.spec_from_file_location("patch_hakkun_spaces_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def _split_appended_flag(patched: str, var: str, item: str) -> list[str]:
    """Pull `set(<var> "${<var>} ...")` out of the patched toolchain, expand
    it the way CMake would for a single list entry, and shell-split the
    result. That is exactly the round trip the build performs: CMake pastes
    the string into the command line, the shell re-splits it."""
    match = re.search(
        r'set\(' + var + r' "\$\{' + var + r'\}(.*)"\)\n', patched
    )
    assert match, f"no appending set({var} ...) found in patched toolchain"
    rendered = match.group(1).replace('\\"', '"').replace("${item}", item)
    return shlex.split(rendered)


def test_include_flags_quote_spaced_paths(tmp_path, monkeypatch):
    toolchain, _ = _fake_hakkun(tmp_path)
    assert _run_patcher(tmp_path, monkeypatch) == 0

    patched = toolchain.read_text(encoding="utf-8")
    assert '-isystem \\"${item}\\"' in patched
    # The unquoted form must be gone: it is what tore the path in half.
    assert '-isystem ${item}"' not in patched

    # Render the flag the way CMake will and shell-split it. A correctly
    # quoted path yields exactly two argv entries; the unpatched form
    # yielded three, the last of which clang treated as an input file.
    assert _split_appended_flag(patched, "DEFAULTINCLUDES_F", _SPACED) == [
        "-isystem",
        _SPACED,
    ]


def test_linker_flags_quote_spaced_paths(tmp_path, monkeypatch):
    toolchain, _ = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)

    patched = toolchain.read_text(encoding="utf-8")
    assert 'set(DEFAULTLIBS_F "${DEFAULTLIBS_F} \\"${item}\\"")' in patched

    lib = "/home/user/Chris Haugh/smo_archipelago/switch-mod/lib/std/libc++.a"
    assert _split_appended_flag(patched, "DEFAULTLIBS_F", lib) == [lib]


def test_sail_output_path_is_quoted(tmp_path, monkeypatch):
    _, fakelib = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)

    patched = fakelib.read_text(encoding="utf-8")
    # The `-o ` append must be followed by an opening quote, then the
    # path pieces, then the closing quote — before the flags append.
    body = patched.split('-o ");', 1)[1].split("cmd.append(flags);", 1)[0]
    assert body.count("cmd.push_back('\"');") == 2
    assert body.index("cmd.push_back('\"');") < body.index("cmd.append(outPath);")


def test_patches_are_idempotent(tmp_path, monkeypatch):
    toolchain, fakelib = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)
    once = (toolchain.read_text(encoding="utf-8"), fakelib.read_text(encoding="utf-8"))
    _run_patcher(tmp_path, monkeypatch)
    assert (toolchain.read_text(encoding="utf-8"), fakelib.read_text(encoding="utf-8")) == once
    assert once[0].count("SMO_HAKKUN_PATCH_11") == 1
    assert once[1].count("SMO_HAKKUN_PATCH_12") == 1


def test_repo_submodule_copy_is_patched_when_present():
    """The checked-out submodule (if initialized) must not still carry the
    unquoted forms after a build. Skips on a shallow checkout where
    switch-mod/sys was never initialized."""
    sys_dir = _REPO_ROOT / "switch-mod" / "sys"
    toolchain = sys_dir / "cmake" / "toolchain.cmake"
    if not toolchain.exists():
        pytest.skip("switch-mod/sys submodule not initialized")
    text = toolchain.read_text(encoding="utf-8")
    if "SMO_HAKKUN_PATCH_11" not in text:
        pytest.skip("patch_hakkun.py has not been run against this checkout")
    assert 'set(DEFAULTINCLUDES_F "${DEFAULTINCLUDES_F} -isystem ${item}")' not in text
    assert 'set(DEFAULTLIBS_F "${DEFAULTLIBS_F} ${item}")' not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
