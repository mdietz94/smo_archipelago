"""Tests for `_setup.build.run_extract_maps` subprocess-unbuffer setup.

Regression coverage for the v0.1.6-alpha wizard bug where the "Extract
moon + capture maps" step's log box stayed blank for 60+ seconds. Root
cause was the extractor child process buffering its stdout because (a)
Python defaults to block-buffering when stdout isn't a TTY, and (b) the
bootstrap calls `pip install --quiet oead` which suppresses pip's
progress lines. The wizard captures stdout via Popen — so anything
buffered inside the child is invisible until the child either fills the
buffer (~8 KB), exits, or explicitly flushes.

The fix is to pass `-u` AND set PYTHONUNBUFFERED=1; this test pins both
in place so a future refactor can't accidentally drop them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from _setup import build


def test_run_extract_maps_passes_dash_u_to_python(tmp_path) -> None:
    """The extractor must be launched with `-u` so stdout/stderr are
    line-buffered. Without this the wizard log box looks frozen for the
    silent 30-90s of venv creation + oead install.

    `-u` must appear AFTER the python-invoker prefix (which can be either
    `[sys.executable]` or `["py", "-3.12"]`) and BEFORE the script path
    so it applies to the script's Python interpreter, not to the script
    itself as an argv arg."""
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        return build.BuildResult(ok=True, returncode=0, log="")

    # Stub both file lookups: bundled_script (the extractor itself) and
    # bundled_data_file (the locations.json / items.json the wizard now
    # threads through as --locations / --items overrides). Both must
    # exist on disk so the wizard's existence checks pass before spawn.
    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "fake.nsp")

    cmd = captured["cmd"]
    # Find the script path; -u must be immediately before it.
    script_idx = next(i for i, a in enumerate(cmd) if a.endswith(".py"))
    assert cmd[script_idx - 1] == "-u", (
        f"'-u' should be the last arg before the script path; got "
        f"{cmd[max(0, script_idx-2):script_idx+1]}"
    )


def test_run_extract_maps_sets_pythonunbuffered_env(tmp_path) -> None:
    """The bootstrap's re-launched child Python (the one running under the
    new venv) must stay unbuffered. We pass `-u` explicitly when we
    subprocess.run the venv Python, but PYTHONUNBUFFERED=1 in the env is
    belt-and-braces — it survives any future refactor that drops the
    explicit `-u` and matches what the wizard's prior os.execv-based
    bootstrap required (env vars were the only thing it carried through
    reliably)."""
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["env"] = env or {}
        return build.BuildResult(ok=True, returncode=0, log="")

    # Stub both file lookups: bundled_script (the extractor itself) and
    # bundled_data_file (the locations.json / items.json the wizard now
    # threads through as --locations / --items overrides). Both must
    # exist on disk so the wizard's existence checks pass before spawn.
    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "fake.nsp")

    env = captured["env"]
    assert env.get("PYTHONUNBUFFERED") == "1", (
        f"PYTHONUNBUFFERED=1 must be set so the post-execv re-launched "
        f"Python stays unbuffered; got env keys: {sorted(env.keys())}"
    )


def test_run_extract_maps_dispatches_nsp_flag_for_nsp(tmp_path) -> None:
    """An `.nsp` dump path must be passed to the extractor as `--nsp <path>`.

    Locks in the extension-based flag dispatch added when XCI support
    landed — a sibling test below pins the .xci branch. The pair guards
    against accidental swap or removal of the dispatch logic in
    `build.run_extract_maps`.
    """
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["cmd"] = cmd
        return build.BuildResult(ok=True, returncode=0, log="")

    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "fake.nsp")

    cmd = captured["cmd"]
    assert "--nsp" in cmd, f"--nsp missing from extract cmd: {cmd}"
    assert "--xci" not in cmd, f"--xci should NOT be in NSP cmd: {cmd}"
    nsp_idx = cmd.index("--nsp")
    assert cmd[nsp_idx + 1].endswith("fake.nsp"), (
        f"--nsp value should be the dump path; got {cmd[nsp_idx + 1]!r}"
    )


def test_run_extract_maps_dispatches_xci_flag_for_xci(tmp_path) -> None:
    """An `.xci` dump path must be passed to the extractor as `--xci <path>`.

    XCI cartridge dumps have a different hactool unpack path (HFS0
    secure partition rather than PFS0); the extractor branches on the
    flag, so the wizard must pick the right one based on file extension.
    """
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["cmd"] = cmd
        return build.BuildResult(ok=True, returncode=0, log="")

    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "fake.xci")

    cmd = captured["cmd"]
    assert "--xci" in cmd, f"--xci missing from extract cmd: {cmd}"
    assert "--nsp" not in cmd, f"--nsp should NOT be in XCI cmd: {cmd}"
    xci_idx = cmd.index("--xci")
    assert cmd[xci_idx + 1].endswith("fake.xci"), (
        f"--xci value should be the dump path; got {cmd[xci_idx + 1]!r}"
    )


def test_run_extract_maps_xci_dispatch_is_case_insensitive(tmp_path) -> None:
    """`.XCI` (uppercase, as some dump tools produce) must still dispatch
    to `--xci`. The extension check lowercases the suffix to handle this."""
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["cmd"] = cmd
        return build.BuildResult(ok=True, returncode=0, log="")

    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "FAKE.XCI")

    cmd = captured["cmd"]
    assert "--xci" in cmd, f"--xci missing from extract cmd: {cmd}"


def test_run_extract_maps_preserves_existing_env(monkeypatch, tmp_path) -> None:
    """Setting PYTHONUNBUFFERED must NOT wipe the rest of os.environ —
    the child process needs PATH, SMOAP_LLVM_BIN / SMOAP_MINGW_BIN (set
    by the prereq detector for the build step), TEMP, etc. Test that
    we merge into os.environ rather than overwriting it."""
    monkeypatch.setenv("SMOAP_LLVM_BIN", "C:/portable/llvm/bin")
    monkeypatch.setenv("MY_TEST_MARKER", "carry-me-through")
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["env"] = env or {}
        return build.BuildResult(ok=True, returncode=0, log="")

    # Stub both file lookups: bundled_script (the extractor itself) and
    # bundled_data_file (the locations.json / items.json the wizard now
    # threads through as --locations / --items overrides). Both must
    # exist on disk so the wizard's existence checks pass before spawn.
    (tmp_path / "extract_shine_map.py").write_text("")
    (tmp_path / "locations.json").write_text("[]")
    (tmp_path / "items.json").write_text("[]")
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name):
        build.run_extract_maps(tmp_path / "fake.nsp")

    env = captured["env"]
    assert env.get("MY_TEST_MARKER") == "carry-me-through", (
        "existing env vars must be preserved alongside PYTHONUNBUFFERED"
    )
    assert env.get("SMOAP_LLVM_BIN") == "C:/portable/llvm/bin"


# ---- _python_invoker ----------------------------------------------------

def test_python_invoker_uses_sys_executable_when_real_python(monkeypatch) -> None:
    """Dev / source checkout: sys.executable is a real Python interp;
    don't fall through to the `py` launcher, just use it directly so the
    script runs under the developer's venv.

    Path uses forward slashes (still a valid Windows path) so PosixPath
    on CI also parses it correctly — backslash separators would only be
    recognized by WindowsPath, but pathlib picks the local-OS flavor.
    """
    monkeypatch.setattr(build.sys, "executable", "C:/Users/me/.venv/Scripts/python.exe")
    assert build._python_invoker() == ["C:/Users/me/.venv/Scripts/python.exe"]


def test_python_invoker_uses_py_launcher_when_sys_executable_is_frozen_launcher(
    monkeypatch,
) -> None:
    """Frozen AP Launcher: sys.executable is ArchipelagoLauncher.exe, which
    is not a Python interpreter. Must fall back to `py -3.12` (already
    verified by the prereq check). Regression test for v0.1.8-alpha
    diagnostic-build crash: `[ArchipelagoLauncher.exe, '-u', script.py,
    ...]` runs the launcher with garbage argv and fails with
    'unrecognized arguments'."""
    monkeypatch.setattr(
        build.sys, "executable", r"C:\ProgramData\Archipelago\ArchipelagoLauncher.exe",
    )
    monkeypatch.setattr(build.shutil, "which",
                        lambda name: r"C:\Windows\py.exe" if name == "py" else None)
    assert build._python_invoker() == ["py", "-3.12"]


def test_python_invoker_falls_through_to_python312_when_no_py_launcher(
    monkeypatch,
) -> None:
    """POSIX or stripped-down Windows where `py` isn't installed: prefer
    `python3.12` (matches the extractor's bootstrap-venv version) over
    plain `python` so we don't end up running the extractor on 3.13+
    where `oead` has no wheel."""
    monkeypatch.setattr(
        build.sys, "executable", r"C:\ProgramData\Archipelago\ArchipelagoLauncher.exe",
    )
    monkeypatch.setattr(
        build.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in ("python3.12", "python3") else None,
    )
    assert build._python_invoker() == ["python3.12"]


def test_python_invoker_handles_pythonw(monkeypatch) -> None:
    """pythonw.exe (Windows no-console Python) is a real Python interp;
    must be recognized as such even though its name isn't 'python'.

    Forward slashes so PosixPath on CI parses it correctly (see sibling
    test above for the cross-platform note).
    """
    monkeypatch.setattr(build.sys, "executable", "C:/Python313/pythonw.exe")
    assert build._python_invoker() == ["C:/Python313/pythonw.exe"]


def test_run_sync_capture_table_threads_explicit_paths(tmp_path) -> None:
    """sync_capture_table.py has the same REPO_ROOT-relative defaults as
    extract_shine_map.py — items.json + the capture_table.h output path
    + capture_map.json. All three must be passed explicitly from the
    wizard because the bundled layout doesn't match the dev-checkout
    layout the defaults assume.

    Regression test for the v0.1.8-alpha bug report:
      items.json not found at C:\\...\\bundled\\apworld\\smo_archipelago\\data\\items.json
      [stream] subprocess exited with code 1"""
    captured: dict = {}

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None, **_kwargs):
        captured["cmd"] = cmd
        return build.BuildResult(ok=True, returncode=0, log="")

    (tmp_path / "sync_capture_table.py").write_text("")
    (tmp_path / "items.json").write_text("[]")
    fake_mod = tmp_path / "switch_mod"
    fake_mod.mkdir()
    with patch.object(build, "_stream_subprocess", fake_stream), \
         patch.object(build, "bundled_script",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_data_file",
                      lambda name: tmp_path / name), \
         patch.object(build, "bundled_switch_mod", lambda: fake_mod), \
         patch.object(build, "data_dir", lambda: tmp_path):
        build.run_sync_capture_table()

    cmd = captured["cmd"]
    assert "--items" in cmd, f"--items missing from sync cmd: {cmd}"
    assert "--out" in cmd, f"--out missing from sync cmd: {cmd}"
    assert "--capture-map" in cmd, f"--capture-map missing from sync cmd: {cmd}"
    # The --out path must land inside switch_mod so the cmake build picks
    # up the generated header.
    out_idx = cmd.index("--out") + 1
    assert "switch_mod" in cmd[out_idx], (
        f"--out {cmd[out_idx]!r} should land inside switch_mod/ so cmake "
        f"finds the generated capture_table.h"
    )
