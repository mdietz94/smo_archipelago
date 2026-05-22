"""Tests for the DeathLink wiring in SMOContext.

Phase 2 reshape: where the bridge's `SmoApBridgeContext` used composition
to defer Archipelago imports, `SMOContext(CommonContext)` is a real
subclass, so this test needs Archipelago on sys.path. Module-level
importorskip handles a fresh checkout where the submodule isn't pulled in.

Run with the repo-root `.venv` (which has AP's deps installed):
  .venv/Scripts/python -m pytest apworld/smo_archipelago/tests/test_deathlink.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add vendor/Archipelago BEFORE the import-skip so CommonClient is reachable.
_AP = Path(__file__).resolve().parents[3] / "vendor" / "Archipelago"
if _AP.exists() and str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

# Suppress AP's auto-pip update step (.venv already has the deps).
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
from client.protocol import KillMsg  # noqa: E402
from client.state import BridgeState  # noqa: E402


class _StubSwitch:
    """Minimum SwitchServer surface area for these tests."""

    def __init__(self) -> None:
        self.kills: list[KillMsg] = []
        self.items: list = []
        self.prints: list[str] = []
        self.ap_states: list[str] = []

    async def send_kill(self, k: KillMsg) -> None:
        self.kills.append(k)

    async def send_item(self, item) -> None:  # pragma: no cover - unused here
        self.items.append(item)

    async def send_print(self, text: str) -> None:  # pragma: no cover - unused here
        self.prints.append(text)

    async def send_ap_state(self, conn: str) -> None:  # pragma: no cover - unused here
        self.ap_states.append(conn)

    def set_capturesanity_enabled(self, enabled: bool) -> None:  # pragma: no cover - unused here
        pass

    async def push_capturesanity_replay(self) -> None:  # pragma: no cover - unused here
        pass

    def set_deathlink_enabled(self, enabled: bool) -> None:  # pragma: no cover - unused here
        pass

    async def push_deathlink_helloack(self) -> None:  # pragma: no cover - unused here
        pass


def _make_ctx(*, deathlink: bool, slot: str = "Mario") -> tuple[SMOContext, BridgeState, _StubSwitch]:
    state = BridgeState()
    ctx = SMOContext(
        server_address=None,
        password=None,
        state=state,
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
        deathlink_enabled=deathlink,
    )
    ctx.auth = slot
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]
    return ctx, state, sw


# ---------- outbound: Mario died on the Switch ----------------------------


@pytest.mark.asyncio
async def test_report_death_disabled_only_bumps_state():
    ctx, state, _ = _make_ctx(deathlink=False)
    sent: list = []

    async def fake_send_msgs(msgs):
        sent.extend(msgs)

    ctx.send_msgs = fake_send_msgs  # type: ignore[assignment]
    await ctx.report_death(ts_ms=1234)
    assert state.death_count == 1
    assert sent == []


@pytest.mark.asyncio
async def test_report_death_enabled_sends_bounce():
    ctx, state, _ = _make_ctx(deathlink=True)
    sent: list = []

    async def fake_send_msgs(msgs):
        sent.extend(msgs)

    ctx.send_msgs = fake_send_msgs  # type: ignore[assignment]
    await ctx.report_death(ts_ms=42_000)
    assert state.death_count == 1
    assert len(sent) == 1
    pkt = sent[0]
    assert pkt["cmd"] == "Bounce"
    assert "DeathLink" in pkt["tags"]
    assert pkt["data"]["source"] == "Mario"
    assert pkt["data"]["time"] == pytest.approx(42.0)


# ---------- inbound: another slot died, AP sent us a Bounce ---------------


@pytest.mark.asyncio
async def test_inbound_deathlink_forwards_kill_to_switch():
    import asyncio
    ctx, _, sw = _make_ctx(deathlink=True, slot="Mario")
    ctx.on_deathlink({"time": 1.0, "source": "OtherSlot", "cause": "Fell off the world"})
    # Yield once so the create_task in on_deathlink actually runs.
    await asyncio.sleep(0)
    assert len(sw.kills) == 1
    assert sw.kills[0].source == "OtherSlot"
    assert sw.kills[0].cause == "Fell off the world"


@pytest.mark.asyncio
async def test_inbound_deathlink_own_source_is_swallowed():
    import asyncio
    ctx, _, sw = _make_ctx(deathlink=True, slot="Mario")
    ctx.on_deathlink({"time": 2.0, "source": "Mario", "cause": "Hit a spike"})
    await asyncio.sleep(0)
    assert sw.kills == []


@pytest.mark.asyncio
async def test_inbound_deathlink_ignored_when_disabled():
    import asyncio
    ctx, _, sw = _make_ctx(deathlink=False, slot="Mario")
    ctx.on_deathlink({"time": 3.0, "source": "OtherSlot", "cause": "Fell"})
    await asyncio.sleep(0)
    assert sw.kills == []
