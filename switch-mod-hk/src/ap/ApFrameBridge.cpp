// Frame-thread <-> socket-thread marshalling.
//
// All Check fields are fixed char[64] buffers — no allocation on the frame
// thread. libstdc++'s std::string allocator NULL-derefs in our subsdk9
// context for non-SSO strings; see ApProtocol.hpp for the kCheckFieldCap
// rationale.

#include "ApFrameBridge.hpp"

#include <atomic>
#include <cstdio>

#include "ApProtocol.hpp"
#include "ApState.hpp"

namespace smoap::ap {

namespace {

// outbound_logs producer-side spinlock. SpscRing is single-producer only,
// but enqueueRemoteLog can be called from ANY thread (frame, worker, hook
// callbacks). A short spin around the push() keeps the ring invariant
// honest. Lock-free atomic_flag — no allocator path.
std::atomic_flag g_logs_push_lock = ATOMIC_FLAG_INIT;

}  // namespace

static void enqueueCheck(const Check& c) {
    auto& st = ApState::instance();
    if (st.synthetic_grant_this_frame) return;  // suppress on AP-granted moons
    const std::uint64_t h = ApState::hashCheck(c);
    if (!st.locations_checked.tryInsert(h)) return;  // already checked this session
    st.outbound_checks.push(c);
}

void reportMoonChecked(const char* stage_name, const char* object_id, int shine_uid) {
    Check c{};
    c.kind = ItemKind::Moon;
    copyCheckField(c.stage_name, stage_name);
    copyCheckField(c.object_id, object_id);
    c.shine_uid = shine_uid;
    // M6 phase A.5 — stamp a sequence id so the bridge can correlate its
    // MoonLabelMsg reply with *this* check. Only Moon checks get one (it's
    // the only kind that triggers a cutscene with a label to substitute).
    // fetch_add(1) gives us monotonic per-session ids starting at 1.
    c.seq = ApState::instance().next_check_seq.fetch_add(1, std::memory_order_relaxed);
    enqueueCheck(c);
}

void reportCaptureChecked(const char* hack_name) {
    Check c{};
    c.kind = ItemKind::Capture;
    copyCheckField(c.hack_name, hack_name);
    enqueueCheck(c);
}

void reportStatus(const char* stage_name, int scenario_no) {
    // Status updates aren't deduped — bridge logs / tracker uses the latest.
    // For now we don't enqueue these into a ring (would require a new one);
    // M4 ships scenario tracking via the existing log-string path: emit a
    // structured log line that the bridge picks up. M5 (web tracker) will
    // surface this from the bridge state.
    (void)stage_name;
    (void)scenario_no;
    // TODO(M5): wire to a dedicated outbound_status_ring for the tracker.
}

void reportDeath() {
    auto& st = ApState::instance();
    // Debounce: if a death is already queued and unsent, skip.
    bool expected = false;
    if (!st.death_pending_send.compare_exchange_strong(expected, true)) return;
    // ts_ms is filled by the socket worker right before it ships (gives a more
    // accurate wall-clock timestamp than reading on the frame thread, which
    // would need a syscall here). 0 means "stamp at send time".
    StatusEvent e{.goal = false, .death = true, .ts_ms = 0};
    st.outbound_status.push(e);
}

void reportGoal() {
    auto& st = ApState::instance();
    StatusEvent e{.goal = true, .death = false, .ts_ms = 0};
    st.outbound_status.push(e);
}

void enqueueRemoteLog(const char* level, const char* msg) {
    auto& st = ApState::instance();
    // Don't bother enqueuing if no one is listening — the line still went out
    // via svcOutputDebugString. This also prevents an infinite backlog when
    // the bridge has never connected.
    if (st.conn.load(std::memory_order_relaxed) == ConnState::Disconnected) return;
    Log lg;
    copyFixedField(lg.level, level);
    copyFixedField(lg.msg,   msg);
    // SpscRing is single-producer; many threads call log(). Spin the producer
    // side; the consumer (ApClient::pumpOnce) is the sole tail mover and is
    // unaffected. Push is short (memcpy + two atomic stores).
    while (g_logs_push_lock.test_and_set(std::memory_order_acquire)) {
        // spin
    }
    const bool ok = st.outbound_logs.push(lg);
    g_logs_push_lock.clear(std::memory_order_release);
    if (!ok) {
        st.log_drops.fetch_add(1, std::memory_order_relaxed);
    }
}

}  // namespace smoap::ap
