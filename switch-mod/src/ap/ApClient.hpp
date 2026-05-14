// TCP client to the bridge.
//
// Owns a single nn::socket TCP connection; runs its own background thread.
// Reads line-delimited JSON, dispatches into ApState. Writes outbound messages
// from ApState rings.
//
// Reconnect policy: exponential backoff (1, 2, 5, 10, 30 cap) seconds.

#pragma once

#include <atomic>
#include <cstdint>
#include <string>

namespace smoap::ap {

struct BridgeTarget {
    std::string host;
    std::uint16_t port = 17777;
    std::uint32_t retry_ms = 3000;
    std::uint32_t recv_timeout_ms = 200;
};

class ApClient {
public:
    static ApClient& instance();

    // Call ONCE from a frame-thread context (GameSystemInit hook callback,
    // after Orig). Does the nifm + nn::socket bring-up that requires an
    // nn-aware thread. start() depends on this having completed.
    void initNetworking();

    void start(const BridgeTarget& target);
    void stop();

    // Pump outbound rings into the wire. Called by the socket thread.
    void pumpOnce();

    void threadMain();  // public for the worker entry trampoline

private:
    ApClient() = default;

    bool connectOnce();
    void disconnect();
    void sendHello();
    bool readOneLine(std::string& out);
    void handleLine(const std::string& line);

    BridgeTarget target_{};
    std::atomic<bool> running_{false};
    int socket_fd_{-1};
    std::string read_buf_;  // accumulator for partial lines
};

}  // namespace smoap::ap
