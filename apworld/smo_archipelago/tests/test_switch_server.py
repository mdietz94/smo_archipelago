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

        # (a) snapshot-feed assertion — multi-Switch prefix carries the
        # device_id so the UI can attribute lines to the right device.
        # HelloMsg() above had no device_id, so the bridge invents one
        # from the loopback peer's last octet ("sw-1" on 127.0.0.1).
        assert any(
            "[switch:sw-1:warn] [moon_label] no LayoutActor" in line
            for line in state.last_messages
        )
        assert any(
            "[switch:sw-1:info] [hook] installed" in line for line in state.last_messages
        )
        assert any(
            "[switch:sw-1:error] bad thing happened" in line for line in state.last_messages
        )
        assert any("[switch:sw-1:exotic] oddball" in line for line in state.last_messages)

        # (b) caplog assertion — Python logging channel got the records.
        switch_records = [r for r in caplog.records if r.name == "SMO"]
        formatted = [r.getMessage() for r in switch_records]
        assert any("[switch:sw-1:warn] [moon_label]" in m for m in formatted)
        assert any("[switch:sw-1:info] [hook] installed" in m for m in formatted)
        assert any("[switch:sw-1:error] bad thing happened" in m for m in formatted)
        # Level mapping: warn -> WARNING, info -> INFO, error -> ERROR, and
        # "exotic" (unknown) falls back to INFO.
        levels_seen = {r.levelno for r in switch_records}
        assert logging.WARNING in levels_seen
        assert logging.INFO in levels_seen
        assert logging.ERROR in levels_seen
        info_msgs = [r.getMessage() for r in switch_records if r.levelno == logging.INFO]
        assert any("[switch:sw-1:exotic] oddball" in m for m in info_msgs)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_same_host_reconnect_takes_over_stale_writer():
    """A second connection from the same peer IP REPLACES the existing one.

    The previous behavior was to reject extras with ErrMsg(busy). That was
    wrong in production because a Switch Wi-Fi blip can leave the bridge with
    a half-open writer (Switch closed its side but the FIN never delivered),
    and the bridge would then reject the Switch's reconnect attempts until
    the half-open socket eventually surfaces EPIPE on a write — which on TCP
    keepalive defaults can take minutes. We now treat peer-IP match as
    conclusive evidence of a reconnect and take over.
    """
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

        # Reconnect from the same host. The new connection should succeed
        # (HelloAck arrives) instead of being rejected with busy.
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            w2.write(protocol.encode(HelloMsg()))
            await w2.drain()
            msgs = await _drain_messages(r2, n=1, timeout=2.0)
            assert msgs[0]["t"] == "hello_ack", msgs

            # The old reader should see EOF promptly because the takeover
            # closed its writer. read() returns b"" on EOF.
            try:
                async with asyncio.timeout(2.0):
                    leftover = await r1.read(4096)
                assert leftover == b"", (
                    "stale writer should see EOF after takeover, got %r" % leftover
                )
            except asyncio.TimeoutError:
                pytest.fail("stale writer never saw EOF after same-host takeover")
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
async def test_second_switch_accepted_as_inactive():
    """A second Switch connection is now ACCEPTED (no busy rejection).

    The first to HELLO becomes the active Switch (gets HelloAck + the
    full post-HELLO replay). A second connection — even from a different
    peer IP — is accepted, gets its own HelloAck, and is parked with a
    `KickMsg(reason="inactive")` so the Switch UI shows an idle overlay
    until the user toggles it active via the selector popup.

    This replaces the legacy ErrMsg(busy) rejection. The takeover-on-
    same-id reconnect path (test_same_host_reconnect_takes_over_stale_writer)
    still applies for the common Switch Wi-Fi blip case.
    """
    state = BridgeState()

    async def on_check(_): ...
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    # First connection — Mario. Auto-bound as active.
    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    try:
        w1.write(protocol.encode(HelloMsg(device_id="mario")))
        await w1.drain()
        msgs1 = await _drain_messages(r1, n=3, timeout=2.0)
        assert msgs1[0]["t"] == "hello_ack"
        assert msgs1[0]["ok"] is True
        assert sw.get_active_device_id() == "mario"

        # Second connection — Luigi. Accepted as inactive.
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            w2.write(protocol.encode(HelloMsg(device_id="luigi")))
            await w2.drain()
            msgs2 = await _drain_messages(r2, n=2, timeout=2.0)
            kinds = [m["t"] for m in msgs2]
            assert kinds == ["hello_ack", "kick"], msgs2
            assert msgs2[0]["ok"] is True
            assert msgs2[1]["reason"] == "inactive"
            # Both connected; mario still active.
            assert sorted(sw.get_connected_device_ids()) == ["luigi", "mario"]
            assert sw.get_active_device_id() == "mario"
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
    # registered the connection (otherwise stop() may run before the
    # server has any connection at all).
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        for _ in range(50):
            if sw.is_connected():
                break
            await asyncio.sleep(0.01)
        assert sw.is_connected(), "server never registered the connection"

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
# M6 phase D — pay_snapshot + outstanding (derived state)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_defers_outstanding_until_pay_snapshot_lands():
    """On HELLO BEFORE any PaySnapshotMsg has arrived: compute_outstanding
    returns None (Switch on title screen / no save loaded), so the bridge
    MUST NOT push OutstandingMsg. Moons still skip in the item replay —
    OutstandingMsg comes later, after the Switch's first PaySnapshot."""
    state = BridgeState()
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="moon", kingdom="Wooded", shine_id="Power Moon"),
        sender="Mario",
    ))
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="capture", cap="Frog"),
        sender="Bob",
    ))

    async def on_check(_): return None
    async def on_goal(): ...

    from client.protocol import OutstandingEntry

    def get_outstanding():
        out = state.compute_outstanding() or {}
        return [
            OutstandingEntry(kingdom=k, count=v)
            for k, v in sorted(out.items())
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
        # Expect: hello_ack, checked_replay, item (capture only, NOT
        # the moon), ap_state. NO outstanding — no PaySnapshot has landed.
        msgs = await _drain_messages(reader, n=4, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert "outstanding" not in kinds, (
            f"OutstandingMsg must be deferred when compute_outstanding "
            f"returns None; got {kinds}"
        )
        assert kinds == ["hello_ack", "checked_replay", "item", "ap_state"]
        item = msgs[2]
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
async def test_hello_sends_outstanding_when_snapshot_already_landed():
    """If a PaySnapshotMsg landed in a prior session (state pre-seeded)
    HELLO ships an OutstandingMsg right after HelloAck and before the
    item replay, mirroring the legacy contract."""
    state = BridgeState()
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="moon", kingdom="Wooded", shine_id="Power Moon"),
        sender="Mario",
    ))
    state.add_received_item(ItemEvent(
        item=ItemRef(kind="capture", cap="Frog"),
        sender="Bob",
    ))
    # Pre-seed a PaySnapshot — outstanding becomes derivable.
    state.apply_pay_snapshot({"Wooded": 0, "Cap": 0})

    async def on_check(_): return None
    async def on_goal(): ...

    from client.protocol import OutstandingEntry

    def get_outstanding():
        out = state.compute_outstanding() or {}
        return [
            OutstandingEntry(kingdom=k, count=v)
            for k, v in sorted(out.items())
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
        # hello_ack, outstanding, checked_replay, item (capture only,
        # moon skipped), ap_state.
        msgs = await _drain_messages(reader, n=5, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "outstanding", "checked_replay",
                         "item", "ap_state"]
        outstanding = msgs[1]
        # Wooded had 1 received Power Moon, pay=0 → outstanding=1.
        wooded = next(
            e for e in outstanding["entries"] if e["kingdom"] == "Wooded"
        )
        assert wooded["count"] == 1
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
async def test_pay_snapshot_dispatch_updates_state_and_triggers_handler():
    """PaySnapshotMsg from Switch → on_pay_snapshot called with
    AP-form kingdoms + ints. Handler is responsible for state mutation +
    OutstandingMsg push."""
    state = BridgeState()
    handler_calls: list[dict[str, int]] = []

    async def on_check(_): return None
    async def on_goal(): ...

    async def on_pay_snapshot(totals: dict[str, int]) -> None:
        handler_calls.append(dict(totals))
        state.apply_pay_snapshot(totals)

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        on_pay_snapshot=on_pay_snapshot,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=2, timeout=2.0)  # hello_ack + ap_state

        # Switch ships per-kingdom totals — Switch-form names.
        writer.write(
            b'{"t":"pay_snapshot","complete":true,"entries":['
            b'{"kingdom":"Cap","pay":2},'
            b'{"kingdom":"Cascade","pay":3}'
            b']}\n'
        )
        await writer.drain()
        # Give the dispatcher a moment to land.
        await asyncio.sleep(0.05)

        assert len(handler_calls) == 1
        assert handler_calls[0] == {"Cap": 2, "Cascade": 3}
        assert state.get_pay_shine_num() == {"Cap": 2, "Cascade": 3}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_pay_snapshot_translates_bowser_to_apostrophe_form():
    """Switch sends bare 'Bowser'; bridge keys outstanding by AP form
    ("Bowser's"). _on_pay_snapshot_msg must translate before handing the
    kingdom to on_pay_snapshot, else the wrong bucket gets stored."""
    state = BridgeState()
    handler_calls: list[dict[str, int]] = []

    async def on_check(_): return None
    async def on_goal(): ...

    async def on_pay_snapshot(totals: dict[str, int]) -> None:
        handler_calls.append(dict(totals))
        state.apply_pay_snapshot(totals)

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        on_pay_snapshot=on_pay_snapshot,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=2, timeout=2.0)

        # Switch wire format: bare "Bowser".
        writer.write(
            b'{"t":"pay_snapshot","entries":[{"kingdom":"Bowser","pay":5}]}\n'
        )
        await writer.drain()
        await asyncio.sleep(0.05)

        assert handler_calls == [{"Bowser's": 5}]
        assert state.get_pay_shine_num() == {"Bowser's": 5}
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


