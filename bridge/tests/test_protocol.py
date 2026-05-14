"""Wire-protocol unit tests."""

from __future__ import annotations

import json

import pytest

from smo_ap_bridge import protocol
from smo_ap_bridge.protocol import (
    CheckMsg,
    HelloAckMsg,
    HelloMsg,
    ItemMsg,
    ItemRef,
    ItemKind,
    PingMsg,
    PongMsg,
    iter_lines,
)


def test_hello_round_trip():
    msg = HelloMsg(mod_ver="0.1.0+abc", smo_ver="1.0.0", cap_table_hash="sha1:deadbeef")
    raw = protocol.encode(msg)
    assert raw.endswith(b"\n")
    parsed = protocol.decode(raw.rstrip(b"\n"))
    assert parsed["t"] == "hello"
    assert parsed["mod_ver"] == "0.1.0+abc"
    assert parsed["smo_ver"] == "1.0.0"


def test_check_strips_none_fields():
    msg = CheckMsg(kind=ItemKind.MOON.value, kingdom="Cascade", shine_id="DinoNest")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert "cap" not in parsed
    assert "slot" not in parsed
    assert parsed == {"t": "check", "kind": "moon", "kingdom": "Cascade", "shine_id": "DinoNest"}


def test_item_msg_renames_from():
    msg = ItemMsg(kind="capture", cap="Frog", from_="Bob")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["from"] == "Bob"
    assert "from_" not in parsed


def test_hello_ack_optional_fields():
    msg = HelloAckMsg(ok=True, seed="X4F2", slot="Mario")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["ok"] is True
    assert parsed["seed"] == "X4F2"
    assert "err" not in parsed  # None should be stripped


def test_iter_lines_basic():
    buf = bytearray(b'{"t":"ping","ts_ms":1}\n{"t":"pong","ts_ms":2}\n{"t":"ping"')
    lines = list(iter_lines(buf))
    assert len(lines) == 2
    assert json.loads(lines[0])["t"] == "ping"
    assert json.loads(lines[1])["t"] == "pong"
    # Incomplete line remains in buffer.
    assert buf == bytearray(b'{"t":"ping"')


def test_iter_lines_drops_oversized_resync():
    huge = b"x" * (protocol.MAX_LINE_BYTES + 100)
    buf = bytearray(huge + b"\n" + b'{"t":"ping"}\n')
    lines = list(iter_lines(buf))
    assert len(lines) == 1
    assert json.loads(lines[0])["t"] == "ping"


def test_iter_lines_clears_corrupt_no_newline():
    """If we have > MAX_LINE_BYTES with no newline at all, resync by clearing."""
    buf = bytearray(b"x" * (protocol.MAX_LINE_BYTES + 100))
    list(iter_lines(buf))  # exhaust
    assert len(buf) == 0


def test_encode_max_line_enforced():
    msg = HelloMsg(mod_ver="x" * (protocol.MAX_LINE_BYTES + 1))
    with pytest.raises(ValueError):
        protocol.encode(msg)


def test_pong_round_trip():
    msg = PongMsg(ts_ms=12345)
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed == {"t": "pong", "ts_ms": 12345}


def test_ping_default_ts_zero():
    msg = PingMsg()
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["ts_ms"] == 0
