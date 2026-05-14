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


@dataclass
class ItemEvent:
    item: ItemRef
    sender: str = "self"  # "self" or another player's name
    received_at: float = field(default_factory=time.time)


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
        self.kingdoms_unlocked: set[str] = set()
        self.moons_received_by_kingdom: dict[str, int] = {}
        self.moons_checked_by_kingdom: dict[str, int] = {}
        self.last_messages: list[str] = []  # PrintJSON-style log (cap 200)

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
            elif evt.item.kind == "kingdom" and evt.item.kingdom:
                self.kingdoms_unlocked.add(evt.item.kingdom)
            elif evt.item.kind == "moon" and evt.item.kingdom:
                self.moons_received_by_kingdom[evt.item.kingdom] = (
                    self.moons_received_by_kingdom.get(evt.item.kingdom, 0) + 1
                )

    def add_checked_location(self, evt: CheckEvent) -> None:
        with self._lock:
            self.checked_locations.append(evt)
            if evt.item.kind == "moon" and evt.item.kingdom:
                self.moons_checked_by_kingdom[evt.item.kingdom] = (
                    self.moons_checked_by_kingdom.get(evt.item.kingdom, 0) + 1
                )

    def add_log(self, text: str) -> None:
        with self._lock:
            self.last_messages.append(text)
            if len(self.last_messages) > 200:
                self.last_messages = self.last_messages[-200:]

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
                "captures_unlocked": sorted(self.captures_unlocked),
                "kingdoms_unlocked": sorted(self.kingdoms_unlocked),
                "moons_received_by_kingdom": dict(self.moons_received_by_kingdom),
                "moons_checked_by_kingdom": dict(self.moons_checked_by_kingdom),
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
