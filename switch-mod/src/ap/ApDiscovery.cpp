// See ApDiscovery.hpp for the design rationale.
//
// Probe sequence (2026-05-22):
//   1. Loopback 127.0.0.1:17776 — covers Ryujinx-on-same-host. Fast (250ms)
//      and the most common dev path, so we try it first.
//   2. Unicast sweep across the BRIDGE_HOST_STRING `/24` subnet — covers
//      real Switch and the case where the user's PC has moved within the
//      same LAN. All 253 hosts get one probe in a tight burst (~5ms send),
//      then we poll for up to 1s for any reply. First valid {"t":"bridge"}
//      wins. The previous broadcast (255.255.255.255) approach was silently
//      dropped on a lot of real-world LANs (travel routers, mesh repeaters,
//      managed switches with IGMP snooping); see memory note
//      project_udp_broadcast_dead_on_user_network.
//
// We deliberately do NOT call nn::nifm::GetCurrentPrimaryIpAddress to learn
// the Switch's own IP — that symbol caused a silent module-init failure
// when added to sail (memory: project_sail_missing_symbol_crashes_init).
// Using BRIDGE_HOST_STRING (already a verified config input) as the sweep
// seed sidesteps the whole nifm dependency.
//
// Talks to bsd:u via nn::socket::* directly (same session ApClient uses).

#include "ApDiscovery.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "hk/types.h"

#include "../util/Json.hpp"
#include "../util/Log.hpp"
#include "ApConfig.hpp"      // BRIDGE_HOST_STRING
#include "ApProtocol.hpp"    // SMO_AP_MOD_VERSION_STRING
#include "ApState.hpp"       // ApState::nowMs

// Match the layout / declarations ApClient.cpp uses — sail resolves
// `_ZN2nn6socket…E…8sockaddr…` against main.nso, so the struct MUST be
// named `sockaddr` at file scope.
struct in_addr { u32 s_addr; };
struct sockaddr {
    u8 sa_len;
    u8 sa_family;
    u16 sa_port;
    in_addr sa_addr;
    u8 sa_zero[8];
};
struct pollfd { s32 fd; short events; short revents; };

namespace nn::socket {
    s32 Socket(s32, s32, s32);
    s32 SendTo(s32, const void*, unsigned long, s32, const ::sockaddr*, u32);
    s32 RecvFrom(s32, void*, unsigned long, s32, ::sockaddr*, u32*);
    u32 Close(s32);
    s32 Poll(::pollfd*, unsigned long, s32);
    u16 InetHtons(u16);
    s32 InetAton(const char*, ::in_addr*);
    s32 GetLastErrno();
}

