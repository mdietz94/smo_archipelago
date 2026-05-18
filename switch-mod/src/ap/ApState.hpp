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

#include "ApProtocol.hpp"

namespace smoap::ap {

// Allocation-free fixed-capacity open-addressing hash set used for session
// dedupe of location-check hashes. std::set::insert ends up calling into
// libstdc++'s _Rb_tree node allocator, which on devkitA64 hits a TLS path
// (nn::os::GetTlsValue with an unallocated slot) that NULL-derefs in our
// subsdk9 context. The set isn't on a critical hot path — checks fire at
// game-event rate, capped by N — so linear-probing is fine.
//
// N must be a power of 2. With N = 4096 we get 32 KiB of storage and can
// hold up to ~3000 unique checks before probing degrades. Real seeds top out
// around 1000 locations.
template <std::size_t N>
class FlatHashSet {
    static_assert((N & (N - 1)) == 0, "N must be a power of 2");
public:
    // Returns true iff the value was newly inserted. Sentinel 0 is mapped to
    // 1 internally so callers can pass any 64-bit hash. Table-full returns
    // false (matches "already present" semantics — drops the check rather
    // than re-sending it).
    bool tryInsert(std::uint64_t h) {
        if (h == 0) h = 1;
        for (std::size_t i = 0; i < N; ++i) {
            const std::size_t idx = (h + i) & (N - 1);
            const std::uint64_t cur = slots_[idx];
            if (cur == 0) {
                slots_[idx] = h;
                ++size_;
                return true;
            }
            if (cur == h) return false;
        }
        return false;  // table full
    }

    void reset() {
        for (auto& s : slots_) s = 0;
        size_ = 0;
    }

    std::size_t size() const { return size_; }

private:
    std::uint64_t slots_[N] = {};
    std::size_t size_ = 0;
};

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
    // Peek at the front entry without consuming. Consumer-only (single
    // thread w.r.t. tail_). Used by pumpOnce for peek-then-pop sends:
    // a failing Send leaves the entry queued for the next pump cycle so
    // outbound checks survive transient socket errors / brief disconnects.
    bool peek(T& out) {
        const auto t = tail_.load(std::memory_order_relaxed);
        if (t == head_.load(std::memory_order_acquire)) return false;  // empty
        out = buf_[t];
        return true;
    }
    // Pointer to the front entry — zero copies. Use when T owns heap memory
    // (e.g. std::string fields) and copying it onto the consumer thread would
    // hit libstdc++'s allocator (which NULL-derefs in our subsdk9 link, see
    // memory project_libstdcpp_allocator_broken_in_subsdk9.md). Producer
    // still mutates buf_[head] freely; this returned pointer is invalidated
    // by popDiscard() but stable across pushes since tail_ doesn't move.
    const T* peekRef() {
        const auto t = tail_.load(std::memory_order_relaxed);
        if (t == head_.load(std::memory_order_acquire)) return nullptr;
        return &buf_[t];
    }
    // Discard the front entry. Caller must have observed it via peek().
    void popDiscard() {
        const auto t = tail_.load(std::memory_order_relaxed);
        tail_.store((t + 1) % N, std::memory_order_release);
    }
    // Approximate number of pending items. Two separate atomic loads, so the
    // value can race against a concurrent push (under-count by 1). For the
    // toast bulk-suppress heuristic this is fine — a borderline race resolves
    // to "suppressed = false" which is the safer default (toast might fire
    // for an item that arrived mid-frame; not a crash, just a visual quirk).
    std::size_t pendingApprox() const {
        const auto h = head_.load(std::memory_order_acquire);
        const auto t = tail_.load(std::memory_order_relaxed);
        return (h + N - t) % N;
    }

private:
    std::array<T, N> buf_{};
    std::atomic<std::size_t> head_{0};
    std::atomic<std::size_t> tail_{0};
};

struct StatusEvent {
    bool goal = false;
    bool death = false;
    std::int64_t ts_ms = 0;  // populated when death = true
};

