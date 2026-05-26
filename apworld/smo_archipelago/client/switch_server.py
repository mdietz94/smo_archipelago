"""Asyncio TCP server for the Switch.

Accepts N parallel Switch connections (e.g. real hardware + Ryujinx on
the same LAN). Exactly one is the "active" Switch at a time — the one
forwarding telemetry to AP and receiving items / replays. Others are
parked with a `KickMsg(reason="inactive")` until the user toggles
active in the SMOClient UI; the previously-active is then KICKed with
reason="unbound".

On every HELLO (and on every active-toggle), we replay the full
received-items history and the set of locations already checked, so
the Switch module always re-applies state idempotently after a reboot
or after being newly promoted to active.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import protocol
from .protocol import (
    ActivateMsg,
    ApStateMsg,
    CappyMsg,
    CheckedReplayMsg,
    ErrMsg,
    HelloAckMsg,
    ItemMsg,
    KickMsg,
    KillMsg,
    MoonLabelMsg,
    OutstandingMsg,
    PongMsg,
    ShineScoutsMsg,
    ShopLabelsMsg,
    TalkatooPoolMsg,
    kingdom_ap_to_switch,
)
from .state import BridgeState, CheckEvent, ItemEvent

# Max scout entries per ShineScoutsMsg. Each entry is ~25 bytes wire; 200
# stays well under MAX_LINE_BYTES (8 KiB) even with TOML-driven larger
# palette ints. Switch merges chunks by shine_uid overwrite, so order and
# count are immaterial.
_SCOUT_CHUNK_SIZE = 200

log = logging.getLogger(__name__)

# Dedicated logger for forwarded Switch-side log lines. Surfaces in the
# Kivy "Odyssey" tab (gui.py wires the "SMO" logger into the right-hand
# UILog there). Kept distinct from `log` above so Switch-forwarded noise
# stays out of the SMOClient PC-side log stream.
_switch_log = logging.getLogger("SMO")

# Logger that drives the "Archipelago" tab (gui.py:
# logging_pairs = [("Client", "Archipelago")]). Use this for messages the
# user MUST see — e.g. the held-snapshot prompt asking them to type
# /confirm_snapshot or /reject_snapshot. `log` (module-scoped) and
# `_state.add_log` (BridgeState buffer) don't reach either UILog tab.
_client_log = logging.getLogger("Client")

_SWITCH_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info":  logging.INFO,
    "warn":  logging.WARNING,
    "error": logging.ERROR,
}


# When a single snapshot drain produces more freshly-checked loc_ids than this,
# suppress all Cappy bubbles for that drain. Protects against the first-connect
# case where the snapshot enumerates every owned moon + capture as new — a
# 41-bubble flood is worse than silence. Real "missed a few while offline"
# sessions sit comfortably under this cap. CappyMessenger queues at most 8
# anyway; this threshold trips first.
RECONCILE_CAPPY_BURST_THRESHOLD = 5


def _classify_snapshot_for_user_confirm(
    entries: "list[dict]",
    goal_reached: bool,
    resolve_entry_to_loc_id: "EntryResolver | None",
    get_already_checked_loc_ids: "AlreadyCheckedProvider | None",
    is_goal_finished: "GoalFinishedProbe | None",
) -> "tuple[bool, int, int]":
    """Decide whether a snapshot should be auto-applied or held for /confirm_snapshot.

    Returns ``(auto_confirm, new_count, already_count)``.

    The Switch-side `save_was_loaded && CappyMessenger::hasDispatchedSinceReset()`
    gate proves only that Mario is in a live gameplay scene with save data
    fully resident — NOT that the player picked this save for this AP run.
    Without the bridge-side confirm gate, a wrong-save snapshot can credit
    moons to AP before the user can react. LocationChecks are persisted
    server-side; once sent the user must `/forfeit` to unwind.

    Auto-confirm (``True``) when the snapshot is a no-op against current AP
    state — empty New-Game snapshot, reconnect-mid-session where every entry
    already deduped against ``locations_checked``, or a `goal_reached=True`
    snapshot whose goal we already reported this session.

    Hold (``False``) when at least one entry resolves to a brand-new AP
    location, or when ``goal_reached=True`` and we haven't reported it.

    Back-compat: when ``resolve_entry_to_loc_id`` isn't wired (legacy / test
    harnesses constructed without the kwarg), returns ``auto_confirm=True``
    so existing behavior is preserved.
    """
    if resolve_entry_to_loc_id is None:
        return True, 0, 0

    if get_already_checked_loc_ids is not None:
        try:
            already = set(get_already_checked_loc_ids())
        except Exception:
            log.exception("get_already_checked_loc_ids failed; treating as empty")
            already = set()
    else:
        already = set()

    new_count = 0
    already_count = 0
    for entry in entries:
        # Snapshot-derived captures are dropped before forwarding (see
        # _dispatch_snapshot_entries) — exclude them from the gate counts
        # so a save with only captures still auto-confirms.
        if entry.get("kind") == "capture":
            continue
        try:
            loc_id = resolve_entry_to_loc_id(entry)
        except Exception:
            log.exception("resolve_entry_to_loc_id raised for entry=%r", entry)
            loc_id = None
        if loc_id is None:
            continue
        if loc_id in already:
            already_count += 1
        else:
            new_count += 1

    goal_is_new = False
    if goal_reached:
        if is_goal_finished is not None:
            try:
                goal_is_new = not bool(is_goal_finished())
            except Exception:
                log.exception("is_goal_finished raised; treating goal as new")
                goal_is_new = True
        else:
            goal_is_new = True

    auto_confirm = (new_count == 0) and not goal_is_new
    return auto_confirm, new_count, already_count

CheckHandler = Callable[[dict], Awaitable["int | None"]]  # returns AP loc_id or None
GoalHandler = Callable[[], Awaitable[None]]
DeathHandler = Callable[[int], Awaitable[None]]
LabelComposer = Callable[[int], "str | None"]              # loc_id -> label text
# PaySnapshotHandler(totals=dict[str, int]) -> None.
# `totals` is keyed by AP-form kingdom name (dispatcher does the
# Switch→AP translation). Handler folds into BridgeState and re-derives
# outstanding, then pushes OutstandingMsg to the Switch.
PaySnapshotHandler = Callable[..., Awaitable[None]]
# OutstandingProvider() -> list[OutstandingEntry]. Used at HELLO time to
# snapshot the current per-kingdom balance for the Switch.
OutstandingProvider = Callable[[], "list"]
# Capturesanity-OFF replay — returns every known (cap_name, hack_name) pair so
# the bridge can synthesize per-capture ItemMsgs that set every bit of the
# Switch's captures_unlocked bitset. Without this, CaptureStartHook would
# block every capture for the entire seed (no AP Capture items will arrive).
AllCapturesProvider = Callable[[], list]
# `() -> bool`: True when datapackage is loaded and report_check can resolve
# loc_ids. Gates snapshot drain when the Switch HELLO arrives before AP dials.
ApReadyProbe = Callable[[], bool]
# `(loc_id) -> ItemMsg | None`: builds a Cappy-bubble ItemMsg for a moon
# reconciled from a snapshot. Returns None when scouts aren't loaded yet,
# when the item isn't for our slot, or when the loc isn't a moon.
# SwitchServer keeps a pending-set and re-tries on every LocationInfo
# absorption until the cache catches up.
ReconcileItemBuilder = Callable[[int], "ItemMsg | None"]
# `() -> set[int]`: snapshot of currently-checked AP location ids. Captured
# once before drain to distinguish "newly checked from reconcile" (fire Cappy)
# vs "already known" (skip Cappy — player checked it live or saw it announced
# on a previous reconnect).
AlreadyCheckedProvider = Callable[[], "set[int]"]
# `(entry) -> loc_id | None`: pure resolution of a snapshot entry to its AP
# location_id without I/O or state mutation. Used only to decide whether a
# snapshot would credit a NEW location (hold for /confirm_snapshot) vs
# nothing new (auto-confirm).
EntryResolver = Callable[[dict], "int | None"]
# `() -> bool`: True iff the goal has already been reported for the current AP
# session. Combined with snapshot's `goal_reached` flag to decide whether a
# goal-reaching snapshot would credit a fresh goal (hold) or redundantly
# reconfirm one already shipped (auto-confirm).
GoalFinishedProbe = Callable[[], bool]
# UI notification — fired whenever the set of connected Switches OR the
# active selection changes. Synchronous callback (typically schedules a
# Kivy refresh on the next polling tick). Wired by SMOContext / gui.py.
SwitchesChangedHandler = Callable[[], None]


@dataclass
class _SwitchConn:
    """Per-Switch connection record. One per TCP socket; lifecycle owned
    by `_handle_client`.

    `writer_lock` serializes writes to this writer specifically (different
    Switches can be written to in parallel). `hello` retains the parsed
    HELLO dict for diagnostics + the active-toggle replay path.
    """
    device_id: str
    peer_ip: str
    writer: asyncio.StreamWriter
    writer_lock: asyncio.Lock
    hello: dict


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


def _enable_tcp_keepalive(writer: asyncio.StreamWriter) -> None:
    """Force aggressive TCP keepalive on an accepted Switch socket.

    Windows' default keepalive is 2h idle, which is far too slow for the
    same-host-takeover path in `_handle_client` to detect a half-open
    writer. When a Wi-Fi blip or Ryujinx pause leaves the PC's side of
    a connection alive while the Switch's side is gone, `is_closing()`
    stays False until the OS finally times out, and every new Switch
    connection takes over from the previous live one in a self-sustaining
    storm (see SMOClient_2026_05_21_09_46_11.txt). Shorten to idle=10s,
    interval=2s, probes=5 so dead writers surface in ~20s.

    Uses the TCP_KEEP{IDLE,INTVL,CNT} setsockopt surface — supported on
    Linux, macOS (idle via TCP_KEEPALIVE), and Windows 10 1709+ with
    Python 3.12+. asyncio's `TransportSocket` wrapper passes setsockopt
    through but does NOT expose Windows' legacy `SIO_KEEPALIVE_VALS`
    ioctl, so the modern path is the only viable one here.
    """
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    for attr, value in (
        ("TCP_KEEPIDLE", 10),
        ("TCP_KEEPINTVL", 2),
        ("TCP_KEEPCNT", 5),
        ("TCP_KEEPALIVE", 10),  # macOS spelling for idle
    ):
        opt = getattr(socket, attr, None)
        if opt is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, opt, value)
        except OSError:
            pass


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
        on_pay_snapshot: PaySnapshotHandler | None = None,
        get_outstanding_entries: OutstandingProvider | None = None,
        capturesanity_enabled: bool = True,
        get_all_captures: AllCapturesProvider | None = None,
        is_ap_ready: ApReadyProbe | None = None,
        build_reconcile_cappy_item: ReconcileItemBuilder | None = None,
        get_already_checked_loc_ids: AlreadyCheckedProvider | None = None,
        resolve_entry_to_loc_id: EntryResolver | None = None,
        is_goal_finished: GoalFinishedProbe | None = None,
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
        self._on_pay_snapshot = on_pay_snapshot
        self._get_outstanding = get_outstanding_entries
        self._client_ver = client_ver
        self._capturesanity_enabled = capturesanity_enabled
        self._get_all_captures = get_all_captures
        self._is_ap_ready = is_ap_ready
        self._build_reconcile_cappy_item = build_reconcile_cappy_item
        self._get_already_checked = get_already_checked_loc_ids
        self._resolve_entry_to_loc_id = resolve_entry_to_loc_id
        self._is_goal_finished = is_goal_finished
        # Per-device_id connection registry. Exactly one entry's id is in
        # `_active_device_id` at any time; that one forwards telemetry to AP
        # and receives items / replays.
        self._connections: dict[str, _SwitchConn] = {}
        self._active_device_id: str | None = None
        # Notified whenever connections or the active selection changes.
        # Used by the GUI to refresh the Switches popup. Synchronous —
        # the callback should not block (typically schedules a Kivy
        # refresh on the next polling tick).
        self._on_switches_changed: SwitchesChangedHandler | None = None
        self._server: asyncio.AbstractServer | None = None
        # Snapshot entries buffered when state_end arrived but AP wasn't ready
        # yet (datapackage not loaded → report_check can't resolve loc_ids).
        # Drained by drain_pending_snapshot() once AP is ready. None means
        # "no buffered snapshot" (distinct from [] — a valid empty snapshot).
        self._pending_snapshot_entries: list[dict] | None = None
        self._pending_snapshot_goal: bool = False
        # Live `check` messages buffered during the AP-handshake window. The
        # Switch's outbound check ring drains queued offline collects on
        # reconnect; without buffering they hit the same "no AP id" race as
        # the snapshot. Drained by drain_pending_snapshot() alongside it.
        self._pending_live_checks: list[dict] = []
        # /confirm_snapshot gate. When the classifier ("would this snapshot
        # credit any NEW AP location?") returns auto_confirm=False, the
        # snapshot lands here instead of being forwarded. The operator types
        # /confirm_snapshot to release it, or /reject_snapshot to discard.
        # `_held_*_from_reconcile` preserves the original from_reconcile flag
        # so the Cappy-burst threshold keeps the right behavior across confirm.
        # Last-write-wins: a fresh state_end / drain replaces whatever was held
        # so a "now I actually clicked New Game" snapshot supersedes the hold.
        self._held_snapshot_entries: "list[dict] | None" = None
        self._held_snapshot_goal: bool = False
        self._held_snapshot_from_reconcile: bool = False
        # loc_ids freshly dispatched from a snapshot drain (NOT already in
        # locations_checked when drain began). Each waits for its scout to
        # land via LocationInfo absorption; when the scout shows the item is
        # for our slot, we synthesize a Cappy ItemMsg so the player learns
        # what they got offline.
        self._reconcile_cappy_pending: set[int] = set()
        # Talkatoo% mode payload. Set by SMOContext after Connected once the
        # slot_data + datapackage are both available. Stored as the input
        # (enabled flag + AP-form per-kingdom moon lists) so HELLO replays
        # can re-emit one message per kingdom across Switch reconnects.
        # `_talkatoo_configured` distinguishes "never set" (push is a no-op,
        # HELLO arrived before AP Connected) from "deliberately off"
        # (push sends a disable message).
        self._talkatoo_configured: bool = False
        self._talkatoo_enabled: bool = False
        self._talkatoo_kingdoms: dict[str, list[str]] = {}
        # Tracker for "kingdoms we've ever shipped a non-clear message to
        # during this SwitchServer lifetime." Used by push_talkatoo_pool
        # to send `moons=[]` clears for kingdoms that drop out of the
        # current build — either because Phase 5's cursor advanced past
        # the kingdom's last entry (window empty) or because the seed
        # changed and the new talkatoo_order lacks that kingdom. Without
        # this, the Switch's ApState::talkatoo_pools[bit] would retain
        # stale entries that Talkatoo would keep re-suggesting.
        self._talkatoo_ever_shipped: set[str] = set()
        # Shop label table. Built by SMOContext after AP Connected (via
        # set_shop_labels) by looking up each "<Kingdom>: Shopping in X"
        # location's scouted item through compose_moon_label_for_location.
        # The Switch's ShopItemMessageHook substitutes by (file_name, key)
        # so the bridge stores fully-formed entries. `_shop_labels_configured`
        # disambiguates "never set" (HELLO before AP Connected) from "ready
        # to ship" (empty entries means clear, configured=True means send it).
        self._shop_labels_configured: bool = False
        self._shop_label_entries: list[dict] = []

    def set_deathlink_enabled(self, enabled: bool) -> None:
        """Update the bridge-side DeathLink gate. Called by SMOContext after
        AP Connected delivers slot_data so the YAML's `death_link` setting
        wins over launch-time config (host.yaml `deathlink_default`, TOML,
        `--deathlink`). Takes effect on the NEXT HELLO replay; the caller
        should also `await push_deathlink_helloack()` so a Switch that has
        already HELLO'd reacts without waiting for a save-load."""
        self._deathlink_enabled = bool(enabled)

    async def push_deathlink_helloack(self) -> None:
        """Re-send HelloAckMsg with the current `_deathlink_enabled` to
        the active Switch.

        The Switch's `hello_ack` handler is idempotent for the other fields
        (local_slot, conn=Ready, bridge_connected=true), so a second ack
        mid-session just updates `ApState::deathlink_enabled`. Without this,
        a YAML toggle wouldn't reach the Switch until the next save-load
        forces a fresh HELLO — and inbound kills would keep being dropped
        by ApState::maybeApplyInboundKill (`if (!deathlink_enabled)` gate).

        No-op when no active Switch is attached.
        """
        conn = self._active_conn()
        if conn is None or conn.writer.is_closing():
            return
        await self._send_to_conn(conn, HelloAckMsg(
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
        """Synthesize all-captures-unlocked ItemMsgs for the active
        Switch. No-op if capturesanity is enabled (AP-granted captures
        only), if no captures provider is wired, or if there is no
        active Switch.

        Called from two paths: (1) the tail of HELLO replay (against
        the active Switch); (2) SMOContext on AP Connected.
        """
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

    def set_on_switches_changed(self, cb: SwitchesChangedHandler | None) -> None:
        """Register a callback fired when the Switches set / active
        selection changes. Used by the GUI to refresh the selector
        popup. Synchronous — caller must not block."""
        self._on_switches_changed = cb

    def _notify_switches_changed(self) -> None:
        if self._on_switches_changed is None:
            return
        try:
            self._on_switches_changed()
        except Exception:
            log.exception("on_switches_changed callback raised")

    def _active_conn(self) -> _SwitchConn | None:
        if self._active_device_id is None:
            return None
        return self._connections.get(self._active_device_id)

    def is_connected(self) -> bool:
        """True iff an active Switch is currently attached and the socket
        is open. Used by SMOContext to gate the AP dial on Switch presence.
        Inactive (registered-but-idle) Switches don't count — the AP slot
        is bound to the active one only."""
        conn = self._active_conn()
        return conn is not None and not conn.writer.is_closing()

    def get_active_device_id(self) -> str | None:
        return self._active_device_id

    def get_connected_device_ids(self) -> list[str]:
        return list(self._connections.keys())

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        log.info("switch server listening on %s", addrs)

    async def stop(self) -> None:
        # Close every Switch connection FIRST. Python 3.12+'s
        # Server.wait_closed() waits for both the listener and every active
        # client task to finish; _handle_client is parked in reader.read()
        # so the connection task never returns on its own — the listener
        # closing doesn't kick connected clients. Without this teardown,
        # a clean window-close hangs forever whenever any Switch is still
        # connected.
        conns = list(self._connections.values())
        self._connections.clear()
        self._active_device_id = None
        log.info("stop: closing %d Switch connections", len(conns))

        async def _close_one(conn: _SwitchConn) -> None:
            try:
                conn.writer.close()
                # Each connection gets a bounded grace window — a real
                # Switch over LAN normally ACKs the FIN in <100ms, but a
                # half-dead emulator NAT or a dropped Wi-Fi link can stall
                # wait_closed for the full TCP_KEEPALIVE timeout. We'd
                # rather drop the connection abruptly than block the
                # entire shutdown path.
                await asyncio.wait_for(conn.writer.wait_closed(), timeout=1.0)
            except asyncio.TimeoutError:
                log.warning(
                    "stop: %r writer.wait_closed timed out; dropping anyway",
                    conn.device_id,
                )
            except Exception:
                pass

        # Parallel close — sequential `await` per-conn previously meant a
        # slow peer stacked on top of every faster one. With gather, the
        # whole bunch takes ~max(per-conn-close) instead of sum().
        if conns:
            await asyncio.gather(
                *(_close_one(c) for c in conns), return_exceptions=True,
            )
        if self._server is not None:
            self._server.close()
            try:
                # Same defensive timeout — if a _handle_client task is
                # blocked inside an async callback (e.g. an AP send that
                # can't drain because the websocket is also shutting down),
                # waiting forever serves nobody.
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("stop: server.wait_closed timed out")
            except Exception:
                pass
            self._server = None
        log.info("stop: done")

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
        """Push the derived per-kingdom balance to the active Switch.

        Called from context.py whenever the inputs to compute_outstanding
        change (Moon item arrival from AP, or PaySnapshotMsg from Switch).
        The Switch overwrites `ap_moons_kingdom[bit]` for each entry.
        """
        await self._send(msg)

    def set_talkatoo_pool(self, enabled: bool, kingdoms: dict[str, list[str]]) -> None:
        """Stash the Talkatoo% per-kingdom AP-pool payload.

        Stored verbatim so HELLO replays can re-ship it across Switch
        reconnects. Takes effect on the NEXT push_talkatoo_pool() call OR
        the next HELLO. Caller is expected to follow up with
        push_talkatoo_pool() so the currently-attached Switch receives the
        new pool immediately.

        `kingdoms` keys are AP-form kingdom names (e.g. "Cap", "Bowser's");
        push_talkatoo_pool applies kingdom_ap_to_switch per entry at send
        time. Empty `kingdoms` with `enabled=True` is allowed (means "no
        AP-pool moons in any kingdom" — Talkatoo will show no hints) and is
        wire-different from `enabled=False` (which tells the Switch
        Talkatoo% mode is off entirely).
        """
        self._talkatoo_enabled = bool(enabled)
        self._talkatoo_kingdoms = {k: list(v) for k, v in kingdoms.items()}
        # Mark configured even when there are zero kingdoms — distinguishes
        # "never set" (push is a no-op) from "deliberately off" (push sends
        # the disable message).
        self._talkatoo_configured = True

    async def push_talkatoo_pool(self) -> None:
        """Send TalkatooPool message(s) to the Switch — one per kingdom when
        enabled, or a single disable message when off.

        Chunked per-kingdom to stay under the 8 KiB line limit (Sand at 62
        moons would otherwise straddle). No-op when set_talkatoo_pool has
        never been called (HELLO before AP Connected — the context handler
        re-pushes from the Connected handler once slot_data lands).
        """
        if not getattr(self, "_talkatoo_configured", False):
            return
        if not self._talkatoo_enabled:
            await self._send(TalkatooPoolMsg(enabled=False, kingdom="", moons=[]))
            # The disable message wipes Switch-side state for all kingdoms,
            # so the tracker no longer needs to remember what we shipped.
            self._talkatoo_ever_shipped.clear()
            return
        # Send `moons=[]` clears for any kingdoms we previously shipped to
        # that are NOT in the current push. Covers two paths: (a) Phase 5
        # cursor advanced past a kingdom's last entry — its window is now
        # empty and the kingdom drops out of _talkatoo_kingdoms; without
        # the clear, the Switch would keep its last-seen (stale) pool and
        # Talkatoo would re-suggest already-collected moons. (b) Seed
        # swap without SMO restart — the new talkatoo_order has different
        # kingdom keys, and any kingdoms in the OLD set would leak.
        current_kingdoms = set(self._talkatoo_kingdoms.keys())
        for kingdom_ap in self._talkatoo_ever_shipped - current_kingdoms:
            await self._send(TalkatooPoolMsg(
                enabled=True,
                kingdom=kingdom_ap_to_switch(kingdom_ap) or kingdom_ap,
                moons=[],
            ))
        # One message per kingdom. Apply AP→Switch kingdom translation just
        # before send so the stored payload stays in AP form (matches
        # BridgeState's internal model).
        for kingdom_ap, moons in self._talkatoo_kingdoms.items():
            await self._send(TalkatooPoolMsg(
                enabled=True,
                kingdom=kingdom_ap_to_switch(kingdom_ap) or kingdom_ap,
                moons=list(moons),
            ))
        self._talkatoo_ever_shipped = current_kingdoms

    def set_shop_labels(self, entries: list[dict]) -> None:
        """Stash the per-shop label entries for shipping to the Switch.

        Each entry is `{"file": <str>, "key": <str>, "label": <str>}`,
        matching the wire shape the Switch's parseShopLabels expects.
        Stored verbatim so HELLO replays can re-ship across Switch
        reconnects. Caller is expected to follow up with
        `push_shop_labels()` so an already-attached Switch picks up the
        new table immediately.

        Empty `entries` is allowed (and meaningful — sends a clear).
        """
        self._shop_label_entries = list(entries or [])
        self._shop_labels_configured = True

    async def push_shop_labels(self) -> None:
        """Send the stashed shop labels to the active Switch.

        No-op when `set_shop_labels` has never been called (HELLO arrived
        before AP Connected — the context handler re-pushes once
        slot_data + datapackage land). A configured-but-empty table is
        wire-different from "never set": it actively CLEARS the Switch's
        shop_labels storage and reverts to vanilla "Power Moon" / SMO's
        own moon names.
        """
        if not self._shop_labels_configured:
            return
        await self._send(ShopLabelsMsg(entries=self._shop_label_entries))

    async def send_shine_scouts(self, palette: dict[int, int]) -> None:
        """Push (shine_uid -> palette) to the active Switch, chunked.

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
        """Send to the active Switch. No-op if there is none."""
        conn = self._active_conn()
        if conn is None:
            return
        await self._send_to_conn(conn, msg)

    async def _send_to_conn(self, conn: _SwitchConn, msg: Any) -> None:
        async with conn.writer_lock:
            w = conn.writer
            if w.is_closing():
                return
            try:
                w.write(protocol.encode(msg))
                await w.drain()
            except (ConnectionResetError, BrokenPipeError):
                log.warning("switch write failed (%s); closing", conn.device_id)
                try:
                    w.close()
                except Exception:
                    pass

    # ---- selector ----

    async def set_active(self, device_id: str | None) -> bool:
        """Promote a connected Switch to active. Returns True on success.

        On rebind: send `KickMsg(reason="unbound")` to the previously-active
        Switch and run the full post-HELLO replay sequence against the
        newly-active one (OutstandingMsg + non-Moon ItemMsg backlog +
        shine palette + ApStateMsg + capturesanity unlocks).

        device_id=None unbinds the current active (rare; mostly for tests
        and shutdown paths).

        No-op if device_id is already active, or doesn't correspond to any
        connected Switch.
        """
        if device_id is not None and device_id not in self._connections:
            log.warning("set_active: unknown device_id %r", device_id)
            return False
        if device_id == self._active_device_id:
            return True
        old_id = self._active_device_id
        old_conn = self._connections.get(old_id) if old_id else None
        new_conn = self._connections.get(device_id) if device_id else None

        # Demote old first so a transient race in the replay path can't
        # bleed item replays to the wrong Switch.
        self._active_device_id = device_id
        self._state.set_active_switch(device_id)
        if old_conn is not None:
            await self._send_to_conn(old_conn, KickMsg(reason="unbound"))
        if new_conn is not None:
            await self._send_to_conn(new_conn, ActivateMsg())
            await self._run_post_hello_replay(new_conn)
        self._notify_switches_changed()
        return True

    # ---- per-connection handler ----

    def _resolve_device_id(self, raw_id: str, peer_ip: str) -> str:
        """Pick an effective device_id given a HELLO + peer IP.

        Same id + same peer_ip = reconnect (caller will replace the
        existing entry in `_connections`). Same id + different peer_ip =
        collision — synthesize a peer-IP-suffixed id so both Switches
        can coexist in the registry.
        """
        existing = self._connections.get(raw_id)
        if existing is None or existing.peer_ip == peer_ip:
            return raw_id
        # Collision — try the peer's last octet first, then the full IP.
        for tail in (peer_ip.rsplit(".", 1)[-1], peer_ip.replace(".", "-")):
            candidate = f"{raw_id}-{tail}"
            other = self._connections.get(candidate)
            if other is None or other.peer_ip == peer_ip:
                return candidate
        return f"{raw_id}-{peer_ip}"

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "?"
        # Aggressive TCP keepalive surfaces a dead Wi-Fi link inside
        # ~10s instead of hanging on the OS-default ~2h. Imported from
        # main; safe to call on every accepted socket regardless of
        # whether the connection becomes the active Switch.
        _enable_tcp_keepalive(writer)
        log.info("switch connecting from %s", peer)

        # Read until HELLO arrives. Any non-HELLO messages in the same
        # TCP burst are buffered for re-dispatch after the connection is
        # registered. In normal operation the Switch waits for HelloAck
        # before sending anything else, but be defensive.
        buffer = bytearray()
        hello_msg: dict | None = None
        leftover_msgs: list[dict] = []
        try:
            while hello_msg is None:
                chunk = await reader.read(4096)
                if not chunk:
                    log.info("switch from %s disconnected before HELLO", peer)
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                    return
                buffer.extend(chunk)
                for line in protocol.iter_lines(buffer):
                    try:
                        m = protocol.decode(line)
                    except Exception:
                        log.exception("bad message before HELLO from %s: %r",
                                      peer, line[:200])
                        continue
                    if hello_msg is None and m.get("t") == "hello":
                        hello_msg = m
                    else:
                        leftover_msgs.append(m)
        except (ConnectionResetError, BrokenPipeError):
            log.info("switch from %s reset before HELLO", peer)
            return

        # Determine effective device_id and register the connection.
        raw_id = str(hello_msg.get("device_id") or "").strip()
        if not raw_id:
            # Legacy / fallback identifier from peer IP suffix. Stays in
            # SSO range on the Switch side too.
            tail = peer_ip.rsplit(".", 1)[-1] if peer_ip else "?"
            raw_id = f"sw-{tail}"
        effective_id = self._resolve_device_id(raw_id, peer_ip)

        existing = self._connections.get(effective_id)
        if existing is not None:
            # Same-id reconnect: close the previous writer. Its handler
            # task will see EOF on its reader.read() and tear down in its
            # own finally block; our registration below replaces the dict
            # entry so the old finally's identity check skips the
            # unregister step.
            log.info(
                "switch %r reconnecting from %s; replacing previous writer",
                effective_id, peer,
            )
            try:
                existing.writer.close()
            except Exception:
                pass

        conn = _SwitchConn(
            device_id=effective_id,
            peer_ip=peer_ip,
            writer=writer,
            writer_lock=asyncio.Lock(),
            hello=hello_msg,
        )
        self._connections[effective_id] = conn
        self._state.register_switch(
            device_id=effective_id,
            peer_ip=peer_ip,
            mod_ver=str(hello_msg.get("mod_ver") or ""),
            smo_ver=str(hello_msg.get("smo_ver") or ""),
        )

        # Version policing — apply before auto-bind so a mismatched
        # connection never becomes active.
        if not await self._policy_check_version(conn, hello_msg):
            if self._connections.get(effective_id) is conn:
                del self._connections[effective_id]
                self._state.unregister_switch(effective_id)
                self._notify_switches_changed()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        # Active-slot decision.
        is_first = self._active_device_id is None
        if is_first:
            self._active_device_id = effective_id
            self._state.set_active_switch(effective_id)
            self._state.set_switch_conn("connecting")
            log.info("switch %r connected from %s (active)", effective_id, peer)
            await self._send_hello_ack(conn)
            self._state.set_switch_conn("ready")
            await self._run_post_hello_replay(conn)
        else:
            log.info(
                "switch %r connected from %s (inactive — active is %r)",
                effective_id, peer, self._active_device_id,
            )
            await self._send_hello_ack(conn)
            await self._send_to_conn(conn, KickMsg(reason="inactive"))
        self._notify_switches_changed()

        # Re-dispatch buffered pre-HELLO leftovers (rare).
        for m in leftover_msgs:
            try:
                await self._dispatch_from(conn, m)
            except Exception:
                log.exception("error handling buffered pre-HELLO message: %r", m)

        # Main dispatch loop.
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    log.info("switch %r disconnected (EOF)", effective_id)
                    break
                buffer.extend(chunk)
                for line in protocol.iter_lines(buffer):
                    try:
                        msg = protocol.decode(line)
                        await self._dispatch_from(conn, msg)
                    except Exception:
                        log.exception("error handling message: %r", line[:200])
                        await self._send_to_conn(conn, ErrMsg(code="bad_message", ctx="rx"))
        except (ConnectionResetError, BrokenPipeError):
            log.info("switch %r connection reset", effective_id)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            # Only mutate state if our connection is still the registered
            # one — a faster reconnect may have already replaced it (see
            # the `existing is not None` branch above).
            if self._connections.get(effective_id) is conn:
                del self._connections[effective_id]
                self._state.unregister_switch(effective_id)
                if self._active_device_id == effective_id:
                    self._active_device_id = None
                    self._state.set_active_switch(None)
                    # Auto-promote another connection if any remain. Keeps
                    # the AP slot bound even when the user is shuffling
                    # devices.
                    promoted = next(iter(self._connections), None)
                    if promoted is not None:
                        log.info(
                            "active %r dropped; auto-promoting %r",
                            effective_id, promoted,
                        )
                        await self.set_active(promoted)
                    else:
                        self._state.set_switch_conn("disconnected")
                self._notify_switches_changed()

    async def _policy_check_version(self, conn: _SwitchConn, msg: dict) -> bool:
        """Refuse the connection on mod_ver / SMOClient mismatch.

        Returns True when the connection is OK to proceed, False when we
        already sent a HelloAck(ok=false) and the caller should tear down.
        """
        mod_ver = str(msg.get("mod_ver") or "")
        smo_ver = str(msg.get("smo_ver") or "")
        log.info(
            "switch HELLO: device=%r mod=%s smo=%s (client=%s)",
            conn.device_id, mod_ver, smo_ver, self._client_ver or "(unchecked)",
        )
        if not (self._client_ver and mod_ver and mod_ver != self._client_ver):
            return True
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
        await self._send_to_conn(conn, HelloAckMsg(
            ok=False,
            seed=self._state.seed,
            slot=self._state.slot,
            cap_table_hash=msg.get("cap_table_hash", ""),
            deathlink_enabled=self._deathlink_enabled,
            client_ver=self._client_ver,
            err=err,
        ))
        return False

    async def _send_hello_ack(self, conn: _SwitchConn) -> None:
        await self._send_to_conn(conn, HelloAckMsg(
            ok=True,
            seed=self._state.seed,
            slot=self._state.slot,
            cap_table_hash=conn.hello.get("cap_table_hash", ""),
            deathlink_enabled=self._deathlink_enabled,
            client_ver=self._client_ver or None,
        ))

    async def _run_post_hello_replay(self, conn: _SwitchConn) -> None:
        """Replay state to a newly-active Switch.

        Same sequence as the legacy `_on_hello` tail (M6-D outstanding,
        non-Moon ItemMsg replay, capturesanity flush, shine palette,
        ApStateMsg). Idempotent — re-running on rebind is safe because
        OutstandingMsg carries authoritative per-kingdom balance and
        non-Moon items skip on the mod side via dedupe.
        """
        # M6 phase D — push authoritative per-kingdom balance BEFORE the
        # item replay, but only if compute_outstanding has a reading.
        if (
            self._get_outstanding is not None
            and self._state.compute_outstanding() is not None
        ):
            try:
                entries = self._get_outstanding()
            except Exception:
                log.exception("get_outstanding_entries failed during HELLO")
                entries = []
            await self._send_to_conn(conn, OutstandingMsg(entries=entries))

        # Replay snapshots so the Switch can re-apply state idempotently.
        replay_ids = [evt.item for evt in self._state.all_checked_locations()]
        await self._send_to_conn(conn, CheckedReplayMsg(ids=replay_ids))
        for evt in self._state.all_received_items():
            # M6 phase D — skip Moon items in replay; OutstandingMsg above
            # is authoritative for per-kingdom moon credit.
            if evt.item.kind == "moon":
                continue
            await self._send_to_conn(conn, ItemMsg(
                kind=evt.item.kind,
                kingdom=evt.item.kingdom,
                shine_id=evt.item.shine_id,
                cap=evt.item.cap,
                name=evt.item.name,
                # Live-path Cappy suppression decision (gameplay self-finds
                # collapse to "").
                from_=evt.cappy_from,
                hack_name=evt.item.hack_name,
                classification=evt.item.classification,
            ))

        await self.push_capturesanity_replay()

        # Talkatoo% mode: ship the per-kingdom AP-pool (or Phase 5 cursor
        # window) to the Switch so the speech-bubble hook can pick from
        # it. No-op when SMOContext hasn't delivered a payload yet
        # (Switch HELLO before AP Connected — the context handler
        # re-pushes after slot_data lands).
        await self.push_talkatoo_pool()

        # Shop moon labels: replace Crazy Cap's purple-coin moon slot
        # display text with the AP-aware label for the corresponding
        # "Shopping in X" check. No-op when SMOContext hasn't built the
        # table yet (Switch HELLO before AP Connected — the context
        # handler re-pushes after the datapackage + scout cache land).
        await self.push_shop_labels()

        # Shine palette replay (per-uid). Routed through _send so it
        # targets the active conn — but during the post-HELLO sequence we
        # may be replaying to a connection that's about to become active.
        # Send directly to avoid a window where active hasn't been set yet.
        palette = self._state.all_shine_palette()
        if palette:
            items = list(palette.items())
            for i in range(0, len(items), _SCOUT_CHUNK_SIZE):
                chunk = items[i : i + _SCOUT_CHUNK_SIZE]
                await self._send_to_conn(conn, ShineScoutsMsg(entries=[
                    {"shine_uid": uid, "palette": p} for uid, p in chunk
                ]))
        await self._send_to_conn(conn, ApStateMsg(conn=self._state.ap_conn))

    async def _dispatch_from(self, conn: _SwitchConn, msg: dict) -> None:
        """Route a message from a specific Switch.

        Active Switch: full dispatch. Inactive Switch: only safe / read-only
        message kinds are processed (ping/pong, log surfacing); AP-mutating
        kinds (check, goal, death, state_*, pay_snapshot) are dropped to
        keep AP state authoritative against the user's chosen active.
        """
        is_active = (conn.device_id == self._active_device_id)
        t = msg.get("t")

        # Messages always safe regardless of active state.
        if t == "hello":
            # Duplicate HELLO on an already-established connection. Ignore.
            log.debug("duplicate HELLO from %r; ignoring", conn.device_id)
            return
        if t == "ping":
            await self._send_to_conn(conn, PongMsg(
                ts_ms=msg.get("ts_ms", int(time.time() * 1000)),
            ))
            return
        if t == "log":
            level = str(msg.get("level", "info"))
            text = str(msg.get("msg", ""))
            self._state.add_log(f"[switch:{conn.device_id}:{level}] {text}")
            _switch_log.log(
                _SWITCH_LEVEL_MAP.get(level, logging.INFO),
                "[switch:%s:%s] %s", conn.device_id, level, text,
            )
            return

        # Everything below mutates AP / per-slot state — gate on active.
        if not is_active:
            log.debug(
                "dropping %s from inactive Switch %r", t, conn.device_id,
            )
            return

        if t == "check":
            if self._is_ap_ready is not None and not self._is_ap_ready():
                self._pending_live_checks.append(msg)
                log.info(
                    "live check buffered (kind=%s stage=%s obj=%s) — AP not ready",
                    msg.get("kind"), msg.get("stage_name"), msg.get("object_id"),
                )
                return
            await self._dispatch_check(msg)
        elif t == "goal":
            log.info("switch %r reported goal completion", conn.device_id)
            await self._on_goal()
        elif t == "death":
            ts_ms = int(msg.get("ts_ms") or 0)
            log.info("switch %r reported death ts_ms=%d", conn.device_id, ts_ms)
            if self._on_death is not None:
                await self._on_death(ts_ms)
        elif t == "status":
            log.debug("switch %r status: %s", conn.device_id, msg)
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
            await self._send_to_conn(conn, ErrMsg(code="unknown_kind", ctx=str(t)))

    async def _on_pay_snapshot_msg(self, msg: dict) -> None:
        """M6 phase D — active Switch reported PayShineNum per kingdom.

        Parses the entries list, translates each kingdom to AP form, and
        hands off to the configured handler.
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

        Behaves exactly as before: dedup via BridgeState.add_checked_location,
        M6-A.5 MoonLabelMsg for the same TCP push, capturesanity CappyMsg
        bubble, snapshot-derived suppression.

        Returns the resolved AP `loc_id` (or None when unresolvable) so the
        snapshot drain can distinguish fresh vs already-known checks.
        """
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
        if self._is_ap_ready is not None and not self._is_ap_ready():
            self._pending_snapshot_entries = list(entries)
            self._pending_snapshot_goal = bool(goal_reached)
            log.info(
                "snapshot buffered (%d entries) — AP not ready; will drain on Connected",
                len(entries),
            )
            return
        await self._gate_or_dispatch_snapshot(entries, goal_reached, from_reconcile=False)

    async def _gate_or_dispatch_snapshot(
        self,
        entries: "list[dict]",
        goal_reached: bool,
        from_reconcile: bool,
    ) -> None:
        """Route a snapshot through the /confirm_snapshot gate.

        Wrapper around `_dispatch_snapshot_entries` that classifies first.
        Auto-confirms a snapshot that wouldn't credit any new AP location
        (empty New-Game state, reconnect-mid-session where everything is
        already in `locations_checked`, redundant goal_reached on an
        already-goaled slot). Otherwise stashes the entries on the held
        slot and waits for the operator's /confirm_snapshot.
        """
        auto_confirm, new_count, already_count = _classify_snapshot_for_user_confirm(
            entries,
            goal_reached,
            self._resolve_entry_to_loc_id,
            self._get_already_checked,
            self._is_goal_finished,
        )
        if auto_confirm:
            log.info(
                "[confirm-gate] auto-confirming snapshot (new=%d already=%d goal=%s)",
                new_count, already_count, goal_reached,
            )
            # Auto-confirm supersedes any prior held snapshot — a fresh
            # state_end is authoritative for current Switch state. New
            # Game produces an empty snapshot that auto-confirms; without
            # this clear, the previously-held wrong-save entries would
            # outlive their intent and a later /confirm_snapshot would
            # credit stale moons.
            self._held_snapshot_entries = None
            self._held_snapshot_goal = False
            self._held_snapshot_from_reconcile = False
            await self._dispatch_snapshot_entries(
                entries, goal_reached, from_reconcile=from_reconcile,
            )
            return

        was_holding = self._held_snapshot_entries is not None
        self._held_snapshot_entries = list(entries)
        self._held_snapshot_goal = bool(goal_reached)
        self._held_snapshot_from_reconcile = from_reconcile
        verb = "replaced" if was_holding else "held"
        log.warning(
            "[confirm-gate] snapshot %s — pending /confirm_snapshot "
            "(new=%d already=%d goal_reached=%s). "
            "Type /confirm_snapshot to apply, /reject_snapshot to discard.",
            verb, new_count, already_count, goal_reached,
        )
        goal_clause = ", goal_reached=true" if goal_reached else ""
        prompt = (
            f"Switch loaded a save with {new_count} new moon(s) + "
            f"{already_count} already-credited{goal_clause} — held to "
            "protect against the wrong save auto-loading. "
            "Type /confirm_snapshot to apply, or /reject_snapshot to "
            "discard and load a different save."
        )
        # Surface in the Archipelago tab — `log` (module logger) and
        # `_state.add_log` (BridgeState buffer) don't render there, and
        # the user reasonably expects the prompt where the slash commands
        # respond.
        _client_log.warning(prompt)
        self._state.add_log(f"[confirm-gate] {prompt}")

    async def confirm_pending_snapshot(self) -> bool:
        """Apply the held snapshot through the normal dispatch path.

        Returns True when a snapshot was released, False when nothing was
        held. Wired to /confirm_snapshot in SMOClientCommandProcessor.
        """
        if self._held_snapshot_entries is None:
            return False
        entries = self._held_snapshot_entries
        goal_reached = self._held_snapshot_goal
        from_reconcile = self._held_snapshot_from_reconcile
        self._held_snapshot_entries = None
        self._held_snapshot_goal = False
        self._held_snapshot_from_reconcile = False
        log.info(
            "[confirm-gate] confirming held snapshot (%d entries goal=%s)",
            len(entries), goal_reached,
        )
        await self._dispatch_snapshot_entries(
            entries, goal_reached, from_reconcile=from_reconcile,
        )
        return True

    def reject_pending_snapshot(self) -> bool:
        """Discard the held snapshot without forwarding.

        Returns True when something was discarded. Wired to /reject_snapshot.
        Synchronous because the discard is just clearing local state.
        """
        if self._held_snapshot_entries is None:
            return False
        log.info(
            "[confirm-gate] rejecting held snapshot (%d entries goal=%s)",
            len(self._held_snapshot_entries), self._held_snapshot_goal,
        )
        self._held_snapshot_entries = None
        self._held_snapshot_goal = False
        self._held_snapshot_from_reconcile = False
        return True

    def held_snapshot_summary(self) -> "tuple[int, int, bool] | None":
        """Report what is currently held, for /smo_status display.

        Returns (new_count, already_count, goal_reached) or None when nothing
        is held. Re-classifies on demand — the held entries are the same
        ones that would be applied by confirm_pending_snapshot.
        """
        if self._held_snapshot_entries is None:
            return None
        _, new_count, already_count = _classify_snapshot_for_user_confirm(
            self._held_snapshot_entries,
            self._held_snapshot_goal,
            self._resolve_entry_to_loc_id,
            self._get_already_checked,
            self._is_goal_finished,
        )
        return new_count, already_count, self._held_snapshot_goal

    async def _dispatch_snapshot_entries(
        self,
        entries: list[dict],
        goal_reached: bool,
        from_reconcile: bool,
    ) -> None:
        pre_checked: set[int] = set()
        if from_reconcile and self._get_already_checked is not None:
            try:
                pre_checked = set(self._get_already_checked())
            except Exception:
                log.exception("get_already_checked_loc_ids failed; assuming empty")
        fresh_this_drain: set[int] = set()
        for entry in entries:
            # See the original M7-era note for the rationale on dropping
            # snapshot captures: dict-derived snapshots aren't a
            # manual-capture signal — live CaptureStartHook is the only
            # authoritative source.
            if entry.get("kind") == "capture":
                log.info(
                    "snapshot: dropping capture-check for hack=%r "
                    "(dict-derived snapshot is not a manual-capture signal)",
                    entry.get("hack_name") or "",
                )
                continue
            synthetic = {"t": "check", **entry}
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
                log.info(
                    "[reconcile] %d fresh entries exceeds Cappy burst threshold (%d) — "
                    "suppressing bubbles for this drain",
                    len(fresh_this_drain), RECONCILE_CAPPY_BURST_THRESHOLD,
                )
            else:
                self._reconcile_cappy_pending |= fresh_this_drain
                await self.try_fire_reconcile_cappy()

    async def drain_pending_snapshot(self) -> None:
        """Drain anything buffered because AP wasn't ready."""
        live_checks = self._pending_live_checks
        self._pending_live_checks = []
        if live_checks:
            log.info("draining %d buffered live checks", len(live_checks))
            # Live checks are gameplay events the player actually triggered
            # while online — they're not subject to the wrong-save gate.
            # The save-load-with-no-prior-checks path can only produce a
            # state_end, never a live `check`.
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
        await self._gate_or_dispatch_snapshot(
            entries, goal_reached, from_reconcile=True,
        )

    async def try_fire_reconcile_cappy(self) -> None:
        """Pump pending reconciled loc_ids through the Cappy speech bubble."""
        if not self._reconcile_cappy_pending or self._build_reconcile_cappy_item is None:
            return
        fired: list[int] = []
        for loc_id in list(self._reconcile_cappy_pending):
            try:
                item = self._build_reconcile_cappy_item(loc_id)
            except Exception:
                log.exception("build_reconcile_cappy_item failed for loc_id=%s", loc_id)
                fired.append(loc_id)
                continue
            if item is None:
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
