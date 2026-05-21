"""LAN-IP autodetect — back-compat shim.

The implementation moved to `client/net_util.py` so the runtime client
(`DiscoveryResponder`) doesn't depend on `_setup/` (which is wizard
code). This module keeps the old import paths working for tests and
any wizard pages that still import from here.
"""

from __future__ import annotations

try:
    from ..client.net_util import detect_lan_ip, is_plausible_ipv4
except ImportError:
    from client.net_util import detect_lan_ip, is_plausible_ipv4

__all__ = ["detect_lan_ip", "is_plausible_ipv4"]