// Inbound DeathLink debounce. Covers BOTH "Mario is currently in his death
// animation" and "two kills landed too close together". A single timestamp
// stamped on every observed death (organic or synthetic) is enough — if the
// last death was within this window, swallow.
inline constexpr std::int64_t kInboundKillDebounceMs = 15 * 1000;

// M6 phase A.5 — pending cutscene label slot.
//
// Single-slot publish-and-consume: socket thread (ApClient) writes the text +
// deadline, then release-stores `published_seq` to publish. Frame thread
// (MoonLabelHook callbacks) acquire-loads `published_seq`; if it differs from
// `last_consumed_seq`, reads the buffer and applies the label, then bumps
// `last_consumed_seq`. The release/acquire pair guarantees text-bytes ordering.
//
// `last_consumed_seq` is read/written only by the frame thread, so it doesn't
// need to be atomic. Same single-thread invariant holds for the buffer reads
// (consume side reads them once per cutscene; the socket thread won't
// overwrite while the cutscene is in flight unless a second moon is collected
// within the same ~3-5s window, in which case the newer label wins — which is
// what we want).
//
// Text buffer 32 bytes; bridge truncates to ≤30 bytes UTF-8 to leave room for
// the null terminator and a safety byte.
inline constexpr std::size_t kPendingMoonLabelCap = 32;

struct PendingMoonLabel {
    char text[kPendingMoonLabelCap] = {};
    std::int64_t deadline_ms = 0;     // monotonic; expired labels are dropped
    std::atomic<int> published_seq{0}; // 0 = empty / never set
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
    // any-thread -> socket. Mirror of every smoap::util::log() call above
    // SMOAP_LOG_FORWARD_MIN_LEVEL — surfaced in the PC client's "Switch" tab
    // so we can diagnose retail-Switch behaviour without `lm` capture.
    // SpscRing is single-producer; enqueueRemoteLog serialises producers
    // with its own atomic_flag spinlock since log() can be called from any
    // thread (frame, worker, hook callbacks).
    SpscRing<Log, 256> outbound_logs;
    std::atomic<std::uint32_t> log_drops{0};  // ring-full counter

    // socket -> frame. Pre-collection moon color: bridge sends
    // ShineScoutsMsg(s) once per AP connect after LocationScouts, then again
    // on every Switch HELLO. Each chunk holds up to ~200 (shine_uid, palette)
    // pairs. Frame thread drains and folds into shine_palette[].
    SpscRing<ShineScout, 4096> inbound_scouts;

    // frame-thread-only state below

    std::bitset<128> captures_unlocked;     // 43 used; index from capture_table.h
    FlatHashSet<4096> locations_checked;    // session dedupe (hash of message body)
    std::uint32_t received_kingdom_mask = 0;
    bool goal_sent = false;
    bool synthetic_grant_this_frame = false;

    // M7: set immediately before we invoke PlayerHackKeeper::forceKillHack
    // from the deferred-kill tick. Defense-in-depth — today nothing observes
    // the kill, but if a future hook lands on the post-cancel path it can
    // check this flag and skip outbound reporting so we don't echo a
    // synthetic "Mario un-captured" event back to AP.
    bool synthetic_uncapture_this_frame = false;

    // M7 deferred kill — CaptureStartHook's deny branch sets these instead of
    // calling forceKillHack inline; smoap::hooks::tickPendingUncapture()
    // drains them from drawMain ~1s later. The delay serves two purposes:
    //   (1) cancel/forceKillHack appears to be a no-op when invoked from
    //       inside startHack — playtest 2026-05-16 showed cancelHack ran
    //       cleanly but Mario stayed captured. By the time the hack demo has
    //       run its course, the keeper is in a state where teardown sticks.
    //   (2) it's funnier UX — the player runs around as the captured enemy
    //       for a beat before being yanked back to Mario.
    // Both fields touched only from the frame thread (CaptureStartHook fires
    // inline from game code during frame processing, drawMain runs there
    // too). Atomic for paranoid cross-frame visibility / consistency with
    // the surrounding state fields.
    std::atomic<void*> pending_kill_keeper{nullptr};
    std::atomic<std::int64_t> pending_kill_at_ms{0};

