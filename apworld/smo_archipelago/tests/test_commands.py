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
        self.deathlink_calls: list[bool] = []
        self.push_deathlink_calls: int = 0
        self.talkatoo_pool_calls: list[tuple[bool, dict[str, list[str]]]] = []
        self.push_talkatoo_calls: int = 0
        self.shop_label_calls: list[list[dict]] = []
        self.push_shop_label_calls: int = 0

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

    def set_deathlink_enabled(self, enabled: bool) -> None:
        self.deathlink_calls.append(bool(enabled))

    async def push_deathlink_helloack(self) -> None:
        self.push_deathlink_calls += 1

    def set_talkatoo_pool(self, enabled: bool, kingdoms: dict[str, list[str]]) -> None:
        self.talkatoo_pool_calls.append((bool(enabled), {k: list(v) for k, v in kingdoms.items()}))

    async def push_talkatoo_pool(self) -> None:
        self.push_talkatoo_calls += 1

    def set_shop_labels(self, entries: list[dict]) -> None:
        self.shop_label_calls.append([dict(e) for e in entries])

    async def push_shop_labels(self) -> None:
        self.push_shop_label_calls += 1

    async def drain_pending_snapshot(self) -> None:
        """M6 phase C reconcile path — Connected calls this. The real
        SwitchServer drains snapshot entries buffered during the AP
        handshake window; for unit tests there's nothing to drain."""
        pass


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
    kinds, so MOON/CAPTURE items arrived on the Switch with no
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
        # Other Connected fields the handler tolerates being absent.
        # M6-phase-D no longer subscribes to any AP data-store key
        # (outstanding is derived), and display/colors default off so
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
    assert ctx.talkatoo_mode is False

    # Explicit None.
    sw.capturesanity_calls.clear()
    await ctx._handle_ap_package("Connected", {"slot_data": None})
    assert sw.capturesanity_calls == [False]
    assert ctx.talkatoo_mode is False


@pytest.mark.asyncio
async def test_connected_handler_honors_slot_data_talkatoo_mode_on():
    """`talkatoo_mode: 1` in slot_data flips the SMOContext flag and pushes
    the per-kingdom AP-pool to the Switch. The pool is derived from the
    union of missing_locations + checked_locations classified through the
    DataPackage, so a context with no datapackage entries pushes an empty
    pool but still sets the flag."""
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

    assert ctx.talkatoo_mode is False

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 0, "talkatoo_mode": 1},
    })

    assert ctx.talkatoo_mode is True
    # set_talkatoo_pool fires regardless of pool content so the Switch's
    # _talkatoo_configured flag flips and push_talkatoo_pool() stops being
    # a no-op.
    assert len(sw.talkatoo_pool_calls) == 1
    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    # No datapackage entries on this stub ctx → empty pool, but the call
    # itself happened.
    assert kingdoms == {}
    assert sw.push_talkatoo_calls == 1


