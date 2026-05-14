// TCP client to the PC bridge.
//
// Owns a single nn::socket TCP connection on a dedicated worker thread.
// The frame thread (drawMain trampoline) only touches ApState's lock-free
// SPSC rings; this thread does all blocking I/O.
//
// Thread sequence:
//   1. start() (called from frame thread inside GameSystemInit hook):
//      saves target, spawns worker, returns immediately.
//   2. threadMain() bring-up: nn::nifm::Initialize ->
//      SubmitNetworkRequestAndWait -> nn::socket::Initialize. Once.
//   3. threadMain() loop: connectOnce -> sendHello -> Select+recv read,
//      pumpOnce drain outbound, error-on-disconnect with backoff retry.

#include "ApClient.hpp"

#include <cstdint>
#include <cstring>

#include "nn/nifm.h"
#include "nn/socket.hpp"
// nx.h is the C-linkage umbrella for libnx (svc + result + ...). Including
// the inner headers directly from C++ gives C++ mangling and unresolved
// links against the assembly stubs.
#include "lib/nx/nx.h"

#include "ApProtocol.hpp"
#include "ApState.hpp"
#include "../util/Log.hpp"

namespace smoap::ap {

namespace {

// BSD socket constants (not exposed by lunakit's nn/socket.hpp).
constexpr int kAfInet      = 2;
constexpr int kSockStream  = 1;
constexpr int kSolSocket   = 0xffff;
constexpr int kSoKeepAlive = 0x0008;

// Single TCP socket, ~8 KiB max payload — 256 KB is the canonical "single
// socket" sizing leaving headroom for kernel-side metadata.
constexpr std::size_t kSocketPoolSize = 0x40000;
constexpr std::size_t kAllocPoolSize  = 0x20000;
constexpr int         kConcurLimit    = 2;

constexpr std::size_t kWorkerStackSize = 64 * 1024;

// Exponential backoff caps (ms): 1s, 2s, 5s, 10s, 30s.
constexpr std::uint32_t kBackoffCapMs = 30 * 1000;

// Static buffers — exlaunch modules can't grow heap freely from background
// threads. These are sized once and reused.
alignas(0x4000) std::byte g_socket_pool[kSocketPoolSize];
alignas(0x1000) std::byte g_worker_stack[kWorkerStackSize];
Handle g_worker_thread = INVALID_HANDLE;

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

