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
    KINGDOM = "kingdom"
    SHOP = "shop"
    OTHER = "other"


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
    """A location was just checked in-game."""
    t: str = "check"
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    slot: int | None = None  # for shop slots


@dataclass
class StatusMsg:
    t: str = "status"
    kingdom: str | None = None
    scenario: int | None = None
    moons_collected: int | None = None


@dataclass
class GoalMsg:
    t: str = "goal"


@dataclass
class PingMsg:
    t: str = "ping"
    ts_ms: int = 0


@dataclass
class LogMsg:
    t: str = "log"
    level: str = "info"
    msg: str = ""


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
    err: str | None = None


@dataclass
class ItemRef:
    """Minimum info to locate an item or check on the Switch."""
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    slot: int | None = None
    name: str | None = None  # for OTHER kinds where we just have a label


@dataclass
class CheckedReplayMsg:
    t: str = "checked_replay"
    ids: list[ItemRef] = field(default_factory=list)


@dataclass
class ItemMsg:
    """Item granted by AP to be applied on Switch."""
    t: str = "item"
    kind: str = ItemKind.MOON.value
    kingdom: str | None = None
    shine_id: str | None = None
    cap: str | None = None
    slot: int | None = None
    name: str | None = None
    from_: str = "self"

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return _strip_none(d)


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
