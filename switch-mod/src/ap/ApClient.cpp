// TCP client to the PC bridge.
//
// Owns a single nn::socket TCP connection on a dedicated worker thread.
// The frame thread (drawMain trampoline) only touches ApState's lock-free
// SPSC rings; this thread does all blocking I/O.
//
// Thread sequence:
//   1. start() (called from frame thread inside GameSystemInit hook):
//      saves target, spawns worker, returns immediately.
//   2. initNetworking() (frame thread, before start): nn::nifm::Initialize +
//      SubmitNetworkRequestAndWait. nn::socket::Initialize is owned by SMO
//      itself — it's already brought up by the time GameSystem::init returns
//      (BSD RegisterClient happens during the Orig call). Calling Initialize
//      a second time asserts inside InitializeCommon. LunaKit avoids this by
//      installing a REPLACE no-op hook on nn::socket::Initialize and doing
//      its own bring-up first; we just piggy-back on SMO's.
//   3. threadMain() loop: connectOnce -> sendHello -> Select+recv read,
//      pumpOnce drain outbound, error-on-disconnect with backoff retry.

#include "ApClient.hpp"

#include <cstdint>
#include <cstring>

#include "nn/nifm.h"
#include "nn/os.h"
#include "nn/socket.hpp"
// nx.h is the C-linkage umbrella for libnx (svc + result + ...). Including
// the inner headers directly from C++ gives C++ mangling and unresolved
// links against the assembly stubs.
#include "lib/nx/nx.h"

#include "ApProtocol.hpp"
#include "ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../game/KingdomUnlock.hpp"  // kingdomBitFor (M6 phase D OutstandingMsg apply)
#include "../game/MoonApply.hpp"
#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

namespace smoap::ap {

namespace {

// BSD socket constants (not exposed by lunakit's nn/socket.hpp).
constexpr int kAfInet      = 2;
constexpr int kSockStream  = 1;
constexpr int kSolSocket   = 0xffff;
constexpr int kSoKeepAlive = 0x0008;

constexpr std::size_t kWorkerStackSize = 64 * 1024;

// Exponential backoff caps (ms): 1s, 2s, 5s, 10s, 30s.
constexpr std::uint32_t kBackoffCapMs = 30 * 1000;

// "Quick bounce" threshold: a connection held for less than this many ms
// before disconnecting is treated as a failure for backoff purposes. Covers
// the bridge's stale-_writer rejection path (TCP handshake succeeds, app
// layer sends ErrMsg(busy) and closes within ms), which without this gate
// would keep resetting backoff_ms on every "successful" connect and hammer
// the bridge at LAN line-rate. Symmetric semantics: we also wait until a
// connection has been held this long before resetting backoff_ms — so a
// connect-then-quick-disconnect cycle escalates monotonically.
constexpr std::int64_t kStableConnectMs = 1000;

// Last AP-side connection state we observed from the bridge. Drives the Cappy
// "Connected to Archipelago" / "Disconnected from Archipelago" speech bubbles
// on ready <-> not-ready transitions. Two writers, both on the worker thread:
//   - ap_state message dispatch (graceful: bridge told us AP state changed)
//   - disconnect() (ungraceful: bridge TCP socket died — covers SMOClient
//     being killed / crashing without sending a final ap_state).
// Default "disconnected" so the first ap_state("ready") push (HELLO replay or
// live Connected) fires the Connected bubble when bridge was already up at
// SMO boot. Worker-thread exclusive; no atomic needed.
char s_last_ap_state[24] = "disconnected";

// Stack must be page-aligned; size must be a multiple of page size. nn::os
// CreateThread takes the BASE address + size (svcCreateThread takes top).
alignas(0x1000) std::byte g_worker_stack[kWorkerStackSize];
nn::os::ThreadType g_worker_thread{};

// M6 phase D — worker-thread-only "in-flight" deposit tracking. After the
// frame thread pushes into ApState::pending_deposits, the worker copies the
// entry here AND sends it to the bridge. The entry sits until the bridge
// acks (DepositAckMsg) or until reconnect re-sends. Fixed-size array (not
// std::vector / std::map) per the libstdc++-allocator-NULL-deref discipline.
//
// 32 slots covers many seconds of offline buffering even at the most
// pessimistic Multi-Moon cadence (one deposit ≤ once per few seconds in
// practice). Overflow truncates with a warn log.
constexpr std::size_t kUnackedDepositCap = 32;
struct UnackedDeposit {
    bool slot_used = false;
    std::uint64_t seq = 0;
    char kingdom[32] = {};
    int amount = 0;
};
UnackedDeposit g_unacked_deposits[kUnackedDepositCap]{};

// Reset all slots — called when a re-HELLO request fires after save load,
// since SaveLoadHook clears last_acked_deposit_seq and the bridge will send
// a fresh OutstandingMsg that supersedes anything we have queued.
void clearUnackedDeposits() {
    for (auto& u : g_unacked_deposits) {
        u.slot_used = false;
        u.seq = 0;
        u.kingdom[0] = '\0';
        u.amount = 0;
    }
}

void copyKingdomTo32(char (&dst)[32], const char* src) {
    if (!src) { dst[0] = '\0'; return; }
    std::size_t i = 0;
    while (i + 1 < sizeof(dst) && src[i] != '\0') {
        dst[i] = src[i];
        ++i;
    }
    dst[i] = '\0';
}

// Place a pending deposit into g_unacked_deposits. Returns true on success,
// false if the array is full (caller logs).
bool stashUnackedDeposit(const ApState::PendingDeposit& pd) {
    for (auto& u : g_unacked_deposits) {
        if (!u.slot_used) {
            u.slot_used = true;
            u.seq = pd.seq;
            copyKingdomTo32(u.kingdom, pd.kingdom);
            u.amount = pd.amount;
            return true;
        }
    }
    return false;
}

// Serialize an unacked entry into the caller's LineBuffer + transmit. Returns
// the Send() return value (n bytes written, or negative on socket error).
int sendDepositMessage(int socket_fd, smoap::util::json::LineBuffer& line,
                       std::uint64_t seq, const char* kingdom, int amount) {
    Deposit dep{};
    dep.seq = seq;
    copyCheckField(dep.kingdom, kingdom);
    dep.amount = amount;
    encodeDeposit(line, dep);
    return nn::socket::Send(socket_fd, line.data(), line.size(), 0);
}

extern "C" void workerEntry(void* arg) {
    static_cast<ApClient*>(arg)->threadMain();
    // Should not return; if we do, just sleep forever.
    while (true) svcSleepThread(INT64_MAX);
}

}  // namespace

ApClient& ApClient::instance() {
    static ApClient s;
    return s;
}

void ApClient::initNetworking() {
    SMOAP_LOG_INFO("[frame] nn::nifm::Initialize");
    const Result nifm_rc = nn::nifm::Initialize();
    if (R_FAILED(nifm_rc)) {
        SMOAP_LOG_ERROR("[frame] nn::nifm::Initialize FAILED rc=0x%x", nifm_rc);
        return;
    }
    SMOAP_LOG_INFO("[frame] SubmitNetworkRequestAndWait");
    nn::nifm::SubmitNetworkRequestAndWait();
    const bool net_up = nn::nifm::IsNetworkAvailable();
    SMOAP_LOG_INFO("[frame] network available: %s", net_up ? "YES" : "NO");

    // nn::socket::Initialize is intentionally NOT called here — SMO already
    // initialized the BSD client during GameSystem::init (Orig). A second
    // Initialize asserts inside nn::socket::detail::InitializeCommon.
    SMOAP_LOG_INFO("[frame] networking ready (sockets owned by SMO)");
}

void ApClient::start(const BridgeTarget& target) {
    target_ = target;
    running_ = true;
    SMOAP_LOG_INFO("ApClient::start target=%s:%u", target.host.c_str(), target.port);

    // Use nn::os::CreateThread (NOT raw svcCreateThread): the worker calls
    // nn::socket::Socket which is an IPC to the bsd: service, and IPC needs
    // per-thread nn-runtime state that only nn::os-managed threads have.
    // Raw svcCreateThread threads NULL-deref inside HipcSimpleClientSession
    // Manager::Allocate -> InternalCriticalSectionImplByHorizon::Enter.
    // Use the no-coreNum overload — nn::os picks the process's default core
    // internally. The 7-arg overload would forward our value to the kernel
    // SVC, which only accepts 0..N (process-allowed cores) or -2 ("default");
    // -1 / IdealCoreDontCare returns InvalidCoreId from svcCreateThread.
    //
    // Priority must be in nn::os range [0, 31] (0 = highest, 16 = default,
    // 31 = lowest). svcCreateThread accepts a wider 0..63 range; nn::os is
    // stricter and aborts InvalidPriority on anything outside [0, 31].
    const Result rc = nn::os::CreateThread(
        &g_worker_thread, &workerEntry, this,
        g_worker_stack, kWorkerStackSize,
        /*priority=*/16);
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("ApClient: nn::os::CreateThread failed (rc=0x%x)", rc);
        running_ = false;
        return;
    }
    nn::os::SetThreadName(&g_worker_thread, "smoap-worker");
    nn::os::StartThread(&g_worker_thread);
}

