// Frame-thread <-> socket-thread marshalling.

#include "ApFrameBridge.hpp"

#include "ApProtocol.hpp"
#include "ApState.hpp"

namespace smoap::ap {

static void enqueueCheck(const Check& c) {
    auto& st = ApState::instance();
    if (st.synthetic_grant_this_frame) return;  // suppress on AP-granted moons
    const std::uint64_t h = ApState::hashCheck(c);
    if (!st.locations_checked.insert(h).second) return;  // already checked this session
    st.outbound_checks.push(c);
}

void reportMoonChecked(const std::string& kingdom, const std::string& shine_id) {
    enqueueCheck(Check{
        .kind = ItemKind::Moon,
        .kingdom = kingdom,
        .shine_id = shine_id,
    });
}

void reportCaptureChecked(const std::string& cap) {
    enqueueCheck(Check{
        .kind = ItemKind::Capture,
        .cap = cap,
    });
}

void reportStatus(const std::string& /*kingdom*/, int /*scenario*/, int /*moons_collected*/) {
    // M4: enqueue Status. For now, just leave a slot for it.
}

void reportGoal() {
    auto& st = ApState::instance();
    if (st.goal_sent) return;
    st.goal_sent = true;
    StatusEvent e{.goal = true};
    st.outbound_status.push(e);
}

}  // namespace smoap::ap
