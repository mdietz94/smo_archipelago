// TCP client to the PC bridge.
//
// Owns a single TCP connection on a dedicated worker thread. The frame
// thread (drawMain trampoline) only touches ApState's lock-free SPSC
// rings; this thread does all blocking I/O.
//
// Socket session: we use nn::socket::* directly (no parallel hk::socket
// client + no parallel sm: session). main.cpp's GameSystemInit pre-orig
// hook calls nn::socket::Initialize with our own 6MB+128KB pool and
// installs a no-op trampoline at the SMO-side Initialize so the game
// can't re-init / clobber the pool. By the time initNetworking() runs
// post-orig, nn::socket is fully up. Pattern from Kgamer77/
// SMOO-Plus-Hakkun:main.cpp — fixes KernelResult_OutOfSessions seen on
// retail when opening a second sm: connection via hk::sm.
//
// Thread sequence:
//   1. start() (called from frame thread inside GameSystemInit hook):
//      saves target, spawns worker, returns immediately.
//   2. initNetworking() (frame thread, before start): nn::nifm bring-up
//      only — socket session was already established pre-orig.
//   3. threadMain() loop: connectOnce -> sendHello -> poll+recv read,
//      pumpOnce drain outbound, error-on-disconnect with backoff retry.

#include "ApClient.hpp"

#include <cstdint>
#include <cstring>
#include <new>

#include "hk/os/Thread.h"
#include "hk/svc/api.h"
#include "hk/svc/cpu.h"
#include "hk/types.h"

#include "ApDiscovery.hpp"
#include "ApProtocol.hpp"
#include "ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../game/MoonApply.hpp"
#include "../ui/ApDebugConsole.hpp"
#include "../ui/CappyMessenger.hpp"
#include "../util/Log.hpp"

// nn::nifm — sail resolves these against SMO's dynsym. See
// switch-mod/syms/nn/nifm.sym for the matching mangled entries.
namespace nn::nifm {
    u32 Initialize();
    void SubmitNetworkRequestAndWait();
    bool IsNetworkAvailable();
}  // namespace nn::nifm

// Nintendo bsd:u sockaddr layout. NOT POSIX. Names MUST be `sockaddr` /
// `in_addr` / `pollfd` at file scope — the mangled symbols sail resolves
// against main.nso (e.g. _ZN2nn6socket7ConnectEiPK8sockaddrj) encode these
// literal type names. Layout per lunakit-vendor src/nn/socket.hpp.
struct in_addr { u32 s_addr; };
struct sockaddr {
    u8 sa_len;
    u8 sa_family;
    u16 sa_port;   // network byte order
    in_addr sa_addr;
    u8 sa_zero[8];
};
struct pollfd { s32 fd; short events; short revents; };

// nn::socket — sail resolves against SMO's dynsym. See syms/nn/socket.sym.
// Initialize is NOT declared here (main.cpp owns it).
namespace nn::socket {
    s32 Socket(s32 domain, s32 type, s32 protocol);
    u32 Connect(s32 socket, const ::sockaddr* addr, u32 addrLen);
    s32 Send(s32 socket, const void* data, unsigned long len, s32 flags);
    s32 Recv(s32 socket, void* out, unsigned long len, s32 flags);
    s32 SendTo(s32 socket, const void* data, unsigned long len, s32 flags,
               const ::sockaddr* addr, u32 addrLen);
    s32 RecvFrom(s32 socket, void* out, unsigned long len, s32 flags,
                 ::sockaddr* addr, u32* addrLen);
    s32 SetSockOpt(s32 socket, s32 level, s32 option, const void* val, u32 len);
    u32 Close(s32 socket);
    s32 Poll(::pollfd* fds, unsigned long n, s32 timeout_ms);
    u16 InetHtons(u16 val);
    s32 InetAton(const char* str, ::in_addr* out);
    s32 GetLastErrno();
}  // namespace nn::socket