void ApClient::stop() {
    running_ = false;
    disconnect();
    // We don't join the thread — the module lives for the process lifetime.
}

// Time window (ms) after a requestRehello() during which the disconnect /
// reconnect Cappy bubbles are suppressed. Sized to cover a typical save-load
// rehello (~100 ms socket cycle + replay) plus headroom; longer than this
// implies SMOClient genuinely died and the user should see the disconnect.
static constexpr std::int64_t kRehelloBubbleSuppressMs = 3000;

// Grace period (ms) before an ungraceful TCP drop surfaces as a Cappy
// "Disconnected from Archipelago" bubble. Sized to absorb routine LAN /
// Wi-Fi blips (observed 5-9s on real hardware, errno 101 ENETUNREACH on
// reconnect attempts) without spamming the user. If TCP + AP recover inside
// this window, both the disconnect AND the matching "Connected" bubble stay
// silent — s_last_ap_state never flips, so the ap_state(ready) handler sees
// was_ready=true,now_ready=true and dispatches nothing. If grace expires
// with no recovery, the worker loop fires the bubble and commits the state
// transition; a later recovery then surfaces "Connected from Archipelago"
// through the normal path.
static constexpr std::int64_t kDisconnectGracePeriodMs = 10000;

void ApClient::requestRehello() {
    // Arm the bubble suppressor BEFORE setting the rehello flag so the worker
    // thread can't race ahead and call disconnect() between these stores.
    // The worker only ever READS this value (against nowMs()), so a relaxed
    // load is fine.
    suppress_state_bubble_until_ms_.store(
        ApState::nowMs() + kRehelloBubbleSuppressMs,
        std::memory_order_relaxed);
    // Set the atomic; the worker reads it on the next loop iteration and
    // closes-and-reopens. We do NOT call disconnect() here because we're on
    // the frame thread and socket close should be owned by the worker.
    rehello_requested_.store(true, std::memory_order_release);
}

