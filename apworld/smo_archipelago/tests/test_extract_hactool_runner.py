"""Tests for `_run_hactool` in scripts/extract_shine_map.py.

The interesting bit is the section-corruption tolerance flow. hactool 1.4.0
reports `Error: section X is corrupted!` whenever the IVFC superblock hash
check fails on a decrypted RomFS section — but it ALSO writes the extracted
files to disk first, so the files we actually need (~4 small SZS/MSBT files)
may be intact even when the broader section is "corrupt". The old behavior
exited on any `Error:` line; the new behavior:

  - exits on `Unable to match rights id to titlekey` (keys problem)
  - exits on any `Error:` line that is NOT `... is corrupted!` (unknown)
  - exits on non-zero exit code
  - returns the section-corrupted lines for the caller to log and proceed

The caller (`extract_romfs`) logs a warning and continues; the real integrity
check is whether oead can parse the ~4 files we need downstream in `main()`.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent.parent.parent
          / "scripts" / "extract_shine_map.py")


def _load_extract_module():
    """Import scripts/extract_shine_map.py without its oead dependency.

    The script `import oead`s at top-level and falls into a self-bootstrap
    that creates a venv if the import fails. We don't want either side
    effect in tests, so we inject a stub `oead` module before loading.
    """
    if not SCRIPT.exists():
        pytest.skip(f"{SCRIPT} not present (running from installed apworld)")
    # Stub oead and a couple of its sub-attributes used at module load.
    if "oead" not in sys.modules:
        stub = types.ModuleType("oead")
        stub.yaz0 = types.SimpleNamespace(decompress=lambda b: b)
        stub.Sarc = MagicMock()
        stub.byml = types.SimpleNamespace(from_binary=lambda b: {})
        sys.modules["oead"] = stub
    spec = importlib.util.spec_from_file_location(
        "extract_shine_map_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec — `@dataclass` in Python 3.13
    # looks up `cls.__module__` in `sys.modules` to resolve string
    # annotations (`from __future__ import annotations` is in effect), and
    # crashes with AttributeError on a missing module entry.
    sys.modules[spec.name] = mod
    # The script wraps sys.stdout in a TextIOWrapper at module load so it
    # can emit Japanese strings. That wrapper takes ownership of the
    # underlying buffer — when it's later GC'd, the buffer is closed.
    # In production that's pytest-irrelevant; in tests it closes pytest's
    # captured stdout file descriptor underneath us. Substitute a
    # devnull-backed stream so the wrap targets something disposable,
    # then restore the real stdout/stderr.
    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    sys.stdout = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")
    sys.stderr = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
    return mod


@pytest.fixture(scope="module")
def extract_mod():
    return _load_extract_module()


def _fake_popen(stdout_lines: list[str], returncode: int = 0):
    """Build a subprocess.Popen replacement that yields the given lines.

    Mirrors enough of Popen's interface for _run_hactool: `.stdout` is an
    iterable of lines, `.wait()` returns the supplied returncode.
    """
    def _factory(*args, **kwargs):
        m = MagicMock()
        m.stdout = iter(stdout_lines)
        m.wait.return_value = returncode
        return m
    return _factory


# -- the tolerant section-corruption path --


def test_section_corrupted_returns_lines_without_exiting(extract_mod, tmp_path):
    """The user's bug report: hactool reported section corruption but the
    title key derivation succeeded. We should NOT sys.exit — the caller is
    responsible for deciding what to do with the extracted (possibly partial)
    output.
    """
    fake_output = [
        "PFS0:\n",
        "Magic:                              PFS0\n",
        "Done!\n",
        "Error: section 0 is corrupted!\n",
        "Error: section 1 is corrupted!\n",
    ]
    keys = tmp_path / "prod.keys"
    keys.write_text("titlekek_02 = 00000000000000000000000000000000\n")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")  # existence is all we check

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen(fake_output, returncode=0)):
        result = extract_mod._run_hactool(hactool, keys, "-t", "nca")

    assert len(result.section_corrupt_lines) == 2
    assert "section 0 is corrupted" in result.section_corrupt_lines[0]
    assert "section 1 is corrupted" in result.section_corrupt_lines[1]
    assert result.returncode == 0


# -- the still-fatal paths --


def test_titlekey_missing_still_exits(extract_mod, tmp_path):
    """Section-corrupt + titlekey-missing means we have no romfs at all.
    The script must still surface the actionable "update title.keys"
    message rather than tolerating the corruption.
    """
    fake_output = [
        "[WARN] Unable to match rights id to titlekey. Update title.keys?\n",
        "Error: section 0 is corrupted!\n",
    ]
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen(fake_output, returncode=0)):
        with pytest.raises(SystemExit) as exc_info:
            extract_mod._run_hactool(hactool, keys, "-t", "nca")

    msg = str(exc_info.value)
    assert "title.keys" in msg
    assert "01000000000100000000000000000003" in msg  # SMO rights ID


def test_unknown_error_still_exits(extract_mod, tmp_path):
    """`Error:` lines that aren't `... is corrupted!` are unknown territory
    — we don't know whether the extraction succeeded, so refuse to continue.
    """
    fake_output = [
        "Error: failed to open file for reading.\n",
    ]
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen(fake_output, returncode=0)):
        with pytest.raises(SystemExit) as exc_info:
            extract_mod._run_hactool(hactool, keys, "-t", "nca")

    assert "hactool reported failures" in str(exc_info.value)
    assert "failed to open file" in str(exc_info.value)


def test_nonzero_returncode_still_exits_with_actionable_message(extract_mod, tmp_path):
    """Even with no `Error:` lines in stdout, a non-zero exit code is fatal
    — hactool crashed or aborted before completing. Common real-world cause:
    a truncated PFS0 where hactool prints "Failed to read file!" and exits
    non-zero without prefixing the line with "Error:". Surface the actionable
    "re-dump" diagnostic so the user knows what to do.
    """
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen(["Failed to read file!\n"], returncode=1)):
        with pytest.raises(SystemExit) as exc_info:
            extract_mod._run_hactool(hactool, keys, "-t", "nca")

    msg = str(exc_info.value)
    assert "exit code 1" in msg
    assert "NXDumpTool" in msg  # actionable advice present
    assert "Re-dump" in msg


# -- happy path: no errors, returns clean result --


def test_clean_extraction_returns_empty_result(extract_mod, tmp_path):
    fake_output = [
        "PFS0:\n",
        "Magic:                              PFS0\n",
        "Saving foo.nca to /tmp/foo.nca...\n",
        "Done!\n",
    ]
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen(fake_output, returncode=0)):
        result = extract_mod._run_hactool(hactool, keys, "-t", "pfs0")

    assert result.section_corrupt_lines == []
    assert result.returncode == 0


# -- --titlekey value MUST be a 32-char hex key, never a path --
#
# Regression guard. Hactool's `--titlekey=val` parses `val` via
# `parse_hex_key(..., 16)` (16 raw bytes = 32 hex chars). A filesystem path
# like `C:\Users\maxwe\.switch\title.keys` is not hex — hactool exits with
# "Key must be hex!" before extraction starts. There's also no hactool flag
# for overriding the title.keys file path (it only auto-loads from
# `$HOME/.switch/title.keys`). An earlier version of `_run_hactool` happily
# forwarded `--titlekey=<path>` when `title_keys=...` was passed, which
# silently broke any XCI extract whose `title.keys` lived outside the
# default location.


def _capturing_popen_factory(seen_cmds: list[list[str]],
                             stdout_lines: list[str], returncode: int = 0):
    """Like _fake_popen but records each invocation's argv into `seen_cmds`."""
    def _factory(cmd, *args, **kwargs):
        seen_cmds.append(list(cmd))
        m = MagicMock()
        m.stdout = iter(stdout_lines)
        m.wait.return_value = returncode
        return m
    return _factory