namespace smoap::ap {

namespace {

// Socket-option levels and names. Hakkun doesn't re-export the BSD constants;
// the values match Nintendo's bsd:u service (which derives from FreeBSD).
constexpr s32 kSolSocket   = 0xffff;
constexpr s32 kSoKeepAlive = 0x0008;

// Worker thread stack — 64 KiB, page-aligned. Bigger than Hakkun's 4 KiB
// default because handleLine's static DecodedMsg is 64 KiB on its own and
// we also need headroom for json encode/decode + LineBuffer copies.
constexpr std::size_t kWorkerStackSize = 0x10000;
alignas(0x1000) u8 g_worker_stack[kWorkerStackSize];

// In-place storage for the hk::os::Thread. The Thread ctor immediately
// creates the kernel thread (svc::CreateThread), so we lazy-construct via
// placement new from start() rather than at file-static init time.
alignas(hk::os::Thread) char g_worker_thread_storage[sizeof(hk::os::Thread)];
hk::os::Thread* g_worker_thread = nullptr;

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

// Worker-side system-bubble emitter. Pushes the text onto ApState's
// inbound_system_bubbles SPSC ring; drawMain (frame thread) drains and
// calls CappyMessenger::enqueueSystem from there. Direct worker-thread
// calls into CappyMessenger crash Ryujinx ARMeilleure's JIT (the queue_
// non-atomic state races with frame-thread tryPump reads); production
// exlaunch survived the race, our Hakkun build doesn't.
void enqueueSystemBubble(const char* text) {
    if (!text || !*text) return;
    ApState::SystemBubble msg;
    std::size_t i = 0;
    while (i + 1 < sizeof(msg.text) && text[i] != '\0') {
        msg.text[i] = text[i];
        ++i;
    }
    msg.text[i] = '\0';
    ApState::instance().inbound_system_bubbles.push(msg);
}

// Thin wrappers around nn::socket so the worker-loop call sites stay
// close to the production code shape. Each wrapper returns BSD-style
// (ret, errno):
//   ret >= 0  -> success, value depends on op (bytes / fd / 0)
//   ret  < 0  -> failure, errno carries the reason
struct SockResult {
    s32 ret;
    s32 err;
};

constexpr s32 kAfInet      = 2;
constexpr s32 kSockStream  = 1;
constexpr s32 kSockDgram   = 2;
constexpr s32 kIpprotoTcp  = 6;
constexpr s32 kIpprotoUdp  = 17;

constexpr s32 kPollIn      = 0x0001;  // POLLIN / POLLRDNORM
constexpr s32 kPollErr     = 0x0008;
constexpr s32 kPollHup     = 0x0010;

SockResult sockSocketTcp() {
    const s32 fd = nn::socket::Socket(kAfInet, kSockStream, kIpprotoTcp);
    if (fd < 0) return {fd, nn::socket::GetLastErrno()};
    return {fd, 0};
}

SockResult sockSocketUdp() {
    const s32 fd = nn::socket::Socket(kAfInet, kSockDgram, kIpprotoUdp);
    if (fd < 0) return {fd, nn::socket::GetLastErrno()};
    return {fd, 0};
}

SockResult sockConnect(s32 fd, const ::sockaddr& addr) {
    const u32 rc = nn::socket::Connect(fd, &addr, sizeof(addr));
    if (rc != 0) return {-1, nn::socket::GetLastErrno()};
    return {0, 0};
}

SockResult sockClose(s32 fd) {
    const u32 rc = nn::socket::Close(fd);
    if (rc != 0) return {-1, nn::socket::GetLastErrno()};
    return {0, 0};
}

SockResult sockSend(s32 fd, const void* data, std::size_t len) {
    const s32 n = nn::socket::Send(fd, data, len, 0);
    if (n < 0) return {n, nn::socket::GetLastErrno()};
    return {n, 0};
}

SockResult sockRecv(s32 fd, void* data, std::size_t len) {
    const s32 n = nn::socket::Recv(fd, data, len, 0);
    if (n < 0) return {n, nn::socket::GetLastErrno()};
    return {n, 0};
}

SockResult sockSetKeepalive(s32 fd) {
    const s32 keepalive = 1;
    const s32 rc = nn::socket::SetSockOpt(
        fd, kSolSocket, kSoKeepAlive, &keepalive, sizeof(keepalive));
    if (rc < 0) return {rc, nn::socket::GetLastErrno()};
    return {0, 0};
}

// Returns >0 if socket is readable, 0 on timeout, <0 on error.
int sockPollReadable(s32 fd, std::uint32_t timeout_ms) {
    ::pollfd pfd{ .fd = fd, .events = kPollIn, .revents = 0 };
    const s32 n = nn::socket::Poll(&pfd, 1, static_cast<s32>(timeout_ms));
    if (n < 0) return -1;
    if (n == 0) return 0;
    if (pfd.revents & (kPollErr | kPollHup)) return -1;
    return (pfd.revents & kPollIn) ? 1 : 0;
}

// Fill out a `sockaddr` with a dotted-IPv4 host + port. Returns false if
// the host string isn't a valid IPv4 literal. We don't currently resolve
// hostnames — all bridge targets are IPv4 (discovery / fallback / wizard
// LAN IP); add InetPton or GetAddrInfo here if hostname targets land later.
bool sockAddrFromIpv4(const char* host, std::uint16_t port, ::sockaddr& out) {
    ::in_addr ia{};
    if (nn::socket::InetAton(host, &ia) == 0) return false;
    out = ::sockaddr{};
    out.sa_len    = sizeof(out);
    out.sa_family = kAfInet;
    out.sa_port   = nn::socket::InetHtons(port);
    out.sa_addr   = ia;
    return true;
}

// M6 phase D — translate an ApState snapshot into the wire PaySnapshot,
// encode, and send. Always sends complete=true; entries are filled for all
// 17 kingdoms (kingdoms with PayShineNum=0 still ship so the bridge can
// zero-out anything it has stored from a prior session).
int sendPaySnapshotMessage(int socket_fd, smoap::util::json::LineBuffer& line,
                           const ApState::PendingPaySnapshot& ps) {
    PaySnapshot wire{};
    wire.save_slot = -1;  // -1 omits the field; bridge does not fence on it
    wire.complete = true;
    for (int bit = 0; bit < 17; ++bit) {
        const char* name = smoap::game::kingdomForBit(static_cast<std::uint8_t>(bit));
        if (!name || !*name) continue;
        auto& entry = wire.entries[wire.entry_count++];
        copyCheckField(entry.kingdom, name);
        entry.pay = ps.totals[bit];
    }
    encodePaySnapshot(line, wire);
    return sockSend(socket_fd, line.data(), line.size()).ret;
}

void workerEntry(ApClient* self) {
    self->threadMain();
    // Should not return; if we do, just sleep forever.
    while (true) hk::svc::SleepThread(INT64_MAX);
}

}  // namespace

ApClient& ApClient::instance() {
    static ApClient s;
    return s;
}

void ApClient::initNetworking() {
    // nifm only — nn::socket bring-up happened in main.cpp's pre-orig
    // GameSystem::init hook (with our 6MB+128KB pool) and SMO's later
    // Initialize call was trampoline'd to a no-op. So by the time we get
    // here, the socket session is live; no sm/socket setup needed.
    SMOAP_LOG_INFO("[frame] nn::nifm::Initialize");
    const u32 nifm_rc = nn::nifm::Initialize();
    if (nifm_rc != 0) {
        SMOAP_LOG_ERROR("[frame] nn::nifm::Initialize FAILED rc=0x%x", nifm_rc);
        return;
    }
    SMOAP_LOG_INFO("[frame] SubmitNetworkRequestAndWait");
    nn::nifm::SubmitNetworkRequestAndWait();
    const bool net_up = nn::nifm::IsNetworkAvailable();
    SMOAP_LOG_INFO("[frame] network available: %s", net_up ? "YES" : "NO");
    SMOAP_LOG_INFO("[frame] nn::socket session ready (pool from main.cpp)");
}

void ApClient::start(const BridgeTarget& target) {
    target_ = target;
    running_ = true;
    SMOAP_LOG_INFO("ApClient::start target=%s:%u", target.host.c_str(), target.port);

    // Construct the worker thread in-place. Hakkun's Thread ctor immediately
    // svc::CreateThread's; start() then svc::StartThread's it.
    g_worker_thread = new (g_worker_thread_storage) hk::os::Thread(
        &workerEntry, this,
        reinterpret_cast<ptr>(g_worker_stack), kWorkerStackSize,
        /*priority=*/44, /*coreId=*/-2);
    g_worker_thread->setName("smoap-worker");
    const auto rc = g_worker_thread->start();
    if (rc.failed()) {
        SMOAP_LOG_ERROR("ApClient: thread start failed (rc=0x%x)", rc.getValue());
        running_ = false;
    }
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

// Deferred-announce window for the save-load "current connection status"
// bubble. SaveLoadHook arms a deadline at now + this many ms; the worker
// loop fires "Connected to Archipelago" the instant ap_state=ready is
// observed inside the window, or "Not connected to Archipelago" once the
// window expires without one. 1500ms covers the ~1.4s SMOClient typically
// needs between accepting the Switch HELLO and the AP server returning
// Connected, with ~100ms slack for slower hosts. Bigger windows just delay
// the negative answer in the "bridge genuinely down" case.
static constexpr std::int64_t kSaveLoadAnnounceWaitMs = 1500;

void ApClient::requestRehello() {
    suppress_state_bubble_until_ms_.store(
        ApState::nowMs() + kRehelloBubbleSuppressMs,
        std::memory_order_relaxed);
    rehello_requested_.store(true, std::memory_order_release);
}

void ApClient::deferSaveLoadStatusBubble() {
    save_load_announce_deadline_ms_.store(
        ApState::nowMs() + kSaveLoadAnnounceWaitMs,
        std::memory_order_relaxed);
    SMOAP_LOG_INFO("[bubble] deferring save-load status announcement "
                   "(wait %lldms for AP handshake to settle)",
                   static_cast<long long>(kSaveLoadAnnounceWaitMs));
}

void ApClient::threadMain() {
    SMOAP_LOG_INFO("[worker] thread started, target=%s:%u",
                   target_.host.c_str(), target_.port);
    SMOAP_LOG_INFO("[worker] entering connect loop");

    std::uint32_t backoff_ms = target_.retry_ms;
    std::int64_t connected_at_ms = 0;
    bool snapshot_pending = false;

    while (running_) {
        // Fire deferred "Disconnected from Archipelago" bubble if the grace
        // window armed by disconnect() has expired with no recovery.
        if (const auto deadline = pending_disconnect_bubble_at_ms_.load(
                std::memory_order_relaxed);
            deadline > 0 && ApState::nowMs() >= deadline) {
            SMOAP_LOG_INFO("[bubble] firing deferred 'Disconnected from "
                           "Archipelago' (grace expired)");
            smoap::ap::enqueueSystemBubble("Disconnected from Archipelago");
            std::strcpy(s_last_ap_state, "disconnected");
            pending_disconnect_bubble_at_ms_.store(0, std::memory_order_relaxed);
        }

        // Save-load deferred status announcement.
        if (const auto deadline = save_load_announce_deadline_ms_.load(
                std::memory_order_relaxed);
            deadline > 0) {
            const bool ready_now = std::strcmp(s_last_ap_state, "ready") == 0;
            const bool expired = ApState::nowMs() >= deadline;
            if (ready_now) {
                SMOAP_LOG_INFO("[bubble] firing deferred save-load status "
                               "'Connected to Archipelago' (ap_state=ready)");
                smoap::ap::enqueueSystemBubble("Connected to Archipelago");
                save_load_announce_deadline_ms_.store(0, std::memory_order_relaxed);
            } else if (expired) {
                SMOAP_LOG_INFO("[bubble] firing deferred save-load status "
                               "'Not connected to Archipelago' (wait expired, "
                               "ap_state=%s)", s_last_ap_state);
                smoap::ap::enqueueSystemBubble("Not connected to Archipelago");
                save_load_announce_deadline_ms_.store(0, std::memory_order_relaxed);
            }
        }

        // Reset backoff after we've held a connection for kStableConnectMs.
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
        }

        if (socket_fd_ < 0) {
            connected_at_ms = 0;
            ApState::instance().conn.store(ConnState::Connecting);
            // Runtime bridge discovery via UDP subnet sweep + loopback.
            // On success, target_ is overwritten with the discovered
            // host:port for this connect cycle and we TCP-connect to it.
            // On failure (no UDP reply on either path) we just sleep and
            // retry — there is no compile-time-baked fallback IP.
            bool connected = false;
            {
                BridgeTarget discovered{};
                if (resolveBridge(discovered)) {
                    target_.host = discovered.host;
                    target_.port = discovered.port;
                    connected = connectOnce();
                }
            }
            if (!connected) {
                SMOAP_LOG_WARN("connect failed; sleeping %u ms before retry", backoff_ms);
                hk::svc::SleepThread(static_cast<s64>(backoff_ms) * 1'000'000);
                backoff_ms = backoff_ms < kBackoffCapMs ? backoff_ms * 2 : kBackoffCapMs;
                continue;
            }
            connected_at_ms = ApState::nowMs();
            sendHello();
            {
                auto& s = ApState::instance();
                const bool save_ok = s.save_was_loaded.load(std::memory_order_acquire);
                const bool cappy_ok =
                    smoap::ui::CappyMessenger::instance().hasDispatchedSinceReset();
                if (save_ok && cappy_ok) {
                    sendSnapshot();
                    snapshot_pending = false;
                } else {
                    SMOAP_LOG_INFO("[conn] snapshot deferred (save_was_loaded=%d "
                                   "cappy_ok=%d); will retry from worker loop "
                                   "once both are true",
                                   static_cast<int>(save_ok),
                                   static_cast<int>(cappy_ok));
                    snapshot_pending = true;
                }
            }
            ApState::instance().conn.store(ConnState::Hello);
        }

        // Wait up to recv_timeout_ms for inbound data.
        const int sel = sockPollReadable(socket_fd_, target_.recv_timeout_ms);

        auto quickBounceBackoff = [&]() {
            if (connected_at_ms <= 0) return;
            const auto held_ms = ApState::nowMs() - connected_at_ms;
            if (held_ms >= kStableConnectMs) return;
            backoff_ms = backoff_ms < kBackoffCapMs ? backoff_ms * 2 : kBackoffCapMs;
            SMOAP_LOG_WARN("[conn] quick bounce after only %lldms held; "
                           "backoff -> %u ms; sleeping",
                           static_cast<long long>(held_ms), backoff_ms);
            hk::svc::SleepThread(static_cast<s64>(backoff_ms) * 1'000'000);
        };

        if (sel < 0) {
            SMOAP_LOG_WARN("poll returned error; reconnecting");
            quickBounceBackoff();
            disconnect();
            continue;
        }
        if (sel > 0) {
            if (!recvIntoBuf()) {
                SMOAP_LOG_WARN("recv error or peer closed; reconnecting");
                quickBounceBackoff();
                disconnect();
                continue;
            }
        }

        // Drain ALL complete lines from read_buf_ each iteration.
        char line_buf[kInboundLineCap];
        std::size_t line_len = 0;
        while (popLine(line_buf, line_len)) {
            handleLine(line_buf, line_len);
        }

        // Late-arriving snapshot send: at HELLO time the gate may have been
        // closed because the player was still on the file-select screen, or
        // because the stage's first Cappy balloon hadn't fired yet.
        if (snapshot_pending && socket_fd_ >= 0) {
            auto& s = ApState::instance();
            const bool save_ok = s.save_was_loaded.load(std::memory_order_acquire);
            const bool cappy_ok =
                smoap::ui::CappyMessenger::instance().hasDispatchedSinceReset();
            if (save_ok && cappy_ok) {
                SMOAP_LOG_INFO("[conn] snapshot gate now open "
                               "(save_was_loaded=1 cappy_ok=1); sending");
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
    const char* host = target_.host.c_str();
    const std::uint16_t port = target_.port;

    SMOAP_LOG_INFO("[conn] socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)");
    auto r = sockSocketTcp();
    SMOAP_LOG_INFO("[conn] socket returned fd=%d errno=%d", r.ret, r.err);
    if (r.ret < 0) {
        SMOAP_LOG_WARN("[conn] socket() failed errno=%d", r.err);
        socket_fd_ = -1;
        return false;
    }
    socket_fd_ = r.ret;

    ::sockaddr addr{};
    if (!sockAddrFromIpv4(host, port, addr)) {
        SMOAP_LOG_WARN("[conn] InetAton failed for %s", host);
        sockClose(socket_fd_);
        socket_fd_ = -1;
        return false;
    }
    SMOAP_LOG_INFO("[conn] connecting to %s:%u", host, port);

    auto cr = sockConnect(socket_fd_, addr);
    if (cr.ret < 0) {
        SMOAP_LOG_WARN("[conn] connect FAILED ret=%d errno=%d (host=%s port=%u fd=%d)",
                       cr.ret, cr.err, host, port, socket_fd_);
        sockClose(socket_fd_);
        socket_fd_ = -1;
        return false;
    }

    sockSetKeepalive(socket_fd_);
    SMOAP_LOG_INFO("[conn] CONNECTED to %s:%u (fd=%d)",
                   host, port, socket_fd_);
    smoap::ui::notifyConnectChange(true);
    return true;
}

void ApClient::disconnect() {
    if (socket_fd_ >= 0) {
        sockClose(socket_fd_);
        socket_fd_ = -1;
    }
    read_buf_len_ = 0;
    auto& st = ApState::instance();
    st.conn.store(ConnState::Disconnected);
    st.bridge_connected.store(false, std::memory_order_relaxed);
    smoap::ui::notifyConnectChange(false);
    const bool suppress = ApState::nowMs() <
        suppress_state_bubble_until_ms_.load(std::memory_order_relaxed);
    if (std::strcmp(s_last_ap_state, "ready") == 0) {
        if (suppress) {
            SMOAP_LOG_INFO("[bubble] suppressing 'Disconnected from Archipelago' "
                           "(rehello window)");
            std::strcpy(s_last_ap_state, "disconnected");
        } else {
            std::int64_t expected = 0;
            const std::int64_t target = ApState::nowMs() + kDisconnectGracePeriodMs;
            if (pending_disconnect_bubble_at_ms_.compare_exchange_strong(
                    expected, target, std::memory_order_relaxed)) {
                SMOAP_LOG_INFO("[bubble] deferring 'Disconnected from "
                               "Archipelago' (%lldms grace for TCP recovery)",
                               static_cast<long long>(kDisconnectGracePeriodMs));
            }
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
    const auto r = sockSend(socket_fd_, line.data(), line.size());
    SMOAP_LOG_INFO("[conn] HELLO send returned %d errno=%d", r.ret, r.err);
}

namespace {

// Per-stage shine accumulator used by sendSnapshot's enumeration callback.
// We bucket shines by stage_name so each kingdom emits one StateChunk message
// (instead of one chunk per shine), keeping wire chatter low and respecting
// the 8 KiB per-line cap. Fixed buffers throughout.
struct SnapshotBuilder {
    int sock_fd = -1;
    StateChunk current;
    bool current_active = false;
    smoap::util::json::LineBuffer line;  // reused across chunks

    void flushIfNeeded(const char* stage) {
        if (current_active && std::strcmp(current.stage_name, stage) != 0) {
            encodeStateChunk(line, current);
            sockSend(sock_fd, line.data(), line.size());
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
            sockSend(sock_fd, line.data(), line.size());
            current_active = false;
        }
    }
};

}  // namespace

void ApClient::sendSnapshot() {
    auto& st = ApState::instance();
    smoap::util::json::LineBuffer line;

    // 1) state_begin
    {
        StateBegin b;
        b.mod_ver = SMO_AP_MOD_VERSION_STRING;
        b.save_slot = -1;
        encodeStateBegin(line, b);
        if (sockSend(socket_fd_, line.data(), line.size()).ret < 0) {
            SMOAP_LOG_WARN("[snapshot] state_begin send failed; aborting");
            return;
        }
    }

    // 2) per-stage chunks.
    SnapshotBuilder builder{};
    builder.sock_fd = socket_fd_;
    smoap::game::enumerateOwnedShines(
        [](void* ctx, const char* stage, const char* obj, int uid) {
            auto* b = static_cast<SnapshotBuilder*>(ctx);
            b->addShine(stage, obj, uid);
        },
        &builder);
    builder.finalize();

    // 3) _meta chunk.
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
        sockSend(socket_fd_, line.data(), line.size());
    }

    // 4) state_end
    encodeStateEnd(line);
    sockSend(socket_fd_, line.data(), line.size());

    // 5) M6 phase D PayShineNum snapshot.
    {
        ApState::PendingPaySnapshot ps{};
        if (st.buildPaySnapshot(ps)) {
            const int n = sendPaySnapshotMessage(socket_fd_, line, ps);
            if (n < 0) {
                SMOAP_LOG_WARN("[conn] post-HELLO PaySnapshot send failed");
            } else {
                SMOAP_LOG_INFO("[conn] post-HELLO PaySnapshot sent (%d bytes)", n);
            }
        } else {
            SMOAP_LOG_WARN("[conn] post-HELLO PaySnapshot build failed "
                           "(symbol unresolved or GDH not cached); the next "
                           "AddPayShineHook fire will retry");
        }
    }

    SMOAP_LOG_INFO("[conn] snapshot sent");
}

void ApClient::pumpOnce() {
    auto& st = ApState::instance();
    smoap::util::json::LineBuffer line;
    Check c;
    while (st.outbound_checks.peek(c)) {
        encodeCheck(line, c);
        SMOAP_LOG_INFO("[pump] peek check kind=%d stage=%s obj=%s (line=%u bytes)",
                       static_cast<int>(c.kind),
                       c.stage_name[0] ? c.stage_name : "<empty>",
                       c.object_id[0] ? c.object_id : "<empty>",
                       static_cast<unsigned>(line.size()));
        const int n = sockSend(socket_fd_, line.data(), line.size()).ret;
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
            if (sockSend(socket_fd_, line.data(), line.size()).ret < 0) return;
        }
        if (e.death) {
            Death d{.ts_ms = e.ts_ms};
            encodeDeath(line, d);
            if (sockSend(socket_fd_, line.data(), line.size()).ret < 0) return;
            st.death_pending_send.store(false, std::memory_order_release);
        }
        st.outbound_status.popDiscard();
    }

    // Drain outbound_logs.
    if (const std::uint32_t drops = st.log_drops.exchange(0, std::memory_order_relaxed); drops > 0) {
        Log marker;
        copyFixedField(marker.level, "warn");
        std::snprintf(marker.msg, kLogMsgCap,
                      "[log_forward] %u log line(s) dropped (ring full)", drops);
        encodeLog(line, marker);
        if (sockSend(socket_fd_, line.data(), line.size()).ret < 0) {
            st.log_drops.fetch_add(drops, std::memory_order_relaxed);
            return;
        }
    }
    Log lg;
    while (st.outbound_logs.peek(lg)) {
        encodeLog(line, lg);
        if (sockSend(socket_fd_, line.data(), line.size()).ret < 0) return;
        st.outbound_logs.popDiscard();
    }

    // M6 phase D — drain pending_pay_snapshots.
    ApState::PendingPaySnapshot ps;
    while (st.pending_pay_snapshots.pop(ps)) {
        const int n = sendPaySnapshotMessage(socket_fd_, line, ps);
        if (n < 0) {
            SMOAP_LOG_WARN("[m6-pay-snapshot] send failed; next snapshot will "
                           "carry the latest reading regardless");
            return;
        }
        SMOAP_LOG_INFO("[m6-pay-snapshot] sent (%d bytes)", n);
    }
}

bool ApClient::recvIntoBuf() {
    const std::size_t avail = kInboundLineCap - read_buf_len_;
    if (avail == 0) {
        char drain[256];
        sockRecv(socket_fd_, drain, sizeof(drain));
        return true;
    }
    const std::size_t cap = avail < 1024 ? avail : 1024;
    const auto r = sockRecv(socket_fd_, read_buf_ + read_buf_len_, cap);
    if (r.ret <= 0) {
        SMOAP_LOG_WARN("[recv] recv -> %d errno=%d (%s)",
                       r.ret, r.err,
                       r.ret == 0 ? "clean EOF from peer"
                                  : "socket error");
        return false;
    }
    read_buf_len_ += static_cast<std::size_t>(r.ret);
    return true;
}

bool ApClient::popLine(char* out, std::size_t& out_len) {
    std::size_t nl = read_buf_len_;
    for (std::size_t i = 0; i < read_buf_len_; ++i) {
        if (read_buf_[i] == '\n') { nl = i; break; }
    }
    if (nl == read_buf_len_) {
        if (read_buf_len_ >= kInboundLineCap) {
            SMOAP_LOG_WARN("read_buf overflow without newline; resyncing");
            read_buf_len_ = 0;
        }
        return false;
    }
    for (std::size_t i = 0; i < nl; ++i) out[i] = read_buf_[i];
    out_len = nl;
    const std::size_t consumed = nl + 1;
    const std::size_t remaining = read_buf_len_ - consumed;
    for (std::size_t i = 0; i < remaining; ++i) {
        read_buf_[i] = read_buf_[consumed + i];
    }
    read_buf_len_ = remaining;
    return true;
}

void ApClient::handleLine(char* line, std::size_t line_len) {
    // DecodedMsg holds large fixed-size buffers; static so it lives in BSS.
    // handleLine is only ever called from the worker thread, and we dispatch
    // on m.t so stale variant fields from a previous call are never read.
    static DecodedMsg m;
    if (!decode(line, line_len, m)) {
        SMOAP_LOG_WARN("malformed message from bridge: %.*s",
                       static_cast<int>(line_len), line);
        return;
    }
    auto eq = [](const char* a, const char* b) {
        while (*a && *b && *a == *b) { ++a; ++b; }
        return *a == '\0' && *b == '\0';
    };
    if (eq(m.t, "hello_ack")) {
        auto& st = ApState::instance();
        if (!m.hello_ack.ok) {
            SMOAP_LOG_ERROR("hello_ack REJECTED: ok=false bridge=%s mod=%s err=%s",
                            m.hello_ack.client_ver[0] ? m.hello_ack.client_ver : "(unknown)",
                            SMO_AP_MOD_VERSION_STRING,
                            m.hello_ack.err);
            return;
        }
        std::snprintf(st.local_slot, sizeof(st.local_slot),
                      "%s", m.hello_ack.slot);
        st.deathlink_enabled.store(m.hello_ack.deathlink_enabled, std::memory_order_relaxed);
        st.conn.store(ConnState::Ready, std::memory_order_release);
        st.bridge_connected.store(true, std::memory_order_release);
        SMOAP_LOG_INFO("hello_ack: ok=%d seed=%s slot=%s deathlink_enabled=%d client_ver=%s mod_ver=%s",
                       m.hello_ack.ok ? 1 : 0,
                       m.hello_ack.seed,
                       m.hello_ack.slot,
                       m.hello_ack.deathlink_enabled ? 1 : 0,
                       m.hello_ack.client_ver[0] ? m.hello_ack.client_ver : "(unset)",
                       SMO_AP_MOD_VERSION_STRING);
    } else if (eq(m.t, "checked_replay")) {
        for (std::size_t i = 0; i < m.checked_replay.id_count; ++i) {
            const auto& ref = m.checked_replay.ids[i];
            Check synth{};
            synth.kind = ref.kind;
            copyCheckField(synth.kingdom, ref.kingdom);
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
        const bool was_ready = (std::strcmp(s_last_ap_state, "ready") == 0);
        const bool now_ready = (std::strcmp(m.ap_state.conn, "ready") == 0);
        const bool now_disconnected =
            (std::strcmp(m.ap_state.conn, "disconnected") == 0);
        const bool bubble_suppressed = ApState::nowMs() <
            suppress_state_bubble_until_ms_.load(std::memory_order_relaxed);
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
                smoap::ap::enqueueSystemBubble("Connected to Archipelago");
            }
        } else if (was_ready && now_disconnected) {
            if (bubble_suppressed) {
                SMOAP_LOG_INFO("[bubble] suppressing 'Disconnected from Archipelago' "
                               "(rehello window, ap_state path)");
            } else {
                smoap::ap::enqueueSystemBubble("Disconnected from Archipelago");
            }
        }
        std::size_t i = 0;
        while (i + 1 < sizeof(s_last_ap_state) && m.ap_state.conn[i] != '\0') {
            s_last_ap_state[i] = m.ap_state.conn[i];
            ++i;
        }
        s_last_ap_state[i] = '\0';
        if (now_ready) {
            const auto deadline = save_load_announce_deadline_ms_.load(
                std::memory_order_relaxed);
            if (deadline > 0) {
                SMOAP_LOG_INFO("[bubble] firing deferred save-load status "
                               "'Connected to Archipelago' (ap_state=ready "
                               "arrived inside wait window)");
                smoap::ap::enqueueSystemBubble("Connected to Archipelago");
                save_load_announce_deadline_ms_.store(0, std::memory_order_relaxed);
            }
        }
    } else if (eq(m.t, "print")) {
        SMOAP_LOG_INFO("[bridge] %s", m.print.text);
    } else if (eq(m.t, "pong")) {
        // Liveness ack — could update last_rx_ns here in a future iteration.
    } else if (eq(m.t, "err")) {
        SMOAP_LOG_WARN("bridge err code=%s ctx=%s", m.err.code, m.err.ctx);
    } else if (eq(m.t, "kill")) {
        SMOAP_LOG_INFO("[deathlink in] queued source=%s cause=%s",
                       m.kill.source, m.kill.cause);
        ApState::instance().inbound_kill_pending.store(true, std::memory_order_release);
    } else if (eq(m.t, "moon_label")) {
        const auto now = ApState::nowMs();
        const auto deadline = (m.moon_label.valid_for_ms > 0)
            ? now + m.moon_label.valid_for_ms
            : 0;
        SMOAP_LOG_INFO("[moon_label] seq=%d text='%s' valid_for=%dms",
                       m.moon_label.seq,
                       m.moon_label.text,
                       m.moon_label.valid_for_ms);
        ApState::instance().setPendingMoonLabel(
            m.moon_label.text, m.moon_label.seq, deadline);
    } else if (eq(m.t, "cappy")) {
        SMOAP_LOG_INFO("[cappy] system bubble text='%s'", m.cappy.text);
        smoap::ap::enqueueSystemBubble(m.cappy.text);
    } else if (eq(m.t, "shine_scouts")) {
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
    } else if (eq(m.t, "outstanding")) {
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
    } else if (eq(m.t, "talkatoo_pool")) {
        // Talkatoo% mode — bridge ships one message per kingdom on HELLO
        // replay (and again whenever the user toggles mode), or a single
        // enabled=false message to disable the feature entirely. The per-
        // kingdom write uses a seqlock so the frame-thread speech hook
        // can re-read without holding a lock.
        auto& st = ApState::instance();
        const auto& tp = m.talkatoo_pool;
        if (!tp.enabled) {
            SMOAP_LOG_INFO("[talkatoo] disable received — clearing pool state");
            st.clearTalkatoo();
        } else {
            if (tp.kingdom[0] == '\0') {
                SMOAP_LOG_WARN("[talkatoo] enable msg without kingdom — ignoring");
            } else {
                const std::uint8_t bit = smoap::game::kingdomBitFor(tp.kingdom);
                if (bit >= 17) {
                    SMOAP_LOG_WARN("[talkatoo] unknown kingdom='%s' moons=%zu",
                                   tp.kingdom, tp.moon_count);
                } else {
                    st.writeTalkatooKingdom(bit, tp.moons, tp.moon_count);
                    if (tp.truncated) {
                        SMOAP_LOG_WARN("[talkatoo] kingdom=%s truncated at %zu moons "
                                       "(bump kTalkatooMaxMoonsPerKingdom?)",
                                       tp.kingdom, tp.moon_count);
                    }
                    SMOAP_LOG_INFO("[talkatoo] applied kingdom=%s moons=%zu",
                                   tp.kingdom, tp.moon_count);
                }
            }
        }
    } else {
        SMOAP_LOG_WARN("unknown message t=%s", m.t);
    }
}

}  // namespace smoap::ap