void ApClient::threadMain() {
    SMOAP_LOG_INFO("[worker] thread started, target=%s:%u",
                   target_.host.c_str(), target_.port);
    // nifm Initialize was done on the frame thread inside
    // GameSystemInitHook::Callback because it's an nn-IPC call and our
    // raw-svcCreateThread worker can't make those. Socket bring-up is
    // SMO's; the worker only does socket-level ops (Socket, Connect,
    // Send, Recv, Select) which empirically work on raw threads.
    SMOAP_LOG_INFO("[worker] entering connect loop");

    std::uint32_t backoff_ms = target_.retry_ms;
    // Monotonic ms when current connection was established; 0 = not
    // connected. Drives the quick-bounce / stability gates below.
    std::int64_t connected_at_ms = 0;
    // Snapshot gate has two halves: save_was_loaded (SaveLoadHook latch) and
    // scene_cache != nullptr (HakoniwaSequence has a live curScene, i.e. the
    // player is in an actual stage rather than on the file-select screen).
    // sendHello fires unconditionally on connect; if either half is missing
    // at that point the snapshot is deferred and re-checked from the loop
    // body below on each iteration. Cleared after a successful sendSnapshot
    // and re-evaluated on every (re)connect. Worker-thread-local — both gate
    // signals are atomics so no extra locking.
    bool snapshot_pending = false;

    while (running_) {
        // Fire deferred "Disconnected from Archipelago" bubble if the grace
        // window armed by disconnect() has expired with no recovery. We get
        // here on every loop iteration including the connect-fail backoff
        // path (which `continue`s back to the top), so even a 30s backoff
        // sleep adds at most one extra cycle of latency to the bubble.
        // s_last_ap_state can only be "ready" here if we deferred and never
        // recovered; the ap_state(ready) handler clears the timer.
        if (const auto deadline = pending_disconnect_bubble_at_ms_.load(
                std::memory_order_relaxed);
            deadline > 0 && ApState::nowMs() >= deadline) {
            SMOAP_LOG_INFO("[bubble] firing deferred 'Disconnected from "
                           "Archipelago' (grace expired)");
            smoap::ui::CappyMessenger::instance()
                .enqueueSystem("Disconnected from Archipelago");
            std::strcpy(s_last_ap_state, "disconnected");
            pending_disconnect_bubble_at_ms_.store(0, std::memory_order_relaxed);
        }

        // Reset backoff after we've held a connection for kStableConnectMs.
        // Gated on backoff being elevated so this is a no-op in the steady
        // state. Done HERE (not on connect-success) so the bridge's
        // accept-then-reject path can't keep resetting backoff to retry_ms
        // and hammering the bridge at LAN line-rate.
        if (connected_at_ms > 0 && backoff_ms != target_.retry_ms &&
            ApState::nowMs() - connected_at_ms >= kStableConnectMs) {
            SMOAP_LOG_INFO("[conn] connection stable for >=%lldms; backoff "
                           "reset to %u ms",
                           static_cast<long long>(kStableConnectMs),
                           target_.retry_ms);
            backoff_ms = target_.retry_ms;
        }

        // Drain any frame-thread re-HELLO request before doing anything else.
        bool expected = true;
        if (rehello_requested_.compare_exchange_strong(expected, false)) {
            SMOAP_LOG_INFO("re-HELLO requested; cycling connection");
            disconnect();
            // M6 phase D — save-load-triggered re-HELLOs (the only producer
            // of requestRehello today) invalidate any in-flight deposits.
            // Vanilla's PayShine counter rolled back to the save state; the
            // bridge-side outstanding remains authoritative for AP credit
            // and will arrive as a fresh OutstandingMsg on the next HELLO.
            // Ordinary reconnect-after-network-drop does NOT take this path
            // — those keep unacked entries for replay.
            clearUnackedDeposits();
            ApState::instance().last_acked_deposit_seq.store(0, std::memory_order_relaxed);
        }

        if (socket_fd_ < 0) {
            connected_at_ms = 0;
            ApState::instance().conn.store(ConnState::Connecting);
            if (!connectOnce()) {
                SMOAP_LOG_WARN("connect failed; sleeping %u ms before retry", backoff_ms);
                svcSleepThread(static_cast<s64>(backoff_ms) * 1'000'000);  // ms -> ns
                backoff_ms = backoff_ms < kBackoffCapMs ? backoff_ms * 2 : kBackoffCapMs;
                continue;
            }
            connected_at_ms = ApState::nowMs();
            // Backoff reset is DEFERRED to the stability check at the top of
            // the loop. A "successful" TCP handshake followed by an immediate
            // app-layer ErrMsg(busy)+FIN from the bridge would otherwise reset
            // backoff every iteration; the deferred reset means a sustained
            // rejection cycle keeps backoff escalating.
            sendHello();
            // Two-part gate (see snapshot_pending declaration above):
            //   1. save_was_loaded — SaveLoadHook fired at least once.
            //   2. scene_cache != nullptr — HakoniwaSequence has a live
            //      curScene (player is in a stage), NOT on the file-select
            //      screen.
            // Why both: SaveLoadHook fires not just on user-initiated Load
            // Save / New Game, but also for every file-select preview render
            // on the title screen. On real Switch, SMO boots → file-select
            // renders all save previews → SaveLoadHook latches save_was_loaded
            // long before the user clicks anything; if we only gated on (1),
            // the snapshot would faithfully report the previous save's
            // moons/captures the moment the bridge connected, and AP would
            // credit them as fresh LocationChecks (observed 2026-05-18 with
            // 10 moons forwarded from a never-loaded save). curScene is null
            // at the file-select screen and becomes non-null once a real
            // stage instantiates, so requiring both filters out preview-fires
            // without needing to teach SaveLoadHook to distinguish them.
            //
            // If the gate is closed at connect time, the loop body's drain
            // block below polls scene_cache each iteration (~200 ms) and
            // sends the snapshot once both conditions are true. No extra
            // requestRehello round-trip needed — the existing socket is fine.
            {
                auto& s = ApState::instance();
                const bool save_ok = s.save_was_loaded.load(std::memory_order_acquire);
                const bool scene_ok = s.scene_cache.load(std::memory_order_relaxed) != nullptr;
                if (save_ok && scene_ok) {
                    sendSnapshot();
                    snapshot_pending = false;
                } else {
                    SMOAP_LOG_INFO("[conn] snapshot deferred (save_was_loaded=%d "
                                   "scene_loaded=%d); will retry from worker loop "
                                   "once both are true",
                                   static_cast<int>(save_ok),
                                   static_cast<int>(scene_ok));
                    snapshot_pending = true;
                }
            }
            ApState::instance().conn.store(ConnState::Hello);

            // M6 phase D — replay every unacked deposit so a reconnect-blip
            // (or a save-load-driven re-HELLO) doesn't lose the bridge-side
            // notification. The bridge's deposit handler is idempotent for
            // re-acks; seqs already applied in a previous HELLO session of
            // THIS bridge process get treated as fresh because the bridge
            // resets last_processed_seq on every HELLO (acceptable rare
            // double-apply across bridge restarts — see plan).
            {
                smoap::util::json::LineBuffer line;
                std::size_t replayed = 0;
                for (const auto& u : g_unacked_deposits) {
                    if (!u.slot_used) continue;
                    if (sendDepositMessage(socket_fd_, line, u.seq, u.kingdom,
                                           u.amount) < 0) {
                        SMOAP_LOG_WARN("[m6-deposit] replay send failed seq=%llu — "
                                       "will retry on next reconnect", u.seq);
                        break;
                    }
                    ++replayed;
                }
                if (replayed > 0) {
                    SMOAP_LOG_INFO("[m6-deposit] replayed %zu unacked deposits on reconnect",
                                   replayed);
                }
            }
        }

        // Wait up to recv_timeout_ms for inbound data.
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(socket_fd_, &rfds);
        struct timeval tv;
        tv.tv_sec  = static_cast<long>(target_.recv_timeout_ms / 1000);
        tv.tv_usec = static_cast<long>((target_.recv_timeout_ms % 1000) * 1000);
        const int sel = nn::socket::Select(socket_fd_ + 1, &rfds, nullptr, nullptr, &tv);

        // Lambda: if the connection died within kStableConnectMs of being
        // established, escalate backoff and sleep here so the next reconnect
        // attempt is paced. Bridge-rejection cycles ("busy" ErrMsg + FIN
        // immediately after handshake) would otherwise loop at ~100Hz with
        // no sleep, since connectOnce() returns true and the failure path
        // that increments backoff_ms is bypassed.
        auto quickBounceBackoff = [&]() {
            if (connected_at_ms <= 0) return;
            const auto held_ms = ApState::nowMs() - connected_at_ms;
            if (held_ms >= kStableConnectMs) return;
            backoff_ms = backoff_ms < kBackoffCapMs ? backoff_ms * 2 : kBackoffCapMs;
            SMOAP_LOG_WARN("[conn] quick bounce after only %lldms held; "
                           "backoff -> %u ms; sleeping",
                           static_cast<long long>(held_ms), backoff_ms);
            svcSleepThread(static_cast<s64>(backoff_ms) * 1'000'000);
        };

        if (sel < 0) {
            SMOAP_LOG_WARN("Select returned error; reconnecting");
            quickBounceBackoff();
            disconnect();
            continue;
        }
        if (sel > 0 && FD_ISSET(socket_fd_, &rfds)) {
            if (!recvIntoBuf()) {
                SMOAP_LOG_WARN("recv error or peer closed; reconnecting");
                quickBounceBackoff();
                disconnect();
                continue;
            }
        }

        // Drain ALL complete lines from read_buf_ each iteration. When the
        // bridge sends N messages in a single TCP push (e.g. hello_ack +
        // checked_replay + ap_state at handshake time, or a kill arriving
        // right after a queued item), Select only fires once on the socket;
        // we must walk the buffer to completion or the trailing messages
        // wait indefinitely for the next socket event before being parsed.
        // Also drains leftover lines on iterations where Select timed out.
        char line_buf[kInboundLineCap];
        std::size_t line_len = 0;
        while (popLine(line_buf, line_len)) {
            handleLine(line_buf, line_len);
        }

        // Late-arriving snapshot send: at HELLO time the gate may have been
        // closed because the player was still on the file-select screen
        // (scene_cache null). Poll scene_cache here — the frame-thread main
        // loop updates it each frame — and send the snapshot the first time
        // both halves of the gate are true. Polling cadence is recv_timeout_ms
        // (default 200 ms), which the user won't perceive between entering a
        // stage and the snapshot landing on the bridge.
        if (snapshot_pending && socket_fd_ >= 0) {
            auto& s = ApState::instance();
            const bool save_ok = s.save_was_loaded.load(std::memory_order_acquire);
            const bool scene_ok = s.scene_cache.load(std::memory_order_relaxed) != nullptr;
            if (save_ok && scene_ok) {
                SMOAP_LOG_INFO("[conn] snapshot gate now open "
                               "(save_was_loaded=1 scene_loaded=1); sending");
                sendSnapshot();
                snapshot_pending = false;
            }
        }

        pumpOnce();
    }

    SMOAP_LOG_INFO("ApClient worker exiting");
    disconnect();
}

