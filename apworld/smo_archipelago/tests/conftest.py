"""Test path setup.

Tests import the client modules as a top-level package
(`from client.X import Y`) — to do that we put `apworld/smo_archipelago/`
on sys.path so `client/` is discovered as a top-level package.

We intentionally do NOT put vendor/Archipelago on sys.path here. Doing so
triggers Archipelago's apworld discovery machinery (worlds/__init__.py
walks custom_worlds/ at import time), which both pulls in unmet
dependencies of unrelated worlds (pyevermizer, requests, zilliandomizer)
AND collides our loose-source apworld with the zipped one in
custom_worlds/ on AutoWorldRegister. The two opt-in live-AP tests
(test_ap_loopback, test_apworld_generation, both gated on SMOAP_LIVE_AP=1)
handle their own Archipelago path setup via subprocess invocations of
scripts/ap_generate.py / scripts/ap_server.py — they don't need
Archipelago importable from this process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_APWORLD_ROOT = _HERE.parent  # apworld/smo_archipelago/

s = str(_APWORLD_ROOT)
if _APWORLD_ROOT.exists() and s not in sys.path:
    sys.path.insert(0, s)


# The five module-level caches in `_setup.prereqs` that store wizard-
# verified toolchain paths. They're populated by `check_*` running real
# detection, and read by `_setup.build._python_invoker` and the build's
# SMOAP_*_BIN env-var pinning. Tests that exercise `check_all` or the
# `_python_invoker` real-Python branch without mocking BOTH the prereqs
# layer AND its cache slots will leak a real path into other tests'
# state. Symptom: `_python_invoker` returns the developer's actual
# Python 3.12 install path instead of the monkeypatched `sys.executable`,
# because `resolved_python312_bin()` short-circuits any sys.executable
# inference.
#
# Snapshot + restore around every test so the leak is contained
# regardless of which test populates the cache. Cheap (5 attr reads),
# silent when nothing changes.
_PREREQ_CACHE_ATTRS = (
    "_resolved_python312_bin",
    "_resolved_llvm_bin",
    "_resolved_mingw_bin",
    "_resolved_cmake",
    "_resolved_ninja_bin",
)


@pytest.fixture(autouse=True)
def _isolate_prereqs_caches():
    """Snapshot prereqs module-level resolver caches before each test and
    restore after. Prevents cross-test pollution when a test triggers
    real `check_*` detection (or imports something that does).

    Import inside the fixture so non-_setup tests don't pay the cost of
    importing the wizard's prereqs module just to instantiate the fixture
    on collection — `prereqs` pulls in installers / shell helpers that
    are wasted for, say, pure-protocol tests."""
    try:
        from _setup import prereqs  # type: ignore[import-not-found]
    except Exception:
        # _setup not importable (e.g. tests run outside the apworld
        # sys.path) — nothing to isolate, no-op cleanly.
        yield
        return
    saved = {attr: getattr(prereqs, attr, None) for attr in _PREREQ_CACHE_ATTRS}
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(prereqs, attr, value)
