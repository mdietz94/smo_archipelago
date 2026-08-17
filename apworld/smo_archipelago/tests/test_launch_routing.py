"""Tests for `__init__.launch_smo_client` — the Launcher's
"Meatballs Client" button + the `.meatballsap` file-association entry point.

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


def test_launcher_component_name_is_meatballs_searchable(smo_mod) -> None:
    """The Launcher button must be findable by typing "meatballs".

    Users routinely have this apworld and the other public SMO apworld
    installed side by side; when both register a button called "SMO
    Client" the only way to tell them apart is trial and error. Leading
    with the shipped zip stem fixes that — and, because
    `add_client_to_launcher`'s idempotency guard matches on
    `display_name`, it also stops the other apworld's registration from
    making ours no-op out entirely."""
    from worlds.LauncherComponents import components  # type: ignore[import-not-found]

    ours = [
        c for c in components
        if c.display_name == smo_mod.CLIENT_DISPLAY_NAME
    ]
    assert len(ours) == 1, "exactly one Meatballs client Component expected"
    assert "meatballs" in smo_mod.CLIENT_DISPLAY_NAME.lower()
    # And it must no longer collide with the other apworld's button — a
    # name containing "SMO Client" would defeat the point.
    assert "SMO Client" not in smo_mod.CLIENT_DISPLAY_NAME
    assert ours[0].func is smo_mod.launch_smo_client


def test_launcher_registration_is_idempotent(smo_mod) -> None:
    """AP's apworld autodiscover can import the module more than once
    across reloads; re-registering would show duplicate buttons."""
    from worlds.LauncherComponents import components  # type: ignore[import-not-found]

    before = sum(
        1 for c in components
        if c.display_name == smo_mod.CLIENT_DISPLAY_NAME
    )
    smo_mod.add_client_to_launcher()
    after = sum(
        1 for c in components
        if c.display_name == smo_mod.CLIENT_DISPLAY_NAME
    )
    assert before == after == 1


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
    """Plain "Meatballs Client" Launcher button click (no .meatballsap arg)
    still routes straight to SMOClient. SMOClient handles a missing slot
    via the GUI Connect bar."""
    smo_mod.launch_smo_client()

    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    assert args == ()


def test_invalid_smoap_surfaces_warning_and_still_launches(spy, tmp_path, smo_mod, monkeypatch) -> None:
    """A malformed `.meatballsap` must (1) not crash the launcher, (2)
    still route to SMOClient with the file path stripped, and (3) leave
    a visible diagnostic so the user/dev can tell *why* the pre-fill
    didn't happen — a silent `logging.warning` is invisible when the
    Launcher process has no console attached (file-association case)."""
    bad = tmp_path / "AP_test_bogus.meatballsap"
    bad.write_bytes(b"not a zip and not valid JSON")

    warnings: list[tuple[str, str]] = []

    def fake_warning(context, exc, *, notifier=None, log_writer=None):
        warnings.append((context, str(exc)))

    # Patch in the loaded launcher_errors module so the call site
    # `from ._setup.launcher_errors import show_launch_warning` picks up
    # the fake when re-imported (Python caches the module).
    import importlib
    le = importlib.import_module("worlds.meatballs._setup.launcher_errors")
    monkeypatch.setattr(le, "show_launch_warning", fake_warning)

    smo_mod.launch_smo_client(str(bad))

    # Warning surfaced
    assert len(warnings) == 1
    context, _ = warnings[0]
    assert "could not be parsed" in context
    assert "AP_test_bogus.meatballsap" in context

    # Launch still happened, with the bad path stripped (no .meatballsap
    # arg reaches argparse downstream)
    assert len(spy) == 1
    name, func_name, args = spy[0]
    assert name == "SMOClient"
    assert func_name == "launch"
    assert all(not a.endswith(".meatballsap") for a in args)


def test_parse_args_empty_does_not_read_sys_argv(smo_mod, monkeypatch) -> None:
    """`parse_args([])` must NOT fall through to sys.argv.

    Regression: file-association launches (double-click `.meatballsap`)
    leave AP Launcher's own argv intact in `sys.argv`. If our parser
    falls through to it, it sees the `.meatballsap` path as an unknown
    positional and exits with code 2 — surfacing as a launch-crash
    popup to the user. Setting `sys.argv` to garbage here would have
    triggered that exact SystemExit before the fix.
    """
    from client.main import parse_args  # type: ignore[import-not-found]

    monkeypatch.setattr(sys, "argv", ["ArchipelagoLauncher.exe", "garbage.meatballsap"])
    # Must not raise SystemExit:
    ns = parse_args([])
    assert ns.name is None
    assert ns.connect is None
