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
    CappyMsg,
    CheckedReplayMsg,
    ErrMsg,
    HelloAckMsg,
    ItemMsg,
    KillMsg,
    MoonLabelMsg,
    OutstandingMsg,
    PongMsg,
    ShineScoutsMsg,
)
from .state import BridgeState, CheckEvent, ItemEvent

# Max scout entries per ShineScoutsMsg. Each entry is ~25 bytes wire; 200
# stays well under MAX_LINE_BYTES (8 KiB) even with TOML-driven larger
# palette ints. Switch merges chunks by shine_uid overwrite, so order and
# count are immaterial.
_SCOUT_CHUNK_SIZE = 200

log = logging.getLogger(__name__)

# Dedicated logger for forwarded Switch-side log lines. Surfaces in the
# Kivy "Switch" tab (gui.py logging_pairs already routes the "SMO" name
# there). Kept distinct from `log` above so the SMOClient's PC-side
# diagnostics (logger "client.switch_server") stay scoped to the
# "Archipelago" tab while Switch-forwarded noise lands where the user
# expects it.
_switch_log = logging.getLogger("SMO")

_SWITCH_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info":  logging.INFO,
    "warn":  logging.WARNING,
    "error": logging.ERROR,
}


# M6 phase C reconcile — when a single drain produces more freshly-checked
# loc_ids than this, suppress all Cappy bubbles for that drain. Protects
# against the first-connect / "fresh AP slot" case where the snapshot enumerates
# every owned moon + capture as new — a 41-bubble flood is worse than silence.
# Real "I missed a few while offline" sessions sit comfortably under this cap.
# CappyMessenger queues at most 8 anyway; this threshold trips first.
RECONCILE_CAPPY_BURST_THRESHOLD = 5

CheckHandler = Callable[[dict], Awaitable["int | None"]]  # returns AP loc_id or None
GoalHandler = Callable[[], Awaitable[None]]
DeathHandler = Callable[[int], Awaitable[None]]
LabelComposer = Callable[[int], "str | None"]              # loc_id -> label text
SwitchReadyHandler = Callable[[], Awaitable[None]]         # fired post-HELLO
# M6 phase D — PaySnapshotHandler(totals=dict[str, int]) -> None.
# `totals` is keyed by AP-form kingdom name (dispatcher does the
# Switch→AP translation). Handler folds into BridgeState and re-derives
# outstanding, then pushes OutstandingMsg to the Switch.
PaySnapshotHandler = Callable[..., Awaitable[None]]
# M6 phase D — OutstandingProvider() -> list[OutstandingEntry]. Used at HELLO
# time to snapshot the current per-kingdom balance for the Switch.
OutstandingProvider = Callable[[], "list"]
# Capturesanity-OFF replay — returns every known (cap_name, hack_name) pair so
# the bridge can synthesize per-capture ItemMsgs that set every bit of the
# Switch's captures_unlocked bitset. Without this, CaptureStartHook would
# block every capture for the entire seed (no AP Capture items will arrive).
AllCapturesProvider = Callable[[], list]
# M6 phase C reconcile — `() -> bool` predicate: is AP fully ready (datapackage
# loaded so report_check can resolve loc_ids)? Used to gate the snapshot drain
# when the Switch HELLO arrives before the AP dial has handshaked.
ApReadyProbe = Callable[[], bool]
# M6 phase C reconcile — `(loc_id) -> ItemMsg | None`: builds a Cappy-bubble
# ItemMsg for a moon reconciled from a snapshot. Returns None when scouts
# aren't loaded yet, when the item isn't for our slot, or when the loc isn't
# a moon. SwitchServer keeps a pending-set of loc_ids and re-tries on every
# LocationInfo absorption until the cache catches up.
ReconcileItemBuilder = Callable[[int], "ItemMsg | None"]
# M6 phase C reconcile — `() -> set[int]`: snapshot of currently-checked AP
# location ids. Captured once before drain so we can distinguish "newly
# checked from reconcile" (fire Cappy) vs "already known" (skip Cappy — the
# player either checked it live or saw it announced on a previous reconnect).
# Implemented in SMOContext as `lambda: set(self.locations_checked)`.
AlreadyCheckedProvider = Callable[[], "set[int]"]


def _compare_versions(a: str, b: str) -> int:
    """Compare two dotted-numeric version strings (e.g. "0.10.0" vs "0.2.0").

    Returns -1 if a < b, +1 if a > b, 0 if equal. Non-numeric trailing
    segments (e.g. "0.1.0+abc") are stripped before parsing — only the
    leading dotted-numeric prefix matters. Non-numeric inputs (or pure-
    metadata diffs) compare as 0; callers fall back to a neutral message.
    """
    def parse(v: str) -> tuple[int, ...]:
        head = v.split("+", 1)[0].split("-", 1)[0]
        parts: list[int] = []
        for token in head.split("."):
            try:
                parts.append(int(token))
            except ValueError:
                break
        return tuple(parts)

    pa, pb = parse(a), parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


