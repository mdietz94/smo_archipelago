// "Cappy speaks for Archipelago" — per-frame driver that routes AP item
// notifications through SMO's existing CapMessage speech-bubble pipeline.
//
// Why this design (the long story is in CLAUDE.md M-track docs):
//   The prior approach rendered toasts directly via sead::TextWriter, which
//   needs the sead::DebugFontMgrJis1Nvn font singleton primed. We can't
//   reliably bootstrap that font from a third-party subsdk (heap lifetime +
//   singleton-instance ordering problems documented in the now-deleted
//   ApHudOverlay::initTextWriter comment). Lunakit cohabit was the only out,
//   and the user wants to ship a self-contained mod.
//
//   So instead: piggy-back on Nintendo's CapMessage system. It already has
//   font, layout, animation, voice gating, 2D suppression, and priority
//   handling wired up. We just need to hijack the MSBT text lookup for one
//   reserved label so it returns OUR string instead of a Nintendo one.
//
// Threading: enqueue + tryPump + setText hook all run on the game frame
// thread. The single backing UTF-16 buffer is read-only from the perspective
// of the rest of the game; rotation of its contents is gated on
// rs::isActiveCapMessage returning false (no overlap window).
//
// Filter rules match the prior ToastQueue contract: skip self-grants, skip
// REPL/bridge-injected items, skip Other kinds, suppress on bulk replays.
// Host tests in switch-mod/tests/test_cappy_messenger.cpp exercise these
// rules without linking the game-side machinery.

#pragma once

#include <cstddef>
#include <cstdint>

#include "../ap/ApProtocol.hpp"

namespace smoap::ui {

// Reserved MSBT label used as the trigger for our CapMessage substitution.
// Nintendo's content never uses the "Archipelago" prefix, so collisions are
// impossible. The MessageHolderTryGetTextHook trampoline keys off an exact
// strcmp against this string.
//
// Cap is 64 bytes; the label itself is ASCII and unlikely to ever grow.
inline constexpr const char* kArchipelagoLabel = "ArchipelagoCappyMsg";

// M6 phase C reconcile sentinel — bridge sets `Item.from = "(offline)"`
// when synthesizing an ItemMsg for a moon that was collected during a
// bridge-offline window (no live cutscene-label was shown, so the player
// has no AP context yet). The Cappy filter accepts it (any non-empty,
// non-local-slot value passes), and the formatter strips the "from
// <sentinel>" suffix to produce a clean "Got X!" — keeping the message
// short and avoiding a confusing literal "(offline)" in the bubble.
inline constexpr const char* kReconcileFromSentinel = "(offline)";

// Manual-grant sentinel — bridge sets `Item.from = "(self)"` for the AP
// echo of a `/send_location` (or any other CommonContext path that
// bypasses the Switch's natural-check pipeline). The item routed back
// to the player's own slot, but no in-game event ran, so we still want a
// bubble — just without an attribution suffix. Formatter handling is
// identical to the reconcile sentinel.
inline constexpr const char* kManualGrantSentinel = "(self)";

class CappyMessenger {
public:
    // Pending-message ring. Cap chosen empirically: a kingdom-unlock burst
    // never exceeds 4-5 items and the user picked "suppress on bulk replay"
    // so anything larger gets dropped at the source. 8 is generous.
    static constexpr std::size_t kQueueCap = 8;

    // Each on-screen balloon lives ~3s; if rs::tryShowCapMessagePriorityLow
    // returns false for this many consecutive frames we drop the head item
    // with a log line rather than queueing indefinitely.
    //
    // Was 1800 (~30s). Bumped 2026-05-18 after a Ryujinx repro showed AP
    // items arriving DURING the new-game intro — Cappy doesn't actually join
    // Mario for ~60s of gameplay at the start of a fresh save, so the
    // CapMessage director is unavailable that entire window. The 30s cap was
    // dropping any item that arrived during the intro before Cappy could
    // ever show it. 9000 frames (~2.5 min @ 60fps) comfortably covers the
    // intro + any pause-menu / cutscene extensions, with the queue still
    // bounded so a truly broken state eventually drops.
    static constexpr std::uint32_t kMaxRetryFrames = 9000;

