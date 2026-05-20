"""Tests for `__init__.launch_smo_client` — the Launcher's "SMO Client"
button + the `.meatballsap` file-association entry point.

Routing rule: always launch SMOClient. The setup wizard is opened
separately via `/setup` inside SMOClient and never auto-fires from this
function. `launch_smo_client` is responsible for expanding any `.meatballsap`
arg into `--name` (and optionally `--connect`) CLI overrides for the
SMOClient subprocess.

These tests intentionally import via `worlds.meatballs.*`, which requires
Archipelago itself to be on sys.path AND a built `meatballs.apworld` in
`vendor/Archipelago/custom_worlds/` (run `scripts/install_apworld.py`).
The conftest deliberately keeps `vendor/Archipelago` off `sys.path` for
the rest of the suite (see conftest.py:7-17) — to avoid violating that
during collection, the path mutation and `import worlds.meatballs` are deferred
into the `smo_mod` fixture below. Module-scope only checks for the
submodule's existence so a missing checkout still skips cleanly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent
_AP_ROOT = _REPO_ROOT / "vendor" / "Archipelago"

if not (_AP_ROOT / "Launcher.py").exists():
    pytest.skip("Archipelago submodule not initialized", allow_module_level=True)


@pytest.fixture
def smo_mod():
    """Load `worlds.meatballs` lazily.

    Done as a fixture (not at module scope) so pytest's collection phase
    never triggers Archipelago's `worlds/__init__.py` discovery walk —
    that walk pollutes `sys.modules` and `AutoWorldRegister` globally and
    has caused cross-file "passes alone, fails in suite" flakes in this
    test directory. Skips cleanly on worktrees where
    `scripts/install_apworld.py` hasn't yet dropped `meatballs.apworld` into
    `vendor/Archipelago/custom_worlds/`."""
    if str(_AP_ROOT) not in sys.path:
        sys.path.insert(0, str(_AP_ROOT))
    try:
        import ModuleUpdate  # type: ignore[import-not-found]
        ModuleUpdate.update_ran = True
    except ImportError:
        pass
    return pytest.importorskip(
        "worlds.meatballs",
        reason="meatballs.apworld not installed; run scripts/install_apworld.py first.",
    )


def test_launch_subprocess_not_imported(smo_mod) -> None:
    """`launch_subprocess` (multiprocessing.Process variant) must not be
    importable on `worlds.meatballs` — its presence on the namespace tempts
    future contributors to call it directly, reintroducing the frozen-Kivy
    crash. `launch_or_subprocess` (AP's `launch` helper) is the only
    sanctioned route."""
    assert not hasattr(smo_mod, "launch_subprocess"), (
        "launch_subprocess must not be imported into worlds.meatballs — use "
        "launch_or_subprocess (the `launch` helper) instead so file-association "
        "invocations stay inline."
    )
    assert hasattr(smo_mod, "launch_or_subprocess"), (
        "launch_or_subprocess must be imported; without it, the routing decoration "
        "for inline-vs-subprocess can't dispatch."
    )


@pytest.fixture
def spy(smo_mod) -> list:
    """Replace `launch_or_subprocess` with a recorder. The bare
    `launch_subprocess` import was removed during the v0.1.x Launcher
    cleanup — `test_launch_subprocess_not_imported` is the regression
    test that keeps it out."""
    via_launch: list[tuple] = []

    def fake_launch(func, name=None, args=()):
        via_launch.append((name, func.__name__, args))

    with patch.object(smo_mod, "launch_or_subprocess", fake_launch):
        yield via_launch


def _write_smoap(tmp_path: Path) -> Path:
    """Round-trip a SmoapFile to disk so the test exercises the real parser."""
    from _setup.smoap_file import SmoapFile  # type: ignore
    p = tmp_path / "AP_test_P1_Mario.meatballsap"
    SmoapFile(slot_name="Mario").write(p)
    return p


def test_smoap_click_routes_to_smoclient(spy, tmp_path, smo_mod) -> None:
    """Double-clicking a .meatballsap opens SMOClient with the slot pre-filled
    — regardless of whether setup has been run yet. The setup wizard is
    invoked via `/setup` inside SMOClient, never auto-fired here."""
    smoap = _write_smoap(tmp_path)

    smo_mod.launch_smo_client(str(smoap))

    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    # SmoapFile(slot_name="Mario") → ["--name", "Mario"]
    assert args == ("--name", "Mario")


def test_button_click_with_no_args_routes_to_smoclient(spy, smo_mod) -> None:
    """Plain "SMO Client" Launcher button click (no .meatballsap argument)
    still routes straight to SMOClient. SMOClient handles a missing slot
    via the GUI Connect bar."""
    smo_mod.launch_smo_client()

    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    assert args == ()