class SwitchServer:
    def __init__(
        self,
        host: str,
        port: int,
        state: BridgeState,
        on_check: CheckHandler,
        on_goal: GoalHandler,
        on_death: DeathHandler | None = None,
        deathlink_enabled: bool = False,
        compose_moon_label: LabelComposer | None = None,
        on_switch_ready: SwitchReadyHandler | None = None,
        on_pay_snapshot: PaySnapshotHandler | None = None,
        get_outstanding_entries: OutstandingProvider | None = None,
        capturesanity_enabled: bool = True,
        get_all_captures: AllCapturesProvider | None = None,
        is_ap_ready: ApReadyProbe | None = None,
        build_reconcile_cappy_item: ReconcileItemBuilder | None = None,
        get_already_checked_loc_ids: AlreadyCheckedProvider | None = None,
        client_ver: str = "",
    ):
        self._host = host
        self._port = port
        self._state = state
        self._on_check = on_check
        self._on_goal = on_goal
        self._on_death = on_death
        self._deathlink_enabled = deathlink_enabled
        self._compose_label = compose_moon_label
        self._on_switch_ready = on_switch_ready
        self._on_pay_snapshot = on_pay_snapshot
        self._get_outstanding = get_outstanding_entries
        # SMOClient version baked at apworld build time. Compared at HELLO
        # against the Switch mod's `mod_ver` field; mismatch -> refuse the
        # connection with a hello_ack carrying ok=false + err describing
        # both halves of the version pair. Empty string disables the check
        # (used by tests that don't care about version policing).
        self._client_ver = client_ver
        # Default True (fail-safe = current behavior, AP-granted captures
        # only). SMOContext flips this from slot_data on AP Connected.
        self._capturesanity_enabled = capturesanity_enabled
        self._get_all_captures = get_all_captures
        self._is_ap_ready = is_ap_ready
        self._build_reconcile_cappy_item = build_reconcile_cappy_item
        self._get_already_checked = get_already_checked_loc_ids
        self._writer: asyncio.StreamWriter | None = None
        self._writer_lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None
        # M6 phase C reconcile — entries buffered when state_end arrived but
        # AP wasn't ready yet (datapackage not loaded → report_check can't
        # resolve loc_ids). Drained by drain_pending_snapshot() once AP is
        # ready. None means "no buffered snapshot" (distinct from [] which
        # would be a valid empty snapshot we already drained).
        self._pending_snapshot_entries: list[dict] | None = None
        self._pending_snapshot_goal: bool = False
        # M6 phase C reconcile — live `check` messages buffered when they
        # arrived during the AP-handshake window. The Switch's outbound
        # check ring drains queued offline collects on reconnect; without
        # buffering they hit the same "no AP id" race as the snapshot.
        # Drained by drain_pending_snapshot() alongside the snapshot.
        self._pending_live_checks: list[dict] = []
        # M6 phase C reconcile — loc_ids freshly dispatched from a snapshot
        # drain (i.e. NOT already in locations_checked when drain began).
        # Each waits for its scout to land via LocationInfo absorption; when
        # the scout shows the item is for our slot, we synthesize a Cappy
        # ItemMsg so the player learns what they got offline.
        self._reconcile_cappy_pending: set[int] = set()

    def set_deathlink_enabled(self, enabled: bool) -> None:
        """Update the bridge-side DeathLink gate. Called by SMOContext after
        AP Connected delivers slot_data so the YAML's `death_link` setting
        wins over launch-time config (host.yaml `deathlink_default`, TOML,
        `--deathlink`). Takes effect on the NEXT HELLO replay; the caller
        should also `await push_deathlink_helloack()` so a Switch that has
        already HELLO'd reacts without waiting for a save-load."""
        self._deathlink_enabled = bool(enabled)

    async def push_deathlink_helloack(self) -> None:
        """Re-send HelloAckMsg with the current `_deathlink_enabled`.

        The Switch's `hello_ack` handler is idempotent for the other fields
        (local_slot, conn=Ready, bridge_connected=true), so a second ack
        mid-session just updates `ApState::deathlink_enabled`. Without this,
        a YAML toggle wouldn't reach the Switch until the next save-load
        forces a fresh HELLO — and inbound kills would keep being dropped
        by ApState::maybeApplyInboundKill (`if (!deathlink_enabled)` gate).

        No-op when no Switch is attached.
        """
        if self._writer is None or self._writer.is_closing():
            return
        await self._send(HelloAckMsg(
            ok=True,
            seed=self._state.seed,
            slot=self._state.slot,
            # cap_table_hash is only used by the Switch's HELLO log line;
            # the handler doesn't act on it. Empty is fine for the re-push.
            cap_table_hash="",
            deathlink_enabled=self._deathlink_enabled,
        ))

    def set_capturesanity_enabled(self, enabled: bool) -> None:
        """Update the capturesanity gate. Called by SMOContext after AP
        Connected delivers slot_data. Takes effect on the NEXT HELLO
        replay (SaveLoadHook forces a re-HELLO, so save-loads pick up
        the latest value automatically). For an already-running Switch,
        the caller should also call push_capturesanity_replay() to
        flush the unlocks without waiting for a save-load."""
        self._capturesanity_enabled = bool(enabled)

    async def push_capturesanity_replay(self) -> None:
        """Synthesize all-captures-unlocked ItemMsgs for the connected
        Switch. No-op if capturesanity is enabled (AP-granted captures
        only) or if no captures provider is wired. Idempotent — the
        Switch's bit.set() is a no-op on already-set bits, so re-runs
        across reconnects don't break anything.

        Called from two paths: (1) the tail of HELLO replay, so a fresh
        Switch session lands with bits set; (2) SMOContext on AP
        Connected, so a Switch that already HELLO'd before slot_data
        arrived (the SNI-style two-stage gate makes this the common
        case for the first connect) gets unlocked without waiting for
        the next save-load to force a re-HELLO."""
        if self._capturesanity_enabled or self._get_all_captures is None:
            return
        for cap_name, hack_name in self._get_all_captures():
            await self._send(ItemMsg(
                kind="capture",
                cap=cap_name,
                hack_name=hack_name,
                # Empty from_ suppresses the Cappy bubble — these are
                # synthetic unlocks, not real AP grants from another
                # player.
                from_="",
            ))

    def is_connected(self) -> bool:
        """True iff a Switch is currently attached and the socket is open.
        Used by SMOContext to gate the AP dial on Switch presence."""
        w = self._writer
        return w is not None and not w.is_closing()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        log.info("switch server listening on %s", addrs)

    async def stop(self) -> None:
        # Close the active Switch connection FIRST. Python 3.12+'s
        # Server.wait_closed() waits for both the listener and every active
        # client task to finish; _handle_client is parked in reader.read()
        # so the connection task never returns on its own — the listener
        # closing doesn't kick connected clients. Without this teardown,
        # a clean window-close hangs forever whenever the Switch (or
        # Ryujinx) is still connected.
        async with self._writer_lock:
            w = self._writer
            self._writer = None
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    # ---- broadcast: bridge -> switch ----

    async def send_item(self, item: ItemMsg) -> None:
        await self._send(item)

    async def send_print(self, text: str) -> None:
        await self._send(protocol.PrintMsg(text=text))

    async def send_ap_state(self, conn: str) -> None:
        await self._send(ApStateMsg(conn=conn))

    async def send_kill(self, kill: KillMsg) -> None:
        await self._send(kill)

    async def send_moon_label(self, label: MoonLabelMsg) -> None:
        await self._send(label)

    async def send_cappy(self, msg: CappyMsg) -> None:
        await self._send(msg)

    async def send_outstanding(self, msg: OutstandingMsg) -> None:
        """Push the derived per-kingdom balance to the Switch.

        Called from context.py whenever the inputs to compute_outstanding
        change (Moon item arrival from AP, or PaySnapshotMsg from Switch).
        The Switch overwrites `ap_moons_kingdom[bit]` for each entry.
        """
        await self._send(msg)

    async def send_shine_scouts(self, palette: dict[int, int]) -> None:
        """Push (shine_uid -> palette) to the Switch, chunked.

        Caller is responsible for filtering zero-palette entries if it
        considers them noise; we send everything so the Switch can
        explicitly clear a previously-set uid by writing 0.
        """
        if not palette:
            return
        items = list(palette.items())
        for i in range(0, len(items), _SCOUT_CHUNK_SIZE):
            chunk = items[i : i + _SCOUT_CHUNK_SIZE]
            await self._send(ShineScoutsMsg(entries=[
                {"shine_uid": uid, "palette": p} for uid, p in chunk
            ]))

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
            # A Switch Wi-Fi blip can leave us with a half-open writer: the
            # Switch's TCP layer closed its side, but the FIN never reached
            # us (link was down at the time), so reader.read() in the old
            # handler stays parked indefinitely. Without intervention, the
            # Switch's reconnect attempts would be rejected with ErrMsg(busy)
            # until our writer eventually surfaces EPIPE on a write attempt.
            # If the new connection's peer IP matches the existing writer's,
            # assume the old socket is dead and take over — two distinct
            # Switches racing for the same SMOClient is so rare we treat
            # peer-IP match as conclusive evidence of a reconnect.
            existing_peer = self._writer.get_extra_info("peername")
            same_host = (
                peer is not None and existing_peer is not None
                and peer[0] == existing_peer[0]
            )
            if same_host:
                log.info(
                    "Switch reconnecting from %s; replacing stale writer (was %s)",
                    peer, existing_peer,
                )
                try:
                    self._writer.close()
                except Exception:
                    pass
                # Don't await wait_closed — the old handler's reader.read()
                # will surface EOF and its finally block will tear down. We
                # null self._writer here so the old finally's
                # `self._writer is writer` check skips state mutation; the
                # new handler below claims ownership immediately.
                self._writer = None
            else:
                log.warning(
                    "rejecting extra Switch connection from %s (one already "
                    "active from %s)", peer, existing_peer,
                )
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
            # If we've been replaced (same-host takeover above), skip the
            # state mutation — the new handler has already updated
            # set_switch_conn to "connecting"/"ready" and a stray
            # "disconnected" here would flicker the bridge UI.
            is_active = self._writer is writer
            if is_active:
                self._state.set_switch_conn("disconnected")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if is_active:
                self._writer = None

    async def _dispatch(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "hello":
            await self._on_hello(msg)
        elif t == "check":
            # M6 phase C reconcile — live `check` messages also race AP
            # connect: the Switch's outbound-check ring drains queued offline
            # collects the moment the bridge accepts the TCP socket, which
            # is ~1.4s before SMOContext finishes the AP handshake. Without
            # buffering, report_check sees an empty dp.location_name_to_id
            # and drops the entry with "no AP id for location ...". Same
            # gate as the snapshot path: defer when AP isn't ready, then
            # drain on Connected.
            if self._is_ap_ready is not None and not self._is_ap_ready():
                self._pending_live_checks.append(msg)
                log.info(
                    "live check buffered (kind=%s stage=%s obj=%s) — AP not ready",
                    msg.get("kind"), msg.get("stage_name"), msg.get("object_id"),
                )
                return
            await self._dispatch_check(msg)
        elif t == "goal":
            log.info("switch reported goal completion")
            await self._on_goal()
        elif t == "death":
            ts_ms = int(msg.get("ts_ms") or 0)
            log.info("switch reported death ts_ms=%d", ts_ms)
            if self._on_death is not None:
                await self._on_death(ts_ms)
        elif t == "status":
            log.debug("switch status: %s", msg)
        elif t == "ping":
            await self._send(PongMsg(ts_ms=msg.get("ts_ms", int(time.time() * 1000))))
        elif t == "log":
            level = str(msg.get("level", "info"))
            text = str(msg.get("msg", ""))
            # Snapshot feed (web-tracker `recent_messages`) — pre-existing.
            self._state.add_log(f"[switch:{level}] {text}")
            # Surface in the Kivy "Switch" tab via Archipelago's LogtoUI
            # handler attached to logger "SMO". Prefix the message so a
            # reader can tell forwarded Switch lines apart from PC-side
            # SMO diagnostics in the same tab.
            _switch_log.log(
                _SWITCH_LEVEL_MAP.get(level, logging.INFO),
                "[switch:%s] %s", level, text,
            )
        elif t == "state_begin":
            self._state.begin_snapshot(save_slot=msg.get("save_slot"))
            log.info("snapshot begin: mod_ver=%s save_slot=%s",
                     msg.get("mod_ver"), msg.get("save_slot"))
        elif t == "state_chunk":
            stage = msg.get("stage_name", "")
            if stage == "_meta":
                self._state.add_snapshot_chunk_meta(
                    captures=msg.get("captures"),
                    goal_reached=msg.get("goal_reached"),
                )
            else:
                self._state.add_snapshot_chunk_shines(stage, msg.get("shines") or [])
        elif t == "state_end":
            await self._on_state_end()
        elif t == "pay_snapshot":
            await self._on_pay_snapshot_msg(msg)
        else:
            log.warning("unknown message type from Switch: %s", t)
            await self._send(ErrMsg(code="unknown_kind", ctx=str(t)))

    async def _on_pay_snapshot_msg(self, msg: dict) -> None:
        """M6 phase D — Switch reported authoritative per-kingdom PayShineNum.

        Parses the entries list (Switch-form kingdoms with `pay` int
        readings), translates each kingdom to AP form, and hands off to
        the configured handler. The handler folds the totals into
        BridgeState and re-derives outstanding. No ack: the snapshot is
        idempotent and the next snapshot supersedes whatever the bridge
        currently believes.
        """
        if self._on_pay_snapshot is None:
            log.debug("pay_snapshot dropped: no handler wired")
            return

        entries = msg.get("entries")
        if not isinstance(entries, list):
            log.warning("malformed PaySnapshotMsg (entries not list): %r", msg)
            await self._send(ErrMsg(code="bad_pay_snapshot", ctx="entries"))
            return

        totals: dict[str, int] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kingdom_switch = entry.get("kingdom")
            pay = entry.get("pay")
            if not isinstance(kingdom_switch, str) or not kingdom_switch:
                continue
            try:
                pay_int = int(pay)
            except (TypeError, ValueError):
                log.warning("PaySnapshot entry has non-int pay: %r", entry)
                continue
            kingdom_ap = protocol.kingdom_switch_to_ap(kingdom_switch)
            if not kingdom_ap:
                log.warning("PaySnapshot entry has unknown kingdom: %r", kingdom_switch)
                continue
            totals[kingdom_ap] = max(0, pay_int)

        try:
            await self._on_pay_snapshot(totals)
        except Exception:
            log.exception("on_pay_snapshot handler raised")

    async def _dispatch_check(self, msg: dict, *, from_snapshot: bool = False) -> "int | None":
        """Forward a check (live or snapshot-derived) to AP and record locally.

        BridgeState.add_checked_location dedupes via the full ItemRef identity,
        so snapshot replays don't grow the list (or trigger spurious tracker
        increments) on every reconnect.

        M6 phase A.5: if the Switch sent a non-zero `seq` and AP returned a
        resolved location_id and Channel A is wired, synthesize a
        MoonLabelMsg in the same TCP push (Nagle-batched) so it arrives
        before the cutscene fires.

        Capturesanity capture-checks have no cutscene to label; instead we
        route the composed text into a CappyMsg so the speech bubble
        surfaces what the check yielded ("Got Goomba!" / "Sent Frog ->
        Player2"). No seq for the Cappy path — the bubble queue is FIFO
        and there's no cutscene-timing race to dedupe against.

        Capture-bubble suppression for already-known checks: captures fire
        many times in normal gameplay (a Goomba in Cap Kingdom is captured
        every time the player walks back through the area), so a bubble on
        every re-capture would be noise. Moons keep firing the label on
        re-collect because (1) SMO itself prevents re-collection within a
        save file, so this only ever fires when loading a different save
        slot where the moon hasn't been collected yet, and (2) the cutscene
        is already playing — overwriting "Power Moon" with the real item
        name is useful.

        Snapshot-derived captures (`from_snapshot=True`) also skip the
        Cappy path here. The Switch will receive an inbound ItemMsg for
        whatever the location yielded — either right now via the
        M6-phase-C reconcile-Cappy machinery (deferred drains) or
        moments later when AP echoes ReceivedItems back from the
        LocationChecks we just sent (immediate drains). Either way the
        Switch's enqueue() path formats and shows the bubble. Firing
        CappyMsg from here too would pop the same "Got Cascade Power
        Moon!" twice (once via this path, once via the inbound ItemMsg).

        Returns the resolved AP `loc_id` (or None when unresolvable) so the
        snapshot drain can distinguish fresh vs already-known checks for the
        reconcile-Cappy path.
        """
        # Snapshot pre-call so we can tell "newly-checked this dispatch"
        # apart from "already-known" — gates the capture-Cappy suppression
        # below. report_check still returns loc_id for already-known checks
        # (so the moon-label path can re-label re-collected moons), so
        # post-call membership doesn't help. Falls back to empty (treats
        # every check as fresh) when the wiring isn't present — matches
        # legacy behavior for callers that don't care.
        already_checked = (
            set(self._get_already_checked()) if self._get_already_checked is not None
            else set()
        )
        loc_id = await self._on_check(msg)
        seq = msg.get("seq") or 0
        kind = msg.get("kind", "moon")
        was_new = loc_id is not None and loc_id not in already_checked
        if loc_id is not None and self._compose_label is not None:
            text: str | None = None
            if (kind == "moon" and seq) or kind == "capture":
                try:
                    text = self._compose_label(loc_id)
                except Exception:
                    log.exception(
                        "compose_label failed for loc_id=%s kind=%s seq=%s",
                        loc_id, kind, seq,
                    )
                    text = None
            if text and kind == "moon":
                await self.send_moon_label(MoonLabelMsg(text=text, seq=seq))
            elif text and kind == "capture" and was_new and not from_snapshot:
                await self.send_cappy(CappyMsg(text=text))
        self._state.add_checked_location(
            CheckEvent(item=protocol.ItemRef(
                kind=msg.get("kind", "moon"),
                kingdom=msg.get("kingdom"),
                shine_id=msg.get("shine_id"),
                cap=msg.get("cap"),
                stage_name=msg.get("stage_name"),
                object_id=msg.get("object_id"),
                shine_uid=msg.get("shine_uid"),
                hack_name=msg.get("hack_name"),
            ))
        )
        return loc_id

    async def _on_state_end(self) -> None:
        entries, goal_reached = self._state.end_snapshot()
        log.info("snapshot end: %d entries goal=%s", len(entries), goal_reached)
        # M6 phase C — dispatch races AP connect. The Switch HELLO can arrive
        # before SMOContext.connect() has finished the AP handshake (≈ 1.4s
        # gap observed in real-world tests). If we dispatched now, report_check
        # would see an empty `dp.location_name_to_id` and log every entry as
        # "no AP id for location ..." — the snapshot would be silently lost.
        # Buffer instead; SMOContext.drain_pending_snapshot() runs the loop
        # below from the Connected handler once dp is loaded.
        if self._is_ap_ready is not None and not self._is_ap_ready():
            self._pending_snapshot_entries = list(entries)
            self._pending_snapshot_goal = bool(goal_reached)
            log.info(
                "snapshot buffered (%d entries) — AP not ready; will drain on Connected",
                len(entries),
            )
            return
        await self._dispatch_snapshot_entries(entries, goal_reached, from_reconcile=False)

    async def _dispatch_snapshot_entries(
        self,
        entries: list[dict],
        goal_reached: bool,
        from_reconcile: bool,
    ) -> None:
        """Common dispatch path for snapshot entries (immediate or deferred).

        When `from_reconcile=True`, freshly-checked loc_ids are queued on
        `_reconcile_cappy_pending` so try_fire_reconcile_cappy() can pop a
        Cappy speech bubble for each one whose scout has landed. We compute
        "freshly checked" against the set captured BEFORE the dispatch loop
        starts — using `self._on_check`'s post-call state would race against
        the dispatch itself when AP echoes ReceivedItems back in the same
        burst.
        """
        # Capture the pre-drain "already-checked" set. report_check adds to
        # ctx.locations_checked when it successfully sends a LocationCheck.
        # We snapshot the set before drain so post-drain
        # `loc_id NOT in pre_set` is the right freshness check.
        # Without the callback, every dispatched entry counts as "fresh" —
        # matches the legacy behavior for callers that don't care about Cappy.
        pre_checked: set[int] = set()
        if from_reconcile and self._get_already_checked is not None:
            try:
                pre_checked = set(self._get_already_checked())
            except Exception:
                log.exception("get_already_checked_loc_ids failed; assuming empty")
        # Per-drain queue of fresh loc_ids destined for Cappy. Held separately
        # from `_reconcile_cappy_pending` so we can apply the burst threshold
        # below without affecting any entries already pending from a previous
        # drain.
        fresh_this_drain: set[int] = set()
        for entry in entries:
            # Drop ALL snapshot capture entries. The Switch builds
            # _meta.captures by walking SMO's hack dictionary, which is
            # populated by being ALLOWED to use a capture — every
            # addHackDictionary call (AP grant, baseline pre-populate,
            # prior-session /grant REPL, save-file edits, manual capture)
            # lands an entry. So a dict-derived snapshot would ALWAYS
            # credit every AP-granted capture as a manual check on the
            # next reload, even when the player never actually captured
            # that enemy in-game. Confirmed live 2026-05-18: granting
            # T-Rex on a save with 6 leftover dict entries from prior
            # testing fired 6 phantom AP check-credits the moment the
            # bridge reconnected.
            #
            # Live CaptureStartHook is the only authoritative signal —
            # it fires reportCaptureChecked directly (not via snapshot),
            # so manual captures still credit. The Switch's outbound
            # check ring drains queued live captures on reconnect, so
            # brief disconnects are covered. The only window this drops
            # is a manual capture done during a disconnect followed by
            # a Switch reboot before the ring drains — rare, and the
            # player can re-capture in seconds vs. an AP server credited
            # with locations they never earned.
            if entry.get("kind") == "capture":
                log.info(
                    "snapshot: dropping capture-check for hack=%r "
                    "(dict-derived snapshot is not a manual-capture signal)",
                    entry.get("hack_name") or "",
                )
                continue
            synthetic = {"t": "check", **entry}
            # Always mark snapshot-derived dispatches so _dispatch_check
            # suppresses its CappyMsg path. Whether this drain is
            # immediate (from_reconcile=False) or deferred
            # (from_reconcile=True), the Switch's inbound-ItemMsg path
            # will fire the bubble for whatever the location yielded;
            # firing here too would double it up.
            loc_id = await self._dispatch_check(synthetic, from_snapshot=True)
            if (
                from_reconcile
                and loc_id is not None
                and entry.get("kind") in ("moon", "capture")
                and loc_id not in pre_checked
            ):
                fresh_this_drain.add(loc_id)
        if goal_reached:
            log.info("snapshot reports goal already reached; forwarding")
            await self._on_goal()
        if from_reconcile and fresh_this_drain:
            if len(fresh_this_drain) > RECONCILE_CAPPY_BURST_THRESHOLD:
                # Bulk reconcile (first connect / fresh AP slot / long offline
                # binge): suppress Cappy for the whole drain rather than queue
                # a flood the CappyMessenger ring can only partially deliver.
                # Counts still went through dispatch — AP knows about every
                # check; the player just doesn't get a per-item bubble.
                log.info(
                    "[reconcile] %d fresh entries exceeds Cappy burst threshold (%d) — "
                    "suppressing bubbles for this drain",
                    len(fresh_this_drain), RECONCILE_CAPPY_BURST_THRESHOLD,
                )
            else:
                self._reconcile_cappy_pending |= fresh_this_drain
                # Best-effort first pass: scouts may already be loaded for some
                # entries (warmup scout is racing the drain itself); fire what
                # we can immediately, leave the rest for LocationInfo
                # absorption.
                await self.try_fire_reconcile_cappy()

    async def drain_pending_snapshot(self) -> None:
        """Drain anything buffered by `_dispatch` / `_on_state_end` because
        AP wasn't ready: the state snapshot AND any live `check` messages
        that arrived during the same handshake window.

        Called by SMOContext from the `Connected` handler once the datapackage
        is loaded. Both buffers route through the reconcile-Cappy queue so
        an offline-collected moon shows its bubble whether it arrived via
        the snapshot enumerate path or the Switch's outbound check ring
        drain. Idempotent — no-op when both buffers are empty.
        """
        # Drain live checks first so they get the same "from_reconcile"
        # treatment as snapshot entries. Live checks may also include moons
        # the player just collected mid-handshake; treating them identically
        # is what makes the Cappy bubble fire regardless of which channel
        # carried the report.
        live_checks = self._pending_live_checks
        self._pending_live_checks = []
        if live_checks:
            log.info("draining %d buffered live checks", len(live_checks))
            await self._dispatch_snapshot_entries(
                live_checks, goal_reached=False, from_reconcile=True,
            )
        if self._pending_snapshot_entries is None:
            return
        entries = self._pending_snapshot_entries
        goal_reached = self._pending_snapshot_goal
        self._pending_snapshot_entries = None
        self._pending_snapshot_goal = False
        log.info("draining %d buffered snapshot entries", len(entries))
        await self._dispatch_snapshot_entries(entries, goal_reached, from_reconcile=True)

    async def try_fire_reconcile_cappy(self) -> None:
        """Pump pending reconciled loc_ids through the Cappy speech bubble.

        For each loc_id where the scout cache now resolves to an item routed
        to our slot, synthesize an ItemMsg with `from_="(offline)"` so the
        Switch's `shouldShowCappyMsg` filter passes (it suppresses on
        `from == local_slot`; "(offline)" is a sentinel that never matches a
        real player name) and the speech bubble fires.

        Called from SMOContext on every LocationInfo absorption so late-
        arriving scouts get retried until the cache catches up.
        """
        if not self._reconcile_cappy_pending or self._build_reconcile_cappy_item is None:
            return
        fired: list[int] = []
        for loc_id in list(self._reconcile_cappy_pending):
            try:
                item = self._build_reconcile_cappy_item(loc_id)
            except Exception:
                log.exception("build_reconcile_cappy_item failed for loc_id=%s", loc_id)
                # Drop on hard error — don't retry-loop forever on a bad item.
                fired.append(loc_id)
                continue
            if item is None:
                # Scout not loaded yet, or item isn't a moon, or not for self.
                # Leave pending; the next LocationInfo absorption will retry.
                continue
            log.info(
                "[reconcile] firing Cappy for loc_id=%d name=%r kingdom=%r",
                loc_id, item.name, item.kingdom,
            )
            try:
                await self.send_item(item)
            except Exception:
                log.exception("send_item failed for reconcile loc_id=%s", loc_id)
            fired.append(loc_id)
        for loc_id in fired:
            self._reconcile_cappy_pending.discard(loc_id)

    async def _on_hello(self, msg: dict) -> None:
        mod_ver = str(msg.get("mod_ver") or "")
        smo_ver = str(msg.get("smo_ver") or "")
        log.info(
            "switch HELLO: mod=%s smo=%s (client=%s)",
            mod_ver, smo_ver, self._client_ver or "(unchecked)",
        )

        # Version policing: the Switch mod and SMOClient ship in lockstep
        # (mod_ver in switch-mod/CMakeLists.txt tracks client/__init__.py's
        # __version__). A mismatch here means the user updated one half
        # without rebuilding/redeploying the other; refuse the connection
        # so AP traffic doesn't get applied with stale wire-format
        # assumptions. The two-stage AP gate stays parked because we
        # never fire on_switch_ready below.
        if self._client_ver and mod_ver and mod_ver != self._client_ver:
            order = _compare_versions(mod_ver, self._client_ver)
            if order < 0:
                advice = (
                    f"Switch mod is older than SMOClient. Re-run /setup to "
                    f"rebuild and redeploy the Switch mod at {self._client_ver}."
                )
            elif order > 0:
                advice = (
                    f"SMOClient is older than Switch mod. Install a later "
                    f"meatballs.apworld into vendor/Archipelago/custom_worlds/ "
                    f"(needs to match {mod_ver})."
                )
            else:
                # Versions differ as strings but compare equal numerically
                # (e.g. "0.1.0" vs "0.1.0+abc"). Treat as Switch-side
                # rebuild needed by default.
                advice = (
                    f"Re-run /setup to rebuild and redeploy the Switch mod, "
                    f"or install a matching meatballs.apworld."
                )
            err = (
                f"Version mismatch: SMOClient is {self._client_ver}, "
                f"Switch mod is {mod_ver}. {advice}"
            )
            log.error("[version] refusing Switch connection — %s", err)
            self._state.add_log(f"[version mismatch] {err}")
            await self._send(HelloAckMsg(
                ok=False,
                seed=self._state.seed,
                slot=self._state.slot,
                cap_table_hash=msg.get("cap_table_hash", ""),
                deathlink_enabled=self._deathlink_enabled,
                client_ver=self._client_ver,
                err=err,
            ))
            # Close the writer so the Switch sees EOF and reconnects with
            # backoff. On every retry we re-check — a rebuilt-and-redeployed
            # Switch mod (or a re-installed apworld + restarted client)
            # recovers automatically.
            async with self._writer_lock:
                w = self._writer
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass
                    self._writer = None
            self._state.set_switch_conn("disconnected")
            return

        await self._send(HelloAckMsg(
            ok=True,
            seed=self._state.seed,
            slot=self._state.slot,
            cap_table_hash=msg.get("cap_table_hash", ""),
            deathlink_enabled=self._deathlink_enabled,
            client_ver=self._client_ver or None,
        ))
        self._state.set_switch_conn("ready")

        # M6 phase D — push authoritative per-kingdom balance to the Switch
        # BEFORE replaying items, BUT only if we have a snapshot to derive
        # from. compute_outstanding returns None until the first
        # PaySnapshotMsg arrives (Switch on title screen). The Switch's
        # post-HELLO sendSnapshot ships its own PaySnapshotMsg the same
        # connection cycle; we get a second chance via the snapshot handler
        # to push OutstandingMsg with real values then.
        if (
            self._get_outstanding is not None
            and self._state.compute_outstanding() is not None
        ):
            try:
                entries = self._get_outstanding()
            except Exception:
                log.exception("get_outstanding_entries failed during HELLO")
                entries = []
            await self._send(OutstandingMsg(entries=entries))

        # Replay snapshots so the Switch can re-apply state idempotently.
        replay_ids = [evt.item for evt in self._state.all_checked_locations()]
        await self._send(CheckedReplayMsg(ids=replay_ids))
        for evt in self._state.all_received_items():
            # M6 phase D — skip Moon items in the replay loop. OutstandingMsg
            # above is authoritative for per-kingdom moon credit; the
            # ItemMsg-apply path on the mod side would also fetch_add to
            # ap_moons_kingdom[], double-counting every grant on every
            # reconnect. Captures + kingdoms + others still replay (they're
            # persistent unlocks, not spendable balances).
            if evt.item.kind == "moon":
                continue
            await self._send(ItemMsg(
                kind=evt.item.kind,
                kingdom=evt.item.kingdom,
                shine_id=evt.item.shine_id,
                cap=evt.item.cap,
                name=evt.item.name,
                # Use the live-path's Cappy-suppression decision (gameplay
                # self-finds collapse to ""). evt.sender stays populated for
                # logging / web tracker but must NOT drive the bubble — a
                # self-find item that was silent live would otherwise pop a
                # bubble on every save reload / Switch reconnect.
                from_=evt.cappy_from,
                # M6 phase B: hack_name was resolved bridge-side when the item
                # was first received; carry it through replay so the Switch
                # mod can grant the capture after reconnect without needing
                # bridge to re-resolve.
                hack_name=evt.item.hack_name,
                classification=evt.item.classification,
            ))

        # Capturesanity OFF: no Capture items will ever arrive from AP, so
        # the Switch's captures_unlocked bitset would stay all-zero and
        # CaptureStartHook would block every capture for the entire seed.
        # Push synthetic unlocks. No-op when capturesanity is on, or when
        # AP Connected hasn't flipped the flag yet (in which case SMOContext
        # calls this again from its Connected handler).
        await self.push_capturesanity_replay()

        # Replay the shine-palette scout map so a Switch reconnect after the
        # bridge has already received LocationInfo doesn't lose colors.
        await self.send_shine_scouts(self._state.all_shine_palette())

        await self._send(ApStateMsg(conn=self._state.ap_conn))

        # Fire the post-HELLO callback last so SMOContext can promote a
        # pending AP-connect (SNI-style two-stage gate): the user clicked
        # Connect earlier but the AP dial was deferred until the Switch
        # was up. Sending the HelloAck/replay first means the new AP
        # connection has already had its state pushed when it lands.
        if self._on_switch_ready is not None:
            try:
                await self._on_switch_ready()
            except Exception:
                log.exception("on_switch_ready callback failed")
