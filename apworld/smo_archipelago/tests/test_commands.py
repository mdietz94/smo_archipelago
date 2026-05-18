"""Tests for SMOClientCommandProcessor — the `/`-command surface in
`context.py` — plus a regression test for the AP-server-issued ItemMsg
name-resolution path.

The pure parser is exercised in test_repl.py.

Gated on Archipelago availability (subclassing CommonContext requires
CommonClient on sys.path) — same pattern as test_deathlink.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Worktrees don't carry an initialized vendor/Archipelago submodule, so fall
# back to the main checkout one level above the worktree root (the same
# pattern test_connect_gate.py uses). Without this, every test in this file
# silently skips in a `git worktree`-based dev loop.
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

from client.context import SMOContext, SMOClientCommandProcessor  # noqa: E402
from client.datapackage import DataPackage  # noqa: E402
from client.maps import CaptureMap, ShineMap  # noqa: E402
from client.protocol import ItemMsg  # noqa: E402
from client.state import BridgeState  # noqa: E402

_APWORLD_DATA = Path(__file__).resolve().parent.parent / "data"


class _StubSwitch:
    def __init__(self) -> None:
        self.items: list[ItemMsg] = []
        self.kills: list = []
        self.labels: list = []
        self.outstanding: list = []
        self.ap_states: list[str] = []
        self.capturesanity_calls: list[bool] = []
        self.push_capturesanity_calls: int = 0

    async def send_item(self, item: ItemMsg) -> None:
        self.items.append(item)

    async def send_kill(self, kill) -> None:
        self.kills.append(kill)

    async def send_moon_label(self, label) -> None:
        self.labels.append(label)

    async def send_outstanding(self, msg) -> None:
        # M6 phase D: context.py pushes the authoritative per-kingdom
        # balance to the Switch whenever a Moon item is granted (so
        # ap_moons_kingdom[bit] on the mod side stays in sync). Stub it
        # for tests that just observe send_item.
        self.outstanding.append(msg)

    async def send_ap_state(self, conn: str) -> None:
        self.ap_states.append(conn)

    def set_capturesanity_enabled(self, enabled: bool) -> None:
        self.capturesanity_calls.append(bool(enabled))

    async def push_capturesanity_replay(self) -> None:
        self.push_capturesanity_calls += 1


@pytest.mark.asyncio
async def test_cmd_inject_deathlink_routes_killmsg_to_switch():
    import asyncio
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

    proc = SMOClientCommandProcessor(ctx)
    proc._cmd_inject_deathlink("Tester", "for science")
    await asyncio.sleep(0)

    assert len(sw.kills) == 1
    assert sw.kills[0].source == "Tester"
    assert sw.kills[0].cause == "for science"


@pytest.mark.asyncio
async def test_ap_received_item_carries_name_for_moon():
    """Regression: AP-issued moons must reach the Switch with their name.

    The bug: `ClassifiedItem.to_ref()` used to zero `name` for non-OTHER
    kinds, so MOON/CAPTURE/KINGDOM items arrived on the Switch with no
    `name` field (stripped by `_strip_none`) and rendered as `?` in-game.
    """
    import asyncio
    state = BridgeState()
    ctx = SMOContext(
        server_address=None, password=None,
        state=state,
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    # Pretend the AP DataPackage handshake completed.
    ctx.dp.item_id_to_name[42] = "Cascade Kingdom Power Moon"
    ctx.dp.item_name_to_id["Cascade Kingdom Power Moon"] = 42

    await ctx._handle_ap_package("ReceivedItems", {
        "items": [{"item": 42, "player": 0, "flags": 0}],
    })
    await asyncio.sleep(0)

    assert len(sw.items) == 1
    msg = sw.items[0]
    assert msg.kind == "moon"
    assert msg.kingdom == "Cascade"
    assert msg.shine_id == "Power Moon"
    assert msg.name == "Cascade Kingdom Power Moon"

    # Wire payload must include the name (not stripped as None).
    from client.protocol import encode
    wire = encode(msg).decode("utf-8")
    assert '"name":"Cascade Kingdom Power Moon"' in wire


@pytest.mark.asyncio
async def test_connected_handler_pushes_capturesanity_off_to_switch():
    """Regression: the Connected handler must extract `capturesanity`
    from the packet's slot_data dict (NOT from `self.slot_data`, which
    CommonContext does not auto-stash) and push it to the Switch.

    The original implementation hit an AttributeError mid-handler,
    which silently broke EVERY post-Connected side effect (scout warm,
    notify subscription, capturesanity push). Catching this here
    prevents a regression where a future change reintroduces
    `self.slot_data` or other CommonContext attributes that don't exist."""
    import asyncio
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

    # Default before Connected: True (fail-safe = current behavior).
    assert ctx.capturesanity_enabled is True

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 0},
        # Other Connected fields the handler tolerates being absent —
        # team/slot stay None so _outstanding_key() returns None and
        # set_notify is skipped, and display/colors default off so
        # scout warming is skipped.
    })

    assert sw.capturesanity_calls == [False]
    assert sw.push_capturesanity_calls == 1
    assert sw.ap_states == ["ready"]
    # ctx mirror gets flipped too — used by gui.py to hide the
    # "Captures unlocked" section (which would otherwise list 50
    # synthetic unlocks).
    assert ctx.capturesanity_enabled is False


