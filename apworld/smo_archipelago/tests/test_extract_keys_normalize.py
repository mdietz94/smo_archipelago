"""Tests for `_normalize_keys_file` in scripts/extract_shine_map.py.

Some Switch key dumpers emit prod.keys with oversized entries — most
commonly `header_key` as 34 bytes (64 hex chars + a stray `0000` from a
buffer-overrun bug). hactool rejects these with "key '...' is wrong size".
We auto-trim trailing zero bytes into a COPY of the prod.keys file (never
modifying the user's input) and pass the copy to hactool. Any other
mismatch (non-zero trailing bytes, undersized key, overage beyond a small
budget) raises with an actionable "re-dump" message rather than guessing.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent.parent.parent
          / "scripts" / "extract_shine_map.py")


def _load_extract_module():
    """Import scripts/extract_shine_map.py without its oead dependency.

    Mirrors test_extract_hactool_runner._load_extract_module — same stub
    pattern, same stdout/stderr shielding for the module's TextIOWrapper
    re-wrap.
    """
    if not SCRIPT.exists():
        pytest.skip(f"{SCRIPT} not present (running from installed apworld)")
    if "oead" not in sys.modules:
        stub = types.ModuleType("oead")
        stub.yaz0 = types.SimpleNamespace(decompress=lambda b: b)
        stub.Sarc = MagicMock()
        stub.byml = types.SimpleNamespace(from_binary=lambda b: {})
        sys.modules["oead"] = stub
    spec = importlib.util.spec_from_file_location(
        "extract_shine_map_under_test_keys", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
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


# 32-byte header_key (64 hex chars), 16-byte titlekek_02 (32 hex chars).
# Values are arbitrary non-zero bytes — what matters for these tests is
# the LENGTH, not the contents.
HEADER_KEY_32B_HEX = "11" * 32
HEADER_KEY_TRIMMED_HEX = HEADER_KEY_32B_HEX  # post-trim target
TITLEKEK_02_16B_HEX = "22" * 16


def _write_keys(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_clean_prod_keys_returns_input_path(extract_mod, tmp_path):
    """No mismatches -> we return the original path, no file written."""
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    _write_keys(keys, (
        f"header_key = {HEADER_KEY_32B_HEX}\n"
        f"titlekek_02 = {TITLEKEK_02_16B_HEX}\n"
    ))

    result = extract_mod._normalize_keys_file(keys, work_dir)

    assert result == keys
    assert not (work_dir / "prod.keys.normalized").exists()


def test_header_key_34b_with_trailing_zeros_is_trimmed(extract_mod, tmp_path):
    """The motivating case: 34-byte header_key with trailing 0x00 0x00.
    We expect a normalized copy under work_dir; the original is untouched.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    original_body = (
        "# user's hand-edited prod.keys\n"
        f"header_key = {HEADER_KEY_32B_HEX}0000\n"
        f"titlekek_02 = {TITLEKEK_02_16B_HEX}\n"
        "master_key_00 = " + "aa" * 16 + "\n"
    )
    _write_keys(keys, original_body)

    result = extract_mod._normalize_keys_file(keys, work_dir)

    assert result != keys
    assert result == work_dir / "prod.keys.normalized"
    # Original file is byte-for-byte unchanged.
    assert keys.read_text(encoding="utf-8") == original_body
    # The normalized copy parses with the right length on header_key,
    # untouched lengths everywhere else.
    parsed = extract_mod._parse_keys_file(result)
    assert len(parsed["header_key"]) == 32
    assert parsed["header_key"].hex() == HEADER_KEY_TRIMMED_HEX
    assert parsed["titlekek_02"].hex() == TITLEKEK_02_16B_HEX
    assert len(parsed["master_key_00"]) == 16


