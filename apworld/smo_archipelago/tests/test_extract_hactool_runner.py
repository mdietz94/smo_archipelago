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


def test_nonzero_returncode_still_exits(extract_mod, tmp_path):
    """Even with no `Error:` lines in stdout, a non-zero exit code is fatal
    — hactool crashed or aborted before completing.
    """
    keys = tmp_path / "prod.keys"
    keys.write_text("")
    hactool = tmp_path / "hactool.exe"
    hactool.write_bytes(b"")

    with patch.object(extract_mod.subprocess, "Popen",
                      side_effect=_fake_popen([], returncode=2)):
        with pytest.raises(SystemExit) as exc_info:
            extract_mod._run_hactool(hactool, keys, "-t", "nca")

    assert "hactool exited 2" in str(exc_info.value)


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