    // Set by SaveLoadHook around Orig(initializeData) so the dictionary-
    // write filter (AddHackDictionaryHook) lets SMO rehydrate the
    // HackDictionary from save unconditionally. Without this, every
    // capture the player legitimately owned at save time would be
    // blocked by the filter (captures_unlocked is reset to all-zero
    // at the top of the SaveLoadHook callback, before bridge rehello)
    // and the dictionary would be silently truncated. Set/cleared on
    // the frame thread; read on the frame thread; atomic for the
    // release/acquire visibility guarantee.
    std::atomic<bool> save_load_passthrough{false};

    // Cap name queued alongside the keeper. tickPendingUncapture re-reads
    // PlayerHackKeeper::getCurrentHackName(keeper) at deadline and compares
    // against this string. Mismatch (or empty) means SMO already released the
    // capture for some reason — player pressed Y, captured enemy died to the
    // environment (Bullet Bill against a wall, Goomba into lava), scene
    // transitioned, save loaded. Without this guard, forceKillHack/endHack
    // fires on a stale keeper bound to either nothing or a different cap.
    //
    // Frame-thread-only (CaptureStartHook deny writes, tickPendingUncapture
    // reads + clears) — no atomic required. char[64] not std::string for
    // the usual subsdk9 allocator-NULL-deref reason.
    char pending_kill_hack_name[64] = {};

    // M6 phase A — AP-credit counters surfaced via shine-counter hooks.
    // These are NOT shine flag flips: collecting a moon locally still drives
    // SMO's own shine table; AP-granted moons accumulate here and the
    // ShineNumGetHook / ShineNumByWorldGetHook add them on top of orig() so
    // the HUD reflects total credit. Reading these from the hook trampoline
    // (game thread) and writing from applyOnFrame (also game thread) — atomic
    // for paranoid cross-frame visibility only, no contention.
    //
    // kingdomBitFor() in KingdomUnlock.cpp returns 0..16 for known kingdoms;
    // ap_moons_kingdom[bit] is the per-kingdom credit count.
    std::atomic<int> ap_moons_kingdom[17] = {};

    // M6 phase B — GameDataHolder pointer cache.
    //
    // DrawMainHook reads HakoniwaSequence::mGameDataHolder (offset 0xB8, a
    // GameDataHolderAccessor wrapping a GameDataHolder*) on every frame and
    // stores the GameDataHolder* here. CaptureGate::grantCapture (and the
    // upcoming phase C kingdom / snapshot enumerate paths) consume it to
    // construct GameDataHolderWriter / GameDataHolderAccessor wrappers for
    // GameDataFunction:: calls.
    //
    // Same thread on both sides (game frame thread); atomic only for the
    // visibility guarantee — matches the player_hp_cache pattern above.
    // Stored as void* to avoid leaking the game header here.
    std::atomic<void*> game_data_holder_cache{nullptr};

    // AP-classification moon color (M-color milestone).
    // Indexed by SMO ShineInfo::shineId (s32). 0xFF = "no override; let the
    // game's stage color animation pick the default frame". Storage choice:
    // fixed array, NO std::map, to avoid the libstdc++ allocator NULL-deref
    // we hit in earlier milestones. 1 KiB BSS — cheap.
    //
    // Populated entirely on the frame thread by draining inbound_scouts in
    // applyOnFrame. Read on the frame thread by ShineAppearanceHook's
    // trampoline (single-threaded — both run inside drawMain or downstream).
    // Real shine_uid values observed in SMO 1.0.0 reach 1135+, so the original
    // 1024-cap was dropping ~half the moons. 2048 leaves ample headroom (2 KiB
    // BSS — still trivial) and stays a power of 2 for clarity.
    static constexpr std::size_t kMaxShineUid = 2048;
    static constexpr std::uint8_t kNoPaletteOverride = 0xFF;
    // Non-zero sentinel default: we want every uninitialized slot to mean
    // "no override" (let the game run orig() unchanged), not "use palette
    // frame 0" (an actual visible override). Filled to 0xFF in the ctor.
    std::uint8_t shine_palette[kMaxShineUid];