@pytest.mark.asyncio
async def test_connected_handler_filters_progression_moons_from_talkatoo_pool():
    """Gap #1: progression-flagged moons (Multi Moons, scenario bosses,
    Seaside seals, Bowser's chain) MUST NOT appear in the per-kingdom
    talkatoo_pool the bridge ships to the Switch.

    Phase 4's MoonGetHook always lets these through via the
    isProgressionShine bypass, so naming one in Talkatoo's bubble would
    waste a hint slot. The DataPackage knows which locations carry the
    flag (loaded from locations.json); _derive_and_push_talkatoo_pool
    drops them before grouping by kingdom."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    # Wire synthetic loc_ids -> known names. The handler walks
    # missing_locations + checked_locations through dp.location_id_to_name,
    # so loc_id values don't have to match real AP ids — they just have
    # to map onto the apworld's location names so classify_location +
    # is_progression_location find them. Use high ids in the SMO apworld
    # range; CommonContext.__init__ pre-loads `network_data_package`
    # (every installed apworld's id table) into `self.item_names` /
    # `self.location_names`, and _populate_datapackage_from_self mirrors
    # all of those into `ctx.dp`. Sub-10k ids collide with other apworlds
    # (Super Mario Land 2, Starcraft 2) and get clobbered.
    fixtures = {
        # Progression: 2 Cascade entries (1 opener + 1 Multi Moon).
        70001001: "Cascade: Our First Power Moon",
        70001002: "Cascade: Multi Moon Atop the Falls",
        # Progression: a Seaside seal + Bowser's chain entry.
        70001003: "Seaside: The Stone Pillar Seal",
        70001004: "Bowser's: Showdown at Bowser's Castle",
        # Non-progression: regular Cascade moons. These MUST survive.
        70002001: "Cascade: Chomp Through the Rocks",
        70002002: "Cascade: Behind the Waterfall",
        # Non-moon: capture entries are skipped earlier in the loop
        # (classify_location returns CAPTURE, not MOON) — make sure
        # they don't sneak in.
        70003001: "Capture: Goomba",
    }
    for loc_id, name in fixtures.items():
        ctx.dp.location_id_to_name[loc_id] = name
        ctx.dp.location_name_to_id[name] = loc_id
    ctx.missing_locations = set(fixtures.keys())  # type: ignore[assignment]

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"talkatoo_mode": 1},
    })

    assert len(sw.talkatoo_pool_calls) == 1
    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    # Cascade kept ONLY the non-progression moons; the 2 progression
    # entries (Our First Power Moon, Multi Moon Atop the Falls) are gone.
    assert "Cascade" in kingdoms
    cascade_pool = set(kingdoms["Cascade"])
    assert cascade_pool == {"Chomp Through the Rocks", "Behind the Waterfall"}
    # Seaside had ONLY a progression moon → kingdom absent from the pool.
    assert "Seaside" not in kingdoms
    # Bowser's likewise.
    assert "Bowser's" not in kingdoms
    # Capture didn't sneak in as a "kingdom" either.
    assert "Capture" not in kingdoms


@pytest.mark.asyncio
async def test_connected_handler_consumes_phase5_talkatoo_order():
    """Gap #3 / Phase 5: when slot_data ships `talkatoo_order`, the
    bridge ships a window of 3 (the cursor-front of each kingdom) to
    the Switch instead of the full filtered pool. Cursor starts at 0
    because no locations are in checked_locations yet."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    # Wire datapackage name <-> id for the kingdom's moons so the cursor
    # can resolve which loc_ids belong to the order.
    cascade_order_full = [
        "Chomp Through the Rocks", "Behind the Waterfall", "On Top of the Rubble",
        "Treasure of the Waterfall Basin", "Above a High Cliff",
    ]
    for i, shine_id in enumerate(cascade_order_full):
        name = f"Cascade: {shine_id}"
        ctx.dp.location_id_to_name[5000 + i] = name
        ctx.dp.location_name_to_id[name] = 5000 + i

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Cascade": cascade_order_full},
        },
    })

    assert ctx.talkatoo_mode is True
    assert ctx.talkatoo_order == {"Cascade": cascade_order_full}
    assert len(sw.talkatoo_pool_calls) == 1
    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    # Window of 3 from the front of the order — cursor starts at 0
    # because no checked_locations were preloaded.
    assert kingdoms == {"Cascade": cascade_order_full[:3]}


@pytest.mark.asyncio
async def test_connected_phase5_cursor_skips_already_checked():
    """Cursor = smallest index whose loc isn't in checked_locations.
    If the player has already collected the front 2 moons (from a
    prior session), the initial window starts at index 2."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    order = ["A", "B", "C", "D", "E"]
    for i, shine_id in enumerate(order):
        name = f"Cap: {shine_id}"
        ctx.dp.location_id_to_name[6000 + i] = name
        ctx.dp.location_name_to_id[name] = 6000 + i
    # First two already collected.
    ctx.checked_locations = {6000, 6001}  # type: ignore[assignment]

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Cap": order},
        },
    })

    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    # Cursor at 2 → window is [C, D, E].
    assert kingdoms == {"Cap": ["C", "D", "E"]}


@pytest.mark.asyncio
async def test_connected_phase5_window_skips_mid_window_checks():
    """Phase 5 regression (2026-05-21): when the player collects a moon
    that wasn't at the cursor front (e.g. Talkatoo named order[cursor+2]
    and the player went and got it), the window must drop that entry on
    the next re-ship. The original `order[cursor:cursor+3]` slice didn't
    filter checked entries, so Talkatoo kept re-suggesting collected
    moons indefinitely (observed live: 'Chomp Through the Rocks' named
    immediately after the player collected it).

    Fix: walk from cursor and take the first 3 entries that are NOT in
    checked_locations.
    """
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    order = ["A", "B", "C", "D", "E", "F"]
    for i, shine_id in enumerate(order):
        name = f"Cascade: {shine_id}"
        ctx.dp.location_id_to_name[10000 + i] = name
        ctx.dp.location_name_to_id[name] = 10000 + i
    # Player collected B and D mid-window. Cursor stays at 0 (A still
    # uncollected); window walks A,B,C,D,E... skipping B and D → [A,C,E].
    ctx.checked_locations = {10001, 10003}  # type: ignore[assignment]

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Cascade": order},
        },
    })

    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    assert kingdoms == {"Cascade": ["A", "C", "E"]}


@pytest.mark.asyncio
async def test_connected_phase5_empty_window_when_all_collected():
    """When every moon in a kingdom's order is collected, the cursor
    moves past the end and the window is empty — that kingdom drops
    from the pool dict entirely (avoids a no-op send to the Switch)."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    order = ["X", "Y"]
    for i, shine_id in enumerate(order):
        name = f"Lake: {shine_id}"
        ctx.dp.location_id_to_name[7000 + i] = name
        ctx.dp.location_name_to_id[name] = 7000 + i
    ctx.checked_locations = {7000, 7001}  # all collected

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Lake": order},
        },
    })

    enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert enabled is True
    assert kingdoms == {}  # Lake dropped — nothing to send