bool ApClient::connectOnce() {
    SMOAP_LOG_INFO("[conn] Socket(AF_INET, SOCK_STREAM, 0)");
    socket_fd_ = nn::socket::Socket(kAfInet, kSockStream, 0);
    SMOAP_LOG_INFO("[conn] Socket returned fd=%d", socket_fd_);
    if (socket_fd_ < 0) {
        const int err = nn::socket::GetLastErrno();
        SMOAP_LOG_WARN("[conn] Socket() failed errno=%d", err);
        socket_fd_ = -1;
        return false;
    }

    // Nintendo's `sockaddr` is 16 bytes with sa_family as a single byte at
    // offset 1 (after a length-byte at offset 0) — NOT byte-equivalent to
    // POSIX `sockaddr_in` (8 bytes, sin_family as u16 at offset 0). Passing
    // sockaddr_in to nn::socket::Connect makes bsd read byte 1 (= 0 for our
    // AF_INET=2 LE-encoded value) as the family, returns EINVAL.
    sockaddr addr{};
    addr.family = static_cast<u8>(kAfInet);
    addr.port   = nn::socket::InetHtons(target_.port);
    if (nn::socket::InetAton(target_.host.c_str(), &addr.address) == 0) {
        SMOAP_LOG_WARN("[conn] InetAton failed for %s", target_.host.c_str());
        nn::socket::Close(socket_fd_);
        socket_fd_ = -1;
        return false;
    }
    SMOAP_LOG_INFO("[conn] connecting to %s:%u", target_.host.c_str(), target_.port);

    const Result rc = nn::socket::Connect(socket_fd_, &addr, sizeof(addr));
    if (R_FAILED(rc)) {
        const int err = nn::socket::GetLastErrno();
        SMOAP_LOG_WARN("[conn] Connect FAILED rc=0x%x errno=%d (host=%s port=%u fd=%d)",
                       rc, err, target_.host.c_str(), target_.port, socket_fd_);
        nn::socket::Close(socket_fd_);
        socket_fd_ = -1;
        return false;
    }

    const int keepalive = 1;
    nn::socket::SetSockOpt(socket_fd_, kSolSocket, kSoKeepAlive,
                           &keepalive, sizeof(keepalive));

    SMOAP_LOG_INFO("[conn] CONNECTED to %s:%u (fd=%d)",
                   target_.host.c_str(), target_.port, socket_fd_);
    return true;
}

