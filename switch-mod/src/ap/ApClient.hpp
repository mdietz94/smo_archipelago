// TCP client to the bridge.
//
// Owns a single hk::socket TCP connection; runs its own background thread.
// Reads line-delimited JSON, dispatches into ApState. Writes outbound messages
// from ApState rings.
//
// Reconnect policy: exponential backoff (1, 2, 5, 10, 30 cap) seconds.

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

namespace smoap::ap {

// Per-line cap for inbound buffering. Matches the wire-protocol max
// (smoap::ap::kMaxLineBytes = 8 KiB) — declared here without pulling in
// ApProtocol.hpp to keep header deps light.
inline constexpr std::size_t kInboundLineCap = 8 * 1024;

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
    // after Orig). Does the nifm bring-up (requires an nn-aware thread) and
    // initializes the hk::socket::Socket client against bsd:u. start() depends
    // on this having completed.
    void initNetworking();

    void start(const BridgeTarget& target);
    void stop();

    // Frame-thread API: ask the worker to close + reopen its socket so a
    // fresh HELLO triggers the bridge's checked_replay. SaveLoadHook calls
    // this after a save reload clears our session dedupe set.
    void requestRehello();

    // Frame-thread API: arm a deferred "connection-status" Cappy bubble for
    // the post-save-load notification. The worker thread fires either
    // "Connected to Archipelago" the instant the bridge reports ap_state=ready
    // (covers the common case where the AP dial finishes within ~1s of save
    // load), or "Not connected to Archipelago" once the wait window expires
    // without a ready signal. This replaces the prior synchronous read of
    // ApState::conn at enqueue time, which announced "Not connected" before
    // the bridge had time to finish its AP handshake — and got the matching
    // ap_state(ready) bubble suppressed by the rehello suppression window.
    void deferSaveLoadStatusBubble();

    // Pump outbound rings into the wire. Called by the socket thread.
    void pumpOnce();

    void threadMain();  // public for the worker entry trampoline

private:
    ApClient() = default;

    bool connectOnce();
    void disconnect();
    void sendHello();
    // M4.5 reconciliation: walks GameDataHolder via game::enumerateOwnedShines
    // / enumerateOwnedCaptures and emits state_begin / N x state_chunk /
    // state_end on the wire. Called from the worker thread right after
    // sendHello on every (re)connect — and transitively on save load via
    // SaveLoadHook -> requestRehello -> reconnect -> sendHello -> sendSnapshot.
    // M4.5 ships with the enumerate functions as no-op stubs (M5/M6 fills
    // them in), so the snapshot currently emits begin + _meta + end only,
    // which the bridge processes as an empty diff (no AP traffic).
    void sendSnapshot();
    // Read from socket into read_buf_. Returns false on socket close/error.
    // Does NOT extract lines — popLine pulls one at a time off read_buf_.
    bool recvIntoBuf();
    // Pop the next complete \n-terminated line from read_buf_ into `out`
    // (which must hold at least kInboundLineCap bytes; `out_len` receives the
    // number of bytes written, NOT including the trailing newline). Returns
    // false if no complete line is buffered. Decoupling this from recv is
    // critical: when the bridge sends N messages in one TCP push, we must
    // drain ALL of them before going back to Select (which only checks the
    // socket, not the buffer). Pre-split implementation conflated these and
    // silently held messages for indefinite time.
    //
    // Fixed `char[]` storage (here + handleLine) keeps the inbound path off
    // the libstdc++ allocator, which NULL-derefs in our subsdk9 link once
    // heap state has drifted (`std::string::assign`/`erase` → memcpy from
    // NULL, seen 2026-05-16 in popLine after sending HELLO on a re-HELLO).
    bool popLine(char* out, std::size_t& out_len);
    // `line` must be a mutable buffer; the Reader decodes escapes in place.
    void handleLine(char* line, std::size_t line_len);

    BridgeTarget target_{};
    std::atomic<bool> running_{false};
    std::atomic<bool> rehello_requested_{false};
    // Time-window suppressor for the "Disconnected/Connected from Archipelago"
    // Cappy bubbles. Set by requestRehello() to (now + kRehelloBubbleSuppressMs);
    // disconnect() and the ap_state bubble path skip their enqueue when
    // monotonic time is still inside the window. Auto-expires so a save-load
    // that ALSO coincides with SMOClient genuinely dying doesn't permanently
    // silence the disconnect notification — after the window, normal bubble
    // behavior resumes.
    std::atomic<std::int64_t> suppress_state_bubble_until_ms_{0};
    // Deadline (monotonic ms) for firing the deferred "Disconnected from
    // Archipelago" Cappy bubble after an ungraceful TCP drop. 0 = no pending.
    // disconnect() arms this instead of enqueueing immediately, so a sub-N-
    // second blip can recover silently (ap_state("ready") clears it, and the
    // matching "Connected" bubble stays silent because s_last_ap_state was
    // never flipped to "disconnected"). If grace expires with no recovery,
    // the worker loop fires the bubble + commits the state transition.
    std::atomic<std::int64_t> pending_disconnect_bubble_at_ms_{0};
    // Deferred save-load status bubble. Set by deferSaveLoadStatusBubble()
    // (frame thread, from SaveLoadHook) to a monotonic-ms deadline. The
    // worker thread checks this each loop iteration: fires
    // "Connected to Archipelago" early if ap_state=ready is observed, or
    // "Not connected to Archipelago" once the deadline expires. Either path
    // clears the deadline back to 0. The ap_state message handler also
    // honors this so the "ready" branch can fire the bubble immediately
    // instead of waiting on the worker's recv_timeout polling cycle.
    std::atomic<std::int64_t> save_load_announce_deadline_ms_{0};
    int socket_fd_{-1};
    char read_buf_[kInboundLineCap];
    std::size_t read_buf_len_{0};
};

}  // namespace smoap::ap