@pytest.mark.asyncio
async def test_connected_handler_pushes_capturesanity_on_to_switch():
    """Symmetric case: when slot_data.capturesanity == 1, the switch
    gets enabled=True and push_capturesanity_replay is still called
    (the method itself is the no-op gate, not the call site)."""
    import asyncio
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

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 1},
    })

    assert sw.capturesanity_calls == [True]
    assert sw.push_capturesanity_calls == 1
    assert ctx.capturesanity_enabled is True


@pytest.mark.asyncio
async def test_connected_handler_tolerates_missing_slot_data():
    """Defensive: a malformed Connected packet (or a server that
    doesn't ship slot_data) must not crash — default to enabled=False
    matches the apworld's default Capturesanity Toggle = OFF."""
    import asyncio
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

    # No slot_data key at all.
    await ctx._handle_ap_package("Connected", {})
    assert sw.capturesanity_calls == [False]

    # Explicit None.
    sw.capturesanity_calls.clear()
    await ctx._handle_ap_package("Connected", {"slot_data": None})
    assert sw.capturesanity_calls == [False]


def test_to_ref_preserves_name_for_all_kinds():
    """Pure unit-level guard against re-introducing the OTHER-only conditional."""
    from client.datapackage import ClassifiedItem
    from client.protocol import ItemKind

    for kind, kwargs in [
        (ItemKind.MOON, {"kingdom": "Cascade", "shine_id": "Power Moon"}),
        (ItemKind.CAPTURE, {"cap": "Goomba"}),
        (ItemKind.KINGDOM, {"kingdom": "Sand"}),
        (ItemKind.OTHER, {}),
    ]:
        ci = ClassifiedItem(kind=kind, name=f"test-{kind.value}", **kwargs)
        ref = ci.to_ref()
        assert ref.name == f"test-{kind.value}", (
            f"to_ref() dropped name for kind={kind.value!r}; "
            f"this is the AP-server `?`-display regression."
        )