void ApClient::disconnect() {
    if (socket_fd_ >= 0) {
        nn::socket::Close(socket_fd_);
        socket_fd_ = -1;
    }
    read_buf_len_ = 0;
    auto& st = ApState::instance();
    st.conn.store(ConnState::Disconnected);
    // M6 phase D: clear bridge_connected so ShineNumGetHook freezes the HUD
    // to 0 and AddPayShineHook stops acting. Unacked deposits remain in
    // pending_deposits ring + unacked tracking; they'll replay after
    // reconnect.
    st.bridge_connected.store(false, std::memory_order_relaxed);
    // Cappy "Disconnected" bubble policy. Three paths:
    //   (a) rehello window: suppress + commit state transition. The
    //       matching "Connected" on reconnect is suppressed by the same
    //       window on the ap_state path.
    //   (b) was-ready + TCP-blip path: DEFER. Arm the grace timer and
    //       leave s_last_ap_state at "ready". The worker loop fires the
    //       bubble + commits the transition if grace expires. If
    //       ap_state(ready) arrives first, it clears the timer and the
    //       was_ready=true,now_ready=true comparison fires no bubble.
    //   (c) not previously ready: nothing to fire; just commit state.
    const bool suppress = ApState::nowMs() <
        suppress_state_bubble_until_ms_.load(std::memory_order_relaxed);
    if (std::strcmp(s_last_ap_state, "ready") == 0) {
        if (suppress) {
            SMOAP_LOG_INFO("[bubble] suppressing 'Disconnected from Archipelago' "
                           "(rehello window)");
            std::strcpy(s_last_ap_state, "disconnected");
        } else {
            // Use CAS so a re-disconnect during an already-pending grace
            // window doesn't push the deadline out — we want the bubble
            // to fire on the ORIGINAL schedule even if instability causes
            // multiple short drops in a row.
            std::int64_t expected = 0;
            const std::int64_t target = ApState::nowMs() + kDisconnectGracePeriodMs;
            if (pending_disconnect_bubble_at_ms_.compare_exchange_strong(
                    expected, target, std::memory_order_relaxed)) {
                SMOAP_LOG_INFO("[bubble] deferring 'Disconnected from "
                               "Archipelago' (%lldms grace for TCP recovery)",
                               static_cast<long long>(kDisconnectGracePeriodMs));
            }
            // s_last_ap_state stays "ready" so a quick ap_state(ready)
            // recovery hits the was_ready=true,now_ready=true silent path.
        }
    } else {
        std::strcpy(s_last_ap_state, "disconnected");
    }
}

void ApClient::sendHello() {
    Hello hello;
    hello.mod_ver = SMO_AP_MOD_VERSION_STRING;
    hello.smo_ver = SMO_VERSION_STRING;
    smoap::util::json::LineBuffer line;
    encodeHello(line, hello);
    SMOAP_LOG_INFO("[conn] sending HELLO (%zu bytes)", line.size());
    const int sent = nn::socket::Send(socket_fd_, line.data(), line.size(), 0);
    SMOAP_LOG_INFO("[conn] HELLO send returned %d", sent);
}

namespace {

// Per-stage shine accumulator used by sendSnapshot's enumeration callback.
// We bucket shines by stage_name so each kingdom emits one StateChunk message
// (instead of one chunk per shine), keeping wire chatter low and respecting
// the 8 KiB per-line cap. Fixed buffers throughout (M6.1: worker thread can
// never grow a std::string past SSO or push_back into a std::vector).
struct SnapshotBuilder {
    int sock_fd = -1;
    StateChunk current;
    bool current_active = false;
    smoap::util::json::LineBuffer line;  // reused across chunks

    void flushIfNeeded(const char* stage) {
        if (current_active && std::strcmp(current.stage_name, stage) != 0) {
            encodeStateChunk(line, current);
            nn::socket::Send(sock_fd, line.data(), line.size(), 0);
            current = StateChunk{};
            current_active = false;
        }
    }
    void addShine(const char* stage, const char* obj, int uid) {
        if (!stage || !*stage) return;
        flushIfNeeded(stage);
        if (!current_active) {
            copyFixedFieldN(current.stage_name, stage, std::strlen(stage));
            current_active = true;
        }
        if (current.shine_count >= static_cast<int>(kSnapshotMaxShinesPerStage)) {
            SMOAP_LOG_WARN("[snapshot] shines/stage cap (%d) hit for '%s' — dropping",
                           static_cast<int>(kSnapshotMaxShinesPerStage),
                           current.stage_name);
            return;
        }
        ShineEntry& s = current.shines[current.shine_count++];
        if (obj) copyFixedFieldN(s.object_id, obj, std::strlen(obj));
        s.shine_uid = uid;
    }
    void finalize() {
        if (current_active) {
            encodeStateChunk(line, current);
            nn::socket::Send(sock_fd, line.data(), line.size(), 0);
            current_active = false;
        }
    }
};

}  // namespace

void ApClient::sendSnapshot() {
    auto& st = ApState::instance();
    smoap::util::json::LineBuffer line;  // reused across all snapshot messages

    // 1) state_begin
    {
        StateBegin b;
        b.mod_ver = SMO_AP_MOD_VERSION_STRING;
        // M4.5 has no save-slot accessor wired up yet; M5/M6 will populate
        // this from GameDataHolder. -1 omits the field on the wire.
        b.save_slot = -1;
        encodeStateBegin(line, b);
        if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) {
            SMOAP_LOG_WARN("[snapshot] state_begin send failed; aborting");
            return;
        }
    }

    // 2) per-stage chunks. M4.5 stub for enumerateOwnedShines emits nothing,
    //    so the only wire output here is when M5/M6 lands the real impl.
    SnapshotBuilder builder{};
    builder.sock_fd = socket_fd_;
    smoap::game::enumerateOwnedShines(
        [](void* ctx, const char* stage, const char* obj, int uid) {
            auto* b = static_cast<SnapshotBuilder*>(ctx);
            b->addShine(stage, obj, uid);
        },
        &builder);
    builder.finalize();

    // 3) _meta chunk: cross-stage state. Always emitted so the bridge sees
    //    the goal flag (and so we have a "snapshot is complete" canary).
    {
        StateChunk meta;
        copyFixedFieldN(meta.stage_name, "_meta", 5);
        smoap::game::enumerateOwnedCaptures(
            [](void* ctx, const char* hack) {
                auto* m = static_cast<StateChunk*>(ctx);
                if (!hack || !*hack) return;
                if (m->capture_count >= static_cast<int>(kSnapshotMaxCaptures)) {
                    SMOAP_LOG_WARN("[snapshot] captures cap (%d) hit — dropping '%s'",
                                   static_cast<int>(kSnapshotMaxCaptures), hack);
                    return;
                }
                copyFixedFieldN(m->captures[m->capture_count++], hack, std::strlen(hack));
            },
            &meta);
        meta.include_goal_reached = true;
        meta.goal_reached = st.goal_sent;
        encodeStateChunk(line, meta);
        nn::socket::Send(socket_fd_, line.data(), line.size(), 0);
    }

    // 4) state_end
    encodeStateEnd(line);
    nn::socket::Send(socket_fd_, line.data(), line.size(), 0);
    SMOAP_LOG_INFO("[conn] snapshot sent");
}