@pytest.mark.asyncio
async def test_roomupdate_slides_cursor_when_check_lands_in_order():
    """RoomUpdate with a new check on a moon in talkatoo_order advances
    the cursor for that kingdom and re-ships the pool."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    order = ["A", "B", "C", "D"]
    for i, shine_id in enumerate(order):
        name = f"Sand: {shine_id}"
        ctx.dp.location_id_to_name[8000 + i] = name
        ctx.dp.location_name_to_id[name] = 8000 + i

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Sand": order},
        },
    })
    sw.talkatoo_pool_calls.clear()  # focus assertions on the next push

    # Player collects A. CommonContext.process_server_cmd merges the
    # delta into checked_locations BEFORE on_package fires — simulate.
    ctx.checked_locations.add(8000)
    await ctx._handle_ap_package("RoomUpdate", {
        "checked_locations": [8000],
    })

    # Cursor advanced to 1; window is now [B, C, D].
    assert len(sw.talkatoo_pool_calls) == 1
    _enabled, kingdoms = sw.talkatoo_pool_calls[0]
    assert kingdoms == {"Sand": ["B", "C", "D"]}


@pytest.mark.asyncio
async def test_roomupdate_skips_reship_when_check_unrelated_to_talkatoo():
    """Capture-location checks and other-game checks shouldn't trigger
    a re-ship — the cursor for talkatoo_order can't possibly have moved."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    order = ["A", "B", "C"]
    for i, shine_id in enumerate(order):
        name = f"Wooded: {shine_id}"
        ctx.dp.location_id_to_name[9000 + i] = name
        ctx.dp.location_name_to_id[name] = 9000 + i
    # A capture loc_id that's not in any kingdom order.
    ctx.dp.location_id_to_name[9999] = "Capture: Goomba"
    ctx.dp.location_name_to_id["Capture: Goomba"] = 9999

    await ctx._handle_ap_package("Connected", {
        "slot_data": {
            "talkatoo_mode": 1,
            "talkatoo_order": {"Wooded": order},
        },
    })
    sw.talkatoo_pool_calls.clear()

    ctx.checked_locations.add(9999)
    await ctx._handle_ap_package("RoomUpdate", {
        "checked_locations": [9999],
    })

    # No re-ship — the check wasn't a talkatoo_order moon.
    assert len(sw.talkatoo_pool_calls) == 0