    // Bounds-checked accessors. Out-of-range uids return "no override" and
    // log once (the producer should never send these).
    std::uint8_t getShinePalette(int uid) const {
        if (uid < 0 || static_cast<std::size_t>(uid) >= kMaxShineUid) return kNoPaletteOverride;
        return shine_palette[uid];
    }
    void setShinePalette(int uid, std::uint8_t palette) {
        if (uid < 0 || static_cast<std::size_t>(uid) >= kMaxShineUid) return;
        shine_palette[uid] = palette;
    }

    // DeathLink debounce. Set by the frame thread when PlayerHitPointData::kill
    // fires; cleared by the socket worker after the death message ships. A
    // second kill() within the same death event short-circuits.
    std::atomic<bool> death_pending_send{false};

    // ---- Inbound DeathLink (bridge -> mod) ----------------------------------
    //
    // Bridge sets deathlink_enabled in hello_ack so the user toggles DeathLink
    // in bridge config without rebuilding the mod. When false, inbound kill
    // messages are queued (in case the flag flips later) but never applied.
    std::atomic<bool> deathlink_enabled{false};

    // PlayerHitPointData* captured on every DeathHook fire so the frame thread
    // can call DeathHook::Orig with it later when applying an inbound kill.
    // Stored as void* to avoid leaking the game header into ApState.hpp.
    std::atomic<void*> player_hp_cache{nullptr};

    // Monotonic timestamp (ms) of the last observed death — organic OR our
    // own synthetic kill. The single source of truth for both "Mario currently
    // dead" and "too soon since last inbound kill" checks.
    std::atomic<std::int64_t> last_observed_death_ms{0};

    // Inbound queue collapsed to a single bit: closely-spaced bounces overwrite
    // each other → automatic producer-side debounce. Socket worker sets, frame
    // thread drains via exchange(false).
    std::atomic<bool> inbound_kill_pending{false};

    // Set by the frame thread immediately before invoking DeathHook::Orig on
    // a synthetic kill. Defense-in-depth: DeathHook's trampoline Orig already
    // bypasses our Callback, but a future hook anywhere downstream of
    // PlayerHitPointData::kill could re-enter — this flag lets the death path
    // recognize "we caused this" and short-circuit outbound reporting.
    bool synthetic_death_this_frame = false;

    // M6 phase A.5 — Channel A. Socket thread publishes via
    // setPendingMoonLabel(); frame thread (MoonLabelHook) consumes via
    // tryTakePendingMoonLabel().
    PendingMoonLabel pending_moon_label;
    int label_last_consumed_seq = 0;  // frame-thread only

    // Publish a new label. Producer side (socket thread).
    void setPendingMoonLabel(const char* text, int seq, std::int64_t deadline_ms);

    // Consume the pending label if there's a fresh, unexpired one. Returns
    // false if no fresh label, label expired, or already consumed this seq.
    // On success, fills `text_out` (null-terminated, ≤ kPendingMoonLabelCap)
    // and marks the seq consumed so subsequent calls are no-ops until a new
    // label arrives. Consumer side (frame thread).
    bool tryTakePendingMoonLabel(char (&text_out)[kPendingMoonLabelCap]);

    // Monotonic per-Switch-session counter that MoonGetHook stamps onto
    // outbound Check messages. Bridge echoes back in MoonLabelMsg.seq. Starts
    // at 1 so the wire encoder's "seq > 0 means present" check works.
    std::atomic<int> next_check_seq{1};

