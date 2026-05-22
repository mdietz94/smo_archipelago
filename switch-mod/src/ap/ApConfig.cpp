// AP bridge config: compile-time defaults baked in via CMake.

#include "ApConfig.hpp"

#include "../util/Log.hpp"

namespace smoap::ap {

ApConfig loadApConfig() {
    ApConfig cfg;
    SMOAP_LOG_INFO("ApConfig: bridge_host=%s bridge_port=%u retry=%ums recv_to=%ums",
                   cfg.bridge_host.c_str(), cfg.bridge_port,
                   cfg.retry_ms, cfg.recv_timeout_ms);
    return cfg;
}

}  // namespace smoap::ap
