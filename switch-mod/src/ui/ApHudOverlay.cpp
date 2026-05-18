// Connection-status heartbeat log.
//
// In-game text rendering was attempted via sead::TextWriter +
// sead::DebugFontMgrJis1Nvn here, but bootstrapping that font singleton
// reliably from a third-party subsdk requires LunaKit cohabit (font heap +
// init ordering). The user's preference is a self-contained mod, so
// notifications now route through SMO's existing Cappy-speech pipeline via
// CappyMessenger — which uses Nintendo's fonts/layout for free and doesn't
// fight for the sead singleton.

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
    SMOAP_LOG_INFO("HUD initialized (heartbeat mode; Cappy speech via CappyMessenger)");
}

void drawHudFrame() {
    auto& st = smoap::ap::ApState::instance();

    static std::uint32_t s_frame = 0;
    if ((++s_frame % kHeartbeatInterval) == 0) {
        // DEBUG-level so it stays in svcOutputDebugString + smo_ap.txt
        // (local-only diagnostic) but does NOT cross the wire to the PC
        // client's Odyssey-tab log. The 1Hz cadence was drowning out the
        // event-driven INFO lines in that pane — heartbeat is useful for
        // post-mortem from the SD dump, noisy for live observation.
        SMOAP_LOG_DEBUG("AP heartbeat: conn=%s checked=%u captures=%u",
                        connStateName(st.conn.load()),
                        static_cast<unsigned>(st.locations_checked.size()),
                        static_cast<unsigned>(st.captures_unlocked.count()));
    }
}

}  // namespace smoap::ui
