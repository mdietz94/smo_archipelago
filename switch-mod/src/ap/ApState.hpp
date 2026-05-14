// Module-resident game-state mirror.
//
// Singleton accessed from two threads:
//   - Socket thread (ApClient::loop) — produces inbound items, consumes outbound.
//   - Frame thread (drawMain trampoline) — produces outbound checks, consumes inbound.
// All cross-thread state goes through SPSC ring buffers + std::atomic.

#pragma once

#include <array>
#include <atomic>
#include <bitset>
#include <cstdint>
#include <set>

#include "ApProtocol.hpp"

namespace smoap::ap {

enum class ConnState : std::uint8_t {
    Disconnected = 0,
    Connecting = 1,
    Hello = 2,
    Ready = 3,
};

template <typename T, std::size_t N>
class SpscRing {
public:
    bool push(const T& v) {
        const auto h = head_.load(std::memory_order_relaxed);
        const auto next = (h + 1) % N;
        if (next == tail_.load(std::memory_order_acquire)) return false;  // full
        buf_[h] = v;
        head_.store(next, std::memory_order_release);
        return true;
    }
    bool pop(T& out) {
        const auto t = tail_.load(std::memory_order_relaxed);
        if (t == head_.load(std::memory_order_acquire)) return false;  // empty
        out = buf_[t];
        tail_.store((t + 1) % N, std::memory_order_release);
        return true;
    }

private:
    std::array<T, N> buf_{};
    std::atomic<std::size_t> head_{0};
    std::atomic<std::size_t> tail_{0};
};

struct StatusEvent {
    bool goal = false;
};

class ApState {
public:
    static ApState& instance();

    std::atomic<ConnState> conn{ConnState::Disconnected};
    std::atomic<std::int64_t> last_rx_ns{0};

    // socket -> frame
    SpscRing<Item, 256> inbound;
    // frame -> socket
    SpscRing<Check, 256> outbound_checks;
    SpscRing<StatusEvent, 16> outbound_status;

    // frame-thread-only state below

    std::bitset<128> captures_unlocked;     // 42 used; index from capture_table.h
    std::set<std::uint64_t> locations_checked;  // session dedupe (hash of message body)
    std::uint32_t received_kingdom_mask = 0;
    bool goal_sent = false;
    bool synthetic_grant_this_frame = false;

    // Apply queued inbound items to the game (frame thread).
    void applyOnFrame();

    // Hash a Check message body for dedupe purposes.
    static std::uint64_t hashCheck(const Check&);

private:
    ApState() = default;
};

}  // namespace smoap::ap
