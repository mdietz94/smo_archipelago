"""Tests for the multi-Switch registry on `BridgeState`.

The bridge accepts N parallel Switch connections (real hardware +
Ryujinx, etc.) and tracks them in a per-device_id registry. Exactly one
is "active" at a time; the GUI's `get_switches()` snapshot is sorted
stably by peer IP so toggling active doesn't reorder rows (the user
should see the active marker move between rows, not the rows jump).
"""

from __future__ import annotations

from client.state import BridgeState


def test_register_switch_records_entry():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="192.168.1.10",
                      mod_ver="0.1.0", smo_ver="1.0.0")
    entries = s.get_switches()
    assert len(entries) == 1
    assert entries[0]["device_id"] == "mario"
    assert entries[0]["peer_ip"] == "192.168.1.10"
    assert entries[0]["mod_ver"] == "0.1.0"
    assert entries[0]["smo_ver"] == "1.0.0"
    # Nothing active yet — GUI shows the row as inactive.
    assert entries[0]["active"] is False


def test_register_same_device_id_is_idempotent_reconnect():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="10.0.0.1")
    s.register_switch(device_id="mario", peer_ip="10.0.0.5",
                      mod_ver="0.2.0")
    entries = s.get_switches()
    assert len(entries) == 1
    # peer_ip + mod_ver updated to the latest values.
    assert entries[0]["peer_ip"] == "10.0.0.5"
    assert entries[0]["mod_ver"] == "0.2.0"


def test_set_active_switch_marks_entry_but_does_not_reorder():
    """Active-toggle MUST NOT reorder rows — IP-sort is stable across
    toggles so the user sees the active marker move between rows
    instead of the rows themselves shuffling positions."""
    s = BridgeState()
    s.register_switch(device_id="zebra", peer_ip="10.0.0.10")
    s.register_switch(device_id="apple", peer_ip="10.0.0.11")
    s.register_switch(device_id="mango", peer_ip="10.0.0.12")
    s.set_active_switch("zebra")

    entries = s.get_switches()
    # Stable IP order regardless of which entry is active.
    assert [e["device_id"] for e in entries] == ["zebra", "apple", "mango"]
    assert [e["peer_ip"] for e in entries] == [
        "10.0.0.10", "10.0.0.11", "10.0.0.12",
    ]
    assert entries[0]["active"] is True
    assert all(e["active"] is False for e in entries[1:])

    # Toggle active to the LAST IP in the order — list order MUST NOT
    # change (zebra@.10 stays first; mango@.12 stays last). Only the
    # active flag moves.
    s.set_active_switch("mango")
    entries = s.get_switches()
    assert [e["device_id"] for e in entries] == ["zebra", "apple", "mango"]
    assert entries[0]["active"] is False
    assert entries[2]["active"] is True


def test_get_switches_sorts_ips_numerically_not_lexically():
    """`.10` > `.2` numerically but `.10` < `.2` lex-sorted. The popup
    must show `.2` before `.10` so a user with a Switch at .2 and one
    at .10 sees them in the order they'd reason about them."""
    s = BridgeState()
    s.register_switch(device_id="bigger", peer_ip="192.168.1.10")
    s.register_switch(device_id="smaller", peer_ip="192.168.1.2")
    entries = s.get_switches()
    assert [e["peer_ip"] for e in entries] == [
        "192.168.1.2", "192.168.1.10",
    ]


def test_set_active_unknown_device_id_is_noop():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="10.0.0.1")
    s.set_active_switch("luigi")
    assert s.get_active_switch() is None


def test_unregister_clears_active_when_dropping_active_switch():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="10.0.0.1")
    s.set_active_switch("mario")
    s.unregister_switch("mario")
    assert s.get_active_switch() is None
    assert s.get_switches() == []


def test_unregister_inactive_switch_leaves_active_intact():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="10.0.0.1")
    s.register_switch(device_id="luigi", peer_ip="10.0.0.2")
    s.set_active_switch("mario")
    s.unregister_switch("luigi")
    assert s.get_active_switch() == "mario"
    assert [e["device_id"] for e in s.get_switches()] == ["mario"]


def test_set_active_none_unbinds():
    s = BridgeState()
    s.register_switch(device_id="mario", peer_ip="10.0.0.1")
    s.set_active_switch("mario")
    s.set_active_switch(None)
    assert s.get_active_switch() is None
    # The Switch is still registered; just no longer the active one.
    assert [e["device_id"] for e in s.get_switches()] == ["mario"]
    assert s.get_switches()[0]["active"] is False
