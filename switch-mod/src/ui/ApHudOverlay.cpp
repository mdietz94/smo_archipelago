// M3 HUD: a heartbeat-only "overlay" that logs the AP connection state
// every ~60 frames (~1s at 60 fps). On-screen drawing via agl::DrawContext
// is deferred to M8.
//
// The web tracker (running on the PC bridge at http://localhost:8000) is the
// canonical source of truth for connection status during M3-M7 testing.

#include "ApHudOverlay.hpp"

#include <cstdint>

#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::ui {

namespace {

const char* connStateName(smoap::ap::ConnState s) {
    using S = smoap::ap::ConnState;
    switch (s) {
        case S::Disconnected: return "DISC";
        case S::Connecting:   return "CONN";
        case S::Hello:        return "HELO";
        case S::Ready:        return "RDY ";
    }
    return "????";
}

constexpr std::uint32_t kHeartbeatInterval = 60;  // frames

}  // namespace

void initHud() {
    SMOAP_LOG_INFO("HUD initialized (heartbeat-only mode for M3)");
}

void drawHudFrame() {
    static std::uint32_t s_frame = 0;
    if ((++s_frame % kHeartbeatInterval) != 0) return;

    const auto& st = smoap::ap::ApState::instance();
    SMOAP_LOG_INFO("AP heartbeat: conn=%s checked=%u captures=%u",
                   connStateName(st.conn.load()),
                   static_cast<unsigned>(st.locations_checked.size()),
                   static_cast<unsigned>(st.captures_unlocked.count()));
}

}  // namespace smoap::ui