@pytest.mark.asyncio
async def test_roomupdate_skips_when_talkatoo_mode_off():
    """RoomUpdate handler is a no-op when talkatoo_mode is off, even if
    talkatoo_order is somehow populated. Guards against doing work on
    non-Talkatoo seeds where this whole path is irrelevant."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]
    # talkatoo_mode off, talkatoo_order empty.
    await ctx._handle_ap_package("RoomUpdate", {
        "checked_locations": [12345],
    })
    assert sw.talkatoo_pool_calls == []


@pytest.mark.asyncio
async def test_connected_handler_honors_slot_data_talkatoo_mode_off():
    """`talkatoo_mode: 0` keeps the default — Switch still gets a set_
    talkatoo_pool call (with enabled=False) so any prior session's state
    is cleared on the Switch side."""
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
        "slot_data": {"talkatoo_mode": 0},
    })

    assert ctx.talkatoo_mode is False
    assert len(sw.talkatoo_pool_calls) == 1
    enabled, _ = sw.talkatoo_pool_calls[0]
    assert enabled is False
    assert sw.push_talkatoo_calls == 1


@pytest.mark.asyncio
async def test_connected_handler_honors_slot_data_death_link_on():
    """`death_link: true` in the player YAML lands in slot_data and the
    Connected handler must flip the bridge into DeathLink mode — set the
    local mirror, update the AP "DeathLink" tag, and propagate to the
    Switch (set the flag + push a fresh HelloAck so the Switch stops
    dropping inbound kills in ApState::maybeApplyInboundKill).

    Regression: pre-fix, slot_data["death_link"] was ignored entirely and
    the user had to enable DeathLink via host.yaml / --deathlink / TOML
    config, contradicting the standard AP convention."""
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

    # Stub out update_death_link so we don't need a live server connection
    # to observe the call (the real method tries to send_msgs over a
    # non-existent socket otherwise).
    tag_updates: list[bool] = []
    async def _fake_update_death_link(enabled: bool) -> None:
        tag_updates.append(enabled)
        if enabled:
            ctx.tags.add("DeathLink")
        else:
            ctx.tags.discard("DeathLink")
    ctx.update_death_link = _fake_update_death_link  # type: ignore[assignment]

    assert ctx.deathlink_enabled is False

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 0, "death_link": 1},
    })

    assert ctx.deathlink_enabled is True
    assert "DeathLink" in ctx.tags
    assert tag_updates == [True]
    assert sw.deathlink_calls == [True]
    assert sw.push_deathlink_calls == 1


@pytest.mark.asyncio
async def test_connected_handler_honors_slot_data_death_link_off():
    """Symmetric case: a slot whose YAML explicitly says `death_link: 0`
    forces the bridge off even if it launched with `--deathlink`.

    DeathLink is per-slot (each player opts in via their own YAML; an N-
    player seed can have any subset participating), and slot_data carries
    this player's authoritative choice for this seed. The launch-time
    --deathlink override is legacy/dev — slot_data wins, which also drops
    the "DeathLink" server tag so the player stops receiving deaths from
    the opted-in subset they explicitly opted out of."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
        deathlink_enabled=True,  # simulate --deathlink at launch
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    tag_updates: list[bool] = []
    async def _fake_update_death_link(enabled: bool) -> None:
        tag_updates.append(enabled)
        if enabled:
            ctx.tags.add("DeathLink")
        else:
            ctx.tags.discard("DeathLink")
    ctx.update_death_link = _fake_update_death_link  # type: ignore[assignment]

    assert ctx.deathlink_enabled is True

    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 0, "death_link": 0},
    })

    assert ctx.deathlink_enabled is False
    assert "DeathLink" not in ctx.tags
    assert tag_updates == [False]
    assert sw.deathlink_calls == [False]
    assert sw.push_deathlink_calls == 1


@pytest.mark.asyncio
async def test_connected_handler_leaves_deathlink_alone_when_slot_data_absent():
    """Missing `death_link` key (older apworld build) must NOT clobber the
    launch-time setting — silently flipping it would surprise users on an
    old seed mid-session."""
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
        deathlink_enabled=True,
    )
    ctx.auth = "Mario"
    sw = _StubSwitch()
    ctx.switch = sw  # type: ignore[assignment]

    update_calls: list[bool] = []
    async def _fake_update_death_link(enabled: bool) -> None:
        update_calls.append(enabled)
    ctx.update_death_link = _fake_update_death_link  # type: ignore[assignment]

    # slot_data present but no death_link key — launch state preserved,
    # no tag update, no push to Switch.
    await ctx._handle_ap_package("Connected", {
        "slot_data": {"capturesanity": 0},
    })

    assert ctx.deathlink_enabled is True
    assert update_calls == []
    assert sw.deathlink_calls == []
    assert sw.push_deathlink_calls == 0


