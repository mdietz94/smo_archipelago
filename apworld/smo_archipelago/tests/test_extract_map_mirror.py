"""Tests for the post-extract user-data mirror in scripts/extract_shine_map.py.

`main()` copies the freshly-written shine/capture maps into the per-user data
dir and touches the `.maps-updated` sentinel, so an *installed* apworld zip
(which never bundles the maps — they're Nintendo IP) resolves them without the
Windows wizard having run. Three properties matter and none of them are
obvious from reading the copy loop:

  - It only fires for a default-path run. `tests/test_extract_real_nsp.py`
    redirects every output under `tmp_path` specifically so a test run can't
    touch real state — including `test_minor_corruption_extracts_anyway`,
    which extracts from a deliberately corrupted NSP and passes on as few as
    500 of the 775 moons. An unconditional mirror would let that degraded map
    overwrite a real install's good one.
  - `_APPDATA_ROOT` honours `SMOAP_APPDATA_ROOT`, matching
    `_setup.appdata_root()`. The release-audit sandbox sets that override so
    the user's real %APPDATA% is never touched; deriving the root from
    APPDATA alone would let the mirror escape the sandbox.
  - A failed copy leaves the previous map intact. SMOClient's
    `_resolve_map_path` prefers the user-data copy over the loose
    `client/data/` one, so a truncated file there is worse than no file.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import shutil
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent.parent.parent
          / "scripts" / "extract_shine_map.py")


def _load_extract_module():
    """Import scripts/extract_shine_map.py without its oead dependency.

    Same shim as `test_extract_hactool_runner.py` — the script `import
    oead`s at top-level and self-bootstraps a venv when that fails, and it
    rewraps sys.stdout in a TextIOWrapper that would close pytest's captured
    fd when GC'd. Deliberately NOT cached in a module-scoped fixture: the
    override test needs the module re-executed under a different environment,
    and `_APPDATA_ROOT` is resolved at import time.
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
        "extract_shine_map_mirror_under_test", SCRIPT)
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


@pytest.fixture
def extract_mod():
    return _load_extract_module()


def _args(out: Path, cap_out: Path) -> argparse.Namespace:
    return argparse.Namespace(out=out, cap_out=cap_out)


# ---------- gating ----------

def test_mirror_wanted_at_default_paths(extract_mod) -> None:
    """The plain `python scripts/extract_shine_map.py --nsp <dump>` run is
    exactly the case the mirror exists for."""
    assert extract_mod._mirror_is_wanted(
        _args(extract_mod.DEFAULT_OUT, extract_mod.DEFAULT_CAP_OUT))


def test_mirror_skipped_when_out_redirected(extract_mod, tmp_path) -> None:
    """A caller that directed `--out` elsewhere owns its own output.

    This is the shape `tests/test_extract_real_nsp.py::_run_extract` uses —
    every output under tmp_path so a real-NSP test run (including the
    corrupted-dump one) cannot clobber the maps a real install depends on.
    """
    assert not extract_mod._mirror_is_wanted(
        _args(tmp_path / "shine_map.json", tmp_path / "capture_map.json"))


def test_mirror_skipped_when_only_cap_out_redirected(
    extract_mod, tmp_path,
) -> None:
    """Half-redirected is still redirected — mirroring one of a pair would
    leave a fresh shine_map next to a stale capture_map."""
    assert not extract_mod._mirror_is_wanted(
        _args(extract_mod.DEFAULT_OUT, tmp_path / "capture_map.json"))


# ---------- SMOAP_APPDATA_ROOT ----------

def test_appdata_root_honours_override(monkeypatch, tmp_path) -> None:
    """`SMOAP_APPDATA_ROOT` wins over APPDATA, matching
    `_setup.appdata_root()`. The release-audit sandbox depends on this."""
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("SMOAP_APPDATA_ROOT", str(sandbox))
    monkeypatch.setenv("APPDATA", str(tmp_path / "real-appdata"))
    mod = _load_extract_module()
    assert mod._APPDATA_ROOT == sandbox
    # The hactool fallback rides on the same root — keep it in the sandbox
    # too, so an audit run can't pick up the dev machine's install.
    assert mod.DEFAULT_HACTOOL_FALLBACK.parent == sandbox