void ApClient::pumpOnce() {
    // Peek-then-pop: a failed Send leaves the entry queued for the next pump
    // cycle. Combined with the snapshot on (re)connect, this means brief
    // disconnects don't lose outbound checks (the deque covers the gap; the
    // snapshot covers anything beyond it).
    auto& st = ApState::instance();
    smoap::util::json::LineBuffer line;  // reused across loop iterations
    Check c;
    while (st.outbound_checks.peek(c)) {
        encodeCheck(line, c);
        SMOAP_LOG_INFO("[pump] peek check kind=%d stage=%s obj=%s (line=%u bytes)",
                       static_cast<int>(c.kind),
                       c.stage_name[0] ? c.stage_name : "<empty>",
                       c.object_id[0] ? c.object_id : "<empty>",
                       static_cast<unsigned>(line.size()));
        const int n = nn::socket::Send(socket_fd_, line.data(), line.size(), 0);
        if (n < 0) {
            SMOAP_LOG_WARN("[pump] check Send returned %d; leaving in queue for retry", n);
            return;
        }
        SMOAP_LOG_INFO("[pump] check Send returned %d (sent %u bytes)", n,
                       static_cast<unsigned>(line.size()));
        st.outbound_checks.popDiscard();
    }
    StatusEvent e;
    while (st.outbound_status.peek(e)) {
        if (e.goal) {
            encodeGoal(line);
            if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) return;
        }
        if (e.death) {
            // The Switch doesn't have a useful wall-clock; the bridge stamps
            // time when it converts the death to an AP Bounce. Send ts_ms=0
            // and let the bridge fill it in.
            Death d{.ts_ms = e.ts_ms};
            encodeDeath(line, d);
            if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) return;
            // Clear the debounce flag so the next death can be reported.
            st.death_pending_send.store(false, std::memory_order_release);
        }
        st.outbound_status.popDiscard();
    }

    // Drain outbound_logs: every smoap::util::log() call above the configured
    // threshold landed here from any thread (frame, worker, hooks). Best-
    // effort delivery — if a Send fails we leave the entry queued for the
    // next pump cycle, identical to outbound_checks. We do NOT log around
    // the send loop itself (would re-enter into the same ring; the re-entry
    // guard in Log.cpp covers it but logging here adds zero diagnostic
    // value). Drains run BEFORE the deposit loop so a log-storm doesn't
    // starve deposits if the consumer side is slow.
    if (const std::uint32_t drops = st.log_drops.exchange(0, std::memory_order_relaxed); drops > 0) {
        // Surface the drop count as a one-shot WARN line so the gap is
        // visible in the tab. Built directly (not via SMOAP_LOG_*) so we
        // don't pump the synthesized line back into our own ring.
        Log marker;
        copyFixedField(marker.level, "warn");
        std::snprintf(marker.msg, kLogMsgCap,
                      "[log_forward] %u log line(s) dropped (ring full)", drops);
        encodeLog(line, marker);
        if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) {
            // Re-arm so the next pump retries. Fold back into the counter.
            st.log_drops.fetch_add(drops, std::memory_order_relaxed);
            return;
        }
    }
    Log lg;
    while (st.outbound_logs.peek(lg)) {
        encodeLog(line, lg);
        if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) return;
        st.outbound_logs.popDiscard();
    }

    // M6 phase D — drain pending_deposits: copy into worker-local unacked
    // array, then transmit to bridge. The unacked array survives across
    // reconnects (a save-load-driven re-HELLO would clear it via
    // clearUnackedDeposits in the requestRehello path; ordinary disconnects
    // do not, so we replay on reconnect).
    ApState::PendingDeposit pd;
    while (st.pending_deposits.pop(pd)) {
        if (!stashUnackedDeposit(pd)) {
            SMOAP_LOG_WARN("[m6-deposit] unacked array full, dropping seq=%llu "
                           "kingdom=%s amount=%d",
                           pd.seq, pd.kingdom, pd.amount);
            // Don't send — losing the ack tracking but vanilla state already
            // applied. Better to drop than send something we can't track.
            continue;
        }
        const int n = sendDepositMessage(socket_fd_, line, pd.seq, pd.kingdom,
                                         pd.amount);
        if (n < 0) {
            SMOAP_LOG_WARN("[m6-deposit] send seq=%llu failed; will retry on reconnect", pd.seq);
            // Entry stays in g_unacked_deposits, will replay after reconnect.
            return;
        }
        SMOAP_LOG_INFO("[m6-deposit] sent seq=%llu kingdom=%s amount=%d (%d bytes)",
                       pd.seq, pd.kingdom, pd.amount, n);
    }
}

bool ApClient::recvIntoBuf() {
    // Cap incoming read at the headroom remaining in read_buf_. If we already
    // hold a partial unterminated line that fills the buffer, the next call
    // to popLine will see the overflow and reset.
    const std::size_t avail = kInboundLineCap - read_buf_len_;
    if (avail == 0) {
        // No headroom — popLine's overflow guard will reset us next call.
        // Recv into a small throwaway buffer so we don't stall the socket.
        char drain[256];
        nn::socket::Recv(socket_fd_, drain, sizeof(drain), 0);
        return true;
    }
    const std::size_t cap = avail < 1024 ? avail : 1024;
    const int n = nn::socket::Recv(socket_fd_, read_buf_ + read_buf_len_,
                                   cap, 0);
    if (n <= 0) {
        // Distinguish clean EOF (n==0, peer sent FIN) from socket-level
        // failures (n<0, errno explains why). Common values on this stack:
        //   ECONNRESET (104): peer sent RST — SMOClient crashed, or the
        //     kernel surfaced a stale half-open connection.
        //   ENETUNREACH (101): IP route gone — Switch Wi-Fi blip.
        //   ETIMEDOUT (110): TCP keepalive or retransmit timer expired.
        // We log the raw values rather than translating: errno mappings
        // are libc-version-dependent and the log goes to bridge analysis
        // anyway, where the mapping is stable.
        const int err = (n < 0) ? nn::socket::GetLastErrno() : 0;
        SMOAP_LOG_WARN("[recv] Recv -> %d errno=%d (%s)",
                       n, err,
                       n == 0 ? "clean EOF from peer"
                              : "socket error");
        return false;
    }
    read_buf_len_ += static_cast<std::size_t>(n);
    return true;
}

