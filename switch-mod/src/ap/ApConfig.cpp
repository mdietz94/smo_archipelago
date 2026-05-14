// AP bridge config: compile-time defaults baked in via CMake.
//
// We previously read sd:/atmosphere/contents/<TID>/romfs/ap_config.json at
// runtime so the bridge IP could be edited on the SD without rebuilding.
// That path requires nn::fs::MountSdCardForDebug, which fails on retail
// emulators and newer firmware. Build-time configuration via CMake's
// -DBRIDGE_HOST=... is sufficient and keeps the runtime simple.

#include "ApConfig.hpp"

#include "../util/Log.hpp"

namespace smoap::ap {

ApConfig loadApConfig() {
    // Defaults from header are populated from CMake at compile time.
    ApConfig cfg;
    SMOAP_LOG_INFO("ApConfig (compile-time): %s:%u retry=%ums recv_to=%ums",
                   cfg.bridge_host.c_str(), cfg.bridge_port,
                   cfg.retry_ms, cfg.recv_timeout_ms);
    return cfg;
}

}  // namespace smoap::ap
