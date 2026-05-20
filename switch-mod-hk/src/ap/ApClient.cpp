// ApClient — TCP client to the PC bridge.
//
// Phase 3b in progress: minimal scaffolding so the rest of switch-mod-hk can
// link. The full socket/thread/nifm port lands in a follow-up commit. Until
// then, the worker is not actually spawned (start() logs and returns) — hooks
// fire and enqueue into ApState's rings, but the rings never drain. The
// bridge-side test harness can drive ApState directly to validate the
// inbound-item path without the worker.

#include "ApClient.hpp"

#include "ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::ap {

ApClient& ApClient::instance() {
    static ApClient s;
    return s;
}

void ApClient::initNetworking() {
    SMOAP_LOG_INFO("[apclient] initNetworking — stub (nifm/socket port pending)");
}

void ApClient::start(const BridgeTarget& target) {
    target_ = target;
    SMOAP_LOG_INFO("[apclient] start target=%s:%u — stub (worker not spawned)",
                   target.host.c_str(), target.port);
}

void ApClient::stop() {
    running_ = false;
}

void ApClient::requestRehello() {
    rehello_requested_.store(true, std::memory_order_release);
}

void ApClient::deferSaveLoadStatusBubble() {
    // Stub — full impl will arm save_load_announce_deadline_ms_ once the
    // worker thread exists. Current behavior: log only.
    SMOAP_LOG_INFO("[apclient] deferSaveLoadStatusBubble — stub");
}

void ApClient::pumpOnce() {}
void ApClient::threadMain() {}

bool ApClient::connectOnce() { return false; }
void ApClient::disconnect() {}
void ApClient::sendHello() {}
void ApClient::sendSnapshot() {}
bool ApClient::recvIntoBuf() { return false; }
bool ApClient::popLine(char* /*out*/, std::size_t& /*out_len*/) { return false; }
void ApClient::handleLine(char* /*line*/, std::size_t /*line_len*/) {}

}  // namespace smoap::ap
