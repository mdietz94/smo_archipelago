"""Bridge-side mirror of game state.

The bridge maintains an authoritative snapshot independently of any single
connection: AP can drop, the Switch can reboot, the bridge keeps state. Both
sides resync from this snapshot when they reconnect.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .protocol import ItemRef


def _ip_sort_key(ip: str) -> tuple:
    """Natural numeric ordering for IPv4 addresses.

    Lex-sorting `"192.168.1.10"` against `"192.168.1.2"` puts `.10`
    before `.2` — wrong for a UI list. Split octets and compare as
    ints. Anything not a dotted quad sorts to the end alphabetically.
    """
    try:
        parts = ip.split(".")
        if len(parts) == 4:
            return (0, tuple(int(p) for p in parts))
    except (ValueError, AttributeError):
        pass
    return (1, ip)


@dataclass
class ItemEvent:
    item: ItemRef
    sender: str = "self"  # "self" or another player's name
    received_at: float = field(default_factory=time.time)
    # What to display in the Switch's Cappy speech bubble. "" = suppress
    # (gameplay self-finds collapse to "" so AP→loopback doesn't pop a
    # bubble for an item we just picked up). HELLO replay reads this
    # field so a self-find stays silent across save loads / reconnects.
    cappy_from: str = ""


@dataclass
class CheckEvent:
    item: ItemRef
    checked_at: float = field(default_factory=time.time)


class BridgeState:
    """Thread-safe snapshot. Web tracker reads it; AP/Switch loops mutate it."""

    def __init__(self):
        self._lock = threading.RLock()
        self.ap_conn: str = "disconnected"
        self.switch_conn: str = "disconnected"
        self.seed: str = ""
        self.slot: str = ""
        self.received_items: list[ItemEvent] = []
        self.checked_locations: list[CheckEvent] = []
        self.captures_unlocked: set[str] = set()
        self.moons_received_by_kingdom: dict[str, int] = {}
        self.moons_checked_by_kingdom: dict[str, int] = {}
        # M6 phase D — per-kingdom PayShineNum from the Switch's save.
        # Authoritative source: SMO's save file, snapshotted on HELLO + on
        # every Odyssey toss via PaySnapshotMsg. Outstanding is DERIVED
        # from this (compute_outstanding: lifetime_received - pay), so a
        # crash-rolled-back save shrinks pay and outstanding rebounds
        # automatically on the next snapshot. None sentinel = "no snapshot
        # received yet this session"; bridge defers sending OutstandingMsg
        # until the first snapshot lands.
        self.pay_shine_num_by_kingdom: dict[str, int] | None = None
        self.last_messages: list[str] = []  # PrintJSON-style log (cap 200)
        self.death_count: int = 0  # M4 DeathLink: how many times Mario died
        # AP-classification moon coloring. Populated when AP's LocationInfo
        # reply lands (scouted at Connected) and replayed to the Switch on
        # every (re)connect via SwitchServer._on_hello. Key is SMO's
        # ShineInfo::shineId int; value is the palette index for
        # rs::setStageShineAnimFrame.
        self.shine_palette: dict[int, int] = {}
        # Dedup keyset for checked_locations. Snapshot replays emit synthetic
        # checks for everything in the save; without dedup the list would
        # grow on every reconnect. Key is the full ItemRef identity (canonical
        # OR raw fields, whichever the producer filled in).
        self._checked_keys: set[tuple] = set()
        # Snapshot accumulator. begin_snapshot resets it; chunks append; end
        # returns the raw entries for downstream dispatch. Single in-flight
        # snapshot — the TCP stream is serial, so no need for epoch keying.
        self._pending_snapshot_active: bool = False
        self._pending_snapshot_entries: list[dict] = []
        self._pending_snapshot_save_slot: int | None = None
        self.last_snapshot_save_slot: int | None = None
        # Multi-Switch registry. SwitchServer accepts N parallel connections
        # (one Switch and one Ryujinx; future co-op streaming) and the user
        # picks which one is bound to the AP slot via the Switches popup.
        # Keyed by device_id; entries are plain dicts (kivy-thread reads them
        # via get_switches and shouldn't depend on dataclass identity).
        # `_active_device_id` is the one currently forwarding telemetry to AP;
        # others are connected-but-idle (KickMsg(reason="inactive")).
        self._switches: dict[str, dict] = {}
        self._active_device_id: str | None = None

    # ---------- AP <-> internal ----------

    def set_ap_conn(self, conn: str) -> None:
        with self._lock:
            self.ap_conn = conn

    def set_switch_conn(self, conn: str) -> None:
        with self._lock:
            self.switch_conn = conn

    def add_received_item(self, evt: ItemEvent) -> None:
        with self._lock:
            self.received_items.append(evt)
            if evt.item.kind == "capture" and evt.item.cap:
                self.captures_unlocked.add(evt.item.cap)
            elif evt.item.kind == "moon" and evt.item.kingdom:
                # Effective moon credits, matching `KingdomMoons` rule weighting:
                # Multi-Moon is worth 3, Power Moon is worth 1. So the GUI's
                # `recv / need` comparison is apples-to-apples against the
                # exit threshold (which is also in effective moons).
                weight = 3 if evt.item.shine_id == "Multi-Moon" else 1
                self.moons_received_by_kingdom[evt.item.kingdom] = (
                    self.moons_received_by_kingdom.get(evt.item.kingdom, 0) + weight
                )

    def add_checked_location(self, evt: CheckEvent) -> bool:
        """Append a CheckEvent. Returns True if newly added, False if duplicate.

        Dedup uses the full ItemRef identity (canonical + raw fields). Snapshot
        replay paths rely on this — they emit synthetic checks for every owned
        shine on every reconnect, and we don't want `checked_locations` to grow
        unboundedly.
        """
        key = (
            evt.item.kind,
            evt.item.kingdom, evt.item.shine_id, evt.item.cap,
            evt.item.stage_name, evt.item.object_id, evt.item.shine_uid,
            evt.item.hack_name,
        )
        with self._lock:
            if key in self._checked_keys:
                return False
            self._checked_keys.add(key)
            self.checked_locations.append(evt)
            if evt.item.kind == "moon" and evt.item.kingdom:
                self.moons_checked_by_kingdom[evt.item.kingdom] = (
                    self.moons_checked_by_kingdom.get(evt.item.kingdom, 0) + 1
                )
            return True

    def add_log(self, text: str) -> None:
        with self._lock:
            self.last_messages.append(text)
            if len(self.last_messages) > 200:
                self.last_messages = self.last_messages[-200:]

    def bump_death_count(self) -> None:
        with self._lock:
            self.death_count += 1

    # ---------- M6 phase D — derived per-kingdom outstanding ----------

    def apply_pay_snapshot(self, totals: dict[str, int]) -> None:
        """Wholesale-replace pay_shine_num_by_kingdom from a PaySnapshotMsg.

        The Switch sends complete snapshots (every kingdom Mario has visited
        or has any pay-shine for), so a wholesale replace gives us a clean
        reading even if a kingdom's pay rolled BACK between snapshots (e.g.
        the deposit-then-crash recovery path). Caller is responsible for
        kingdom-name translation (Switch form → AP form) before calling.
        """
        with self._lock:
            self.pay_shine_num_by_kingdom = {
                str(k): max(0, int(v)) for k, v in totals.items()
            }

    def compute_outstanding(self) -> dict[str, int] | None:
        """Derive per-kingdom outstanding = lifetime_received − PayShineNum.

        Returns None until the first PaySnapshotMsg arrives — caller must
        not push OutstandingMsg before then (the Switch would receive a
        spurious "outstanding = lifetime − 0" the moment SMOClient connects
        before SMO is even on a save file). Once a snapshot lands, every
        kingdom in either lifetime_received or pay_shine_num contributes
        an entry; defensive clamp at zero in case PayShineNum exceeds
        lifetime (vanilla SMO moons not credited to AP can bump PayShineNum
        past what we've received).
        """
        with self._lock:
            if self.pay_shine_num_by_kingdom is None:
                return None
            out: dict[str, int] = {}
            for k, lifetime in self.moons_received_by_kingdom.items():
                pay = self.pay_shine_num_by_kingdom.get(k, 0)
                out[k] = max(0, lifetime - pay)
            for k in self.pay_shine_num_by_kingdom:
                out.setdefault(k, 0)
            return out

    def get_pay_shine_num(self) -> dict[str, int] | None:
        """Return a defensive copy of the last received PayShineNum snapshot."""
        with self._lock:
            if self.pay_shine_num_by_kingdom is None:
                return None
            return dict(self.pay_shine_num_by_kingdom)

    def get_kingdom_lifetime_received(self, kingdom: str) -> int:
        """Lifetime sum of moon items received for `kingdom`, with
        Multi-Moon weighted as 3 and Power Moon as 1 (matching
        `KingdomMoons` in hooks/Rules.py).

        Used as one of two inputs to compute_outstanding (the other being
        PayShineNum from PaySnapshotMsg). Also read by the Kivy GUI for the
        per-kingdom recv/need display. Data is populated by add_received_item,
        which runs for every item in the AP items_received history (so it
        survives bridge restarts without explicit persistence — the next
        Connected/ReceivedItems rebuilds it from the authoritative
        server-side list).

        Note: the M7 Path A kingdom-order gate USED to consume this signal
        via OutstandingMsg lifetime scalars; that gate moved to a Switch-side
        visited bit + current-kingdom OR-check that needs no bridge state.
        """
        with self._lock:
            return int(self.moons_received_by_kingdom.get(kingdom, 0))

    def set_shine_palette(self, entries: dict[int, int]) -> None:
        """Replace the (shine_uid -> palette) table with the given entries.

        Called once per AP `LocationInfo` reply. Non-zero values overwrite
        existing entries; zero is treated as a "no override" sentinel and
        also stored so reconnect-replay reflects the same intent.
        """
        with self._lock:
            self.shine_palette = dict(entries)

    def all_shine_palette(self) -> dict[int, int]:
        with self._lock:
            return dict(self.shine_palette)

    # ---------- Multi-Switch registry ----------

    def register_switch(
        self,
        device_id: str,
        peer_ip: str,
        mod_ver: str = "",
        smo_ver: str = "",
    ) -> None:
        """Record a Switch connection. Idempotent: a same-id reconnect
        updates peer_ip + last_seen rather than duplicating the entry.

        Called from SwitchServer after HELLO is parsed. The caller is
        responsible for collision-handling (appending a suffix to
        device_id) before registering.
        """
        with self._lock:
            self._switches[device_id] = {
                "device_id": device_id,
                "peer_ip": peer_ip,
                "mod_ver": mod_ver,
                "smo_ver": smo_ver,
                "last_seen": time.time(),
            }

    def touch_switch(self, device_id: str) -> None:
        with self._lock:
            entry = self._switches.get(device_id)
            if entry is not None:
                entry["last_seen"] = time.time()

    def unregister_switch(self, device_id: str) -> None:
        with self._lock:
            self._switches.pop(device_id, None)
            if self._active_device_id == device_id:
                self._active_device_id = None

    def set_active_switch(self, device_id: str | None) -> None:
        with self._lock:
            if device_id is not None and device_id not in self._switches:
                return
            self._active_device_id = device_id

    def get_active_switch(self) -> str | None:
        with self._lock:
            return self._active_device_id

    def get_switches(self) -> list[dict]:
        """Snapshot of connected Switches, sorted stably by peer IP.

        IP-sorted (NOT active-first) so a user-driven active-toggle
        doesn't reorder rows — the swap is visible because the active
        marker moves between rows, not because the rows jump positions
        around. Each entry carries an `active: bool` flag the UI uses
        to color and label the row.
        """
        with self._lock:
            active = self._active_device_id
            entries = [
                {**v, "active": (k == active)}
                for k, v in self._switches.items()
            ]
        entries.sort(key=lambda e: _ip_sort_key(e.get("peer_ip", "")))
        return entries

    # ---------- Snapshot for web tracker / replay ----------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ap_conn": self.ap_conn,
                "switch_conn": self.switch_conn,
                "seed": self.seed,
                "slot": self.slot,
                "received_count": len(self.received_items),
                "checked_count": len(self.checked_locations),
                "death_count": self.death_count,
                "captures_unlocked": sorted(self.captures_unlocked),
                "moons_received_by_kingdom": dict(self.moons_received_by_kingdom),
                "moons_checked_by_kingdom": dict(self.moons_checked_by_kingdom),
                "pay_shine_num_by_kingdom": (
                    dict(self.pay_shine_num_by_kingdom)
                    if self.pay_shine_num_by_kingdom is not None
                    else None
                ),
                "recent_items": [
                    {
                        "kind": e.item.kind,
                        "kingdom": e.item.kingdom,
                        "shine_id": e.item.shine_id,
                        "cap": e.item.cap,
                        "name": e.item.name,
                        "from": e.sender,
                        "at": e.received_at,
                    }
                    for e in self.received_items[-50:]
                ],
                "recent_messages": list(self.last_messages[-50:]),
            }

    def all_received_items(self) -> list[ItemEvent]:
        with self._lock:
            return list(self.received_items)

    def all_checked_locations(self) -> list[CheckEvent]:
        with self._lock:
            return list(self.checked_locations)

    # ---------- Snapshot accumulator (M4.5) ----------

    def begin_snapshot(self, save_slot: int | None) -> None:
        """Open a fresh snapshot accumulator, discarding any in-flight one.

        State is per-connection: the TCP stream is single-Switch, single-thread
        on the bridge end, so begin/chunk/end always arrive in order. If the
        Switch reconnects mid-snapshot the connection drops first and the new
        connection starts a fresh snapshot anyway.
        """
        with self._lock:
            self._pending_snapshot_active = True
            self._pending_snapshot_entries = []
            self._pending_snapshot_save_slot = save_slot

    def add_snapshot_chunk_shines(self, stage_name: str, shines: list[dict]) -> None:
        """Append per-stage shine entries from a StateChunkMsg."""
        with self._lock:
            if not self._pending_snapshot_active:
                return
            for s in shines:
                if not isinstance(s, dict):
                    continue
                self._pending_snapshot_entries.append({
                    "kind": "moon",
                    "stage_name": stage_name,
                    "object_id": s.get("object_id"),
                    "shine_uid": s.get("shine_uid"),
                })

    def add_snapshot_chunk_meta(
        self,
        captures: list[str] | None,
        goal_reached: bool | None,
    ) -> None:
        """Append cross-stage `_meta` chunk entries (captures + goal)."""
        with self._lock:
            if not self._pending_snapshot_active:
                return
            for hack in (captures or []):
                if isinstance(hack, str) and hack:
                    self._pending_snapshot_entries.append({
                        "kind": "capture",
                        "hack_name": hack,
                    })
            # goal_reached is dispatched separately by switch_server, not
            # accumulated as an entry. Stash it on a separate flag for the
            # caller to read on end_snapshot.
            if goal_reached is not None:
                self._pending_snapshot_goal = bool(goal_reached)

    def end_snapshot(self) -> tuple[list[dict], bool]:
        """Finalize: returns (entries, goal_reached_flag) and resets buffer."""
        with self._lock:
            entries = list(self._pending_snapshot_entries)
            goal = bool(getattr(self, "_pending_snapshot_goal", False))
            self.last_snapshot_save_slot = self._pending_snapshot_save_slot
            self._pending_snapshot_active = False
            self._pending_snapshot_entries = []
            self._pending_snapshot_save_slot = None
            self._pending_snapshot_goal = False
            return entries, goal
