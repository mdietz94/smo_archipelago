"""Tests for the ItemMsg.from_ Cappy-suppression filter in SMOContext.

`shouldShowCappyMsg` (switch-mod/src/ui/CappyMessenger.cpp) treats an empty
`from` field as "do not surface a Cappy bubble." The bridge collapses
`from_` to "" only on the gameplay self-find path (AP routed the item we
just checked back to ourselves). Server-injected items (`/send`, releases,
collects) and items from other real players still get a bubble.

Run with the bridge venv:
  bridge/.venv/Scripts/python -m pytest \
      apworld/smo_archipelago/tests/test_cappy_from_filter.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add vendor/Archipelago BEFORE the import-skip so CommonClient is reachable.
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


class _StubSwitch:
    def __init__(self) -> None:
        self.items: list = []

    async def send_item(self, item) -> None:
        self.items.append(item)

    async def send_kill(self, k) -> None:  # pragma: no cover - unused here
        pass

    async def send_print(self, text: str) -> None:  # pragma: no cover - unused here
        pass

    async def send_ap_state(self, conn: str) -> None:  # pragma: no cover - unused here
        pass

    def set_capturesanity_enabled(self, enabled: bool) -> None:  # pragma: no cover - unused here
        pass

    async def push_capturesanity_replay(self) -> None:  # pragma: no cover - unused here
        pass

    def set_deathlink_enabled(self, enabled: bool) -> None:  # pragma: no cover - unused here
        pass

    async def push_deathlink_helloack(self) -> None:  # pragma: no cover - unused here
        pass


_ITEM_ID = 4242
_ITEM_NAME = "Goomba"


def _make_ctx(*, my_slot: int = 1) -> tuple[SMOContext, _StubSwitch]:
    """Build an SMOContext wired enough to receive a ReceivedItems packet
    and route the resulting ItemMsg to a stub switch.

    The DataPackage gets a single fabricated item registered under id
    `_ITEM_ID` so the classify path resolves to a known name (kind doesn't
    matter — the `from_` collapse runs regardless of item kind).
    """
    dp = DataPackage()
    dp.item_id_to_name[_ITEM_ID] = _ITEM_NAME
    dp.item_name_to_id[_ITEM_NAME] = _ITEM_ID
    dp._item_categories[_ITEM_NAME] = ["capture"]  # so classify_item -> CAPTURE
    ctx = SMOContext(
        server_address=None,
        password=None,
        state=BridgeState(),
        datapackage=dp,
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    ctx.slot = my_slot
    ctx.team = 0
    # Stand in for what AP's Connected handler normally writes; covers the
    # three slot indices our scenarios reference.
    ctx.player_names = {0: "Archipelago", 1: "Mario", 2: "Player2"}
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]
    return ctx, sw


async def _drive(
    ctx: SMOContext, sender_idx: int | None, *, location: int | None = None,
) -> None:
    ni = {"item": _ITEM_ID, "player": sender_idx, "location": location, "flags": 0}
    await ctx._handle_ap_package("ReceivedItems", {"items": [ni]})


async def _drive_batch(
    ctx: SMOContext, items: list[tuple[int, int | None]], *, index: int = 0
) -> None:
    """Drive a ReceivedItems packet with multiple items and an explicit
    `index`. Mirrors AP's wire format — `index` is the absolute position
    of the first item in the receiver's items_received list; `items` is
    [(item_id, sender_idx), ...]."""
    nis = [{"item": iid, "player": s, "location": None, "flags": 0} for (iid, s) in items]
    await ctx._handle_ap_package("ReceivedItems", {"index": index, "items": nis})


# ---------------------------------------------------------------- scenarios


@pytest.mark.asyncio
async def test_other_player_keeps_real_sender_name():
    """Real other-player check → bubble should fire → `from_` is the name."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive(ctx, sender_idx=2)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == "Player2"


@pytest.mark.asyncio
async def test_gameplay_self_find_collapses_to_empty():
    """Sender == our own slot AND the Switch reported the location (natural
    in-game collection) → silence Cappy → `from_` is empty. The in-game
    moon-get cutscene or capture animation already gave the player feedback;
    a Cappy bubble on top would double it up."""
    ctx, sw = _make_ctx(my_slot=1)
    # Simulate report_check having tracked this loc_id before the AP echo.
    ctx._switch_reported_loc_ids.add(9001)
    await _drive(ctx, sender_idx=1, location=9001)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == ""


@pytest.mark.asyncio
async def test_send_location_self_grant_uses_manual_sentinel():
    """User typed `/send_location` for a loc the Switch never reported
    (e.g. a capture they never naturally captured). AP echoes back with
    sender == self.slot but the loc_id is NOT in _switch_reported_loc_ids.
    Bridge tags `from_` with the "(self)" sentinel so CappyMessenger
    surfaces a "Got X!" bubble — without the manual path the capture
    would unlock silently and the player would have no feedback."""
    ctx, sw = _make_ctx(my_slot=1)
    # Note: _switch_reported_loc_ids stays empty — the user bypassed the
    # natural-check pipeline.
    await _drive(ctx, sender_idx=1, location=9001)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == "(self)"