def test_to_ref_preserves_name_for_all_kinds():
    """Pure unit-level guard against re-introducing the OTHER-only conditional."""
    from client.datapackage import ClassifiedItem
    from client.protocol import ItemKind

    for kind, kwargs in [
        (ItemKind.MOON, {"kingdom": "Cascade", "shine_id": "Power Moon"}),
        (ItemKind.CAPTURE, {"cap": "Goomba"}),
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

    # Seed a PaySnapshot so compute_outstanding has a reading. Without
    # this, the bridge defers OutstandingMsg until the Switch's first
    # PaySnapshotMsg lands (Switch on title screen guard).
    state.apply_pay_snapshot({})

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

    # Seed a PaySnapshot so compute_outstanding has a reading.
    state.apply_pay_snapshot({})

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


# (The M6-phase-D `_outstanding_*` / rii dedup / v1 migration / hydration
# test block was deleted alongside the derivation refactor. Outstanding is
# now derived from (lifetime_received_AP - PayShineNum); the new equivalent
# tests live in test_outstanding.py — see test_crash_rollback_recovers_outstanding
# for the headline bug-class regression.)


@pytest.mark.asyncio
async def test_populate_datapackage_mirrors_all_games_not_just_smo():
    """Channel A cutscene-label regression: cross-game items must resolve.

    Bug: `_populate_datapackage_from_self` iterated `(GAME_NAME,
    "Archipelago")` only, so when an SMO location held an item destined
    for another player's *game*, `dp.item_id_to_name.get(scout.item_id)`
    missed and `compose_moon_label_for_location` returned None — the
    Switch saw no MoonLabelMsg and the get-cinematic kept its vanilla
    Nintendo text instead of "Sent X to <player>".

    Two reported instances (Cap: Shopping in Bonneton → Paint, Cascade:
    Very Nice Shot with the Chain Chomp! → Paint) were the same failure.
    Fix is to iterate every game CommonContext has datapackage entries
    for, not a hard-coded pair.
    """
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )

    # Simulate CommonContext absorbing a DataPackage packet for three games:
    # our own, the implicit "Archipelago" generic-items game, and a third
    # one belonging to another player whose game we know nothing about.
    ctx.item_names.update_game(
        "Spicy Meatball Overdrive",
        {"Cap Kingdom Power Moon": 71001},
    )
    ctx.location_names.update_game(
        "Spicy Meatball Overdrive",
        {"Cap: Shopping in Bonneton": 81001},
    )
    ctx.item_names.update_game("Archipelago", {"Nothing": 0})
    ctx.location_names.update_game("Archipelago", {})
    ctx.item_names.update_game(
        "Paint",
        {"Additional Palette Color": 234567},
    )
    ctx.location_names.update_game(
        "Paint",
        {"Paint Canvas 1": 345678},
    )

    ctx._populate_datapackage_from_self()

    # Own-game ids: present (pre-fix worked here).
    assert ctx.dp.item_id_to_name.get(71001) == "Cap Kingdom Power Moon"
    assert ctx.dp.location_name_to_id.get(
        "Cap: Shopping in Bonneton") == 81001
    # Cross-game ids: present (the regression). Pre-fix, this assert
    # failed because Paint's item id was never mirrored.
    assert ctx.dp.item_id_to_name.get(234567) == "Additional Palette Color"
    assert ctx.dp.location_name_to_id.get("Paint Canvas 1") == 345678


@pytest.mark.asyncio
async def test_compose_moon_label_for_cross_game_recipient_resolves():
    """End-to-end Channel A path: collecting an SMO location that holds a
    foreign-game item must produce a 'Sent <item> to <player>' label.

    This is the bug as it presents externally — the cutscene-label hook
    on the Switch wants a synthesized string, and the dispatcher gives
    up (returns None) without it. Walks the same code path
    SwitchServer._dispatch_check uses on a live moon collect.
    """
    ctx = SMOContext(
        server_address=None, password=None,
        state=BridgeState(),
        datapackage=DataPackage(apworld_data_dir=_APWORLD_DATA),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Talkatoo"
    ctx.player_names = {1: "Paint", 2: "Talkatoo"}

    LOC_ID = 81002    # SMO location id we own
    ITEM_ID = 234567  # Item id in Paint's game
    ctx.item_names.update_game(
        "Spicy Meatball Overdrive",
        {"Cap Kingdom Power Moon": 71001},
    )
    ctx.location_names.update_game(
        "Spicy Meatball Overdrive",
        {"Cap: Shopping in Bonneton": LOC_ID},
    )
    ctx.item_names.update_game(
        "Paint",
        {"Additional Palette Color": ITEM_ID},
    )
    ctx._populate_datapackage_from_self()

    # Pre-warm the scout cache the way LocationInfo would: SMO location
    # LOC_ID holds Paint's ITEM_ID, recipient is slot 1 ("Paint").
    ctx.scout_cache.absorb(location=LOC_ID, item=ITEM_ID, recipient=1)

    label = ctx.compose_moon_label_for_location(LOC_ID)

    assert label is not None, (
        "compose_moon_label returned None — Paint's item id never made it "
        "into dp.item_id_to_name (the regression)"
    )
    # Paint is the recipient, Talkatoo is us → "Sent ... to Paint",
    # truncated to ≤ 30 bytes by display.format_moon_label.
    from client.display import TRUNCATION_MARKER
    assert label.startswith("Sent "), label
    assert "Paint" in label or label.endswith(TRUNCATION_MARKER), label
