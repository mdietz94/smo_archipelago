// Bridge config — defaults baked in at compile time via CMake.
// Override with -DBRIDGE_HOST=... -DBRIDGE_PORT=... at configure time.

#pragma once

#include <cstdint>
#include <string>

// Fallback defaults if not provided by CMake.
#ifndef BRIDGE_HOST_STRING
#define BRIDGE_HOST_STRING "192.168.1.10"
#endif
#ifndef BRIDGE_PORT_VALUE
#define BRIDGE_PORT_VALUE 17777
#endif
#ifndef BRIDGE_RETRY_MS_VALUE
#define BRIDGE_RETRY_MS_VALUE 3000
#endif
#ifndef BRIDGE_RECV_TIMEOUT_MS_VALUE
#define BRIDGE_RECV_TIMEOUT_MS_VALUE 200
#endif

namespace smoap::ap {

struct ApConfig {
    std::string   bridge_host     = BRIDGE_HOST_STRING;
    std::uint16_t bridge_port     = BRIDGE_PORT_VALUE;
    std::uint32_t retry_ms        = BRIDGE_RETRY_MS_VALUE;
    std::uint32_t recv_timeout_ms = BRIDGE_RECV_TIMEOUT_MS_VALUE;
    std::string   log_level       = "info";
};

ApConfig loadApConfig();

}  // namespace smoap::ap