@pytest.mark.asyncio
async def test_self_grant_with_no_location_field_uses_manual_sentinel():
    """Defensive: NetworkItem with no `location` field can't be matched
    against the reported set, so we conservatively treat it as a manual
    grant (bubble) rather than silently dropping the only feedback channel."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive(ctx, sender_idx=1, location=None)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == "(self)"


@pytest.mark.asyncio
async def test_server_grant_keeps_sender_name():
    """Admin /send / release / collect arrive with player == 0 and should
    still surface a Cappy bubble — only gameplay self-finds suppress."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive(ctx, sender_idx=0)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == "Archipelago"


@pytest.mark.asyncio
async def test_unattributed_sender_passes_self_string_through():
    """`sender_idx is None` is a wire oddity (no `player` field on the
    NetworkItem) that should not be mistaken for a self-find. `_sender_name`
    returns "self" for None — bubble fires, the C++ side renders it."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive(ctx, sender_idx=None)
    assert len(sw.items) == 1
    assert sw.items[0].from_ == "self"


# ---------------------------------------------------------------- state side


@pytest.mark.asyncio
async def test_state_received_item_keeps_real_sender_for_logging():
    """ItemEvent recorded in BridgeState keeps the real sender name even
    when ItemMsg.from_ is collapsed for the gameplay self-find case — the
    in-app tracker UI and log lines rely on attribution."""
    ctx, _ = _make_ctx(my_slot=1)
    ctx._switch_reported_loc_ids.add(9001)
    await _drive(ctx, sender_idx=1, location=9001)
    evts = list(ctx.state.received_items)
    assert len(evts) == 1
    assert evts[0].sender == "Mario"
    # The Cappy-suppression decision is persisted on the ItemEvent so the
    # HELLO replay path can re-use it without recomputing — a gameplay
    # self-find stays silent across save loads.
    assert evts[0].cappy_from == ""


@pytest.mark.asyncio
async def test_state_received_item_persists_cappy_from_for_other_player():
    """Non-self-find ItemEvents carry the sender name on the cappy_from
    field too, so HELLO replay surfaces a bubble (matching live UX)."""
    ctx, _ = _make_ctx(my_slot=1)
    await _drive(ctx, sender_idx=2)
    evts = list(ctx.state.received_items)
    assert len(evts) == 1
    assert evts[0].sender == "Player2"
    assert evts[0].cappy_from == "Player2"


# ---------------------------------------------------------------- dedup


@pytest.mark.asyncio
async def test_received_items_dedups_on_full_resend():
    """AP re-sends the full received-items history on every Connect with
    index=0. The bridge must skip items it has already processed in this
    session — otherwise state.received_items grows unboundedly and the
    HELLO replay sends a fresh ItemMsg per duplicate (the Goomba-x3 bug
    on save reload)."""
    ctx, sw = _make_ctx(my_slot=1)
    # First connect: AP delivers Goomba.
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    assert len(ctx.state.received_items) == 1
    assert len(sw.items) == 1
    # Bridge reconnects → AP re-sends the full history starting at index=0.
    # No new items have arrived in the meantime.
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    assert len(ctx.state.received_items) == 1, (
        "duplicate Goomba should not have been appended"
    )
    assert len(sw.items) == 1, "stub switch must not have received a 2nd ItemMsg"


@pytest.mark.asyncio
async def test_received_items_dedups_three_resends_then_processes_new_item():
    """Pathological case: AP cycles three times (matches the user's
    repro). After three full resends only one Goomba lives in state.
    Then a new item arrives in a fresh batch and is processed live."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    assert len(ctx.state.received_items) == 1
    assert len(sw.items) == 1

    # Now a new Goomba arrives genuinely. AP would re-send the existing
    # one + the new one at index=0 (the "full history" wire convention).
    await _drive_batch(ctx, [(_ITEM_ID, 0), (_ITEM_ID, 0)], index=0)
    assert len(ctx.state.received_items) == 2
    assert len(sw.items) == 2


@pytest.mark.asyncio
async def test_received_items_processes_incremental_update():
    """The common live path: bridge has been connected, item is awarded,
    AP sends an incremental ReceivedItems with index = current length
    and items = [new]. Must process normally."""
    ctx, sw = _make_ctx(my_slot=1)
    await _drive_batch(ctx, [(_ITEM_ID, 0)], index=0)
    assert len(ctx.state.received_items) == 1

    # Incremental: index=1, items=[B] (B is a different item — register
    # one on the fly).
    other_id = _ITEM_ID + 1
    ctx.dp.item_id_to_name[other_id] = "Frog"
    ctx.dp.item_name_to_id["Frog"] = other_id
    ctx.dp._item_categories["Frog"] = ["capture"]
    await _drive_batch(ctx, [(other_id, 0)], index=1)
    assert len(ctx.state.received_items) == 2
    assert ctx.state.received_items[1].item.cap == "Frog"
    assert len(sw.items) == 2