bool ApClient::popLine(char* out, std::size_t& out_len) {
    // Find first '\n' in [0, read_buf_len_).
    std::size_t nl = read_buf_len_;
    for (std::size_t i = 0; i < read_buf_len_; ++i) {
        if (read_buf_[i] == '\n') { nl = i; break; }
    }
    if (nl == read_buf_len_) {
        // Cap runaway lines so a malformed peer can't grow the buffer forever.
        if (read_buf_len_ >= kInboundLineCap) {
            SMOAP_LOG_WARN("read_buf overflow without newline; resyncing");
            read_buf_len_ = 0;
        }
        return false;
    }
    // Copy line bytes to caller. Bounded by kInboundLineCap on both sides.
    for (std::size_t i = 0; i < nl; ++i) out[i] = read_buf_[i];
    out_len = nl;
    // Shift any remaining bytes left over the consumed line + '\n'.
    const std::size_t consumed = nl + 1;
    const std::size_t remaining = read_buf_len_ - consumed;
    for (std::size_t i = 0; i < remaining; ++i) {
        read_buf_[i] = read_buf_[consumed + i];
    }
    read_buf_len_ = remaining;
    return true;
}

void ApClient::handleLine(char* line, std::size_t line_len) {
    // Reader decodes escapes in place — caller's buffer is already mutable.
    //
    // DecodedMsg holds large fixed-size buffers (notably CheckedReplay::ids,
    // 128 × sizeof(ItemRef) ≈ 65 KiB). Stack-allocating it from handleLine
    // would blow the worker thread's 64 KiB stack. As a function-local
    // static it lives in BSS — single instance reused across calls. Safe
    // because handleLine is only ever called from the worker thread, and we
    // dispatch on m.t so stale variant fields from a previous call are
    // never read (each `decode` writes whichever variant matches its `t`).
    static DecodedMsg m;
    if (!decode(line, line_len, m)) {
        SMOAP_LOG_WARN("malformed message from bridge: %.*s",
                       static_cast<int>(line_len), line);
        return;
    }
    // Tiny strcmp wrapper — m.t is char[] now, not std::string.
    auto eq = [](const char* a, const char* b) {
        while (*a && *b && *a == *b) { ++a; ++b; }
        return *a == '\0' && *b == '\0';
    };
    if (eq(m.t, "hello_ack")) {
        auto& st = ApState::instance();
        // Publish local_slot + deathlink_enabled BEFORE the conn.store(Ready)
        // release. The frame thread observes conn == Ready first (acquire),
        // then reads local_slot — no separate fence needed. Toast filter
        // compares item.from against local_slot to skip self-grants.
        // M6.1: HelloAck::slot is now a fixed char[] (not std::string) —
        // no .c_str() needed. snprintf still safest for length-bounded copy.
        std::snprintf(st.local_slot, sizeof(st.local_slot),
                      "%s", m.hello_ack.slot);
        st.deathlink_enabled.store(m.hello_ack.deathlink_enabled, std::memory_order_relaxed);
        st.conn.store(ConnState::Ready, std::memory_order_release);
        // M6 phase D: bridge_connected gates AddPayShineHook + ShineNumGetHook.
        // Set AFTER conn.store so the same release fence orders both.
        st.bridge_connected.store(true, std::memory_order_release);
        SMOAP_LOG_INFO("hello_ack: ok=%d seed=%s slot=%s deathlink_enabled=%d",
                       m.hello_ack.ok ? 1 : 0,
                       m.hello_ack.seed,
                       m.hello_ack.slot,
                       m.hello_ack.deathlink_enabled ? 1 : 0);
    } else if (eq(m.t, "checked_replay")) {
        for (std::size_t i = 0; i < m.checked_replay.id_count; ++i) {
            const auto& ref = m.checked_replay.ids[i];
            Check synth{};
            synth.kind = ref.kind;
            copyCheckField(synth.kingdom, ref.kingdom);
            // ref.shine_id is char[kMediumFieldCap=128]; copyCheckField truncates
            // to kCheckFieldCap=64. Shine ids historically fit in 64 (see Check's
            // existing kCheckFieldCap shine_id field) so this is consistent.
            copyCheckField(synth.shine_id, ref.shine_id);
            copyCheckField(synth.cap, ref.cap);
            ApState::instance().locations_checked.tryInsert(ApState::hashCheck(synth));
        }
        if (m.checked_replay.truncated) {
            SMOAP_LOG_WARN("checked_replay: TRUNCATED at %u entries — bridge "
                           "sent more than CheckedReplay::kMaxIds; bump that "
                           "cap if this becomes routine",
                           static_cast<unsigned>(m.checked_replay.id_count));
        } else {
            SMOAP_LOG_INFO("checked_replay: %u entries",
                           static_cast<unsigned>(m.checked_replay.id_count));
        }
    } else if (eq(m.t, "item")) {
        ApState::instance().inbound.push(m.item);
    } else if (eq(m.t, "ap_state")) {
        // Cappy bubble on ready <-> disconnected transitions. Intermediate
        // states (waiting_for_switch / connecting / authed) stay silent so
        // reconnect flaps don't spam the queue. The s_last_ap_state tracker
        // lives at file scope so the bridge-TCP-disconnect path (disconnect())
        // can also publish the bubble + reset the tracker — covers the case
        // where SMOClient dies without sending a graceful ap_state.
        const bool was_ready = (std::strcmp(s_last_ap_state, "ready") == 0);
        const bool now_ready = (std::strcmp(m.ap_state.conn, "ready") == 0);
        const bool now_disconnected =
            (std::strcmp(m.ap_state.conn, "disconnected") == 0);
        // Honor the rehello suppression window — the matching disconnect()
        // path skips its bubble within the same window, so a save-load round
        // trip stays silent on both ends. After the window expires, normal
        // transition bubbles resume (covers SMOClient genuinely dying).
        const bool bubble_suppressed = ApState::nowMs() <
            suppress_state_bubble_until_ms_.load(std::memory_order_relaxed);
        // Always cancel any deferred TCP-blip bubble — the bridge is alive
        // and authoritatively reporting AP state, so either:
        //   - now=ready: silent recovery, bubble was unnecessary
        //   - now=disconnected: the was_ready/now_disconnected branch below
        //     fires the bubble now via the graceful path; the deferred timer
        //     would double-fire if left armed.
        if (pending_disconnect_bubble_at_ms_.exchange(
                0, std::memory_order_relaxed) > 0) {
            SMOAP_LOG_INFO("[bubble] cancelling deferred 'Disconnected' "
                           "(ap_state=%s arrived inside grace window)",
                           m.ap_state.conn);
        }
        if (!was_ready && now_ready) {
            if (bubble_suppressed) {
                SMOAP_LOG_INFO("[bubble] suppressing 'Connected to Archipelago' "
                               "(rehello window)");
            } else {
                smoap::ui::CappyMessenger::instance()
                    .enqueueSystem("Connected to Archipelago");
            }
        } else if (was_ready && now_disconnected) {
            if (bubble_suppressed) {
                SMOAP_LOG_INFO("[bubble] suppressing 'Disconnected from Archipelago' "
                               "(rehello window, ap_state path)");
            } else {
                smoap::ui::CappyMessenger::instance()
                    .enqueueSystem("Disconnected from Archipelago");
            }
        }
        std::size_t i = 0;
        while (i + 1 < sizeof(s_last_ap_state) && m.ap_state.conn[i] != '\0') {
            s_last_ap_state[i] = m.ap_state.conn[i];
            ++i;
        }
        s_last_ap_state[i] = '\0';
    } else if (eq(m.t, "print")) {
        SMOAP_LOG_INFO("[bridge] %s", m.print.text);
    } else if (eq(m.t, "pong")) {
        // Liveness ack — could update last_rx_ns here in a future iteration.
    } else if (eq(m.t, "err")) {
        SMOAP_LOG_WARN("bridge err code=%s ctx=%s", m.err.code, m.err.ctx);
    } else if (eq(m.t, "kill")) {
        // Inbound DeathLink. Collapse to a single pending-bit; multiple
        // bounces between frames overwrite each other (producer-side debounce).
        // The frame thread's maybeApplyInboundKill handles the
        // "Mario already dying" / "too soon since last kill" / "deathlink
        // disabled in hello_ack" / "no cached PlayerHitPointData yet" gates.
        SMOAP_LOG_INFO("[deathlink in] queued source=%s cause=%s",
                       m.kill.source, m.kill.cause);
        ApState::instance().inbound_kill_pending.store(true, std::memory_order_release);
    } else if (eq(m.t, "moon_label")) {
        // M6 phase A.5 — Channel A. Publish the label for the upcoming
        // cutscene to consume. Deadline = now + valid_for_ms; the frame
        // thread drops anything past deadline.
        const auto now = ApState::nowMs();
        const auto deadline = (m.moon_label.valid_for_ms > 0)
            ? now + m.moon_label.valid_for_ms
            : 0;  // 0 = never expire (use sparingly; bridge default is 4000)
        SMOAP_LOG_INFO("[moon_label] seq=%d text='%s' valid_for=%dms",
                       m.moon_label.seq,
                       m.moon_label.text,  // char[] decays to const char*
                       m.moon_label.valid_for_ms);
        ApState::instance().setPendingMoonLabel(
            m.moon_label.text, m.moon_label.seq, deadline);
    } else if (eq(m.t, "cappy")) {
        // Capturesanity check announcement. Bridge composes the verbatim
        // bubble text ("Got Goomba!" / "Sent Frog -> Player2") and we
        // route it straight into the speech-bubble queue. Empty text is
        // a no-op (enqueueSystem guards).
        SMOAP_LOG_INFO("[cappy] system bubble text='%s'", m.cappy.text);
        smoap::ui::CappyMessenger::instance().enqueueSystem(m.cappy.text);
    } else if (eq(m.t, "shine_scouts")) {
        // AP-classification moon color. Bridge sends one or more chunks of
        // (shine_uid -> palette) after AP LocationInfo lands, and a full
        // replay on every HELLO. Push each into the SPSC ring; the frame
        // thread folds them into ApState::shine_palette in applyOnFrame.
        // Ring is sized 4096 — well above the seed's ~565 moons — so a full
        // replay in one HELLO won't backpressure.
        auto& ring = ApState::instance().inbound_scouts;
        std::size_t pushed = 0, dropped = 0;
        for (std::size_t i = 0; i < m.shine_scouts.entry_count; ++i) {
            if (ring.push(m.shine_scouts.entries[i])) {
                ++pushed;
            } else {
                ++dropped;
            }
        }
        if (m.shine_scouts.truncated) {
            SMOAP_LOG_WARN("[shine-color] shine_scouts chunk truncated at %zu entries; "
                           "bridge sent more than ShineScouts::kMaxEntries — bump cap",
                           m.shine_scouts.entry_count);
        }
        SMOAP_LOG_INFO("[shine-color] enqueued %zu palette entries (dropped %zu)",
                       pushed, dropped);
    } else if (eq(m.t, "deposit_ack")) {
        // M6 phase D — bridge confirmed this deposit. Clear the matching
        // slot from g_unacked_deposits + advance last_acked_deposit_seq for
        // observability (SaveLoadHook reads it for diagnostics; not the
        // ground truth for replay).
        const std::uint64_t seq = m.deposit_ack.seq;
        std::size_t cleared = 0;
        for (auto& u : g_unacked_deposits) {
            if (u.slot_used && u.seq == seq) {
                u.slot_used = false;
                u.seq = 0;
                u.kingdom[0] = '\0';
                u.amount = 0;
                ++cleared;
            }
        }
        auto& st = ApState::instance();
        // High-water mark — never go backwards on out-of-order acks (bridge
        // is in-order today but be defensive).
        std::uint64_t cur = st.last_acked_deposit_seq.load(std::memory_order_relaxed);
        while (seq > cur && !st.last_acked_deposit_seq.compare_exchange_weak(
                   cur, seq, std::memory_order_relaxed)) {}
        SMOAP_LOG_INFO("[m6-deposit] ack seq=%llu (cleared %zu unacked slot%s)",
                       seq, cleared, cleared == 1 ? "" : "s");
    } else if (eq(m.t, "outstanding")) {
        // M6 phase D — bridge-authoritative per-kingdom balance. Overwrite
        // ap_moons_kingdom[bit] for each entry the bridge sent (kingdoms
        // not present in the message are LEFT UNTOUCHED, allowing partial
        // updates if a future bridge optimization sends only deltas).
        auto& st = ApState::instance();
        std::size_t applied = 0;
        for (std::size_t i = 0; i < m.outstanding.entry_count; ++i) {
            const auto& entry = m.outstanding.entries[i];
            if (entry.kingdom[0] == '\0') continue;
            const std::uint8_t bit = smoap::game::kingdomBitFor(entry.kingdom);
            if (bit >= 17) {
                SMOAP_LOG_WARN("[m6-outstanding] unknown kingdom='%s' count=%d",
                               entry.kingdom, entry.count);
                continue;
            }
            const int v = (entry.count < 0) ? 0 : entry.count;
            st.ap_moons_kingdom[bit].store(v, std::memory_order_relaxed);
            ++applied;
        }
        SMOAP_LOG_INFO("[m6-outstanding] applied %zu kingdom balances", applied);
    } else {
        SMOAP_LOG_WARN("unknown message t=%s", m.t);
    }
}

}  // namespace smoap::ap
