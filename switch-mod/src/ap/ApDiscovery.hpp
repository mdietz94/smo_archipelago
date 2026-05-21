// Runtime bridge discovery via UDP.
//
// The Switch mod historically baked the bridge IP at compile time via
// `-DBRIDGE_HOST`. That broke whenever the user's LAN/DHCP changed.
//
// On every (re)connect cycle, ApClient now calls `resolveBridge()` before
// `connectOnce()` to learn the bridge's current TCP host:port via a UDP
// probe chain:
//
//   1. UDP probe -> 127.0.0.1:<discovery_port> (250ms) — covers Ryujinx
//      running on the same host as SMOClient.
//   2. UDP broadcast -> 255.255.255.255:<discovery_port> (1s, x3) —
//      covers a normal home LAN where broadcast traverses.
//   3. UDP probe -> fallback.host:<discovery_port> (250ms) — covers
//      networks that drop broadcast but pass unicast (some consumer
//      routers, VLAN'd setups). Uses the IP captured silently by the
//      setup wizard.
//
// Any success returns the bridge's advertised TCP host:port (the bridge
// always replies with its own LAN IP via detect_lan_ip() so the answer is
// routable regardless of which probe path won).
//
// On total failure (no UDP discovery worked AND we don't fall through to
// the TCP fallback): caller retries the whole loop with its existing
// exponential backoff. Or sets `*out = fallback` and TCP-connects to that
// directly (step 4 — last resort, handled by the caller).
//
// M6.1-safe: fixed `char[]` buffers throughout; no std::string growth,
// no std::vector, no std::mutex. UDP socket lifetime is one
// resolveBridge() call.

#pragma once

#include <cstddef>
#include <cstdint>

#include "ApClient.hpp"  // BridgeTarget

namespace smoap::ap {

// Default UDP port for the discovery probe/reply protocol. Distinct
// from BRIDGE_PORT (TCP, default 17777). Overridable at build time via
// `-DDISCOVERY_PORT` (see CMakeLists.txt); the CMake value lands here
// via the `DISCOVERY_PORT_VALUE` compile-definition.
#ifdef DISCOVERY_PORT_VALUE
inline constexpr std::uint16_t kDefaultDiscoveryPort = DISCOVERY_PORT_VALUE;
#else
inline constexpr std::uint16_t kDefaultDiscoveryPort = 17776;
#endif

// Attempt to discover the bridge's TCP host:port via the UDP probe chain
// described in the header comment.
//
// `fallback` is the build-time-baked bridge target (host = -DBRIDGE_HOST,
// port = -DBRIDGE_PORT). Used as the probe destination in step 3.
//
// `out` receives the discovered target on success; left untouched on
// failure. The caller should keep its own `target_` and only overwrite
// it from `out` when this function returns true.
//
// Returns true when a `{"t":"bridge", host, port}` reply was received
// within the timeouts; false otherwise.
bool resolveBridge(
    BridgeTarget& out,
    const BridgeTarget& fallback,
    std::uint16_t discovery_port = kDefaultDiscoveryPort);

}  // namespace smoap::ap