# ---------------------------------------------------------------------------
# Capturesanity OFF — synthetic all-captures-unlocked replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_replay_synthesizes_captures_when_capturesanity_off():
    """When capturesanity is OFF the AP server never sends Capture items,
    so the Switch's captures_unlocked bitset would stay all-zero and
    CaptureStartHook would block every capture. The bridge must
    synthesize one ItemMsg per known cap during HELLO replay so the
    Switch sets every bit."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    fake_caps = [("Frog", "Frog"), ("Goomba", "Kuribo"), ("TRex", "TRex")]
    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        capturesanity_enabled=False,
        get_all_captures=lambda: fake_caps,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + 3 synthetic items + ap_state
        msgs = await _drain_messages(reader, n=6, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "checked_replay",
                         "item", "item", "item", "ap_state"]
        items = [m for m in msgs if m["t"] == "item"]
        by_cap = {m["cap"]: m for m in items}
        assert set(by_cap.keys()) == {"Frog", "Goomba", "TRex"}
        for m in items:
            assert m["kind"] == "capture"
            # Empty from_ -> Switch's Cappy filter skips the bubble. The
            # encoder strips empty/None fields, so the "from" key may be
            # absent entirely; either form must be treated as silent.
            assert m.get("from", "") == ""
        # hack_name carries through (Goomba's SMO-internal name is Kuribo).
        assert by_cap["Goomba"]["hack_name"] == "Kuribo"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_replay_does_not_synthesize_captures_when_capturesanity_on():
    """With capturesanity ON, AP delivers real Capture items via the
    normal grant path; the synthetic block must be a no-op so we don't
    pre-unlock every capture and break the lock."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    fake_caps = [("Frog", "Frog"), ("Goomba", "Kuribo")]
    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        capturesanity_enabled=True,
        get_all_captures=lambda: fake_caps,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + ap_state — no synthetic items.
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "checked_replay", "ap_state"]
        assert not any(m["t"] == "item" for m in msgs)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_set_capturesanity_enabled_flips_replay_on_next_hello():
    """If a Switch reconnects (SaveLoadHook → re-HELLO), the latest
    set_capturesanity_enabled() value must take effect — proving the
    same code path covers save-load reconciliation."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    fake_caps = [("Frog", "Frog")]
    # Start with capturesanity ON, then flip OFF before a reconnect.
    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        capturesanity_enabled=True,
        get_all_captures=lambda: fake_caps,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    # First HELLO: ON -> no synthetic items.
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        assert [m["t"] for m in msgs] == ["hello_ack", "checked_replay", "ap_state"]
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    # Flip the gate and reconnect.
    sw.set_capturesanity_enabled(False)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        # hello_ack + checked_replay + 1 synthetic item + ap_state.
        msgs = await _drain_messages(reader, n=4, timeout=2.0)
        kinds = [m["t"] for m in msgs]
        assert kinds == ["hello_ack", "checked_replay", "item", "ap_state"]
        assert msgs[2]["cap"] == "Frog"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_push_capturesanity_replay_can_run_standalone():
    """SMOContext calls push_capturesanity_replay() from its AP Connected
    handler so a Switch that already HELLO'd before slot_data arrived
    (the SNI-style two-stage gate makes this the common case) gets
    unlocked without waiting for a save-load."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    fake_caps = [("Frog", "Frog"), ("Goomba", "Kuribo")]
    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        capturesanity_enabled=True,  # initial default
        get_all_captures=lambda: fake_caps,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        # Drive the initial HELLO with the default (ON) — no synthetics.
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        # Simulate the Connected handler: flip the flag and push.
        sw.set_capturesanity_enabled(False)
        await sw.push_capturesanity_replay()
        msgs = await _drain_messages(reader, n=2, timeout=2.0)
        assert [m["t"] for m in msgs] == ["item", "item"]
        assert {m["cap"] for m in msgs} == {"Frog", "Goomba"}

        # Calling it a second time is also fine (idempotent on Switch
        # side); the bridge re-sends.
        await sw.push_capturesanity_replay()
        msgs = await _drain_messages(reader, n=2, timeout=2.0)
        assert {m["cap"] for m in msgs} == {"Frog", "Goomba"}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_capture_check_emits_cappy_msg_with_compose_label():
    """Capturesanity: a capture-check resolves to a loc_id; the bridge
    composes the bubble text (same formatter used for moon cutscene
    labels) and sends it as a CappyMsg so the Switch's speech bubble
    surfaces what the check yielded. No MoonLabelMsg for captures —
    there's no cutscene to label."""
    state = BridgeState()

    async def on_check(msg: dict) -> int | None:
        # Pretend AP resolved this capture-check to loc_id 9001.
        return 9001 if msg.get("hack_name") == "Kuribo" else None

    async def on_goal(): ...

    composed: list[int] = []

    def compose_label(loc_id: int) -> str:
        composed.append(loc_id)
        return "Got Goomba!"

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        compose_moon_label=compose_label,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        # Capture-check; no seq required — Cappy queue is FIFO, no race.
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.CAPTURE.value, hack_name="Kuribo",
        )))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        assert msgs[0] == {"t": "cappy", "text": "Got Goomba!"}
        assert composed == [9001]

        # Moon-check WITHOUT seq → no label at all (legacy path).
        composed.clear()
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.MOON.value, stage_name="X", object_id="o1",
        )))
        await writer.drain()
        # Send a ping so the reader has something to wake on if a label slipped out.
        writer.write(protocol.encode(protocol.PingMsg(ts_ms=1)))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        assert msgs[0]["t"] == "pong"
        assert composed == []
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_capture_check_suppresses_cappy_bubble_when_already_checked():
    """Captures fire many times in normal gameplay (Goomba walking around
    Cap Kingdom). After the AP credit lands once, subsequent re-captures
    must NOT pop the Cappy bubble — would otherwise spam a "Got X!" message
    every few seconds. Moons keep their re-collect label (separate path,
    moons can only re-collect across save slots — rare and useful)."""
    state = BridgeState()

    async def on_check(msg: dict) -> int | None:
        # Same loc_id for every capture-check; the bridge's locations_checked
        # set is what dedupes.
        return 9001 if msg.get("hack_name") == "Kuribo" else None

    async def on_goal(): ...

    # Bridge mirrors AP's locations_checked. The first check adds 9001;
    # subsequent calls see it already present and treat them as not-new.
    locations_checked: set[int] = set()
    original_on_check = on_check

    async def on_check_tracking(msg: dict) -> int | None:
        loc_id = await original_on_check(msg)
        if loc_id is not None:
            locations_checked.add(loc_id)
        return loc_id

    composed_calls: list[int] = []

    def compose_label(loc_id: int) -> str:
        composed_calls.append(loc_id)
        return "Got Goomba!"

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check_tracking, on_goal,
        compose_moon_label=compose_label,
        get_already_checked_loc_ids=lambda: set(locations_checked),
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        # First capture of Kuribo → fresh check → cappy bubble.
        writer.write(protocol.encode(protocol.CheckMsg(
            kind=ItemKind.CAPTURE.value, hack_name="Kuribo",
        )))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        assert msgs[0] == {"t": "cappy", "text": "Got Goomba!"}

        # Second + third capture of the SAME Kuribo → already checked →
        # no bubble. Send a ping so the reader has something to wake on
        # if a bubble accidentally slipped out.
        for _ in range(3):
            writer.write(protocol.encode(protocol.CheckMsg(
                kind=ItemKind.CAPTURE.value, hack_name="Kuribo",
            )))
        writer.write(protocol.encode(protocol.PingMsg(ts_ms=1)))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        assert msgs[0]["t"] == "pong", (
            f"expected only pong (no cappy bubble); got {msgs[0]}"
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_push_capturesanity_replay_noop_when_enabled():
    """No synthetic items emitted when capturesanity is on — even if
    push_capturesanity_replay is called directly."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        capturesanity_enabled=True,
        get_all_captures=lambda: [("Frog", "Frog")],
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg()))
        await writer.drain()
        await _drain_messages(reader, n=3, timeout=2.0)

        await sw.push_capturesanity_replay()
        # Send a ping so the reader has SOMETHING to pull; if a synthetic
        # ItemMsg snuck out it'd arrive before the pong.
        writer.write(protocol.encode(protocol.PingMsg(ts_ms=7)))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=1.0)
        assert msgs[0]["t"] == "pong"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


# ---------------------------------------------------------------------------
# Version exchange — bridge refuses a Switch built against a different
# SMOClient version. Both halves of the version pair appear in the err
# message so the user can act on it without consulting logs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_refuses_when_mod_ver_older_than_client_ver():
    """Switch shipped from an older apworld: bridge rejects with ok=false,
    err naming both versions, advises re-running /setup."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    ready_calls: list[None] = []

    async def on_switch_ready() -> None:
        ready_calls.append(None)

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        client_ver="0.2.0",
        on_switch_ready=on_switch_ready,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0")))
        await writer.drain()
        # The bridge sends ONLY a rejecting hello_ack, then closes the socket.
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        ack = msgs[0]
        assert ack["t"] == "hello_ack"
        assert ack["ok"] is False
        assert ack["client_ver"] == "0.2.0"
        # Both versions must be in err so the user can read the pair.
        assert "0.2.0" in ack["err"]
        assert "0.1.0" in ack["err"]
        # Older Switch mod → advise re-running /setup.
        assert "/setup" in ack["err"]
        # Two-stage AP gate must stay parked — never promote on a rejection.
        await asyncio.sleep(0.05)
        assert ready_calls == []
        # The bridge closes the socket; EOF arrives promptly.
        try:
            async with asyncio.timeout(2.0):
                leftover = await reader.read(4096)
            assert leftover == b""
        except asyncio.TimeoutError:
            pytest.fail("bridge never closed socket after version rejection")
        # State must reflect the rejection so the UI doesn't show ready.
        assert state.switch_conn == "disconnected"
        # Kivy UI surface: an [version mismatch] line landed in
        # BridgeState.last_messages.
        assert any(
            "version mismatch" in line.lower() for line in state.last_messages
        ), f"no version-mismatch line; got {list(state.last_messages)}"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_refuses_when_client_ver_older_advises_apworld_update():
    """Bridge older than Switch: err advises installing a newer apworld."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        client_ver="0.1.0",
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg(mod_ver="0.2.0", smo_ver="1.0.0")))
        await writer.drain()
        msgs = await _drain_messages(reader, n=1, timeout=2.0)
        ack = msgs[0]
        assert ack["ok"] is False
        assert "0.1.0" in ack["err"]
        assert "0.2.0" in ack["err"]
        assert "apworld" in ack["err"].lower()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_accepts_matching_versions_and_advertises_client_ver():
    """Matching versions → normal handshake + client_ver echoed in ack so
    the Switch mod can log both halves of the version pair."""
    state = BridgeState()
    state.slot = "Mario"
    state.seed = "TEST"

    async def on_check(_): return None
    async def on_goal(): ...

    ready_calls: list[None] = []

    async def on_switch_ready() -> None:
        ready_calls.append(None)

    sw = SwitchServer(
        "127.0.0.1", 0, state, on_check, on_goal,
        client_ver="0.1.0",
        on_switch_ready=on_switch_ready,
    )
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0")))
        await writer.drain()
        # hello_ack + checked_replay + ap_state — full normal handshake.
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        ack = msgs[0]
        assert ack["t"] == "hello_ack"
        assert ack["ok"] is True
        assert ack["client_ver"] == "0.1.0"
        assert state.switch_conn == "ready"
        await asyncio.sleep(0.05)
        assert ready_calls == [None]
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_hello_skips_version_check_when_client_ver_unset():
    """Tests that don't configure client_ver get the legacy behavior:
    no version check, no client_ver on the wire."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        # Any mod_ver should work — check is skipped.
        writer.write(protocol.encode(HelloMsg(mod_ver="9.9.9", smo_ver="1.0.0")))
        await writer.drain()
        msgs = await _drain_messages(reader, n=3, timeout=2.0)
        ack = msgs[0]
        assert ack["ok"] is True
        assert "client_ver" not in ack  # stripped when bridge has none
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_accepted_socket_has_tcp_keepalive_enabled():
    """SO_KEEPALIVE must be set on accepted Switch sockets so Windows'
    2h default keepalive doesn't strand the same-host-takeover path
    behind a half-open writer (see SMOClient_2026_05_21_09_46_11.txt).

    The multi-Switch refactor moved per-connection writer storage from
    `sw._writer` into the keyed `sw._connections` dict, populated only
    AFTER a HELLO completes. _enable_tcp_keepalive runs before that on
    the raw accepted socket, so we still send a HELLO here to make the
    accepted writer accessible through the public is_connected path —
    the keepalive itself is independent of HELLO.
    """
    import socket as _socket

    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(protocol.encode(HelloMsg(device_id="ka")))
        await writer.drain()
        for _ in range(50):
            if sw.is_connected():
                break
            await asyncio.sleep(0.01)
        assert sw.is_connected(), "server never accepted the connection"
        accepted_writer = sw._connections[sw.get_active_device_id()].writer
        accepted_sock = accepted_writer.get_extra_info("socket")
        assert accepted_sock is not None
        assert accepted_sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE) == 1
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await sw.stop()