    // Defensive: dispatch only after BOTH this many drawMain calls AND this
    // many wallclock milliseconds have elapsed since the scene transition.
    // Why both: rs::isActiveCapMessage NULL-derefs on the un-registered
    // CapMessage director, and we have no direct readiness probe. On real
    // Switch 60fps is locked so either heuristic suffices; on Ryujinx the
    // emulator's GPU stalls mean drawMain stops firing for seconds at a
    // time, decoupling wallclock from actual game-state progression. The
    // frame counter blocks while drawMain is stalled (since it only ticks
    // when tryPump fires), the wallclock counter blocks if the game runs
    // faster than 60fps in catch-up mode. The MAX of the two is correct in
    // both cases.
    // Dispatch only after BOTH a minimum number of drawMain calls AND a
    // minimum wallclock interval since the scene transition. On real Switch
    // 60fps is locked so the two thresholds align; on Ryujinx the wallclock
    // half catches the catch-up-frame edge case where guest frames advance
    // faster than wallclock under GPU stalls. Matches production's 600 frame
    // value; the wallclock half is defense-in-depth that costs nothing on
    // real hardware.
    static constexpr std::uint32_t kSceneSettleFrames = 600;     // ~10s @ 60fps
    static constexpr std::int64_t  kSceneSettleMs     = 10000;   // 10s wallclock

    // On-screen duration. Passed as the THIRD positional arg to
    // rs::tryShowCapMessagePriorityLow (which the decompiler signature
    // makes look like "delay" — see disasm note in tryPump). 90 ticks
    // @ 60Hz = 1.5s, the user-requested baseline.
    static constexpr int kWaitTicks = 90;

    // Soft target for total displayed-string length (UTF-8 bytes, ASCII
    // chars 1:1). If the full "Got X from Y!" exceeds this, the formatter
    // truncates the SENDER first (preserving the item name in full) and
    // appends "..." before the trailing "!". 60 chars comfortably fits
    // one line of the CapMessage speech bubble without the layout wrap-
    // or-truncate behavior kicking in. Tunable per UX feedback.
    static constexpr std::size_t kSoftMaxChars = 60;

    // Backing buffer for the substituted text. Must be a static lifetime
    // pointer (the game holds it for the balloon's full lifetime). Single
    // slot — rotation only happens when isActiveCapMessage returns false.
    //
    // Sized for "Got <96-char-name> from <32-char-sender>!" plus margin.
    static constexpr std::size_t kBufferUtf16Words = 160;

    static CappyMessenger& instance();

    // Enqueue an item for display. No-op if shouldShowCappyMsg returns false.
    // suppress = true when applyOnFrame detects a bulk-replay burst.
    // local_slot is ApState::local_slot (may be empty pre-handshake).
    void enqueue(const smoap::ap::Item& item,
                 const char* local_slot,
                 bool suppress);

    // Enqueue a verbatim system message — bypasses shouldShowCappyMsg (system
    // messages have no sender/kind) and skips formatCappyMsg's "Got X from Y!"
    // wrapping. Text is copied verbatim into the Entry buffer. Caller is
    // responsible for keeping the displayed string within the Cappy bubble's
    // ~60-char comfortable width. Used by ApClient for AP connection-state
    // transition bubbles ("Connected to Archipelago" / "Disconnected from
    // Archipelago"). Null or empty text is a no-op.
    void enqueueSystem(const char* text);

    // Per-frame driver. Called from DrawMainHook AFTER applyOnFrame. Tries
    // to dispatch the head item via rs::tryShowCapMessagePriorityLow.
    // scene == nullptr -> no-op (boot scene, scene transition).
    void tryPump(const void* scene);

    // Returns a pointer to the static UTF-16 buffer iff `label` matches our
    // reserved sentinel. Called from the MessageHolderTryGetTextHook
    // trampoline. The hook intentionally has no other state — all logic
    // lives here so the hot path stays a single virtual-method call + a
    // strcmp + a pointer return.
    const char16_t* lookupSubstitution(const char* label) const;

    // Pump-success hook: called by the trampoline-callsite of tryPump when
    // tryShowCapMessagePriorityLow returns true. Advances head_, resets
    // retry counter; the buffer's content stays live until SMO finishes
    // showing this balloon (we don't write to it again until pump finds
    // isActiveCapMessage == false).
    void markDispatched();

    // "Cappy can speak" latch. Flips to true the first time tryPump
    // successfully dispatches a balloon (rs::tryShowCapMessagePriorityLow
    // returned true), and stays true until clearDispatchLatch() is called.
    // The snapshot path in ApClient uses this as its scene-readiness gate:
    // a true value implies scene != null AND settle_frames >= 600 AND the
    // CapMessageDirector was registered AND Nintendo's pipeline accepted
    // our show — i.e. we are unambiguously in a live gameplay scene with
    // save data fully resident, not on a file-select preview render.
    // SaveLoadHook clears this latch alongside its other session-state
    // resets, so a re-HELLO defers snapshot until the new save's first
    // Cappy dispatch lands.
    bool hasDispatchedSinceReset() const { return dispatched_since_reset_; }
    void clearDispatchLatch() { dispatched_since_reset_ = false; }