def test_normalized_copy_is_overwritten_on_rerun(extract_mod, tmp_path):
    """Re-running with the same work_dir replaces (not appends to) the copy."""
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    _write_keys(keys, f"header_key = {HEADER_KEY_32B_HEX}0000\n")

    first = extract_mod._normalize_keys_file(keys, work_dir)
    second = extract_mod._normalize_keys_file(keys, work_dir)

    assert first == second
    # File contents are deterministic — same input -> same output.
    assert first.read_text(encoding="utf-8").count("header_key") == 1


def test_oversized_with_nonzero_trailing_bytes_raises(extract_mod, tmp_path):
    """Trailing bytes that aren't 0x00 mean the key is corrupt, not padded —
    we refuse to guess and surface a re-dump message.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    _write_keys(keys, f"header_key = {HEADER_KEY_32B_HEX}beef\n")

    with pytest.raises(RuntimeError, match=r"trailing byte\(s\) are not zero"):
        extract_mod._normalize_keys_file(keys, work_dir)


def test_undersized_key_raises(extract_mod, tmp_path):
    """A short header_key is truncation, not padding — must error."""
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    _write_keys(keys, "header_key = " + "11" * 30 + "\n")  # 30 bytes

    with pytest.raises(RuntimeError, match=r"appears truncated"):
        extract_mod._normalize_keys_file(keys, work_dir)


def test_overage_beyond_budget_raises(extract_mod, tmp_path):
    """If the overage exceeds MAX_KEY_OVERAGE (4 bytes), it isn't padding
    — error out rather than rewrite a clearly-malformed key.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    # 32 + 6 trailing zero bytes (6 > MAX_KEY_OVERAGE=4)
    _write_keys(keys, f"header_key = {HEADER_KEY_32B_HEX}000000000000\n")

    with pytest.raises(RuntimeError, match=r"too large to be padding"):
        extract_mod._normalize_keys_file(keys, work_dir)


def test_unknown_key_passes_through(extract_mod, tmp_path):
    """Keys not in EXPECTED_KEY_SIZES (e.g. a future BIS key variant)
    must not be touched by the normalizer.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    # 50 bytes of a hypothetical future key — we have no expected size
    # for `mystery_key`, so we leave it alone.
    body = "mystery_key = " + "ab" * 50 + "\n"
    _write_keys(keys, body)

    result = extract_mod._normalize_keys_file(keys, work_dir)

    assert result == keys
    assert not (work_dir / "prod.keys.normalized").exists()


def test_odd_hex_line_is_passed_through(extract_mod, tmp_path):
    """Lines with malformed hex (odd digit count, non-hex chars) are
    `_parse_keys_file`'s territory to ignore; the normalizer must also
    leave them alone so a stray comment-line typo doesn't blow up the
    whole audit.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    body = (
        "broken_line = ZZ_not_hex\n"
        "another = abc\n"  # odd nibble count
        f"header_key = {HEADER_KEY_32B_HEX}\n"
    )
    _write_keys(keys, body)

    result = extract_mod._normalize_keys_file(keys, work_dir)

    assert result == keys


def test_titlekek_padding_is_trimmed(extract_mod, tmp_path):
    """The same trimming logic applies to titlekek_XX / master_key_XX
    (16-byte single-AES keys), not just to header_key. Confirms the
    EXPECTED_KEY_SIZES table is consulted, not a hard-coded
    `header_key` branch.
    """
    keys = tmp_path / "prod.keys"
    work_dir = tmp_path / "work"
    # titlekek_02: 16 expected + 2 trailing zero bytes (the same shape
    # as the user's 34-byte header_key bug, just at half the size).
    _write_keys(keys, f"titlekek_02 = {TITLEKEK_02_16B_HEX}0000\n")

    result = extract_mod._normalize_keys_file(keys, work_dir)

    assert result == work_dir / "prod.keys.normalized"
    parsed = extract_mod._parse_keys_file(result)
    assert parsed["titlekek_02"].hex() == TITLEKEK_02_16B_HEX