def test_titlekey_arg_value_is_hex_not_path(extract_mod, tmp_path):
    """`_run_hactool` must NEVER forward a filesystem path as the value of
    `--titlekey=`. The `title_keys=` parameter is now metadata for error
    messaging only; the actual `--titlekey=<hex>` arg (if any) is built by
    the caller and threaded in via `*args`.
    """
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    title_keys = tmp_path / "title.keys"
    title_keys.write_text(
        # SMO 1.0.0 rights id = encrypted titlekey (arbitrary value, just
        # has to be 32 hex chars so anything that *does* look it up gets a
        # parseable result).
        "01000000000100000000000000000003=00112233445566778899aabbccddeeff\n",
        encoding="utf-8",
    )
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    seen: list[list[str]] = []
    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_capturing_popen_factory(
                          seen, ["Done!\n"], returncode=0)):
        extract_mod._run_hactool(
            hactool, keys, "-t", "nca", title_keys=title_keys)

    assert len(seen) == 1
    cmd = seen[0]
    # Hactool gets no --titlekey at all here — the caller supplied none.
    titlekey_args = [a for a in cmd if a.startswith("--titlekey=")]
    assert titlekey_args == [], (
        f"_run_hactool must not forward title_keys path as --titlekey=, "
        f"got: {titlekey_args!r}"
    )
    # Sanity: title_keys' string form must not appear anywhere in the argv
    # (would catch any other accidental --titlekey-adjacent leak too).
    assert str(title_keys) not in cmd, (
        f"title_keys path leaked into hactool argv: {cmd!r}"
    )


def test_titlekey_arg_value_is_hex_when_caller_forwards_it(extract_mod, tmp_path):
    """When the caller threads `--titlekey=<hex>` via *args (the .tik
    derivation path), it survives intact — 32 hex chars, no path."""
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")
    expected_hex = "00112233445566778899aabbccddeeff"

    seen: list[list[str]] = []
    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_capturing_popen_factory(
                          seen, ["Done!\n"], returncode=0)):
        extract_mod._run_hactool(
            hactool, keys, "-t", "nca", f"--titlekey={expected_hex}")

    assert len(seen) == 1
    cmd = seen[0]
    titlekey_args = [a for a in cmd if a.startswith("--titlekey=")]
    assert titlekey_args == [f"--titlekey={expected_hex}"]
    # The value is exactly 32 hex chars (what hactool's parse_hex_key wants).
    value = titlekey_args[0].split("=", 1)[1]
    assert len(value) == 32
    assert all(c in "0123456789abcdefABCDEF" for c in value), (
        f"--titlekey value is not hex: {value!r}"
    )