@pytest.mark.asyncio
async def test_ap_received_moon_sends_both_itemmsg_and_outstandingmsg():
    """Regression for the M6-phase-D double-credit bug.

    On every Moon ReceivedItems batch, the bridge MUST push both an
    ItemMsg (observation + Cappy speech feed on the mod) AND an
    OutstandingMsg (authoritative per-kingdom counter). The Switch's
    Moon-arm in applyOnFrame is a no-op for the counter — if the bridge
    ever sends ItemMsg without the accompanying OutstandingMsg, the
    counter for that kingdom never ticks. Lock in the contract here.
    """
    import asyncio
    state = BridgeState()
    ctx = SMOContext(
        server_address=None, password=None,
        state=state,
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    ctx.dp.item_id_to_name[42] = "Cascade Kingdom Power Moon"
    ctx.dp.item_name_to_id["Cascade Kingdom Power Moon"] = 42

    await ctx._handle_ap_package("ReceivedItems", {
        "items": [{"item": 42, "player": 0, "flags": 0}],
    })
    await asyncio.sleep(0)

    # Exactly one of each, in either order — both are required to keep the
    # mod's ap_moons_kingdom[bit] correct.
    assert len(sw.items) == 1
    assert sw.items[0].kind == "moon"
    assert sw.items[0].kingdom == "Cascade"
    assert len(sw.outstanding) == 1, (
        "Moon grant did not push OutstandingMsg — the Switch counter "
        "would never tick (the mod's ItemMsg-apply path is observation-"
        "only for moons)."
    )
    cascade_count = next(
        (e.count for e in sw.outstanding[0].entries if e.kingdom == "Cascade"),
        None,
    )
    assert cascade_count == 1, (
        f"OutstandingMsg should report Cascade=1 after one grant; "
        f"got entries={sw.outstanding[0].entries!r}"
    )


@pytest.mark.asyncio
async def test_ap_received_multi_moon_batch_debounces_outstanding():
    """Multiple Moon items in one ReceivedItems packet must collapse to
    a single OutstandingMsg push (per the comment at context.py:618-624).

    This is the debounce that keeps reconnect-driven bulk replays from
    flooding the Switch with one OutstandingMsg per item.
    """
    import asyncio
    state = BridgeState()
    ctx = SMOContext(
        server_address=None, password=None,
        state=state,
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    ctx.dp.item_id_to_name[42] = "Cascade Kingdom Power Moon"
    ctx.dp.item_id_to_name[43] = "Sand Kingdom Power Moon"
    ctx.dp.item_id_to_name[44] = "Cascade Kingdom Multi-Moon"
    for nid, nm in ctx.dp.item_id_to_name.items():
        ctx.dp.item_name_to_id[nm] = nid

    await ctx._handle_ap_package("ReceivedItems", {
        "items": [
            {"item": 42, "player": 0, "flags": 0},
            {"item": 43, "player": 0, "flags": 0},
            {"item": 44, "player": 0, "flags": 0},
        ],
    })
    await asyncio.sleep(0)

    # 3 ItemMsg (one per item), 1 OutstandingMsg (debounced over the batch).
    assert len(sw.items) == 3
    assert len(sw.outstanding) == 1, (
        f"expected 1 debounced OutstandingMsg; got {len(sw.outstanding)}"
    )
    by_kingdom = {e.kingdom: e.count for e in sw.outstanding[0].entries}
    assert by_kingdom.get("Cascade") == 4, (  # 1 Power Moon + 3 Multi-Moon
        f"Cascade should be 4 (1 PM + 3 MM); got {by_kingdom}"
    )
    assert by_kingdom.get("Sand") == 1, (
        f"Sand should be 1; got {by_kingdom}"
    )


# ---------------------------------------------------------------------------
# M6 phase D — cross-restart outstanding double-count guard (rii dedup)
# ---------------------------------------------------------------------------


def _make_ctx_with_slot() -> tuple["SMOContext", "BridgeState", "_StubSwitch"]:
    """Build an SMOContext primed with team/slot so _outstanding_key is
    non-None (which engages the hydration gate). Used by every rii dedup
    test below."""
    state = BridgeState()
    ctx = SMOContext(
        server_address=None, password=None,
        state=state,
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    ctx.team = 0
    ctx.slot = 1
    ctx.dp.item_id_to_name[42] = "Cascade Kingdom Power Moon"
    ctx.dp.item_id_to_name[43] = "Sand Kingdom Power Moon"
    ctx.dp.item_id_to_name[44] = "Cascade Kingdom Multi-Moon"
    for nid, nm in ctx.dp.item_id_to_name.items():
        ctx.dp.item_name_to_id[nm] = nid
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]
    return ctx, state, sw


@pytest.mark.asyncio
async def test_v2_persist_writes_outstanding_and_rii():
    """After a Moon grant batch the bridge persists the v2 schema —
    both outstanding and rii — under the outstanding key."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    # Gate is normally set by _hydrate_outstanding_from_ap; skip the
    # waiting-for-Retrieved phase since this test isn't exercising that.
    ctx._outstanding_hydrated.set()

    sets_observed: list[dict] = []

    async def fake_send_msgs(msgs):
        for m in msgs:
            if isinstance(m, dict) and m.get("cmd") == "Set":
                sets_observed.append(m)

    ctx.send_msgs = fake_send_msgs  # type: ignore[assignment]

    await ctx._handle_ap_package("ReceivedItems", {
        "index": 0,
        "items": [{"item": 42, "player": 0, "flags": 0}],
    })
    # _persist_outstanding fires asyncio.create_task → let it run.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(sets_observed) >= 1, "expected a Set call for the outstanding key"
    payload = sets_observed[0]["operations"][0]["value"]
    assert payload["_v"] == 2
    assert payload["outstanding"] == {"Cascade": 1}
    assert payload["rii"] == 1, (
        f"rii should advance to ap_index + len(items) = 0 + 1 = 1; got {payload['rii']}"
    )


@pytest.mark.asyncio
async def test_v2_hydration_dedups_historical_replay():
    """The headline cross-restart bug. Two-session simulation:

    Session 1: collect 3 Cascade moons. outstanding={Cascade:3}, rii=3.
    Session 2 (bridge restart): AP server's items_received still has
        those 3 historical items. Bridge re-receives them via
        ReceivedItems(index=0). Pre-rii-fix: apply_grant fires for each
        → outstanding doubles to 6. Post-fix: side effects skipped,
        outstanding stays at 3.
    """
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    # Simulate post-hydration state from session 1.
    state.replace_outstanding({"Cascade": 3})
    state.set_received_items_index(3)
    ctx._outstanding_hydrated.set()
    ctx._outstanding_v1_migration_pending = False

    # Session 2: AP replays the full history at index=0.
    await ctx._handle_ap_package("ReceivedItems", {
        "index": 0,
        "items": [
            {"item": 42, "player": 0, "flags": 0},
            {"item": 42, "player": 0, "flags": 0},
            {"item": 42, "player": 0, "flags": 0},
        ],
    })
    await asyncio.sleep(0)

    # The bug we're fixing: outstanding must NOT double. All 3 items are
    # below the rii=3 high-water mark → no apply_grant, no send_item, no
    # OutstandingMsg push.
    assert state.outstanding_by_kingdom == {"Cascade": 3}, (
        f"outstanding should stay at Cascade=3; got {state.outstanding_by_kingdom!r}"
    )
    assert sw.items == [], (
        f"no ItemMsg should be sent for historical replay (Cappy speech "
        f"suppression); got {len(sw.items)}"
    )
    assert sw.outstanding == [], (
        "no OutstandingMsg push needed when nothing changed"
    )
    # received_items mirror IS populated so switch_server can replay
    # captures/kingdoms to a freshly booted mod across this same bridge
    # session — that's the whole reason add_received_item runs unconditionally.
    assert len(state.received_items) == 3


@pytest.mark.asyncio
async def test_v2_processes_live_items_above_rii():
    """After the historical replay, the next ReceivedItems with new items
    (index >= rii) processes them normally and advances rii."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    state.replace_outstanding({"Cascade": 3})
    state.set_received_items_index(3)
    ctx._outstanding_hydrated.set()

    # AP delivers item #4 (position 3) — first item above the rii mark.
    await ctx._handle_ap_package("ReceivedItems", {
        "index": 3,
        "items": [{"item": 43, "player": 0, "flags": 0}],  # Sand PM
    })
    await asyncio.sleep(0)

    assert state.outstanding_by_kingdom == {"Cascade": 3, "Sand": 1}
    assert len(sw.items) == 1
    assert sw.items[0].kingdom == "Sand"
    assert len(sw.outstanding) == 1
    assert state.get_received_items_index() == 4


@pytest.mark.asyncio
async def test_v2_mixed_batch_processes_only_new_items():
    """A reconnect with a mixed batch: AP sends index=0, items=[3 historical
    + 2 new]. Historical items get add_received_item only; new items also
    get apply_grant + send_item. rii advances to 5."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    state.replace_outstanding({"Cascade": 3})
    state.set_received_items_index(3)
    ctx._outstanding_hydrated.set()

    await ctx._handle_ap_package("ReceivedItems", {
        "index": 0,
        "items": [
            # 3 historical (positions 0,1,2 — below rii=3, skip)
            {"item": 42, "player": 0, "flags": 0},
            {"item": 42, "player": 0, "flags": 0},
            {"item": 42, "player": 0, "flags": 0},
            # 2 new (positions 3,4 — above rii, process)
            {"item": 43, "player": 0, "flags": 0},  # Sand PM
            {"item": 44, "player": 0, "flags": 0},  # Cascade Multi (+3)
        ],
    })
    await asyncio.sleep(0)

    assert state.outstanding_by_kingdom == {"Cascade": 6, "Sand": 1}
    assert len(sw.items) == 2  # Only the 2 new items get ItemMsg.
    assert state.get_received_items_index() == 5
    assert len(state.received_items) == 5  # All mirrored.


@pytest.mark.asyncio
async def test_v1_migration_skips_historical_batch():
    """When the AP store value is the legacy bare-dict schema (no `_v`),
    the hydrated outstanding is trusted. The next ReceivedItems(index=0)
    is treated as the historical replay matching that hydrated balance
    and skipped entirely. rii is set to the batch length so subsequent
    items are correctly recognized as new."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    # Simulate hydration from the v1 schema (raw dict, no `_v` tag).
    state.replace_outstanding({"Cascade": 3})
    state.set_received_items_index(0)  # what the v1 hydration path does
    ctx._outstanding_hydrated.set()
    ctx._outstanding_v1_migration_pending = True

    await ctx._handle_ap_package("ReceivedItems", {
        "index": 0,
        "items": [{"item": 42, "player": 0, "flags": 0}] * 3,
    })
    await asyncio.sleep(0)

    # Outstanding stays at the trusted value, NOT doubled.
    assert state.outstanding_by_kingdom == {"Cascade": 3}
    # rii now reflects the historical batch so future grants dedup correctly.
    assert state.get_received_items_index() == 3
    # Migration flag is cleared after handling.
    assert ctx._outstanding_v1_migration_pending is False
    # No ItemMsg sent (Cappy speech suppression for the historical replay).
    assert sw.items == []
    # received_items mirror is populated.
    assert len(state.received_items) == 3


@pytest.mark.asyncio
async def test_hydration_v2_schema_sets_rii():
    """v2-schema AP store value populates both outstanding AND rii on
    hydration; migration flag stays False."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    key = ctx._outstanding_key()
    ctx.stored_data[key] = {
        "_v": 2,
        "outstanding": {"Cascade": 5, "Sand": 2},
        "rii": 12,
    }

    await ctx._hydrate_outstanding_from_ap()
    await asyncio.sleep(0)

    assert state.outstanding_by_kingdom == {"Cascade": 5, "Sand": 2}
    assert state.get_received_items_index() == 12
    assert ctx._outstanding_v1_migration_pending is False
    assert ctx._outstanding_hydrated.is_set()


@pytest.mark.asyncio
async def test_hydration_v1_schema_sets_migration_flag():
    """v1-schema AP store value (bare dict) triggers the migration flag
    + leaves rii at 0 so the next ReceivedItems triggers the skip path."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    key = ctx._outstanding_key()
    ctx.stored_data[key] = {"Cascade": 5}  # legacy bare-dict

    await ctx._hydrate_outstanding_from_ap()
    await asyncio.sleep(0)

    assert state.outstanding_by_kingdom == {"Cascade": 5}
    assert state.get_received_items_index() == 0
    assert ctx._outstanding_v1_migration_pending is True
    assert ctx._outstanding_hydrated.is_set()


@pytest.mark.asyncio
async def test_hydration_empty_store_no_migration():
    """A fresh slot with no AP store entry yet (or an empty dict) hydrates
    cleanly without flagging the migration. Subsequent ReceivedItems take
    the normal rii=0 path and build state from scratch."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    # No stored_data entry at all — _hydrate falls back to {}.

    await ctx._hydrate_outstanding_from_ap()
    await asyncio.sleep(0)

    assert state.outstanding_by_kingdom == {}
    assert state.get_received_items_index() == 0
    assert ctx._outstanding_v1_migration_pending is False
    assert ctx._outstanding_hydrated.is_set()


@pytest.mark.asyncio
async def test_received_items_blocks_until_hydrated():
    """ReceivedItems handler waits on _outstanding_hydrated before running
    dedup. This is the order-A guard: AP often sends ReceivedItems(index=0)
    BEFORE Retrieved (Retrieved is our response, ReceivedItems is a push),
    so without the gate every fresh connect would see rii=0 and treat the
    historical batch as new → double-count."""
    import asyncio
    ctx, state, sw = _make_ctx_with_slot()
    # Don't set the gate.
    assert not ctx._outstanding_hydrated.is_set()

    # Start the ReceivedItems handler — it should block on the gate.
    task = asyncio.create_task(ctx._handle_ap_package("ReceivedItems", {
        "index": 0,
        "items": [{"item": 42, "player": 0, "flags": 0}],
    }))
    # Give it a few event-loop ticks; nothing should land at the Switch yet.
    for _ in range(5):
        await asyncio.sleep(0)
    assert sw.items == [], "ReceivedItems should be blocked on the gate"

    # Now hydrate (simulates Retrieved arriving).
    key = ctx._outstanding_key()
    ctx.stored_data[key] = {"_v": 2, "outstanding": {"Cascade": 3}, "rii": 3}
    await ctx._hydrate_outstanding_from_ap()

    # Wait for the gated handler to complete.
    await asyncio.wait_for(task, timeout=2.0)

    # Hydrated rii=3 means position 0 (the only item we sent) is below
    # the high-water mark → side effects skipped.
    assert state.outstanding_by_kingdom == {"Cascade": 3}
    assert sw.items == []
