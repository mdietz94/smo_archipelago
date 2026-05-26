"""Regression test for SMOContext.output.

Discovered 2026-05-26: production code in SMOContext (the wizard-ran-
mid-session reload notice in `_handle_ap_package('Connected')` and the
stale-shine-map warnings in `report_check`) calls `self.output(...)` on
the context. `CommonContext` does not define `output` — only
`CommandProcessor` does — so any of those code paths crashed with
`AttributeError` the first time `reload_maps()` returned True (which
happens whenever the user's `.maps-updated` sentinel mtime advances
past what we last loaded).

The added `SMOContext.output` mirrors `CommandProcessor.output` and
routes through the `Client` logger so the message lands in the
Archipelago tab. This test pins:
  * `output` exists on SMOContext (no AttributeError)
  * messages get logged through the `Client` logger family

Run with the repo-root `.venv`:
  .venv/Scripts/python -m pytest \\
      apworld/smo_archipelago/tests/test_context_output.py -v
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# Worktrees don't carry an initialized vendor/Archipelago submodule, so fall
# back to the main checkout above the worktree root (mirrors test_commands.py).
def _find_archipelago() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "vendor" / "Archipelago"
        if (cand / "CommonClient.py").exists():
            return cand
        worktrees = parent.parent
        if worktrees.name == "worktrees":
            main_cand = worktrees.parent.parent / "vendor" / "Archipelago"
            if (main_cand / "CommonClient.py").exists():
                return main_cand
    return None


_AP = _find_archipelago()
if _AP is not None and str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

try:  # pragma: no cover
    import ModuleUpdate  # type: ignore[import-not-found]
    ModuleUpdate.update_ran = True
except ImportError:
    pass

pytest.importorskip(
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


@pytest.mark.asyncio
async def test_output_exists_and_does_not_raise():
    """Without SMOContext.output the wizard-ran-mid-session reload
    notice (context.py `_handle_ap_package('Connected')` -> `reload_maps`
    -> `self.output(...)`) crashes with AttributeError. Lock in that
    the method exists and runs without raising."""
    ctx = _make_ctx()
    ctx.output("hello from the test")


@pytest.mark.asyncio
async def test_output_routes_through_client_logger(caplog):
    """The user-visible surface for SMOContext.output is the Archipelago
    tab, which is fed by the `Client` logger family. Pin the routing so
    a future refactor doesn't quietly redirect messages to a logger that
    the UI doesn't read."""
    ctx = _make_ctx()
    with caplog.at_level(logging.INFO, logger="Client"):
        ctx.output("reload notice")
    matching = [r for r in caplog.records
                if r.name == "Client" and "reload notice" in r.getMessage()]
    assert matching, (
        "expected 'reload notice' on the Client logger; "
        f"got records={[(r.name, r.getMessage()) for r in caplog.records]}"
    )
