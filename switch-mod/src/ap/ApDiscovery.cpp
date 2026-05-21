// See ApDiscovery.hpp for the design rationale.

#include "ApDiscovery.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "nn/socket.hpp"
#include "lib/nx/nx.h"

#include "../util/Json.hpp"
#include "../util/Log.hpp"
#include "ApProtocol.hpp"  // SMO_AP_MOD_VERSION_STRING is plumbed in via this TU

namespace smoap::ap {

namespace {

// BSD socket constants (not exposed by lunakit's nn/socket.hpp). Mirrors the
// set in ApClient.cpp — kept duplicated rather than extern'd to keep the
// discovery TU self-contained.
constexpr int kAfInet     = 2;
constexpr int kSockDgram  = 2;
constexpr int kSolSocket  = 0xffff;
constexpr int kSoBroadcast = 0x0020;

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

// Fill `addr` with the (host, port) tuple. Returns false on InetAton failure
// (i.e. host wasn't a dotted-quad). Caller is responsible for socket-fd
// lifetime; this just sets the destination address.
bool makeSockaddr(sockaddr& addr, const char* host, std::uint16_t port) {
    std::memset(&addr, 0, sizeof(addr));
    addr.family = static_cast<u8>(kAfInet);
    addr.port = nn::socket::InetHtons(port);
    return nn::socket::InetAton(host, &addr.address) != 0;
}

// Open a UDP socket, optionally enabling SO_BROADCAST. Returns -1 on
// failure. Caller closes via nn::socket::Close.
int openUdpSocket(bool enable_broadcast) {
    const int fd = nn::socket::Socket(kAfInet, kSockDgram, 0);
    if (fd < 0) return -1;
    if (enable_broadcast) {
        const int on = 1;
        nn::socket::SetSockOpt(fd, kSolSocket, kSoBroadcast, &on, sizeof(on));
    }
    return fd;
}

// Wait up to `timeout_ms` for incoming data on `fd`. Returns true when
// data is readable, false on timeout / select error.
bool waitReadable(int fd, std::uint32_t timeout_ms) {
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(fd, &rfds);
    timeval tv{};
    tv.tv_sec  = static_cast<long>(timeout_ms / 1000);
    tv.tv_usec = static_cast<long>((timeout_ms % 1000) * 1000);
    const s32 rc = nn::socket::Select(fd + 1, &rfds, nullptr, nullptr, &tv);
    return rc > 0 && FD_ISSET(fd, &rfds);
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
// (sendto / select-timeout / parse-fail) return false. Caller manages the
// outer socket lifetime so we can reuse it across the loopback / fallback
// unicast pair.
bool oneProbe(int fd, const char* probe_data, std::size_t probe_len,
              const char* host, std::uint16_t port,
              std::uint32_t timeout_ms, BridgeTarget& out) {
    sockaddr addr;
    if (!makeSockaddr(addr, host, port)) {
        SMOAP_LOG_WARN("[discover] InetAton failed for %s", host);
        return false;
    }
    const s32 sent = nn::socket::SendTo(
        fd, probe_data, probe_len, 0, &addr, sizeof(addr));
    if (sent < 0) {
        const int err = nn::socket::GetLastErrno();
        SMOAP_LOG_WARN("[discover] SendTo %s:%u failed errno=%d",
                       host, port, err);
        return false;
    }
    if (!waitReadable(fd, timeout_ms)) return false;
    char buf[kReplyBufBytes];
    sockaddr from{};
    u32 from_len = sizeof(from);
    const s32 n = nn::socket::RecvFrom(
        fd, buf, sizeof(buf), 0, &from, &from_len);
    if (n <= 0) return false;
    return parseReply(buf, static_cast<std::size_t>(n), out);
}

}  // namespace

bool resolveBridge(BridgeTarget& out, const BridgeTarget& fallback,
                   std::uint16_t discovery_port) {
    char probe[kReplyBufBytes];
    const std::size_t probe_len = buildProbe(probe, sizeof(probe));
    if (probe_len == 0) return false;

    // ---- Step 1: loopback (Ryujinx-on-same-host) ----
    int fd = openUdpSocket(/*enable_broadcast=*/false);
    if (fd >= 0) {
        BridgeTarget t;
        const bool ok = oneProbe(
            fd, probe, probe_len,
            "127.0.0.1", discovery_port,
            kLoopbackProbeMs, t);
        nn::socket::Close(fd);
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
        nn::socket::Close(fd);
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
            nn::socket::Close(fd);
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
