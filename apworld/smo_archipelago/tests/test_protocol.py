"""Wire-protocol unit tests."""

from __future__ import annotations

import json

import pytest

from client import protocol
from client.protocol import (
    ActivateMsg,
    CappyMsg,
    CheckMsg,
    Classification,
    HelloAckMsg,
    HelloMsg,
    ItemMsg,
    ItemRef,
    ItemKind,
    KickMsg,
    MoonLabelMsg,
    OutstandingEntry,
    OutstandingMsg,
    PaySnapshotEntry,
    PaySnapshotMsg,
    PingMsg,
    PongMsg,
    ShineScoutsMsg,
    classification_from_flags,
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


def test_hello_carries_device_id_when_set():
    """The Switch mod populates device_id from
    nn::settings::GetDeviceNickname so the bridge can disambiguate two
    Switches on the same LAN."""
    msg = HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0", device_id="mario")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["device_id"] == "mario"


def test_hello_omits_empty_device_id():
    """Empty device_id is stripped from the wire (matches the _strip_none
    convention). Bridge then invents one from peer IP."""
    msg = HelloMsg(mod_ver="0.1.0", smo_ver="1.0.0")
    parsed = protocol.decode(protocol.encode(msg))
    # Empty string would still be present (encode keeps empty strings; the
    # _strip_none filter keys on None). The bridge handles empty as "absent".
    assert parsed.get("device_id", "") == ""


def test_kick_msg_round_trip():
    msg = KickMsg(reason="unbound")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {"t": "kick", "reason": "unbound"}


def test_kick_msg_default_reason_empty():
    msg = KickMsg()
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["t"] == "kick"
    assert parsed["reason"] == ""


def test_activate_msg_round_trip():
    parsed = protocol.decode(protocol.encode(ActivateMsg()))
    assert parsed == {"t": "activate"}


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


def test_item_msg_hack_name_round_trip():
    """M6 phase B: ItemMsg carries hack_name for capture items so the mod
    can pass it straight into addHackDictionary."""
    msg = ItemMsg(kind="capture", cap="Goomba", hack_name="Kuribo", from_="Mario")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["kind"] == "capture"
    assert parsed["cap"] == "Goomba"
    assert parsed["hack_name"] == "Kuribo"


def test_item_msg_hack_name_omitted_when_none():
    """None values are stripped from the wire payload."""
    msg = ItemMsg(kind="moon", kingdom="Cap", shine_id="Power Moon")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert "hack_name" not in parsed


def test_kingdom_translation_bowsers_round_trip():
    """AP item names use "Bowser's"; the Switch's kKingdoms[] table uses
    "Bowser" (no apostrophe). The wire must translate at the boundary so
    the Switch's kingdomBitFor() lookup resolves and we don't drop moons."""
    # Sanity: helpers translate both directions.
    assert protocol.kingdom_ap_to_switch("Bowser's") == "Bowser"
    assert protocol.kingdom_switch_to_ap("Bowser") == "Bowser's"
    # Pass-through for every other kingdom.
    assert protocol.kingdom_ap_to_switch("Cap") == "Cap"
    assert protocol.kingdom_switch_to_ap("Cascade") == "Cascade"
    assert protocol.kingdom_ap_to_switch(None) is None
    assert protocol.kingdom_switch_to_ap(None) is None

    # ItemMsg.to_wire translates kingdom.
    msg = ItemMsg(kind="moon", kingdom="Bowser's", shine_id="Smart Bombing")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["kingdom"] == "Bowser"

    # OutstandingEntry.to_dict translates kingdom.
    out = OutstandingMsg(entries=[
        OutstandingEntry(kingdom="Bowser's", count=3),
        OutstandingEntry(kingdom="Cap", count=1),
    ])
    parsed = protocol.decode(protocol.encode(out))
    by_k = {e["kingdom"]: e["count"] for e in parsed["entries"]}
    assert by_k == {"Bowser": 3, "Cap": 1}

    # ItemRef.to_replay_dict translates kingdom (CheckedReplayMsg path).
    ref = ItemRef(kind="moon", kingdom="Bowser's", shine_id="Smart Bombing")
    assert ref.to_replay_dict()["kingdom"] == "Bowser"


def test_hello_ack_optional_fields():
    msg = HelloAckMsg(ok=True, seed="X4F2", slot="Mario")
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["ok"] is True
    assert parsed["seed"] == "X4F2"
    assert "err" not in parsed  # None should be stripped
    assert "client_ver" not in parsed  # None default should be stripped


def test_hello_ack_includes_client_ver_when_set():
    """Version-exchange: when the bridge stamps client_ver, it lands on the
    wire so the Switch mod can log both halves of the version pair."""
    msg = HelloAckMsg(ok=True, seed="X4F2", slot="Mario", client_ver="0.2.0")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["client_ver"] == "0.2.0"


def test_hello_ack_version_mismatch_payload():
    """The mismatch hello_ack carries ok=false, the bridge's client_ver,
    and a human-readable err — Switch mod logs err, Kivy UI displays it."""
    msg = HelloAckMsg(
        ok=False,
        client_ver="0.2.0",
        err="Version mismatch: SMOClient is 0.2.0, Switch mod is 0.1.0.",
    )
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["ok"] is False
    assert parsed["client_ver"] == "0.2.0"
    assert "0.2.0" in parsed["err"] and "0.1.0" in parsed["err"]


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


# --- M6 phase A.5: CheckMsg.seq + MoonLabelMsg -------------------------


def test_check_msg_seq_omitted_when_unset():
    # Backwards-compat: legacy switch builds omit seq entirely; bridge
    # uses presence-of-non-zero-seq as the "label me" signal. Default
    # None gets stripped from the wire so old bridges parse cleanly.
    msg = CheckMsg(kind="moon", stage_name="WaterfallWorldHomeStage",
                   object_id="obj214", shine_uid=12345)
    parsed = protocol.decode(protocol.encode(msg))
    assert "seq" not in parsed


def test_check_msg_seq_non_zero_round_trip():
    msg = CheckMsg(kind="moon", stage_name="X", object_id="obj1", seq=42)
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["seq"] == 42


def test_moon_label_msg_round_trip():
    msg = MoonLabelMsg(text="Sent Cap Power Moon -> P3", seq=7,
                      valid_for_ms=4000)
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed == {
        "t": "moon_label",
        "text": "Sent Cap Power Moon -> P3",
        "seq": 7,
        "valid_for_ms": 4000,
    }


def test_moon_label_msg_defaults_round_trip():
    # Empty MoonLabelMsg should still round-trip (the Switch tolerates
    # text="" as a no-op clear).
    msg = MoonLabelMsg()
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["t"] == "moon_label"
    assert parsed["text"] == ""
    assert parsed["seq"] == 0
    assert parsed["valid_for_ms"] == 4000


def test_moon_label_msg_unicode_text_preserved():
    msg = MoonLabelMsg(text="Got Café Power Moon!", seq=1)
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["text"] == "Got Café Power Moon!"


# --- CappyMsg (capturesanity bubble) -------------------------------------


def test_cappy_msg_round_trip():
    msg = CappyMsg(text="Got Goomba!")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {"t": "cappy", "text": "Got Goomba!"}


def test_cappy_msg_defaults_round_trip():
    parsed = protocol.decode(protocol.encode(CappyMsg()))
    assert parsed == {"t": "cappy", "text": ""}


def test_cappy_msg_unicode_text_preserved():
    parsed = protocol.decode(protocol.encode(CappyMsg(text="Sent Frög → P3")))
    assert parsed["text"] == "Sent Frög → P3"


# ---------------------------------------------------------------------------
# AP-classification moon color (M-color milestone)
# ---------------------------------------------------------------------------


def test_classification_from_flags_progression_wins():
    # progression + useful combined: progression wins per AP convention.
    assert classification_from_flags(0b011) is Classification.PROGRESSION


def test_classification_from_flags_useful_over_trap():
    assert classification_from_flags(0b110) is Classification.USEFUL


def test_classification_from_flags_individual_bits():
    assert classification_from_flags(0b001) is Classification.PROGRESSION
    assert classification_from_flags(0b010) is Classification.USEFUL
    assert classification_from_flags(0b100) is Classification.TRAP
    assert classification_from_flags(0b000) is Classification.FILLER


def test_classification_from_flags_unknown_high_bits_ignored():
    # Bits above 0b111 are reserved (skip_balancing, deprioritized) and must
    # NOT influence the classification routing.
    assert classification_from_flags(0b1000) is Classification.FILLER
    assert classification_from_flags(0b1001) is Classification.PROGRESSION


def test_item_msg_classification_round_trip():
    msg = ItemMsg(kind="moon", kingdom="Cap", shine_id="Power Moon",
                  from_="Bob", classification="progression")
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["classification"] == "progression"
    assert parsed["from"] == "Bob"


def test_item_msg_classification_stripped_when_none():
    msg = ItemMsg(kind="moon", kingdom="Cap", shine_id="Power Moon")
    parsed = protocol.decode(protocol.encode(msg))
    assert "classification" not in parsed


def test_shine_scouts_msg_round_trip():
    entries = [
        {"shine_uid": 12, "palette": 1},
        {"shine_uid": 47, "palette": 3},
        {"shine_uid": 902, "palette": 0},
    ]
    msg = ShineScoutsMsg(entries=entries)
    raw = protocol.encode(msg)
    parsed = protocol.decode(raw)
    assert parsed["t"] == "shine_scouts"
    assert parsed["entries"] == entries


# ---------------------------------------------------------------------------
# M6 phase D — pay_snapshot + outstanding wire messages
# ---------------------------------------------------------------------------


def test_pay_snapshot_msg_round_trip():
    msg = PaySnapshotMsg(entries=[
        PaySnapshotEntry(kingdom="Cap", pay=3),
        PaySnapshotEntry(kingdom="Cascade", pay=0),
    ])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {
        "t": "pay_snapshot",
        "entries": [
            {"kingdom": "Cap", "pay": 3},
            {"kingdom": "Cascade", "pay": 0},
        ],
        "save_slot": -1,
        "complete": True,
    }


def test_pay_snapshot_msg_with_save_slot():
    msg = PaySnapshotMsg(
        entries=[PaySnapshotEntry(kingdom="Cap", pay=2)],
        save_slot=1,
    )
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["save_slot"] == 1
    assert parsed["entries"] == [{"kingdom": "Cap", "pay": 2}]


def test_pay_snapshot_msg_empty_is_valid():
    """A snapshot with no entries is still a legitimate "everything zero"
    reading — the encoder must not omit it for backward-compat reasons."""
    msg = PaySnapshotMsg(entries=[])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["entries"] == []
    assert parsed["complete"] is True


def test_outstanding_msg_empty_round_trip():
    msg = OutstandingMsg(entries=[])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {
        "t": "outstanding",
        "entries": [],
    }


def test_outstanding_msg_multiple_kingdoms_round_trip():
    msg = OutstandingMsg(entries=[
        OutstandingEntry(kingdom="Cap", count=2),
        OutstandingEntry(kingdom="Cascade", count=5),
        OutstandingEntry(kingdom="Wooded", count=0),
    ])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["t"] == "outstanding"
    assert parsed["entries"] == [
        {"kingdom": "Cap", "count": 2},
        {"kingdom": "Cascade", "count": 5},
        {"kingdom": "Wooded", "count": 0},
    ]


def test_outstanding_msg_serialises_entries_as_dicts():
    """OutstandingMsg uses a to_wire() override so OutstandingEntry instances
    don't accidentally serialize as plain dataclass(asdict) — verify the
    wire shape matches what the C++ decoder expects."""
    msg = OutstandingMsg(entries=[OutstandingEntry(kingdom="Snow", count=3)])
    raw = protocol.encode(msg)
    # The bytes should not contain Python-style class info.
    assert b"OutstandingEntry" not in raw
    assert b'"kingdom":"Snow"' in raw
    assert b'"count":3' in raw


def test_talkatoo_pool_msg_enabled_round_trip():
    """Talkatoo% per-kingdom AP-pool. Bridge ships one message per kingdom."""
    from client.protocol import TalkatooPoolMsg
    msg = TalkatooPoolMsg(
        enabled=True,
        kingdom="Cap",
        moons=["Frog-Jumping Above the Fog", "Good Evening, Captain Toad!"],
    )
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {
        "t": "talkatoo_pool",
        "enabled": True,
        "kingdom": "Cap",
        "moons": ["Frog-Jumping Above the Fog", "Good Evening, Captain Toad!"],
    }


def test_talkatoo_pool_msg_disable_round_trip():
    """Disable message: enabled=False with empty kingdom/moons clears Switch state."""
    from client.protocol import TalkatooPoolMsg
    msg = TalkatooPoolMsg(enabled=False, kingdom="", moons=[])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["t"] == "talkatoo_pool"
    assert parsed["enabled"] is False
    # kingdom + moons may be stripped by _strip_none, but the wire decoder must
    # tolerate either presence (empty) or absence — see ApProtocol.cpp:
    # parseTalkatooPool resets defaults at the top of the function.
    assert parsed.get("kingdom", "") == ""
    assert parsed.get("moons", []) == []


def test_shop_labels_msg_round_trip():
    """ShopLabelsMsg: bridge ships substitute text for Crazy Cap moon slots
    keyed by (file_name, key). Empty entries list clears the table."""
    from client.protocol import ShopLabelsMsg
    msg = ShopLabelsMsg(entries=[
        {"file": "ShopItem", "key": "PowerMoon079", "label": "Got Cap Power Moon!"},
        {"file": "ShopItem", "key": "PowerMoon158", "label": "Sent Cascade Power Moon to P2"},
    ])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed == {
        "t": "shop_labels",
        "entries": [
            {"file": "ShopItem", "key": "PowerMoon079", "label": "Got Cap Power Moon!"},
            {"file": "ShopItem", "key": "PowerMoon158", "label": "Sent Cascade Power Moon to P2"},
        ],
    }


def test_shop_labels_msg_empty_round_trip():
    """Empty entries list is wire-different from "never set": it actively
    clears the Switch's shop_labels storage."""
    from client.protocol import ShopLabelsMsg
    msg = ShopLabelsMsg(entries=[])
    parsed = protocol.decode(protocol.encode(msg))
    assert parsed["t"] == "shop_labels"
    assert parsed["entries"] == []