def test_appdata_root_without_override(monkeypatch, tmp_path) -> None:
    """Unset override → the normal %APPDATA% path (or the POSIX fallback)."""
    monkeypatch.delenv("SMOAP_APPDATA_ROOT", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "real-appdata"))
    mod = _load_extract_module()
    assert mod._APPDATA_ROOT == tmp_path / "real-appdata" / "SMOArchipelago"


# ---------- copy behaviour ----------

def _write_maps(src_dir: Path) -> list[Path]:
    src_dir.mkdir(parents=True, exist_ok=True)
    shine = src_dir / "shine_map.json"
    cap = src_dir / "capture_map.json"
    shine.write_text('{"fresh": "shine"}', encoding="utf-8")
    cap.write_text('{"fresh": "capture"}', encoding="utf-8")
    return [shine, cap]


def test_mirror_copies_and_stamps_sentinel(
    extract_mod, monkeypatch, tmp_path,
) -> None:
    root = tmp_path / "userdata"
    monkeypatch.setattr(extract_mod, "_APPDATA_ROOT", root)
    srcs = _write_maps(tmp_path / "client-data")

    extract_mod._mirror_maps_to_user_data(srcs)

    data = root / "data"
    assert (data / "shine_map.json").read_text(encoding="utf-8") == '{"fresh": "shine"}'
    assert (data / "capture_map.json").read_text(encoding="utf-8") == '{"fresh": "capture"}'
    assert (root / ".maps-updated").is_file()
    # The staging files must not survive a successful run.
    assert not list(data.glob("*.tmp"))


def test_mirror_is_noop_when_dst_is_src(
    extract_mod, monkeypatch, tmp_path,
) -> None:
    """Nothing copied and no sentinel when the outputs were already written
    into the user-data dir — the wizard's shape, where wizard_cli owns the
    sentinel."""
    root = tmp_path / "userdata"
    monkeypatch.setattr(extract_mod, "_APPDATA_ROOT", root)
    srcs = _write_maps(root / "data")

    extract_mod._mirror_maps_to_user_data(srcs)

    assert not (root / ".maps-updated").exists()


def test_failed_copy_preserves_previous_map(
    extract_mod, monkeypatch, tmp_path, capsys,
) -> None:
    """A copy that dies partway must not leave a truncated map behind.

    SMOClient's `_resolve_map_path` prefers the user-data copy over the
    loose `client/data/` one, so a half-written file there would surface as
    a JSONDecodeError at client startup while the extraction still reported
    success. Staging through `.tmp` + `os.replace` keeps the old map intact.
    """
    root = tmp_path / "userdata"
    monkeypatch.setattr(extract_mod, "_APPDATA_ROOT", root)
    data = root / "data"
    data.mkdir(parents=True)
    (data / "shine_map.json").write_text('{"good": "old"}', encoding="utf-8")
    srcs = _write_maps(tmp_path / "client-data")

    real_copy2 = shutil.copy2

    def _copy2_enospc(src, dst, *a, **kw):
        if Path(src).name == "shine_map.json":
            Path(dst).write_text('{"trun', encoding="utf-8")  # partial write
            raise OSError(28, "No space left on device")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(extract_mod.shutil, "copy2", _copy2_enospc)

    extract_mod._mirror_maps_to_user_data(srcs)

    # Old map survived; the truncated staging file was cleaned up.
    assert (data / "shine_map.json").read_text(encoding="utf-8") == '{"good": "old"}'
    assert not list(data.glob("*.tmp"))
    # A partial mirror must not advertise itself as a fresh extraction.
    assert not (root / ".maps-updated").exists()
    assert "WARNING" in capsys.readouterr().err


def test_failed_copy_does_not_raise(
    extract_mod, monkeypatch, tmp_path,
) -> None:
    """Best-effort: the mirror never fails the extraction. The maps at
    client/data/ are fully usable without it."""
    monkeypatch.setattr(extract_mod, "_APPDATA_ROOT", tmp_path / "userdata")
    srcs = _write_maps(tmp_path / "client-data")

    def _boom(*a, **kw):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(extract_mod.shutil, "copy2", _boom)

    extract_mod._mirror_maps_to_user_data(srcs)  # must not raise
