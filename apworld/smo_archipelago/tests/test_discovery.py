"""Tests for `client.discovery` — UDP bridge-discovery responder.

The responder listens on UDP 17776 (default) and replies to any
`{"t":"discover"}` probe with a `{"t":"bridge", host, port, seed}`
unicast that tells the Switch where to TCP-connect.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from client.discovery import DEFAULT_DISCOVERY_PORT, DiscoveryResponder


def _free_udp_port() -> int:
    """Reserve a UDP port to avoid colliding with the default 17776 (or
    another test process) on shared CI runners."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


async def _send_probe_recv_reply(
    payload: bytes,
    port: int,
    timeout: float = 1.0,
) -> "bytes | None":
    """Send a UDP datagram to 127.0.0.1:port and return the reply (or None)."""
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _ReplyCollector(),
        local_addr=("127.0.0.1", 0),
        family=socket.AF_INET,
    )
    try:
        transport.sendto(payload, ("127.0.0.1", port))
        try:
            await asyncio.wait_for(protocol.got_reply.wait(), timeout=timeout)
            return protocol.last_reply
        except asyncio.TimeoutError:
            return None
    finally:
        transport.close()


class _ReplyCollector(asyncio.DatagramProtocol):
    def __init__(self):
        self.last_reply: bytes | None = None
        self.got_reply = asyncio.Event()

    def datagram_received(self, data, addr):
        self.last_reply = data
        self.got_reply.set()


@pytest.mark.asyncio
async def test_responder_replies_to_well_formed_probe():
    port = _free_udp_port()
    r = DiscoveryResponder(
        tcp_port=17777,
        get_seed=lambda: "TESTSEED",
        bind_host="127.0.0.1",
        port=port,
        # Pin the advertised host so the test doesn't depend on what
        # detect_lan_ip returns on the CI runner.
        get_lan_ip=lambda: "192.168.42.7",
    )
    assert await r.start() is True
    try:
        probe = (json.dumps({"t": "discover", "mod_ver": "0.1.0"}) + "\n").encode("utf-8")
        reply = await _send_probe_recv_reply(probe, port)
        assert reply is not None, "responder did not reply within timeout"
        parsed = json.loads(reply.decode("utf-8"))
        assert parsed["t"] == "bridge"
        assert parsed["host"] == "192.168.42.7"
        assert parsed["port"] == 17777
        assert parsed["seed"] == "TESTSEED"
    finally:
        r.stop()


@pytest.mark.asyncio
async def test_responder_ignores_malformed_json():
    port = _free_udp_port()
    r = DiscoveryResponder(
        tcp_port=17777,
        get_seed=lambda: "",
        bind_host="127.0.0.1",
        port=port,
        get_lan_ip=lambda: "127.0.0.1",
    )
    assert await r.start() is True
    try:
        reply = await _send_probe_recv_reply(
            b"this is not json\n", port, timeout=0.3,
        )
        assert reply is None, f"unexpected reply to malformed probe: {reply!r}"
    finally:
        r.stop()


@pytest.mark.asyncio
async def test_responder_ignores_non_discover_type():
    port = _free_udp_port()
    r = DiscoveryResponder(
        tcp_port=17777,
        get_seed=lambda: "",
        bind_host="127.0.0.1",
        port=port,
        get_lan_ip=lambda: "127.0.0.1",
    )
    assert await r.start() is True
    try:
        probe = (json.dumps({"t": "hello"}) + "\n").encode("utf-8")
        reply = await _send_probe_recv_reply(probe, port, timeout=0.3)
        assert reply is None
    finally:
        r.stop()


@pytest.mark.asyncio
async def test_responder_oversized_probe_is_dropped():
    port = _free_udp_port()
    r = DiscoveryResponder(
        tcp_port=17777,
        get_seed=lambda: "",
        bind_host="127.0.0.1",
        port=port,
        get_lan_ip=lambda: "127.0.0.1",
    )
    assert await r.start() is True
    try:
        # > MAX_PROBE_BYTES (512). Should be ignored without crashing.
        oversized = b"{" + b"x" * 1024 + b"}"
        reply = await _send_probe_recv_reply(oversized, port, timeout=0.3)
        assert reply is None
    finally:
        r.stop()


@pytest.mark.asyncio
async def test_responder_bind_failure_is_nonfatal():
    """When the port is already taken (e.g. a second SMOClient on the
    same host), start() returns False instead of raising. SMOClient
    keeps running — Switches with a baked-in IP still TCP-connect."""
    port = _free_udp_port()
    # Hold the port so the responder can't bind.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", port))
    try:
        r = DiscoveryResponder(
            tcp_port=17777,
            get_seed=lambda: "",
            bind_host="127.0.0.1",
            port=port,
        )
        ok = await r.start()
        assert ok is False, "start() should return False on bind failure"
        # stop() must be a no-op on a never-started responder.
        r.stop()
    finally:
        blocker.close()


def test_default_discovery_port_is_distinct_from_tcp():
    """Sanity: the discovery port must not collide with the TCP port
    (Switches probe 17776, TCP-connect 17777 by default)."""
    assert DEFAULT_DISCOVERY_PORT == 17776
    assert DEFAULT_DISCOVERY_PORT != 17777
