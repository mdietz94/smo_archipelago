"""Regression test for SMOContext.already_checked_loc_ids.

User report 2026-05-26: an SMOClient relaunch + Switch save-load against
an AP slot that already had 10 moons checked produced a spurious
"[confirm-gate] snapshot held — pending /confirm_snapshot (new=10
already=0 goal_reached=False)". Root cause: main.py wired the gate's
already-checked provider to `ctx.locations_checked`, which is
CommonClient's local-session set (starts empty every launch). The
server-authoritative set delivered in the `Connected` packet is
`ctx.checked_locations`. Without it the gate cannot recognize prior-
session checks as already credited.

Fix: SMOContext.already_checked_loc_ids returns the union of both. Test
covers the three cases that would have caught the bug:

  * server-only — the user-report scenario: client relaunched, the
    Connected packet populated `checked_locations`, `locations_checked`
    is still empty. The provider must surface the server set.
  * local-only — fresh slot mid-session: we shipped checks this
    session, `checked_locations` hasn't echoed the corresponding
    RoomUpdate yet. The provider must still include them.
  * both populated with overlap — server already has some, we just sent
    a new one. The provider must surface the union.

Run with the repo-root `.venv`:
  .venv/Scripts/python -m pytest \\
      apworld/smo_archipelago/tests/test_already_checked_provider.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make vendor/Archipelago importable before the importorskip below.
_AP = Path(__file__).resolve().parents[3] / "vendor" / "Archipelago"
if _AP.exists() and str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

try:  # pragma: no cover
    import ModuleUpdate  # type: ignore[import-not-found]
    ModuleUpdate.update_ran = True
except ImportError:
    pass

CommonClient = pytest.importorskip(
    "CommonClient",
    reason="Archipelago checkout not present; init the vendor/Archipelago submodule.",
)

from client.context import SMOContext  # noqa: E402
from client.datapackage import DataPackage  # noqa: E402
from client.maps import CaptureMap, ShineMap  # noqa: E402
from client.state import BridgeState  # noqa: E402


def _make_ctx() -> SMOContext:
    return SMOContext(
        server_address=None,
        password=None,
        state=BridgeState(),
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )


def test_server_only_checked_locations_surfaces():
    """The user-report scenario: client relaunched against an AP slot
    that already has checks. Connected packet populated `checked_locations`,
    local-session `locations_checked` is still empty. The provider must
    return the server set so the confirm-gate sees the snapshot's entries
    as already-credited."""
    ctx = _make_ctx()
    ctx.checked_locations = {1001, 1002, 1003}
    ctx.locations_checked = set()
    assert ctx.already_checked_loc_ids() == {1001, 1002, 1003}


def test_local_only_locations_checked_surfaces():
    """Mid-session window between sending a LocationCheck and AP's
    RoomUpdate echoing it back. The provider must include the local set
    so the gate doesn't double-count a snapshot entry the user just
    triggered."""
    ctx = _make_ctx()
    ctx.checked_locations = set()
    ctx.locations_checked = {2001, 2002}
    assert ctx.already_checked_loc_ids() == {2001, 2002}


def test_union_covers_overlap_and_disjoint():
    """Both populated, with overlap. The provider must return the union
    so neither side's contribution gets dropped."""
    ctx = _make_ctx()
    ctx.checked_locations = {1001, 1002, 1003}
    ctx.locations_checked = {1003, 2001, 2002}
    assert ctx.already_checked_loc_ids() == {1001, 1002, 1003, 2001, 2002}


def test_both_empty_returns_empty():
    """Fresh slot, fresh session — no checks anywhere. Provider returns
    an empty set so the gate classifies every snapshot entry as new
    (correct: prompt the operator to /confirm_snapshot)."""
    ctx = _make_ctx()
    ctx.checked_locations = set()
    ctx.locations_checked = set()
    assert ctx.already_checked_loc_ids() == set()
