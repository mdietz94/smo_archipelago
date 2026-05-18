"""End-to-end test of SwitchServer using a real TCP loopback connection."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from client import protocol
from client.protocol import HelloMsg, ItemMsg, ItemRef, ItemKind, LogMsg
from client.state import BridgeState, ItemEvent, CheckEvent
from client.switch_server import SwitchServer


@pytest.mark.asyncio
async def test_hello_handshake_and_replay():
    state = BridgeState()
    state.slot = "Mario"
    state.seed = "TEST"
    # Pre-populate state so the HELLO replay sends something interesting.
    state.add_received_item(ItemEvent(
        item=ItemRef(kind=ItemKind.CAPTURE.value, cap="Frog"),
        sender="Bob", cappy_from="Bob",
    ))
    state.add_checked_location(CheckEvent(
        item=ItemRef(kind=ItemKind.MOON.value, kingdom="Cascade", shine_id="DinoNest")
    ))

    checks_received: list[dict] = []
    goals_received: list[None] = []

    async def on_check(msg: dict) -> None:
        checks_received.append(msg)

    async def on_goal() -> None:
        goals_received.append(None)

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server  # plug in so stop() works
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        # Send HELLO.
        writer.write(protocol.encode(HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0",
                                              cap_table_hash="sha1:cafebabe")))
        await writer.drain()

        # Expect: hello_ack, then checked_replay, then 1 item, then ap_state.
        msgs = await _drain_messages(reader, n=4, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "checked_replay", "item", "ap_state"]
        assert msgs[0]["seed"] == "TEST"
        assert msgs[0]["slot"] == "Mario"
        # SwitchServer constructed without deathlink_enabled -> defaults False.
        assert msgs[0]["deathlink_enabled"] is False
        assert len(msgs[1]["ids"]) == 1
        assert msgs[1]["ids"][0]["shine_id"] == "DinoNest"
        assert msgs[2]["cap"] == "Frog"
        assert msgs[2]["from"] == "Bob"

        # Send a check; verify on_check fires and bridge state updates.
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.MOON.value, kingdom="Sand", shine_id="PoolUnderwater"
        )))
        await writer.drain()
        await asyncio.sleep(0.1)
        assert len(checks_received) == 1
        assert checks_received[0]["kingdom"] == "Sand"
        assert state.moons_checked_by_kingdom.get("Sand") == 1

        # Send a raw-ID moon check (M4 wire-format additions).
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.MOON.value,
            stage_name="CapWorldHomeStage",
            object_id="MoonOurFirst",
            shine_uid=12,
        )))
        await writer.drain()
        await asyncio.sleep(0.1)
        assert len(checks_received) == 2
        assert checks_received[1]["stage_name"] == "CapWorldHomeStage"
        assert checks_received[1]["object_id"] == "MoonOurFirst"
        assert checks_received[1]["shine_uid"] == 12

        # Send a capture-by-hack_name check.
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.CAPTURE.value, hack_name="Goomba"
        )))
        await writer.drain()
        await asyncio.sleep(0.1)
        assert checks_received[2]["hack_name"] == "Goomba"

        # Send goal; verify on_goal fires.
        writer.write(protocol.encode(protocol.GoalMsg()))
        await writer.drain()
        await asyncio.sleep(0.1)
        assert goals_received == [None]

        # Ping/pong.
        writer.write(protocol.encode(protocol.PingMsg(ts_ms=99)))
        await writer.drain()
        pong = (await _drain_messages(reader, n=1, timeout=1.0))[0]
        assert pong == {"t": "pong", "ts_ms": 99}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_death_message_dispatches_to_handler():
    state = BridgeState()

    deaths_received: list[int] = []

    async def on_check(_): ...
    async def on_goal(): ...
    async def on_death(ts_ms: int) -> None:
        deaths_received.append(ts_ms)

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal, on_death=on_death)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        writer.write(protocol.encode(protocol.DeathMsg(ts_ms=42_000)))
        await writer.drain()
        await asyncio.sleep(0.1)
        assert deaths_received == [42_000]
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_ack_advertises_deathlink_enabled():
    """When bridge config has DeathLink on, hello_ack must tell the mod so it
    will act on inbound kill messages. (Outbound is bridge-gated separately,
    so this flag exists purely for the inbound apply path.)"""
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal, deathlink_enabled=True)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0")))
        await writer.drain()
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        assert msgs[0]["t"] == "hello_ack"
        assert msgs[0]["deathlink_enabled"] is True
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_unknown_message_yields_err():
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(b'{"t":"hello"}\n{"t":"bogus_type"}\n')
        await writer.drain()
        msgs = await _drain_messages(reader, n=4, timeout=2.0)
        # hello_ack + checked_replay (empty) + ap_state + err
        kinds = [m["t"] for m in msgs]
        assert "err" in kinds
        err = next(m for m in msgs if m["t"] == "err")
        assert err["code"] == "unknown_kind"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_log_message_routes_to_switch_logger_and_state(caplog):
    """Switch-forwarded log lines must land in (a) BridgeState.last_messages
    (the snapshot feed the web tracker mirrors) and (b) the 'SMO' logger
    (the Kivy 'Switch' tab's underlying Python logging channel).
    """
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        with caplog.at_level(logging.DEBUG, logger="SMO"):
            writer.write(protocol.encode(LogMsg(
                level="warn",
                msg="[moon_label] no LayoutActor at self+0x20; dropping",
            )))
            writer.write(protocol.encode(LogMsg(
                level="info",
                msg="[hook] installed ShineGet trampoline",
            )))
            writer.write(protocol.encode(LogMsg(
                level="error",
                msg="bad thing happened",
            )))
            # Unknown level should fall back to INFO without erroring.
            writer.write(protocol.encode(LogMsg(level="exotic", msg="oddball")))
            await writer.drain()
            await asyncio.sleep(0.1)

        # (a) snapshot-feed assertion — pre-existing prefix format.
        assert any(
            "[switch:warn] [moon_label] no LayoutActor" in line
            for line in state.last_messages
        )
        assert any(
            "[switch:info] [hook] installed" in line for line in state.last_messages
        )
        assert any(
            "[switch:error] bad thing happened" in line for line in state.last_messages
        )
        assert any("[switch:exotic] oddball" in line for line in state.last_messages)

        # (b) caplog assertion — Python logging channel got the records.
        switch_records = [r for r in caplog.records if r.name == "SMO"]
        # All forwarded records carry the [switch:LEVEL] prefix so a reader
        # can tell them apart from PC-side SMO diagnostics in the tab.
        formatted = [r.getMessage() for r in switch_records]
        assert any("[switch:warn] [moon_label]" in m for m in formatted)
        assert any("[switch:info] [hook] installed" in m for m in formatted)
        assert any("[switch:error] bad thing happened" in m for m in formatted)
        # Level mapping: warn -> WARNING, info -> INFO, error -> ERROR, and
        # "exotic" (unknown) falls back to INFO.
        levels_seen = {r.levelno for r in switch_records}
        assert logging.WARNING in levels_seen
        assert logging.INFO in levels_seen
        assert logging.ERROR in levels_seen
        info_msgs = [r.getMessage() for r in switch_records if r.levelno == logging.INFO]
        assert any("[switch:exotic] oddball" in m for m in info_msgs)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_second_connection_rejected_busy():
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    try:
        w1.write(protocol.encode(HelloMsg()))
        await w1.drain()
        await _drain_messages(r1, n=3, timeout=2.0)  # consume hello_ack/replay/ap_state

        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            msgs = await _drain_messages(r2, n=1, timeout=2.0)
            assert msgs[0]["t"] == "err"
            assert msgs[0]["code"] == "busy"
        finally:
            w2.close()
            try:
                await w2.wait_closed()
            except Exception:
                pass
    finally:
        w1.close()
        try:
            await w1.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_replays_shine_palette():
    """When the bridge already has a (shine_uid -> palette) map from a
    previous AP LocationInfo, a fresh Switch HELLO must replay it so the
    mod restores colors after a reboot."""
    state = BridgeState()
    state.set_shine_palette({12: 1, 47: 3, 100: 2})

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + shine_scouts + ap_state
        msgs = await _drain_messages(reader, n=4, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert "shine_scouts" in kinds
        scouts = next(m for m in msgs if m["t"] == "shine_scouts")
        entries = {e["shine_uid"]: e["palette"] for e in scouts["entries"]}
        assert entries == {12: 1, 47: 3, 100: 2}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_stop_returns_promptly_with_active_switch_connection():
    """Regression for the GUI-close hang: when the user shuts down the
    client (e.g. by clicking the Kivy window's X) while the Switch is
    still connected, sw.stop() must return without blocking.

    Python 3.12+'s Server.wait_closed() waits for both the listener AND
    every active client task — _handle_client is parked in reader.read()
    forever — so stop() has to close the active client writer first."""
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    await sw.start()
    port = sw._server.sockets[0].getsockname()[1]

    # Connect a fake Switch and wait until _handle_client has accepted +
    # captured the writer (otherwise stop() may run before the server has
    # registered the connection at all).
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for _ in range(50):
            if sw._writer is not None:
                break
            await asyncio.sleep(0.01)
        assert sw._writer is not None, "server never registered the connection"

        # The actual regression: this should not hang. Cap at 2s — the
        # bug exhibited as an indefinite block, so even a few hundred ms
        # of slack here is plenty of headroom over the expected ~10ms.
        await asyncio.wait_for(sw.stop(), timeout=2.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# M6 phase D — deposit + outstanding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_sends_outstanding_and_skips_moon_items_in_replay():
    """On HELLO, the bridge must (a) push the per-kingdom OutstandingMsg
    BEFORE the item replay and (b) skip Moon items in the replay so the
    Switch doesn't double-count credits."""
    state = BridgeState()
    # Pre-load received_items with one Moon (must be skipped) and one
    # Capture (must replay through as ItemMsg).
    from client.protocol import OutstandingEntry
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="moon", kingdom="Wooded", shine_id="Power Moon"),
        sender="Mario",
    ))
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="capture", cap="Frog"),
        sender="Bob",
    ))
    # Mirror what context.py would have applied for the Moon grant above.
    state.apply_grant("Wooded", 1)
    state.apply_grant("Cap", 2)

    async def on_check(_): return None
    async def on_goal(): ...

    def get_outstanding():
        return [
            OutstandingEntry(kingdom=k, count=v)
            for k, v in sorted(state.get_outstanding().items())
        ]

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        get_outstanding_entries=get_outstanding,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # Expect: hello_ack, outstanding, checked_replay, item (capture
        # only, NOT the moon), ap_state.
        msgs = await _drain_messages(reader, n=5, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "outstanding", "checked_replay",
                         "item", "ap_state"]
        outstanding = msgs[1]
        assert outstanding["entries"] == [
            {"kingdom": "Cap", "count": 2},
            {"kingdom": "Wooded", "count": 1},
        ]
        item = msgs[3]
        assert item["kind"] == "capture"
        assert item["cap"] == "Frog"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_without_outstanding_provider_replays_legacy_path():
    """When `get_outstanding_entries` is None (older bridge wiring) HELLO
    must NOT send an OutstandingMsg — falls back to the M6-A behavior
    where ItemMsg replay drives the per-kingdom counter."""
    state = BridgeState()
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="moon", kingdom="Cap", shine_id="Power Moon"),
        sender="Mario",
    ))

    async def on_check(_): return None
    async def on_goal(): ...

    # No get_outstanding_entries -> moons still skip in replay (so
    # legacy bridges talking to new switches don't double-count). The
    # mod's M6-A code is gone; if anyone needs the old behavior they
    # need to roll back the mod too.
    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + ap_state (no outstanding, no item).
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert "outstanding" not in kinds
        assert "item" not in kinds  # moon skipped, no captures
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_replay_respects_cappy_suppression_for_self_finds():
    """The HELLO replay path must use ItemEvent.cappy_from (not .sender)
    for ItemMsg.from_, so a self-find item that was silent on the live
    path stays silent across save reloads / Switch reconnects.

    Pre-fix: replay used evt.sender which is the unsuppressed slot name,
    so every save load would pop a Cappy bubble for self-finds.
    """
    state = BridgeState()
    # Self-find: live path stored cappy_from="" (suppressed); sender holds
    # the real name for logging / web tracker.
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="capture", cap="Goomba"),
        sender="Mario", cappy_from="",
    ))
    # Server-injected (e.g. /send Mario Frog): live path kept cappy_from
    # populated so replay still surfaces a bubble.
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="capture", cap="Frog"),
        sender="Archipelago", cappy_from="Archipelago",
    ))

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + item (self) + item (server) + ap_state
        msgs = await _drain_messages(reader, n=5, timeout=2.0)
        items = [m for m in msgs if m["t"] == "item"]
        assert len(items) == 2
        by_cap = {m["cap"]: m for m in items}
        # Self-find: from_ collapsed -> "" -> Switch's Cappy filter skips it.
        assert by_cap["Goomba"]["from"] == ""
        # Server-injected: from_ surfaces -> Cappy fires.
        assert by_cap["Frog"]["from"] == "Archipelago"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_deposit_msg_dispatched_and_acked():
    state = BridgeState()
    state.apply_grant("Wooded", 3)
    deposits_seen: list[dict] = []

    async def on_check(_): return None
    async def on_goal(): ...
    async def on_deposit(*, seq: int, kingdom: str, amount: int) -> bool:
        deposits_seen.append({"seq": seq, "kingdom": kingdom, "amount": amount})
        # Mirror what apply_deposit_from_switch does so the test exercises
        # the realistic flow including the should_skip_deposit dedup.
        if state.should_skip_deposit(seq):
            return False
        state.apply_deposit(kingdom, amount)
        return True

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal,
                      on_deposit=on_deposit)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        # Two deposits in sequence.
        writer.write(protocol.encode(protocol.DepositMsg(
            seq=1, kingdom="Wooded", amount=1,
        )))
        writer.write(protocol.encode(protocol.DepositMsg(
            seq=2, kingdom="Wooded", amount=1,
        )))
        await writer.drain()
        acks = await _drain_messages(reader, n=2, timeout=2.0)
        assert [a["t"] for a in acks] == ["deposit_ack", "deposit_ack"]
        assert [a["seq"] for a in acks] == [1, 2]
        assert deposits_seen == [
            {"seq": 1, "kingdom": "Wooded", "amount": 1},
            {"seq": 2, "kingdom": "Wooded", "amount": 1},
        ]
        # Bridge balance went from 3 -> 1 (one per deposit).
        assert state.get_outstanding()["Wooded"] == 1
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_deposit_msg_replay_is_idempotent():
    """If the same seq arrives twice (reconnect-driven replay), bridge
    should re-ack but only apply once."""
    state = BridgeState()
    state.apply_grant("Cap", 5)
    applied_calls: list[int] = []

    async def on_check(_): return None
    async def on_goal(): ...
    async def on_deposit(*, seq: int, kingdom: str, amount: int) -> bool:
        if state.should_skip_deposit(seq):
            return False
        applied_calls.append(seq)
        state.apply_deposit(kingdom, amount)
        return True

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal,
                      on_deposit=on_deposit)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        for _ in range(3):
            writer.write(protocol.encode(protocol.DepositMsg(
                seq=10, kingdom="Cap", amount=2,
            )))
        await writer.drain()
        acks = await _drain_messages(reader, n=3, timeout=2.0)
        # All three got acked (idempotent re-ack).
        assert [a["seq"] for a in acks] == [10, 10, 10]
        # But only one apply landed.
        assert applied_calls == [10]
        assert state.get_outstanding()["Cap"] == 3
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_deposit_msg_invalid_yields_err():
    state = BridgeState()
    async def on_check(_): return None
    async def on_goal(): ...
    async def on_deposit(**_): return False
    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal,
                      on_deposit=on_deposit)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        # Missing seq.
        writer.write(b'{"t":"deposit","kingdom":"Cap","amount":1}\n')
        # Invalid amount (negative).
        writer.write(b'{"t":"deposit","seq":1,"kingdom":"Cap","amount":-1}\n')
        await writer.drain()
        msgs = await _drain_messages(reader, n=2, timeout=2.0)
        assert all(m["t"] == "err" and m["code"] == "bad_deposit" for m in msgs)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


async def _drain_messages(reader: asyncio.StreamReader, n: int, timeout: float) -> list[dict]:
    """Read until we've parsed n full JSON lines or timeout expires."""
    buf = bytearray()
    out: list[dict] = []

    async def _pump():
        while len(out) < n:
            chunk = await reader.read(4096)
            if not chunk:
                return
            buf.extend(chunk)
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(buf[:nl]).strip()
                del buf[: nl + 1]
                if line:
                    out.append(json.loads(line))
                    if len(out) >= n:
                        return

    await asyncio.wait_for(_pump(), timeout=timeout)
    return out
