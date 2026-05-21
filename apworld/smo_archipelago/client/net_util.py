"""Network helpers shared by the runtime client and the setup wizard.

`detect_lan_ip()` returns the local IP the kernel would use to reach an
arbitrary external host — i.e. the address a peer on the LAN can route
to. The DiscoveryResponder advertises this in its UDP replies so the
Switch always TCP-connects via a routable interface (even when the
probe arrived on loopback or broadcast).

`is_plausible_ipv4()` is a loose dotted-quad validator used by the
wizard's optional "manual override" field.
"""

from __future__ import annotations

import socket

_PROBE_HOST = "8.8.8.8"
_PROBE_PORT = 80

_LOOPBACK = "127.0.0.1"


def detect_lan_ip() -> str:
    """Best-effort LAN IP. Returns "127.0.0.1" when no usable
    interface is available — useful as a default for Ryujinx-on-same-host
    development.

    We never send a packet; `connect()` on a UDP socket only triggers
    kernel route resolution and then we read back the local end.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((_PROBE_HOST, _PROBE_PORT))
        ip, _port = s.getsockname()
        if ip and not ip.startswith("0."):
            return ip
        return _LOOPBACK
    except OSError:
        return _LOOPBACK
    finally:
        s.close()


def is_plausible_ipv4(s: str) -> bool:
    """Loose IPv4 validator — accepts `"a.b.c.d"` with each octet 0-255.

    Does not validate reachability; the wizard's manual-override field
    only needs to refuse obvious typos.
    """
    if not s:
        return False
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p or not p.isdigit():
            return False
        n = int(p)
        if n < 0 or n > 255:
            return False
    return True