def test_lookup_title_key_returns_hex_for_present_entry(extract_mod, tmp_path):
    """`_lookup_title_key` parses Switch title.keys files and returns the
    encrypted titlekey as 32 lowercase hex chars — the form hactool wants."""
    title_keys = tmp_path / "title.keys"
    title_keys.write_text(
        "01000000000100000000000000000003 = 00112233445566778899AABBCCDDEEFF\n"
        "# unrelated comment line\n"
        "0100000000010001000000000000000a = deadbeefdeadbeefdeadbeefdeadbeef\n",
        encoding="utf-8",
    )

    got = extract_mod._lookup_title_key(
        title_keys, "01000000000100000000000000000003")
    assert got == "00112233445566778899aabbccddeeff"
    assert len(got) == 32


def test_lookup_title_key_returns_none_for_missing_entry(extract_mod, tmp_path):
    """No matching rights ID → None (hactool's own title.keys auto-load gets
    a chance, and if that also fails the WARN-detection branch surfaces the
    actionable "Update title.keys" diagnostic)."""
    title_keys = tmp_path / "title.keys"
    title_keys.write_text(
        "ffffffffffffffffffffffffffffffff=00112233445566778899aabbccddeeff\n",
        encoding="utf-8",
    )
    got = extract_mod._lookup_title_key(
        title_keys, "01000000000100000000000000000003")
    assert got is None


def test_lookup_title_key_returns_none_for_missing_file(extract_mod, tmp_path):
    """Caller treats `None` as "fall through to hactool's auto-load" — must
    not raise on a path that doesn't exist."""
    got = extract_mod._lookup_title_key(
        tmp_path / "does-not-exist.keys",
        "01000000000100000000000000000003")
    assert got is None


# -- resolve_hactool fallback paths --
#
# Regression guard for the e1bcdbd drift bug: install_hactool moved the
# wizard's auto-install destination from `%APPDATA%/SMOArchipelago/
# bundled/hactool.exe` → `%APPDATA%/SMOArchipelago/hactool.exe`, but the
# extractor's fallback constant kept pointing at the old `bundled/` path.
# Auto-install users whose wizard didn't persist a `--hactool` override
# got "ERROR: hactool.exe not found" even though the wizard had just
# downloaded it successfully. Lock the new-location + legacy-location
# resolution in.


def test_resolve_hactool_finds_new_appdata_root_location(extract_mod, tmp_path):
    """When `bundled_hactool_path()` is populated (the new top-level
    APPDATA location), `resolve_hactool(None)` returns it."""
    new_loc = tmp_path / "hactool.exe"
    new_loc.write_bytes(b"")
    with patch.object(extract_mod, "DEFAULT_HACTOOL_FALLBACK", new_loc), \
         patch.object(extract_mod, "LEGACY_HACTOOL_FALLBACK",
                      tmp_path / "bundled" / "hactool.exe"), \
         patch.object(extract_mod.shutil, "which", return_value=None):
        got = extract_mod.resolve_hactool(None)
    assert got == new_loc


def test_resolve_hactool_falls_back_to_legacy_bundled_location(extract_mod, tmp_path):
    """Users mid-migration (installed pre-e1bcdbd, haven't re-run the
    wizard's install_hactool) still have `bundled/hactool.exe`. Probe
    it as a secondary fallback so they aren't broken."""
    legacy = tmp_path / "bundled" / "hactool.exe"
    legacy.parent.mkdir()
    legacy.write_bytes(b"")
    with patch.object(extract_mod, "DEFAULT_HACTOOL_FALLBACK",
                      tmp_path / "hactool.exe"), \
         patch.object(extract_mod, "LEGACY_HACTOOL_FALLBACK", legacy), \
         patch.object(extract_mod.shutil, "which", return_value=None):
        got = extract_mod.resolve_hactool(None)
    assert got == legacy


def test_resolve_hactool_error_mentions_both_locations(extract_mod, tmp_path):
    """When nothing resolves, the error names BOTH fallback paths so a
    user staring at it can figure out where to drop the file."""
    new_loc = tmp_path / "hactool.exe"
    legacy = tmp_path / "bundled" / "hactool.exe"
    with patch.object(extract_mod, "DEFAULT_HACTOOL_FALLBACK", new_loc), \
         patch.object(extract_mod, "LEGACY_HACTOOL_FALLBACK", legacy), \
         patch.object(extract_mod.shutil, "which", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            extract_mod.resolve_hactool(None)
    msg = str(exc_info.value)
    assert str(new_loc) in msg
    assert str(legacy) in msg
