"""Tests for the `sweep_kingdom_on_exit` slot option.

When on, a `kingdom_exit` wire message from the Switch (Mario flew the
Odyssey out of a kingdom) makes SMOContext send every remaining AP moon
location in that kingdom as one LocationChecks batch. Covers the Connected
handler flag plumbing, the location filter (departed kingdom only; no
Capture locations; no goal moon; nothing already sent), the per-item Cappy
suppression, and the option-off / AP-not-ready no-ops.

Gated on Archipelago availability (subclassing CommonContext requires
CommonClient on sys.path) — same pattern as test_commands.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


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
from client.protocol import CappyMsg, ItemMsg  # noqa: E402
from client.state import BridgeState  # noqa: E402


class _StubSwitch:
    def __init__(self) -> None:
        self.items: list[ItemMsg] = []
        self.cappy: list[CappyMsg] = []
        self.capturesanity_calls: list[bool] = []

    async def send_item(self, item: ItemMsg) -> None:
        self.items.append(item)

    async def send_cappy(self, msg: CappyMsg) -> None:
        self.cappy.append(msg)

    async def send_outstanding(self, msg) -> None: ...
    async def send_ap_state(self, conn: str) -> None: ...
    async def send_moon_label(self, label) -> None: ...

    def set_capturesanity_enabled(self, enabled: bool) -> None:
        self.capturesanity_calls.append(bool(enabled))

    async def push_capturesanity_replay(self) -> None: ...
    def set_deathlink_enabled(self, enabled: bool) -> None: ...
    async def push_deathlink_helloack(self) -> None: ...
    def set_talkatoo_pool(self, enabled: bool, kingdoms) -> None: ...
    async def push_talkatoo_pool(self) -> None: ...
    def set_shop_labels(self, entries) -> None: ...
    async def push_shop_labels(self) -> None: ...
    async def drain_pending_snapshot(self) -> None: ...


# Synthetic loc_ids in the SMO apworld's range so CommonContext's pre-loaded
# network_data_package doesn't clobber them (see test_commands.py). Moon
# names are placeholders — the sweep keys on the "<Kingdom>: " prefix only.
_FIXTURES = {
    70001001: "Sand: Sweep Test Moon A",
    70001002: "Sand: Sweep Test Moon B",
    70001003: "Sand: Sweep Test Moon C",
    70002001: "Lake: Sweep Test Moon D",
    70003001: "Capture: Goomba",
    70004001: "Metro: A Traditional Festival!",
}


def _make_ctx() -> tuple[SMOContext, _StubSwitch, list[dict]]:
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]
    for loc_id, name in _FIXTURES.items():
        ctx.dp.location_id_to_name[loc_id] = name
        ctx.dp.location_name_to_id[name] = loc_id
    ctx.missing_locations = set(_FIXTURES.keys())  # type: ignore[assignment]
    ctx.state.set_ap_conn("ready")

    sent: list[dict] = []

    async def fake_send_msgs(msgs: list[dict]) -> None:
        sent.extend(msgs)

    ctx.send_msgs = fake_send_msgs  # type: ignore[method-assign]
    return ctx, sw, sent


@pytest.mark.asyncio
async def test_connected_handler_reads_sweep_flag_from_slot_data():
    ctx, _sw, _sent = _make_ctx()
    assert ctx.sweep_kingdom_on_exit_enabled is False

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"sweep_kingdom_on_exit": 1},
    })
    assert ctx.sweep_kingdom_on_exit_enabled is True

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"sweep_kingdom_on_exit": 0},
    })
    assert ctx.sweep_kingdom_on_exit_enabled is False

    # Absent key (older apworld) → off.
    ctx.sweep_kingdom_on_exit_enabled = True
    await ctx._handle_ap_package("Connected", {"slot_data": {}})
    assert ctx.sweep_kingdom_on_exit_enabled is False


@pytest.mark.asyncio
async def test_sweep_sends_only_departed_kingdom_moons():
    ctx, sw, sent = _make_ctx()
    ctx.sweep_kingdom_on_exit_enabled = True

    n = await ctx.sweep_kingdom_on_exit("Sand", "LakeWorldHomeStage")

    assert n == 3
    assert [m["cmd"] for m in sent] == ["LocationChecks"]
    assert sorted(sent[0]["locations"]) == [70001001, 70001002, 70001003]
    # Lake, Capture, and Metro fixtures untouched.
    assert ctx.locations_checked == {70001001, 70001002, 70001003}
    # Per-item Cappy bubbles for own-slot items at swept locations are
    # suppressed (treated like Switch-reported checks); one summary bubble
    # goes out instead.
    assert {70001001, 70001002, 70001003} <= ctx._switch_reported_loc_ids
    assert len(sw.cappy) == 1
    assert "3" in sw.cappy[0].text and "Sand" in sw.cappy[0].text
    # Tracker state sees the swept moons under the kingdom.
    assert ctx.state.moons_checked_by_kingdom.get("Sand") == 3


@pytest.mark.asyncio
async def test_sweep_skips_already_sent_and_goal_location():
    ctx, sw, sent = _make_ctx()
    ctx.sweep_kingdom_on_exit_enabled = True
    # One Sand moon already shipped this session (not yet echoed by the
    # server, so still in missing_locations).
    ctx.locations_checked.add(70001002)

    n = await ctx.sweep_kingdom_on_exit("Sand", "LakeWorldHomeStage")
    assert n == 2
    assert sorted(sent[0]["locations"]) == [70001001, 70001003]

    # Festival goal moon is never swept — its check must come from the
    # in-game collect so ClientGoal fires at the right moment.
    sent.clear()
    ctx._goal_location_name = "Metro: A Traditional Festival!"
    n = await ctx.sweep_kingdom_on_exit("Metro", "SnowWorldHomeStage")
    assert n == 0
    assert sent == []


@pytest.mark.asyncio
async def test_sweep_second_departure_is_idempotent():
    ctx, sw, sent = _make_ctx()
    ctx.sweep_kingdom_on_exit_enabled = True

    assert await ctx.sweep_kingdom_on_exit("Sand", "LakeWorldHomeStage") == 3
    # Fly back into Sand and leave again: nothing new to send, no bubble.
    assert await ctx.sweep_kingdom_on_exit("Sand", "WoodedWorldHomeStage") == 0
    assert len(sent) == 1
    assert len(sw.cappy) == 1


@pytest.mark.asyncio
async def test_sweep_noop_when_option_off_or_ap_not_ready():
    ctx, sw, sent = _make_ctx()

    assert await ctx.sweep_kingdom_on_exit("Sand", "LakeWorldHomeStage") == 0
    assert sent == []
    assert sw.cappy == []
    assert ctx.locations_checked == set()

    ctx.sweep_kingdom_on_exit_enabled = True
    ctx.state.set_ap_conn("connecting")
    assert await ctx.sweep_kingdom_on_exit("Sand", "LakeWorldHomeStage") == 0
    assert sent == []


@pytest.mark.asyncio
async def test_sweep_bowsers_apostrophe_kingdom_matches_ap_names():
    """SwitchServer translates "Bowser" -> "Bowser's" before calling us;
    the AP-form name must match the "Bowser's: ..." location prefix."""
    ctx, sw, sent = _make_ctx()
    ctx.sweep_kingdom_on_exit_enabled = True
    ctx.dp.location_id_to_name[70005001] = "Bowser's: Showdown at Bowser's Castle"
    ctx.dp.location_name_to_id["Bowser's: Showdown at Bowser's Castle"] = 70005001
    ctx.missing_locations.add(70005001)

    assert await ctx.sweep_kingdom_on_exit("Bowser's", "PeachWorldHomeStage") == 1
    assert sent[0]["locations"] == [70005001]