def test_compare_versions():
    """Helper used by the mismatch advice — must rank dotted-numeric
    components numerically, not lexicographically."""
    from client.switch_server import _compare_versions
    assert _compare_versions("0.1.0", "0.1.0") == 0
    assert _compare_versions("0.1.0", "0.2.0") == -1
    assert _compare_versions("0.2.0", "0.1.0") == 1
    # Lex would mis-rank these: "0.10.0" < "0.2.0" lex, but newer numerically.
    assert _compare_versions("0.10.0", "0.2.0") == 1
    assert _compare_versions("0.2.0", "0.10.0") == -1
    # Metadata suffixes ignored.
    assert _compare_versions("0.1.0+abc", "0.1.0") == 0
    assert _compare_versions("0.1.0-dev", "0.1.0") == 0


# ---------------------------------------------------------------------------
# Multi-Switch selector — active toggle, telemetry routing, replay on rebind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_toggle_kicks_old_activates_new_and_replays():
    """When the user picks a new active Switch via the selector popup:
    1. the previously-active receives KickMsg(reason="unbound"),
    2. the newly-active receives ActivateMsg, then
    3. the newly-active sees the full post-HELLO replay (checked_replay
       + non-Moon ItemMsg backlog + ap_state — same shape as a fresh
       HELLO).
    """
    state = BridgeState()
    state.slot = "Mario"
    state.seed = "TEST"
    state.add_received_item(ItemEvent(
        item=ItemRef(kind=ItemKind.CAPTURE.value, cap="Frog"),
        sender="Bob", cappy_from="Bob",
    ))

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    try:
        w1.write(protocol.encode(HelloMsg(device_id="mario")))
        await w1.drain()
        # mario is auto-bound active: hello_ack + checked_replay + item +
        # ap_state.
        msgs1 = await _drain_messages(r1, n=4, timeout=2.0)
        assert [m["t"] for m in msgs1] == [
            "hello_ack", "checked_replay", "item", "ap_state",
        ]

        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            w2.write(protocol.encode(HelloMsg(device_id="luigi")))
            await w2.drain()
            # luigi inactive: hello_ack + kick.
            msgs2 = await _drain_messages(r2, n=2, timeout=2.0)
            assert [m["t"] for m in msgs2] == ["hello_ack", "kick"]
            assert msgs2[1]["reason"] == "inactive"

            # Toggle: luigi becomes active.
            await sw.set_active("luigi")

            # mario sees Kick(unbound).
            kick_msgs = await _drain_messages(r1, n=1, timeout=2.0)
            assert kick_msgs[0]["t"] == "kick"
            assert kick_msgs[0]["reason"] == "unbound"

            # luigi sees Activate + post-HELLO replay (checked_replay +
            # item + ap_state).
            activation = await _drain_messages(r2, n=4, timeout=2.0)
            kinds = [m["t"] for m in activation]
            assert kinds == ["activate", "checked_replay", "item", "ap_state"]
            assert activation[2]["cap"] == "Frog"

            assert sw.get_active_device_id() == "luigi"
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
async def test_inactive_switch_telemetry_dropped():
    """A check sent by an inactive Switch MUST NOT reach the AP-bound
    on_check handler. The active Switch is the sole source of truth for
    AP location forwarding while it's bound."""
    state = BridgeState()
    seen: list[dict] = []

    async def on_check(msg):
        seen.append(msg)
        return None

    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    try:
        w1.write(protocol.encode(HelloMsg(device_id="mario")))
        await w1.drain()
        await _drain_messages(r1, n=3, timeout=2.0)  # hello_ack + checked_replay + ap_state

        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            w2.write(protocol.encode(HelloMsg(device_id="luigi")))
            await w2.drain()
            await _drain_messages(r2, n=2, timeout=2.0)  # hello_ack + kick

            # Inactive luigi sends a check; on_check should NOT see it.
            w2.write(protocol.encode(protocol.CheckMsg(
                kind=ItemKind.MOON.value, kingdom="Sand", shine_id="ShouldDrop",
            )))
            await w2.drain()

            # Active mario sends one; on_check SHOULD see it.
            w1.write(protocol.encode(protocol.CheckMsg(
                kind=ItemKind.MOON.value, kingdom="Cascade", shine_id="ShouldFire",
            )))
            await w1.drain()
            await asyncio.sleep(0.1)
            assert len(seen) == 1
            assert seen[0]["kingdom"] == "Cascade"
            assert seen[0]["shine_id"] == "ShouldFire"
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
async def test_active_disconnect_auto_promotes_remaining_switch():
    """If the active Switch disconnects and another is still attached,
    the bridge auto-promotes the remaining one so the AP slot stays
    bound."""
    state = BridgeState()

    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    server = await asyncio.start_server(sw._handle_client, "127.0.0.1", 0)
    sw._server = server
    port = server.sockets[0].getsockname()[1]

    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    try:
        w1.write(protocol.encode(HelloMsg(device_id="mario")))
        await w1.drain()
        await _drain_messages(r1, n=3, timeout=2.0)
        w2.write(protocol.encode(HelloMsg(device_id="luigi")))
        await w2.drain()
        await _drain_messages(r2, n=2, timeout=2.0)

        assert sw.get_active_device_id() == "mario"

        # Drop mario.
        w1.close()
        try:
            await w1.wait_closed()
        except Exception:
            pass
        w1 = None  # so finally doesn't double-close

        # Wait briefly for the disconnect to propagate + auto-promote.
        for _ in range(50):
            if sw.get_active_device_id() == "luigi":
                break
            await asyncio.sleep(0.02)
        assert sw.get_active_device_id() == "luigi"

        # luigi receives Activate + post-HELLO replay since it was just
        # auto-promoted.
        replay = await _drain_messages(r2, n=1, timeout=2.0)
        assert replay[0]["t"] == "activate"
    finally:
        if w1 is not None:
            w1.close()
            try:
                await w1.wait_closed()
            except Exception:
                pass
        w2.close()
        try:
            await w2.wait_closed()
        except Exception:
            pass
        await sw.stop()


@pytest.mark.asyncio
async def test_set_active_unknown_id_is_noop():
    state = BridgeState()
    async def on_check(_): return None
    async def on_goal(): ...

    sw = SwitchServer("127.0.0.1", 0, state, on_check, on_goal)
    ok = await sw.set_active("never-connected")
    assert ok is False
    assert sw.get_active_device_id() is None


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