    SMOAP_LOG_INFO("[frame] nn::socket::Initialize (pool=%zu KB)",
                   kSocketPoolSize / 1024);
    const Result sock_rc = nn::socket::Initialize(g_socket_pool, kSocketPoolSize,
                                                  kAllocPoolSize, kConcurLimit);
    if (R_FAILED(sock_rc)) {
        SMOAP_LOG_ERROR("[frame] nn::socket::Initialize FAILED rc=0x%x", sock_rc);
        return;
    }
    SMOAP_LOG_INFO("[frame] networking ready");
}

void ApClient::start(const BridgeTarget& target) {
    target_ = target;
    running_ = true;
    SMOAP_LOG_INFO("ApClient::start target=%s:%u", target.host.c_str(), target.port);

    // Use the kernel SVC directly (lunakit pattern) to avoid pulling in nn::os
    // implementation headers that bloat the module.
    const Result rc = svcCreateThread(
        &g_worker_thread, reinterpret_cast<void*>(&workerEntry), this,
        g_worker_stack + kWorkerStackSize,  // stack-top, not stack-base
        /*priority=*/0x20, /*cpuid=*/-2);   // -2 = default core
    if (R_FAILED(rc)) {
        SMOAP_LOG_ERROR("ApClient: svcCreateThread failed (rc=0x%x)", rc);
        running_ = false;
        return;
    }
    svcStartThread(g_worker_thread);
}

void ApClient::stop() {
    running_ = false;
    disconnect();
    // We don't join the thread — the module lives for the process lifetime.
}

void ApClient::threadMain() {
    SMOAP_LOG_INFO("[worker] thread started, target=%s:%u",
                   target_.host.c_str(), target_.port);
    // nifm + socket Initialize were done on the frame thread inside
    // GameSystemInitHook::Callback because they're nn-IPC calls and the
    // raw-svcCreateThread worker can't make those. Worker only does
    // socket-level ops (Socket, Connect, Send, Recv, Select) which
    // empirically work on raw threads.
    SMOAP_LOG_INFO("[worker] entering connect loop");

    std::uint32_t backoff_ms = target_.retry_ms;

    while (running_) {
        if (socket_fd_ < 0) {
            ApState::instance().conn.store(ConnState::Connecting);
            if (!connectOnce()) {
                SMOAP_LOG_WARN("connect failed; sleeping %u ms before retry", backoff_ms);
                svcSleepThread(static_cast<s64>(backoff_ms) * 1'000'000);  // ms -> ns
                backoff_ms = backoff_ms < kBackoffCapMs ? backoff_ms * 2 : kBackoffCapMs;
                continue;
            }
            backoff_ms = target_.retry_ms;  // reset on success
            sendHello();
            ApState::instance().conn.store(ConnState::Hello);
        }

        // Wait up to recv_timeout_ms for inbound data.
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(socket_fd_, &rfds);
        struct timeval tv;
        tv.tv_sec  = static_cast<long>(target_.recv_timeout_ms / 1000);
        tv.tv_usec = static_cast<long>((target_.recv_timeout_ms % 1000) * 1000);
        const int sel = nn::socket::Select(socket_fd_ + 1, &rfds, nullptr, nullptr, &tv);

        if (sel < 0) {
            SMOAP_LOG_WARN("Select returned error; reconnecting");
            disconnect();
            continue;
        }
        if (sel > 0 && FD_ISSET(socket_fd_, &rfds)) {
            std::string line;
            if (!readOneLine(line)) {
                SMOAP_LOG_WARN("recv error or peer closed; reconnecting");
                disconnect();
                continue;
            }
            if (!line.empty()) handleLine(line);
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
        SMOAP_LOG_WARN("[conn] Socket() failed");
        socket_fd_ = -1;
        return false;
    }

    sockaddr_in addr{};
    addr.sin_family = kAfInet;
    addr.sin_port   = nn::socket::InetHtons(target_.port);
    if (nn::socket::InetAton(target_.host.c_str(), &addr.sin_addr) == 0) {
        SMOAP_LOG_WARN("[conn] InetAton failed for %s", target_.host.c_str());
        nn::socket::Close(socket_fd_);
        socket_fd_ = -1;
        return false;
    }
    SMOAP_LOG_INFO("[conn] connecting to %s:%u", target_.host.c_str(), target_.port);

    const Result rc = nn::socket::Connect(socket_fd_,
                                          reinterpret_cast<const sockaddr*>(&addr),
                                          sizeof(addr));
    if (R_FAILED(rc)) {
        SMOAP_LOG_WARN("[conn] Connect FAILED rc=0x%x", rc);
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
    read_buf_.clear();
    ApState::instance().conn.store(ConnState::Disconnected);
}

void ApClient::sendHello() {
    Hello hello;
    hello.mod_ver = SMO_AP_MOD_VERSION_STRING;
    hello.smo_ver = SMO_VERSION_STRING;
    const std::string line = encodeHello(hello);
    SMOAP_LOG_INFO("[conn] sending HELLO (%zu bytes)", line.size());
    const int sent = nn::socket::Send(socket_fd_, line.data(), line.size(), 0);
    SMOAP_LOG_INFO("[conn] HELLO send returned %d", sent);
}

void ApClient::pumpOnce() {
    auto& st = ApState::instance();
    Check c;
    while (st.outbound_checks.pop(c)) {
        const std::string line = encodeCheck(c);
        if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) {
            // Re-queue would lose ordering; for a single-conn world we just
            // drop and rely on next-frame retry. Disconnect handler will
            // trigger the bridge's checked_replay on reconnect.
            return;
        }
    }
    StatusEvent e;
    while (st.outbound_status.pop(e)) {
        if (e.goal) {
            const std::string line = encodeGoal();
            if (nn::socket::Send(socket_fd_, line.data(), line.size(), 0) < 0) return;
        }
    }
}

bool ApClient::readOneLine(std::string& out) {
    out.clear();
    char chunk[1024];
    const int n = nn::socket::Recv(socket_fd_, chunk, sizeof(chunk), 0);
    if (n <= 0) return false;
    read_buf_.append(chunk, static_cast<std::size_t>(n));

    const auto nl = read_buf_.find('\n');
    if (nl == std::string::npos) {
        // Cap runaway lines.
        if (read_buf_.size() > kMaxLineBytes) {
            SMOAP_LOG_WARN("read_buf overflow without newline; resyncing");
            read_buf_.clear();
        }
        return true;  // success but no complete line yet
    }
    out.assign(read_buf_, 0, nl);
    read_buf_.erase(0, nl + 1);
    return true;
}

void ApClient::handleLine(const std::string& line) {
    // Reader decodes escapes in place — copy to mutable buffer.
    std::string buf(line);
    DecodedMsg m;
    if (!decode(buf.data(), buf.size(), m)) {
        SMOAP_LOG_WARN("malformed message from bridge: %.*s",
                       static_cast<int>(line.size()), line.data());
        return;
    }
    if (m.t == "hello_ack") {
        ApState::instance().conn.store(ConnState::Ready);
        SMOAP_LOG_INFO("hello_ack: ok=%d seed=%s slot=%s",
                       m.hello_ack.ok ? 1 : 0,
                       m.hello_ack.seed.c_str(),
                       m.hello_ack.slot.c_str());
    } else if (m.t == "checked_replay") {
        for (const auto& ref : m.checked_replay.ids) {
            ApState::instance().locations_checked.insert(ApState::hashCheck(Check{
                .kind = ref.kind, .kingdom = ref.kingdom, .shine_id = ref.shine_id,
                .cap = ref.cap, .slot = ref.slot,
            }));
        }
        SMOAP_LOG_INFO("checked_replay: %u entries",
                       static_cast<unsigned>(m.checked_replay.ids.size()));
    } else if (m.t == "item") {
        ApState::instance().inbound.push(m.item);
    } else if (m.t == "ap_state") {
        // UI hint only.
    } else if (m.t == "print") {
        SMOAP_LOG_INFO("[bridge] %s", m.print.text.c_str());
    } else if (m.t == "pong") {
        // Liveness ack — could update last_rx_ns here in a future iteration.
    } else if (m.t == "err") {
        SMOAP_LOG_WARN("bridge err code=%s ctx=%s",
                       m.err.code.c_str(), m.err.ctx.c_str());
    } else {
        SMOAP_LOG_WARN("unknown message t=%s", m.t.c_str());
    }
}

}  // namespace smoap::ap
