"""Tests for `__init__.launch_smo_client` — making sure the file-association
entry path stays on the inline-when-no-Kivy `launch` helper rather than
falling back to `launch_subprocess` (which is broken under PyInstaller-frozen
Archipelago because the multiprocessing.Process child can't read its
bundled `kivy/data/style.kv` out of library.zip).

These tests intentionally import via `worlds.smo.*`, which requires
Archipelago itself to be on sys.path AND a built `smo.apworld` in
`vendor/Archipelago/custom_worlds/` (run `scripts/install_apworld.py`).
The conftest deliberately keeps `vendor/Archipelago` off `sys.path` for
the rest of the suite (see conftest.py:7-17) — to avoid violating that
during collection, the path mutation and `import worlds.smo` are deferred
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
    """Load `worlds.smo` lazily.

    Done as a fixture (not at module scope) so pytest's collection phase
    never triggers Archipelago's `worlds/__init__.py` discovery walk —
    that walk pollutes `sys.modules` and `AutoWorldRegister` globally and
    has caused cross-file "passes alone, fails in suite" flakes in this
    test directory. Skips cleanly on worktrees where
    `scripts/install_apworld.py` hasn't yet dropped `smo.apworld` into
    `vendor/Archipelago/custom_worlds/`."""
    if str(_AP_ROOT) not in sys.path:
        sys.path.insert(0, str(_AP_ROOT))
    try:
        import ModuleUpdate  # type: ignore[import-not-found]
        ModuleUpdate.update_ran = True
    except ImportError:
        pass
    return pytest.importorskip(
        "worlds.smo",
        reason="smo.apworld not installed; run scripts/install_apworld.py first.",
    )


def test_launch_subprocess_not_imported(smo_mod) -> None:
    """`launch_subprocess` (multiprocessing.Process variant) must not be
    importable on `worlds.smo` — its presence on the namespace tempts
    future contributors to call it directly, reintroducing the frozen-Kivy
    crash. `launch_or_subprocess` (AP's `launch` helper) is the only
    sanctioned route."""
    assert not hasattr(smo_mod, "launch_subprocess"), (
        "launch_subprocess must not be imported into worlds.smo — use "
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
    `launch_subprocess` import was removed in the .apmanual cleanup
    (v0.1.x) — `test_launch_subprocess_not_imported` is the regression
    test that keeps it out."""
    via_launch: list[tuple] = []

    def fake_launch(func, name=None, args=()):
        via_launch.append((name, func.__name__, args))

    with patch.object(smo_mod, "launch_or_subprocess", fake_launch):
        yield via_launch


@pytest.fixture
def setup_state(monkeypatch):
    """Return a setter that controls what `is_setup_complete()` reports
    inside the launch-routing code path, without touching APPDATA env or
    filesystem.

    Earlier versions of these tests built sentinel files under a tmp
    APPDATA dir. That coupled the routing tests to (a) APPDATA env-var
    plumbing and (b) test-runner cwd/state, and produced an intermittent
    "passes in isolation, fails in suite" flake whose root cause was
    never pinned down (likely env-var ordering between tests on Windows).
    Direct patching of the boolean is the structural fix: the routing
    test only cares about the True/False decision is_setup_complete
    returns, NOT how it computes that boolean from paths.
    `test_is_setup_complete.py` has dedicated coverage for the path
    resolution itself."""
    import worlds.smo.client.setup_state as ss

    def set_complete(value: bool) -> None:
        monkeypatch.setattr(ss, "is_setup_complete", lambda: value)
        # __init__.py does `from .client.setup_state import is_setup_complete`
        # at function-call time inside launch_smo_client, so patching the
        # module attribute is enough — no rebinding of an already-imported
        # symbol needed.

    return set_complete


def _write_smoap(tmp_path: Path) -> Path:
    """Round-trip a SmoapFile to disk so the test exercises the real parser."""
    from _setup.smoap_file import SmoapFile  # type: ignore
    p = tmp_path / "AP_test_P1_Mario.smoap"
    SmoapFile(slot_name="Mario").write(p)
    return p


def test_pre_setup_click_routes_via_launch(spy, setup_state, tmp_path, smo_mod) -> None:
    """When the user double-clicks a .smoap and setup hasn't run yet, the
    wizard must dispatch through `launch_or_subprocess` (which inlines
    when Kivy isn't already running). The bare `launch_subprocess` route
    breaks Kivy bootstrap in the frozen Archipelago installer."""
    setup_state(False)
    smoap = _write_smoap(tmp_path)

    smo_mod.launch_smo_client(str(smoap))

    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOSetup"
    assert func_name == "_run_setup_wizard_with_smoap"
    assert args == (str(smoap),)


def test_post_setup_click_routes_via_launch(spy, setup_state, tmp_path, smo_mod) -> None:
    """Once setup is complete, the same double-click should still go
    through `launch_or_subprocess` — with the .smoap expanded to SMOClient
    CLI args."""
    setup_state(True)
    smoap = _write_smoap(tmp_path)

    smo_mod.launch_smo_client(str(smoap))

    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    # SmoapFile(slot_name="Mario") → ["--name", "Mario"]
    assert args == ("--name", "Mario")


def test_wizard_done_launch_button_handoff_runs_after_kivy_shutdown(
    spy, setup_state, tmp_path, smo_mod,
) -> None:
    """The wizard's "Launch SMOClient" button must NOT spawn from inside
    its own Kivy app — instead, `run_setup_wizard` returns True and the
    parent does the launch after `App().run()` returns. This test sims
    that handoff: pretend the wizard ran and returned True, then confirm
    `_run_setup_wizard_with_smoap` recursively dispatches SMOClient via
    `launch_or_subprocess`."""
    setup_state(True)
    smoap = _write_smoap(tmp_path)

    import worlds.smo._setup.wizard as wiz_mod
    with patch.object(wiz_mod, "run_setup_wizard", lambda _p: True):
        smo_mod._run_setup_wizard_with_smoap(str(smoap))

    # Expect exactly one inline-launch for SMOClient (the recursive
    # launch_smo_client invocation goes through the post-setup path).
    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    assert args == ("--name", "Mario")