namespace smoap::ap {

namespace {

// Socket constants. nn::socket doesn't re-export BSD constants; values match
// Nintendo's bsd:u service (FreeBSD-derived). Mirrors ApClient.cpp.
constexpr s32 kAfInet      = 2;
constexpr s32 kSockDgram   = 2;
constexpr s32 kIpprotoUdp  = 17;
constexpr s32 kPollIn      = 0x0001;

// Probe timeouts.
constexpr std::uint32_t kSweepCollectMs   = 1000;  // wait window after the burst
constexpr std::uint32_t kLoopbackProbeMs  = 250;

// Reply buffer cap. Replies are tiny (~80 bytes); 512 is generous.
constexpr std::size_t kReplyBufBytes = 512;

// Default mask: /24 covers virtually every home network. The sweep iterates
// .1..254 of the seed's network address.
constexpr std::uint32_t kSubnetMask = 0xFFFFFF00u;
constexpr int           kMaxSweepHosts = 254;

// Diagnostic report, spinlock-guarded. Writer: whatever worker thread last
// called resolveBridge(). Reader: any thread (debug overlay, log dump).
DiscoveryReport         g_report;
std::atomic_flag        g_report_lock = ATOMIC_FLAG_INIT;

struct ReportGuard {
    ReportGuard()  { while (g_report_lock.test_and_set(std::memory_order_acquire)) {} }
    ~ReportGuard() { g_report_lock.clear(std::memory_order_release); }
};

void publishReport(const DiscoveryReport& src) {
    ReportGuard g;
    g_report = src;
}

// Build the probe payload once per resolveBridge call.
std::size_t buildProbe(char* dst, std::size_t cap) {
    smoap::util::json::LineBuffer line;
    smoap::util::json::Encoder e{line};
    e.beginObject()
        .key("t").value("discover")
        .key("mod_ver").value(SMO_AP_MOD_VERSION_STRING)
     .endObject();
    line.append('\n');
    const std::size_t take = line.size() < cap ? line.size() : cap;
    std::memcpy(dst, line.data(), take);
    return take;
}

s32 openUdpSocket() {
    return nn::socket::Socket(kAfInet, kSockDgram, kIpprotoUdp);
}

void closeSocket(s32 fd) {
    (void)nn::socket::Close(fd);
}

bool waitReadable(s32 fd, std::uint32_t timeout_ms) {
    ::pollfd pfd{ .fd = fd, .events = kPollIn, .revents = 0 };
    const s32 n = nn::socket::Poll(&pfd, 1, static_cast<s32>(timeout_ms));
    if (n <= 0) return false;
    return (pfd.revents & kPollIn) != 0;
}

bool parseReply(const char* data, std::size_t len, BridgeTarget& out) {
    char scratch[kReplyBufBytes];
    if (len > sizeof(scratch)) len = sizeof(scratch);
    std::memcpy(scratch, data, len);

    smoap::util::json::Reader r(scratch, len);
    if (!r.enterObject()) return false;

    bool saw_t_bridge = false;
    char host[64] = {0};
    int port = 0;

    std::string_view key;
    while (r.nextField(key)) {
        if (key == "t") {
            std::string_view t_val;
            if (!r.nextString(t_val)) return false;
            if (t_val == "bridge") saw_t_bridge = true;
        } else if (key == "host") {
            std::string_view host_val;
            if (!r.nextString(host_val)) return false;
            const std::size_t take = host_val.size() < sizeof(host) - 1
                ? host_val.size() : sizeof(host) - 1;
            std::memcpy(host, host_val.data(), take);
            host[take] = '\0';
        } else if (key == "port") {
            std::int64_t p = 0;
            if (!r.nextInt(p)) return false;
            port = static_cast<int>(p);
        } else {
            std::string_view _sv;
            std::int64_t _i;
            bool _b;
            (void)(r.isNull() || r.nextString(_sv) || r.nextInt(_i) || r.nextBool(_b));
        }
    }
    if (!saw_t_bridge || host[0] == '\0' || port <= 0 || port > 0xFFFF) {
        return false;
    }
    out.host = host;
    out.port = static_cast<std::uint16_t>(port);
    return true;
}

void makeSockAddrFromU32(u32 host_nbo, std::uint16_t port, ::sockaddr& out) {
    out = ::sockaddr{};
    out.sa_len    = sizeof(out);
    out.sa_family = static_cast<u8>(kAfInet);
    out.sa_port   = nn::socket::InetHtons(port);
    out.sa_addr.s_addr = host_nbo;
}

bool oneProbeLiteral(s32 fd, const char* probe_data, std::size_t probe_len,
                     const char* host, std::uint16_t port,
                     std::uint32_t timeout_ms, BridgeTarget& out) {
    ::in_addr ia{};
    if (nn::socket::InetAton(host, &ia) == 0) {
        SMOAP_LOG_WARN("[discover] InetAton failed for %s", host);
        return false;
    }
    ::sockaddr addr{};
    addr.sa_len = sizeof(addr);
    addr.sa_family = static_cast<u8>(kAfInet);
    addr.sa_port = nn::socket::InetHtons(port);
    addr.sa_addr = ia;

    const s32 sent = nn::socket::SendTo(
        fd, probe_data, probe_len, 0, &addr, sizeof(addr));
    if (sent < 0) {
        SMOAP_LOG_WARN("[discover] sendTo %s:%u failed errno=%d",
                       host, port, nn::socket::GetLastErrno());
        return false;
    }
    if (!waitReadable(fd, timeout_ms)) return false;
    char buf[kReplyBufBytes];
    ::sockaddr from{};
    u32 from_len = sizeof(from);
    const s32 got = nn::socket::RecvFrom(
        fd, buf, sizeof(buf), 0, &from, &from_len);
    if (got <= 0) return false;
    return parseReply(buf, static_cast<std::size_t>(got), out);
}

// Burst-send probes to every host on the seed's /24 subnet, then drain
// any inbound replies for up to kSweepCollectMs.
bool sweepSubnet(s32 fd, const char* probe_data, std::size_t probe_len,
                 u32 seed_ip_nbo, std::uint16_t port,
                 DiscoveryReport& report_out, BridgeTarget& out) {
    auto byteswap32 = [](u32 v) -> u32 {
        return ((v & 0x000000FFu) << 24) | ((v & 0x0000FF00u) << 8) |
               ((v & 0x00FF0000u) >> 8)  | ((v & 0xFF000000u) >> 24);
    };

    const u32 seed_ho = byteswap32(seed_ip_nbo);
    const u32 mask_ho = kSubnetMask;
    const u32 net_ho  = seed_ho & mask_ho;
    const u32 bcast_ho = net_ho | ~mask_ho;

    // Send burst — covers seed's /24, .1..254. We DO probe the seed itself
    // (which is the user's PC's IP); the sweep is a superset.
    int sent_count = 0;
    for (u32 ip_ho = net_ho + 1;
         ip_ho < bcast_ho && sent_count < kMaxSweepHosts; ++ip_ho) {
        ::sockaddr addr{};
        makeSockAddrFromU32(byteswap32(ip_ho), port, addr);
        const s32 n = nn::socket::SendTo(
            fd, probe_data, probe_len, 0, &addr, sizeof(addr));
        if (n >= 0) ++sent_count;
        // Per-host failures (errno=ENETUNREACH) aren't surprising on
        // sparse LANs; aggregate stats land in the report.
    }
    report_out.probed_count = static_cast<std::uint16_t>(sent_count);
    SMOAP_LOG_INFO("[discover] sweep sent %d probes (subnet %u.%u.%u.0/24)",
                   sent_count,
                   (net_ho >> 24) & 0xFF, (net_ho >> 16) & 0xFF,
                   (net_ho >> 8)  & 0xFF);

    // Collect replies. First valid wins.
    const std::int64_t deadline_ms = ApState::nowMs() + kSweepCollectMs;
    while (ApState::nowMs() < deadline_ms) {
        const std::int64_t remain = deadline_ms - ApState::nowMs();
        const std::uint32_t wait_ms =
            (remain > 0) ? static_cast<std::uint32_t>(remain) : 1u;
        if (!waitReadable(fd, wait_ms)) break;
        char buf[kReplyBufBytes];
        ::sockaddr from{};
        u32 from_len = sizeof(from);
        const s32 got = nn::socket::RecvFrom(
            fd, buf, sizeof(buf), 0, &from, &from_len);
        if (got <= 0) continue;
        ++report_out.replies;
        BridgeTarget t;
        if (parseReply(buf, static_cast<std::size_t>(got), t)) {
            out = t;
            return true;
        }
    }
    return false;
}

}  // namespace

void snapshotDiscoveryReport(DiscoveryReport& out) {
    ReportGuard g;
    out = g_report;
}

bool resolveBridge(BridgeTarget& out, std::uint16_t discovery_port) {
    char probe[kReplyBufBytes];
    const std::size_t probe_len = buildProbe(probe, sizeof(probe));
    if (probe_len == 0) return false;

    DiscoveryReport report{};
    report.last_attempt_ms = ApState::nowMs();

    // ---- Step 1: loopback (Ryujinx-on-same-host) ----
    {
        const s32 fd = openUdpSocket();
        if (fd >= 0) {
            BridgeTarget t;
            const bool ok = oneProbeLiteral(
                fd, probe, probe_len,
                "127.0.0.1", discovery_port,
                kLoopbackProbeMs, t);
            closeSocket(fd);
            if (ok) {
                report.loopback_used = true;
                std::snprintf(report.last_bridge_host,
                              sizeof(report.last_bridge_host),
                              "%s", t.host.c_str());
                report.last_bridge_port = t.port;
                report.last_success_ms = ApState::nowMs();
                publishReport(report);
                SMOAP_LOG_INFO("[discover] resolved via loopback -> %s:%u",
                               t.host.c_str(), t.port);
                out = t;
                return true;
            }
        } else {
            SMOAP_LOG_WARN("[discover] UDP socket() failed (loopback step)");
        }
    }

    // ---- Step 2: subnet sweep using BRIDGE_HOST_STRING as the seed ----
    ::in_addr seed_ia{};
    if (nn::socket::InetAton(BRIDGE_HOST_STRING, &seed_ia) == 0) {
        SMOAP_LOG_WARN("[discover] BRIDGE_HOST_STRING ('%s') is not a valid "
                       "IPv4 literal; sweep disabled",
                       BRIDGE_HOST_STRING);
    } else {
        // Convert seed IP to host order for report header.
        const u32 seed_nbo = seed_ia.s_addr;
        const u32 seed_ho = ((seed_nbo & 0xFFu) << 24) |
                            ((seed_nbo & 0xFF00u) << 8) |
                            ((seed_nbo & 0xFF0000u) >> 8) |
                            ((seed_nbo & 0xFF000000u) >> 24);
        report.self_ip = seed_ho;  // NOTE: "self_ip" here is actually the seed IP, not Switch's IP — we no longer query nifm
        report.subnet_mask = kSubnetMask;

        const s32 fd = openUdpSocket();
        if (fd >= 0) {
            BridgeTarget t;
            const bool ok = sweepSubnet(
                fd, probe, probe_len, seed_nbo,
                discovery_port, report, t);
            closeSocket(fd);
            if (ok) {
                std::snprintf(report.last_bridge_host,
                              sizeof(report.last_bridge_host),
                              "%s", t.host.c_str());
                report.last_bridge_port = t.port;
                report.last_success_ms = ApState::nowMs();
                publishReport(report);
                SMOAP_LOG_INFO("[discover] resolved via subnet sweep -> %s:%u",
                               t.host.c_str(), t.port);
                out = t;
                return true;
            }
        } else {
            SMOAP_LOG_WARN("[discover] UDP socket() failed (sweep step)");
        }
    }

    publishReport(report);
    SMOAP_LOG_INFO("[discover] no UDP reply (loopback + sweep); caller will retry with backoff");
    return false;
}

}  // namespace smoap::ap
