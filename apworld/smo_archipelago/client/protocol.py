"""Wire format for the Switch <-> Bridge channel.

Single persistent TCP connection. Each message is one line of UTF-8 JSON
terminated by '\n'. Field 't' is the message type. All ids/strings are
canonical (sourced from the apworld's data/items.json) so the Switch can do
a static lookup without holding the AP datapackage.

Max line length: 8 KiB. Longer lines are rejected and the parser resyncs to
the next '\n'.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable

MAX_LINE_BYTES = 8 * 1024


class ItemKind(str, Enum):
    MOON = "moon"
    CAPTURE = "capture"
    OTHER = "other"


class Classification(str, Enum):
    """Wire form of Archipelago's ItemClassification flag bits.

    `as_flag()` in Archipelago's BaseClasses keeps only the low 3 bits:
        progression = 0b001, useful = 0b010, trap = 0b100, filler = 0b000.
    Bits can combine; we collapse to the dominant class:
    progression > useful > trap > filler.
    """
    PROGRESSION = "progression"
    USEFUL = "useful"
    TRAP = "trap"
    FILLER = "filler"


def classification_from_flags(flags: int) -> Classification:
    """Collapse an AP flags bitmask to a single dominant classification."""
    if flags & 0b001:
        return Classification.PROGRESSION
    if flags & 0b010:
        return Classification.USEFUL
    if flags & 0b100:
        return Classification.TRAP
    return Classification.FILLER


# AP item/location names use "Bowser's Kingdom" (the only possessive form
# in the apworld); the Switch's kKingdoms[] table uses bare short names
# ("Bowser"). Translate at the wire boundary so the bridge's internal model
# can stay in AP form (matches AP location strings without translation) and
# the Switch's kingdomBitFor() lookups still resolve.
_AP_TO_SWITCH_KINGDOM = {"Bowser's": "Bowser"}
_SWITCH_TO_AP_KINGDOM = {v: k for k, v in _AP_TO_SWITCH_KINGDOM.items()}


def kingdom_ap_to_switch(kingdom: str | None) -> str | None:
    if kingdom is None:
        return None
    return _AP_TO_SWITCH_KINGDOM.get(kingdom, kingdom)


def kingdom_switch_to_ap(kingdom: str | None) -> str | None:
    if kingdom is None:
        return None
    return _SWITCH_TO_AP_KINGDOM.get(kingdom, kingdom)


# ---------------------------------------------------------------------------
# Switch -> Bridge
# ---------------------------------------------------------------------------

@dataclass
class HelloMsg:
    t: str = "hello"
    mod_ver: str = ""
    smo_ver: str = ""
    cap_table_hash: str = ""


@dataclass
class CheckMsg:
    """A location was just checked in-game.

    Either the legacy resolved fields (kingdom + shine_id / cap) OR the M4 raw
    SMO identifiers (stage_name + object_id / hack_name) may be set. The
    bridge prefers raw fields and resolves them via shine_map / capture_map.
    """
    t: str = "check"
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    # M4 raw identifiers (Switch sends, bridge resolves)
    stage_name: str | None = None   # moons: ShineInfo::stageName
    object_id: str | None = None    # moons: ShineInfo::objectId
    shine_uid: int | None = None    # moons: ShineInfo::shineId
    hack_name: str | None = None    # captures: PlayerHackKeeper::getCurrentHackName
    # M6 phase A.5: monotonic-per-session sequence id. The bridge echoes it
    # back in MoonLabelMsg so the Switch's cutscene-label hook can tell
    # whether the pending label was meant for the moon it's about to display
    # (i.e. it's still fresh). None / omitted = legacy switch build that
    # doesn't support cutscene labels; bridge suppresses MoonLabelMsg in
    # that case.
    seq: int | None = None


@dataclass
class StatusMsg:
    t: str = "status"
    kingdom: str | None = None
    scenario: int | None = None
    moons_collected: int | None = None
    stage_name: str | None = None  # M4: raw stage at the flag flip


@dataclass
class GoalMsg:
    t: str = "goal"


@dataclass
class DeathMsg:
    """Mario died on the Switch. Bridge (when DeathLink is enabled) converts
    this into an AP Bounce so other DeathLink-tagged slots take damage too."""
    t: str = "death"
    ts_ms: int = 0


@dataclass
class PingMsg:
    t: str = "ping"
    ts_ms: int = 0


@dataclass
class LogMsg:
    t: str = "log"
    level: str = "info"
    msg: str = ""


# State snapshot. Sent by the Switch on every (re)connect (right after HELLO).
# Three kinds of message in sequence: one StateBeginMsg, N StateChunkMsg
# (per-stage shines + a trailing "_meta" chunk for cross-stage data), one
# StateEndMsg. The bridge accumulates them and on StateEndMsg dispatches
# each entry through the same `check` path live moon-get hooks use, so the
# AP server learns about anything the Switch collected during a disconnect.
#
# Carries RAW SMO identifiers (stage_name, object_id, shine_uid, hack_name)
# matching M4's check semantics; the bridge resolves them via shine_map.json
# / capture_map.json. Re-sending the same snapshot is a no-op because the
# bridge dedupes at AP-id level (`_ctx.locations_checked`).
#
# Triggers on the Switch side:
#   - Right after sendHello() on every (re)connect
#   - SaveLoadHook calls requestRehello() which closes/reopens the TCP
#     connection, which re-runs sendHello + the snapshot

@dataclass
class StateBeginMsg:
    t: str = "state_begin"
    mod_ver: str = ""
    save_slot: int | None = None  # informational; bridge does NOT fence on it


@dataclass
class StateChunkMsg:
    """One stage's worth of owned shines, OR the cross-stage `_meta` chunk.

    Per-stage chunk: `stage_name` is the SMO stage key (e.g. "CapWorldHomeStage"),
    `shines` is a list of {object_id, shine_uid} dicts.

    `_meta` chunk (stage_name == "_meta"): populates `captures` (raw hack_name
    strings) and `goal_reached`. The bridge is the source of truth for kingdom
    unlocks (received items), so we don't echo them back here.
    """
    t: str = "state_chunk"
    stage_name: str = ""
    shines: list[dict] | None = None  # [{"object_id": "...", "shine_uid": N}]
    captures: list[str] | None = None  # raw hack_names; only on `_meta` chunk
    goal_reached: bool | None = None   # only on `_meta` chunk


@dataclass
class StateEndMsg:
    t: str = "state_end"


@dataclass
class DepositMsg:
    """M6 phase D — Switch reports a moon deposit (per-toss or pay-all).

    Sent by AddPayShineHook / AddPayShineAllHook on the mod side every time
    Mario hands moons to an Odyssey. `seq` is a monotonic per-Switch-session
    counter used by the bridge for idempotent dedup of reconnect replays;
    `kingdom` is the kingdom Mario was in at the time of the deposit (debit
    applies to that kingdom's outstanding balance only); `amount` is the
    number of moons actually debited (clamped at the AP-credit balance, so
    `amount` may be less than the vanilla addPayShine count if Mario also
    had natural moons in the pool).
    """
    t: str = "deposit"
    seq: int = 0
    kingdom: str = ""
    amount: int = 0


# ---------------------------------------------------------------------------
# Bridge -> Switch
# ---------------------------------------------------------------------------

@dataclass
class HelloAckMsg:
    t: str = "hello_ack"
    ok: bool = True
    seed: str = ""
    slot: str = ""
    cap_table_hash: str = ""
    # Bridge-owned DeathLink toggle. The Switch mod ships the apply path
    # unconditionally but only acts on inbound kill messages when this flag is
    # set here, so the user enables/disables DeathLink in bridge config rather
    # than rebuilding the mod. Older Switch builds (M4-era) ignore the field.
    deathlink_enabled: bool = False
    err: str | None = None


@dataclass
class ItemRef:
    """Minimum info to locate an item or check on the Switch.

    Carries both canonical (kingdom/shine_id/cap) AND raw M4 identifiers
    (stage_name/object_id/shine_uid/hack_name). Raw identifiers are filled
    in when the source was a raw-ID `check` (or a snapshot entry); they're
    used by `BridgeState` to dedupe CheckEvents across snapshot replays
    that don't carry canonical fields.

    NOTE: raw fields are STRIPPED when this ItemRef is serialized into a
    CheckedReplayMsg (see `to_replay_dict()`), because the C++ parser at
    `switch-mod/src/ap/ApProtocol.cpp:parseItemRefBody` rejects unknown
    fields. Internal use only — never reach the wire.
    """
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    name: str | None = None  # for OTHER kinds where we just have a label
    # M4 raw identifiers (preserved for dedup; not sent in CheckedReplay)
    stage_name: str | None = None
    object_id: str | None = None
    shine_uid: int | None = None
    hack_name: str | None = None
    # AP classification carried forward into ItemMsg; not in CheckedReplay
    # since the C++ ItemRef parser rejects unknown fields.
    classification: str | None = None

    def to_replay_dict(self) -> dict[str, Any]:
        """Wire payload for inclusion in a CheckedReplayMsg.

        Strips the raw M4 fields because the C++ ItemRef parser
        (`parseItemRefBody`) rejects unknown keys.
        """
        return _strip_none({
            "kind": self.kind,
            "kingdom": kingdom_ap_to_switch(self.kingdom),
            "shine_id": self.shine_id,
            "cap": self.cap,
            "name": self.name,
        })


@dataclass
class CheckedReplayMsg:
    t: str = "checked_replay"
    ids: list[ItemRef] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "ids": [ref.to_replay_dict() for ref in self.ids],
        }


@dataclass
class ItemMsg:
    """Item granted by AP to be applied on Switch."""
    t: str = "item"
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    name: str | None = None
    from_: str = "self"
    # M6 phase B: for capture items, the bridge resolves cap -> hack_name via
    # the reverse CaptureMap and ships hack_name to the Switch. Mod feeds it
    # straight into GameDataFunction::addHackDictionary. None when no map
    # entry exists; the mod logs and drops.
    hack_name: str | None = None
    # AP item classification (progression / useful / trap / filler). None when
    # unknown (e.g. older bridge talking to never-scouted REPL grant). Switch
    # mod uses this for log lines + post-collection effects; pre-collection
    # color uses ShineScoutsMsg instead.
    classification: str | None = None

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        d["kingdom"] = kingdom_ap_to_switch(d.get("kingdom"))
        return _strip_none(d)


@dataclass
class ShineScoutsMsg:
    """Pre-collection palette assignment for each moon location.

    Sent Bridge -> Switch after AP `Connected` + `LocationInfo` reply lands.
    May arrive in multiple chunks; the Switch merges entries by `shine_uid`
    overwrite (each (shine_uid, palette) is a complete fact).

    Entry shape: `{"shine_uid": int, "palette": int}`. Palette is a SMO per-
    stage shine animation frame index (range varies per stage, typically
    0..15); 0 means "leave stage default".
    """
    t: str = "shine_scouts"
    entries: list[dict] = field(default_factory=list)


@dataclass
class PrintMsg:
    t: str = "print"
    text: str = ""


@dataclass
class ApStateMsg:
    t: str = "ap_state"
    conn: str = "disconnected"  # disconnected | connecting | authed | ready


@dataclass
class PongMsg:
    t: str = "pong"
    ts_ms: int = 0


@dataclass
class ErrMsg:
    t: str = "err"
    code: str = ""
    ctx: str = ""


@dataclass
class KillMsg:
    """DeathLink bounce forwarded by the bridge: another slot died, so the
    Switch should kill Mario. M4 only logs this on the Switch side; actual
    killing lands in M6 with the player-state-write machinery."""
    t: str = "kill"
    source: str = ""
    cause: str = ""


@dataclass
class DepositAckMsg:
    """M6 phase D — bridge acknowledges a DepositMsg.

    Sent unconditionally for every received DepositMsg (idempotent — a re-ack
    of an already-processed seq is a no-op on the Switch side). The Switch
    uses this to drop the matching entry from its pending-deposit ring so
    the next reconnect won't replay it.
    """
    t: str = "deposit_ack"
    seq: int = 0


@dataclass
class OutstandingEntry:
    """One per-kingdom balance row inside an OutstandingMsg.

    `kingdom` is the apworld-canonical kingdom name (matching kKingdoms[]
    on the mod side). `count` is the current AP-credit balance: positive
    integer.
    """
    kingdom: str = ""
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kingdom": kingdom_ap_to_switch(self.kingdom) or "",
            "count": self.count,
        }


@dataclass
class OutstandingMsg:
    """M6 phase D — bridge-authoritative per-kingdom balance.

    Sent immediately after HelloAckMsg on every Switch reconnect, and again
    every time the bridge's `outstanding_by_kingdom` mutates (grant arrival
    or deposit applied). The Switch overwrites `ap_moons_kingdom[bit]` for
    each kingdom present in `entries`; kingdoms missing from the message are
    left untouched (lets the bridge omit zero entries if it wants to —
    today it sends all known kingdoms for unambiguous full-state replace).
    """
    t: str = "outstanding"
    entries: list[OutstandingEntry] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass
class MoonLabelMsg:
    """M6 phase A.5 — Channel A: replace the moon-get cutscene's pane text
    with AP-aware text. Bridge sends this in the same TCP push as the
    handshake reply to a CheckMsg so it arrives before the cutscene starts.

    `text` is pre-truncated by the bridge to ≤30 bytes UTF-8 (the Switch
    PendingMoonLabel buffer is 32 bytes including null terminator). Switch
    re-validates length defensively.

    `seq` echoes the CheckMsg.seq it responds to so the Switch knows the
    label is for *this* moon (not a leftover from a previous collect that
    arrived late, e.g. because of a multi-moon race).

    `valid_for_ms` is a Switch-relative TTL counted from receipt; if the
    cutscene doesn't fire within this window, the label is discarded and
    the cutscene shows vanilla "Power Moon". Using a relative TTL avoids
    PC↔Switch clock skew (Switch RTC is often well behind PC NTP)."""
    t: str = "moon_label"
    text: str = ""
    seq: int = 0
    valid_for_ms: int = 4000


# ---------------------------------------------------------------------------
# (de)serialization helpers
# ---------------------------------------------------------------------------

def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def encode(msg: Any) -> bytes:
    """Serialize a dataclass message to a single line of bytes including '\n'."""
    if hasattr(msg, "to_wire"):
        d = msg.to_wire()
    else:
        d = _strip_none(asdict(msg))
    line = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ValueError(f"encoded message exceeds {MAX_LINE_BYTES} bytes")
    return (line + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict[str, Any]:
    """Decode one line into a dict. Caller dispatches on 't'."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    return json.loads(line)


def iter_lines(buffer: bytearray) -> Iterable[bytes]:
    """Yield complete '\n'-terminated lines from buffer; consume them in place.

    Lines longer than MAX_LINE_BYTES are skipped (resync to next '\n').
    Returns when buffer has no more complete lines.
    """
    while True:
        nl = buffer.find(b"\n")
        if nl < 0:
            if len(buffer) > MAX_LINE_BYTES:
                # No newline in 8KB+ of data — drop everything; corrupt stream.
                buffer.clear()
            return
        line = bytes(buffer[:nl])
        del buffer[: nl + 1]
        if len(line) > MAX_LINE_BYTES:
            continue  # skip oversized line, resync
        if line.strip():
            yield line
