"""Asyncio TCP server for the Switch.

One Switch connection at a time (extras are rejected). On HELLO we replay the
full received-items history and the set of locations already checked, so the
Switch module always re-applies state idempotently after a reboot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from . import protocol
from .protocol import (
    ApStateMsg,
    CheckedReplayMsg,
    ErrMsg,
    HelloAckMsg,
    ItemMsg,
    PongMsg,
)
from .state import BridgeState, CheckEvent, ItemEvent

log = logging.getLogger(__name__)


CheckHandler = Callable[[dict], Awaitable[None]]
GoalHandler = Callable[[], Awaitable[None]]


class SwitchServer:
    def __init__(
        self,
        host: str,
        port: int,
        state: BridgeState,
        on_check: CheckHandler,
        on_goal: GoalHandler,
    ):
        self._host = host
        self._port = port
        self._state = state
        self._on_check = on_check
        self._on_goal = on_goal
        self._writer: asyncio.StreamWriter | None = None
        self._writer_lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        log.info("switch server listening on %s", addrs)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # ---- broadcast: bridge -> switch ----

    async def send_item(self, item: ItemMsg) -> None:
        await self._send(item)

    async def send_print(self, text: str) -> None:
        await self._send(protocol.PrintMsg(text=text))

    async def send_ap_state(self, conn: str) -> None:
        await self._send(ApStateMsg(conn=conn))

    async def _send(self, msg: Any) -> None:
        async with self._writer_lock:
            w = self._writer
            if w is None or w.is_closing():
                return
            try:
                w.write(protocol.encode(msg))
                await w.drain()
            except (ConnectionResetError, BrokenPipeError):
                log.warning("switch write failed; closing")
                try:
                    w.close()
                except Exception:
                    pass
                self._writer = None

    # ---- per-connection handler ----

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if self._writer is not None and not self._writer.is_closing():
            log.warning("rejecting extra Switch connection from %s (one already active)", peer)
            try:
                writer.write(protocol.encode(ErrMsg(code="busy", ctx="connect")))
                await writer.drain()
            finally:
                writer.close()
            return

        log.info("switch connected from %s", peer)
        self._writer = writer
        self._state.set_switch_conn("connecting")

        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    log.info("switch disconnected (EOF)")
                    break
                buffer.extend(chunk)
                for line in protocol.iter_lines(buffer):
                    try:
                        msg = protocol.decode(line)
                        await self._dispatch(msg)
                    except Exception:
                        log.exception("error handling message: %r", line[:200])
                        await self._send(ErrMsg(code="bad_message", ctx="rx"))
        except (ConnectionResetError, BrokenPipeError):
            log.info("switch connection reset")
        finally:
            self._state.set_switch_conn("disconnected")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if self._writer is writer:
                self._writer = None

    async def _dispatch(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "hello":
            await self._on_hello(msg)
        elif t == "check":
            await self._on_check(msg)
            self._state.add_checked_location(
                CheckEvent(item=protocol.ItemRef(
                    kind=msg.get("kind", "moon"),
                    kingdom=msg.get("kingdom"),
                    shine_id=msg.get("shine_id"),
                    cap=msg.get("cap"),
                    slot=msg.get("slot"),
                ))
            )
        elif t == "goal":
            log.info("switch reported goal completion")
            await self._on_goal()
        elif t == "status":
            log.debug("switch status: %s", msg)
        elif t == "ping":
            await self._send(PongMsg(ts_ms=msg.get("ts_ms", int(time.time() * 1000))))
        elif t == "log":
            self._state.add_log(f"[switch:{msg.get('level', 'info')}] {msg.get('msg', '')}")
        else:
            log.warning("unknown message type from Switch: %s", t)
            await self._send(ErrMsg(code="unknown_kind", ctx=str(t)))

    async def _on_hello(self, msg: dict) -> None:
        log.info("switch HELLO: mod=%s smo=%s", msg.get("mod_ver"), msg.get("smo_ver"))
        await self._send(HelloAckMsg(
            ok=True,
            seed=self._state.seed,
            slot=self._state.slot,
            cap_table_hash=msg.get("cap_table_hash", ""),
        ))
        self._state.set_switch_conn("ready")

        # Replay snapshots so the Switch can re-apply state idempotently.
        replay_ids = [evt.item for evt in self._state.all_checked_locations()]
        await self._send(CheckedReplayMsg(ids=replay_ids))
        for evt in self._state.all_received_items():
            await self._send(ItemMsg(
                kind=evt.item.kind,
                kingdom=evt.item.kingdom,
                shine_id=evt.item.shine_id,
                cap=evt.item.cap,
                slot=evt.item.slot,
                name=evt.item.name,
                from_=evt.sender,
            ))

        await self._send(ApStateMsg(conn=self._state.ap_conn))