    // Test-only knobs ------------------------------------------------------
    std::size_t pendingCount() const { return live_count_; }
    bool bufferActive() const { return buffer_in_use_; }
    void resetForTest();

private:
    CappyMessenger() = default;

    struct Entry {
        char text[128] = {};  // pre-conversion UTF-8 form for logging
        bool live = false;
    };

    Entry queue_[kQueueCap]{};
    std::size_t head_ = 0;        // next-to-dispatch index
    std::size_t tail_ = 0;        // next-to-enqueue index
    std::size_t live_count_ = 0;
    std::uint32_t retry_frames_ = 0;

    // Scene-stability tracking for the settle-delay guard. last_scene_ is
    // the pointer we saw on the last pump. Both counters reset on scene
    // change; dispatch is gated on settle_frames_ >= kSceneSettleFrames
    // AND (now - scene_change_ms_) >= kSceneSettleMs.
    const void* last_scene_ = nullptr;
    std::uint32_t settle_frames_ = 0;
    std::int64_t scene_change_ms_ = 0;

    // Substitution buffer. Filled by tryPump immediately before calling
    // rs::tryShowCapMessagePriorityLow with kArchipelagoLabel; the hook
    // serves this pointer until markDispatched clears buffer_in_use_.
    char16_t buffer_[kBufferUtf16Words]{};
    bool buffer_in_use_ = false;

    // Sticky "first dispatch since reset" latch — see hasDispatchedSinceReset.
    // Single-threaded (frame-thread only), no atomic needed; the snapshot path
    // reads this via hasDispatchedSinceReset() from the worker thread, but a
    // stale false there only defers the snapshot one more loop iteration
    // (worker re-checks every ~200ms), which is benign.
    bool dispatched_since_reset_ = false;
};

// Filter rules (free function, exercised by host tests) -----------------------
//
// Mirror of the prior shouldShowToast contract:
//   - skip when suppress = true (bulk-replay burst)
//   - skip when from is null/empty (REPL / bridge-injected items)
//   - skip when from equals local_slot (self-grants)
//   - skip Other (no in-game effect worth surfacing)
//
// `from` is taken as a C-string (Item::from is a fixed char[] post-M6.1).
bool shouldShowCappyMsg(smoap::ap::ItemKind kind,
                        const char* from,
                        const char* local_slot,
                        bool suppress);

// Format "Got <name> from <sender>!" into buf. Falls back to "?" for empty
// name or sender. Returns number of bytes written (excluding NUL), or 0 on
// degenerate inputs.
int formatCappyMsg(char* buf, std::size_t cap, const smoap::ap::Item& item);

// Cosmetic name shortening for the speech bubble ONLY. Wire/AP/tracker/REPL/
// logs all keep the canonical name (174 items, see
// apworld/smo_archipelago/data/items.json). 36 items match one of three
// long suffixes and we drop just " Kingdom" from each so the item type
// (Power Moon / Multi-Moon / Sticker) stays full-text — matches the
// bridge's `format_moon_label` shape so live cutscene labels and offline
// reconcile bubbles read identically:
//
//   "X Kingdom Power Moon" -> "X Power Moon"  (saves 8 chars)
//   "X Kingdom Multi-Moon" -> "X Multi-Moon"  (saves 8 chars)
//   "X Kingdom Sticker"    -> "X Sticker"     (saves 8 chars)
//
// Everything else is copied verbatim. dst always NUL-terminates; if src
// is longer than dst_cap-1 the unchanged-copy path truncates silently
// (the formatter's outer fit-check still applies a sensible message-level
// cap afterwards). Returns the number of bytes written, NOT including NUL.
//
// Exposed here so host tests can lock in the substitution rules without
// going through the full enqueue/format path.
std::size_t shortenItemNameForBubble(const char* src,
                                     char* dst,
                                     std::size_t dst_cap);

// UTF-8 -> UTF-16LE. Drops malformed sequences silently (last-resort
// resilience; the strings we feed it are all ASCII apworld names + ASCII
// player slot names, so the lossy path is effectively dead code in
// practice). Returns the number of char16_t words written, NOT including
// a trailing zero. Always writes a zero terminator if cap > 0.
std::size_t utf8ToUtf16(const char* src,
                        char16_t* dst,
                        std::size_t dst_cap_words);

}  // namespace smoap::ui
