// See ApDiscovery.hpp for the design rationale.
//
// Hakkun port of the exlaunch-era ApDiscovery.cpp. Maps nn::socket::*
// (lunakit wrapper) → hk::socket::Socket::instance()->* and the manual
// FreeBSD sockaddr workaround → hk::socket::SocketAddrIpv4::parse.

#include "ApDiscovery.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "hk/Span.h"
#include "hk/services/socket/address.h"
#include "hk/services/socket/poll.h"
#include "hk/services/socket/service.h"
#include "hk/types.h"

#include "../util/Json.hpp"
#include "../util/Log.hpp"
#include "ApProtocol.hpp"  // SMO_AP_MOD_VERSION_STRING is plumbed in via this TU

namespace smoap::ap {

namespace {

// Socket-option levels and names. hk::socket doesn't re-export the BSD
// constants; values match Nintendo's bsd:u service (FreeBSD-derived).
// Mirrors the matching block in ApClient.cpp.
constexpr s32 kSolSocket   = 0xffff;
constexpr s32 kSoBroadcast = 0x0020;

// Probe timeouts (ms).
constexpr std::uint32_t kLoopbackProbeMs  = 250;
constexpr std::uint32_t kBroadcastProbeMs = 1000;
constexpr int           kBroadcastTries   = 3;
constexpr std::uint32_t kFallbackProbeMs  = 250;

// Reply buffer cap. Replies are tiny (~80 bytes); 512 is generous.
constexpr std::size_t kReplyBufBytes = 512;

// Build the probe payload once per resolveBridge call. The mod_ver field
// is informational; the bridge logs it on receipt but doesn't gate on
// match (HelloAck handles real version policing).
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

// Open a UDP socket, optionally enabling SO_BROADCAST. Returns -1 on
// failure. Caller closes via hk::socket::Socket::close.
s32 openUdpSocket(bool enable_broadcast) {
    auto* sock = hk::socket::Socket::instance();
    if (!sock) return -1;
    auto vor = sock->socket(hk::socket::AddressFamily::Ipv4,
                            hk::socket::Type::Datagram,
                            hk::socket::Protocol::Udp);
    if (!vor.hasValue()) return -1;
    auto t = vor.getInnerValue();
    const s32 fd = t.a;
    if (fd < 0) return -1;
    if (enable_broadcast) {
        // Use the explicit Span-taking overload — the templated convenience
        // form `setSockOpt(fd, level, opt, T&)` fails to compile because its
        // body constructs Span<const u8> directly from `&opt` (an `s32*`),
        // and Span's constructor wants `const u8*`. Same trap ApClient hit.
        const s32 on = 1;
        auto _ = sock->setSockOpt(
            fd, kSolSocket, kSoBroadcast,
            hk::Span<const u8>(reinterpret_cast<const u8*>(&on),
                               sizeof(on)));
        (void)_;
    }
    return fd;
}

// Wait up to `timeout_ms` for incoming data on `fd`. Returns true when
// data is readable, false on timeout / poll error.
bool waitReadable(s32 fd, std::uint32_t timeout_ms) {
    auto* sock = hk::socket::Socket::instance();
    if (!sock) return false;
    hk::socket::PollFd pfd[1] = {
        {.fd = fd,
         .requestedEvents = hk::socket::PollEvents::CanRead,
         .returnedEvents  = hk::socket::PollEvents::Default},
    };
    auto vor = sock->poll(hk::Span<hk::socket::PollFd>(pfd, 1),
                          static_cast<s32>(timeout_ms));
    if (!vor.hasValue()) return false;
    const auto t = vor.getInnerValue();
    if (t.a <= 0) return false;
    using PE = hk::socket::PollEvents;
    return (pfd[0].returnedEvents & PE::CanRead) != 0;
}

// Parse a `{"t":"bridge","host":"<ip>","port":<int>,...}` reply into out.
// Returns false on malformed input or missing required fields.
bool parseReply(const char* data, std::size_t len, BridgeTarget& out) {
    // Reader mutates the buffer to decode escape sequences in strings;
    // copy into a writable temp so the caller's buffer isn't mangled.
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
            // Unknown field; skip its value. The Reader API requires us to
            // consume one token before the next nextField() call. nextString
            // / nextInt / nextBool / isNull all advance; pick whichever
            // doesn't fail (best-effort skip).
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

// One probe: send `probe_data` to (host, port), wait up to timeout_ms for a
// reply. On a successful parse, fill `out` and return true. On any failure
// (sendto / poll-timeout / parse-fail) return false.
bool oneProbe(s32 fd, const char* probe_data, std::size_t probe_len,
              const char* host, std::uint16_t port,
              std::uint32_t timeout_ms, BridgeTarget& out) {
    auto* sock = hk::socket::Socket::instance();
    if (!sock) return false;
    // SocketAddrIpv4::parse encapsulates the Nintendo 16-byte sockaddr
    // layout that production exlaunch had to construct by hand.
    auto parsed = hk::socket::SocketAddrIpv4::parse(host, port);
    if (!parsed.hasValue()) {
        SMOAP_LOG_WARN("[discover] SocketAddrIpv4::parse failed for %s", host);
        return false;
    }
    const hk::socket::SocketAddrIpv4 addr = parsed.getInnerValue();
    auto send_vor = sock->sendTo(
        fd,
        hk::Span<const u8>(reinterpret_cast<const u8*>(probe_data), probe_len),
        0, addr);
    if (!send_vor.hasValue()) {
        SMOAP_LOG_WARN("[discover] sendTo %s:%u failed (IPC error)", host, port);
        return false;
    }
    {
        const auto t = send_vor.getInnerValue();
        if (t.a < 0) {
            SMOAP_LOG_WARN("[discover] sendTo %s:%u failed errno=%d",
                           host, port, t.b);
            return false;
        }
    }
    if (!waitReadable(fd, timeout_ms)) return false;
    char buf[kReplyBufBytes];
    hk::socket::SocketAddrIpv4 from{};
    auto recv_vor = sock->recvFrom(
        fd,
        hk::Span<u8>(reinterpret_cast<u8*>(buf), sizeof(buf)),
        0, from);
    if (!recv_vor.hasValue()) return false;
    const auto rt = recv_vor.getInnerValue();
    if (rt.a <= 0) return false;
    return parseReply(buf, static_cast<std::size_t>(rt.a), out);
}

void closeSocket(s32 fd) {
    auto* sock = hk::socket::Socket::instance();
    if (!sock) return;
    auto _ = sock->close(fd);
    (void)_;
}

}  // namespace

bool resolveBridge(BridgeTarget& out, const BridgeTarget& fallback,
                   std::uint16_t discovery_port) {
    char probe[kReplyBufBytes];
    const std::size_t probe_len = buildProbe(probe, sizeof(probe));
    if (probe_len == 0) return false;

    // ---- Step 1: loopback (Ryujinx-on-same-host) ----
    s32 fd = openUdpSocket(/*enable_broadcast=*/false);
    if (fd >= 0) {
        BridgeTarget t;
        const bool ok = oneProbe(
            fd, probe, probe_len,
            "127.0.0.1", discovery_port,
            kLoopbackProbeMs, t);
        closeSocket(fd);
        if (ok) {
            SMOAP_LOG_INFO("[discover] resolved via loopback -> %s:%u",
                           t.host.c_str(), t.port);
            out = t;
            return true;
        }
    } else {
        SMOAP_LOG_WARN("[discover] UDP socket() failed (loopback step)");
    }

    // ---- Step 2: LAN broadcast ----
    fd = openUdpSocket(/*enable_broadcast=*/true);
    if (fd >= 0) {
        bool resolved = false;
        BridgeTarget t;
        for (int i = 0; i < kBroadcastTries && !resolved; ++i) {
            resolved = oneProbe(
                fd, probe, probe_len,
                "255.255.255.255", discovery_port,
                kBroadcastProbeMs, t);
        }
        closeSocket(fd);
        if (resolved) {
            SMOAP_LOG_INFO("[discover] resolved via broadcast -> %s:%u",
                           t.host.c_str(), t.port);
            out = t;
            return true;
        }
    } else {
        SMOAP_LOG_WARN("[discover] UDP socket() failed (broadcast step)");
    }

    // ---- Step 3: unicast probe to fallback IP ----
    if (!fallback.host.empty()) {
        fd = openUdpSocket(/*enable_broadcast=*/false);
        if (fd >= 0) {
            BridgeTarget t;
            const bool ok = oneProbe(
                fd, probe, probe_len,
                fallback.host.c_str(), discovery_port,
                kFallbackProbeMs, t);
            closeSocket(fd);
            if (ok) {
                SMOAP_LOG_INFO("[discover] resolved via fallback-unicast -> %s:%u",
                               t.host.c_str(), t.port);
                out = t;
                return true;
            }
        } else {
            SMOAP_LOG_WARN("[discover] UDP socket() failed (fallback step)");
        }
    }

    SMOAP_LOG_INFO("[discover] no UDP reply; caller will TCP-fallback to %s:%u",
                   fallback.host.c_str(), fallback.port);
    return false;
}

}  // namespace smoap::ap