    // ---- M6 phase D — moon-deposit accounting -------------------------------
    //
    // bridge_connected: set by ApClient::threadMain on HELLO ack, cleared on
    // disconnect/socket error. AddPayShineHook + ShineNumGetHook both read
    // this with relaxed ordering — neither needs synchronization with other
    // state, just an authoritative "are we online" bit.
    std::atomic<bool> bridge_connected{false};

    // next_deposit_seq: monotonic per-session sequence id stamped onto each
    // DepositMsg by AddPayShineHook so the bridge can ack idempotently.
    // Starts at 1; 0 reserved as "absent" / sentinel.
    std::atomic<std::uint64_t> next_deposit_seq{1};

    // last_acked_deposit_seq: high-water mark of seqs the bridge has acked.
    // Updated by ApClient on inbound DepositAckMsg. SaveLoadHook resets to
    // 0 — the post-load HELLO produces a fresh OutstandingMsg from the
    // bridge that becomes authoritative.
    std::atomic<std::uint64_t> last_acked_deposit_seq{0};

    // get_current_world_id_fn: function pointer resolved via nn::ro::Lookup
    // Symbol at module init (same pattern as M6-B's addHackDictionary). Takes
    // a GameDataHolderAccessor by value (1 ptr in x0) and returns s32 world
    // id, clamped to 0 in develop states. Null until resolved.
    void* get_current_world_id_fn = nullptr;

    // Pending deposits awaiting bridge ack. Replayed on reconnect from
    // ApClient::threadMain right after HELLO ack. Fixed-size ring (same
    // allocator-safety discipline as every other outbound buffer) — 32
    // entries × ~80 bytes = ~2.5 KiB BSS. Per-toss deposits are rate-limited
    // by the cutscene/fuel-up animation (sub-second cadence on the upper
    // bound) so the ring covers many seconds of offline buffering before
    // overflow truncates with a log line.
    struct PendingDeposit {
        std::uint64_t seq = 0;
        char kingdom[32] = {};
        int amount = 0;
    };
    SpscRing<PendingDeposit, 32> pending_deposits;

    // Local AP slot name — captured by ApClient when the bridge sends
    // hello_ack. Fixed buffer rather than std::string to avoid subsdk9's
    // libstdc++ allocator NULL-deref (see project_libstdcpp_allocator_broken_in_subsdk9.md).
    // Written once by the socket thread BEFORE conn.store(Ready) (release),
    // read by the frame thread AFTER conn == Ready (acquire) — the publish
    // ordering rides the existing conn-store fence.
    char local_slot[64] = {};

    // IUseSceneObjHolder* of HakoniwaSequence::curScene, refreshed every
    // frame by DrawMainHook and consumed by CappyMessenger::tryPump.
    //
    // Critical: this is NOT the raw StageScene* read from HakoniwaSequence
    // offset 0xB0. al::Scene has 4-way multiple inheritance
    // (NerveExecutor, IUseAudioKeeper, IUseCamera, IUseSceneObjHolder) and
    // the IUseSceneObjHolder sub-object lives at a non-zero offset. The
    // DrawMainHook does the static_cast<IUseSceneObjHolder*>(Scene*)
    // adjustment via the al::Scene header so the compile-time offset is
    // applied; the result of that cast is what gets stored here. Stored as
    // void* to keep this header free of game-side dependencies.
    std::atomic<void*> scene_cache{nullptr};

    // Apply queued inbound items to the game (frame thread).
    void applyOnFrame();

    // Hash a Check message body for dedupe purposes.
    static std::uint64_t hashCheck(const Check&);

    // Monotonic milliseconds. Backed by nn::os::GetSystemTick; safe to call
    // from either thread.
    static std::int64_t nowMs();

private:
    ApState() {
        // Fill the palette table with the "no override" sentinel so a shine
        // we've never scouted just runs orig() and keeps its stage default.
        for (auto& slot : shine_palette) slot = kNoPaletteOverride;
    }

    // Drain inbound_kill_pending; called from applyOnFrame.
    void maybeApplyInboundKill();
};

}  // namespace smoap::ap
