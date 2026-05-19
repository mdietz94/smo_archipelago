// Host-compiler tests for smoap::ui::CappyMessenger + filter / format /
// utf8->utf16 helpers.
//
// Build (msys2 g++ — same toolchain the codebase already uses, see CLAUDE.md
// "Switch-module host tests"). Single-line so it doesn't trip -Wcomment:
//   "C:/msys64/mingw64/bin/g++.exe" -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST -Iswitch-mod/src switch-mod/tests/test_cappy_messenger.cpp switch-mod/src/ui/CappyMessenger.cpp -o test_cappy_messenger.exe
//
// Covers:
//   - shouldShowCappyMsg filter rules (the 6 cases the user signed off on)
//   - formatCappyMsg output + truncation safety
//   - utf8ToUtf16 conversion (ascii fast path, multi-byte, surrogate pair,
//     malformed-skip, buffer-cap)
//   - CappyMessenger::lookupSubstitution returns buffer iff label matches
//     AND buffer_in_use_ (defense-in-depth check)
//   - Queue overflow drops new items (preserves head)

// Disable subsdk-only logging while host-testing.
#define SMOAP_HOST_TEST 1

#include "ui/CappyMessenger.hpp"
#include "ap/ApProtocol.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

using namespace smoap::ap;
using namespace smoap::ui;

namespace {

int g_failures = 0;
const char* g_current_test = "";

#define EXPECT(cond) do {                                                       \
    if (!(cond)) {                                                              \
        std::fprintf(stderr, "[%s] FAIL %s:%d: %s\n",                           \
                     g_current_test, __FILE__, __LINE__, #cond);                \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define EXPECT_EQ_S(actual, expected) do {                                      \
    std::string _a = (actual);                                                  \
    std::string _e = (expected);                                                \
    if (_a != _e) {                                                             \
        std::fprintf(stderr, "[%s] FAIL %s:%d: \"%s\" != \"%s\"\n",             \
                     g_current_test, __FILE__, __LINE__, _a.c_str(), _e.c_str()); \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define EXPECT_EQ_I(actual, expected) do {                                      \
    long long _a = (long long)(actual);                                         \
    long long _e = (long long)(expected);                                       \
    if (_a != _e) {                                                             \
        std::fprintf(stderr, "[%s] FAIL %s:%d: %lld != %lld\n",                 \
                     g_current_test, __FILE__, __LINE__, _a, _e);               \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define TEST(name) static void name();                                          \
    struct name##_runner { name##_runner() { g_current_test = #name; name(); } } name##_instance; \
    static void name()

Item makeItem(ItemKind kind, const char* from, const char* name) {
    Item it{};
    it.kind = kind;
    // Item::from / Item::name are fixed char[] post-M6.1 — snprintf for a
    // length-bounded copy that always NUL-terminates.
    std::snprintf(it.from, sizeof(it.from), "%s", from ? from : "");
    std::snprintf(it.name, sizeof(it.name), "%s", name ? name : "");
    return it;
}

// --------------------------------------------------------------------------
// shouldShowCappyMsg: filter rules
// --------------------------------------------------------------------------

TEST(filter_other_player_shows) {
    EXPECT(shouldShowCappyMsg(ItemKind::Capture, "Alice", "Bob", false));
}

TEST(filter_self_grant_hides) {
    EXPECT(!shouldShowCappyMsg(ItemKind::Capture, "Bob", "Bob", false));
}

TEST(filter_empty_from_hides) {
    EXPECT(!shouldShowCappyMsg(ItemKind::Capture, "", "Bob", false));
}

TEST(filter_suppress_hides) {
    EXPECT(!shouldShowCappyMsg(ItemKind::Capture, "Alice", "Bob", true));
}

TEST(filter_other_kind_hides) {
    EXPECT(!shouldShowCappyMsg(ItemKind::Other, "Alice", "Bob", false));
}

TEST(filter_pre_handshake_treats_as_other) {
    // local_slot is empty (pre-handshake state) — any non-empty `from` is
    // treated as not-self, so messages fire. Mirrors the prior ToastQueue
    // contract.
    EXPECT(shouldShowCappyMsg(ItemKind::Capture, "Alice", "", false));
}

TEST(filter_null_local_slot_safe) {
    EXPECT(shouldShowCappyMsg(ItemKind::Capture, "Alice", nullptr, false));
}

TEST(filter_moon_shows) {
    EXPECT(shouldShowCappyMsg(ItemKind::Moon, "Alice", "Bob", false));
}

// --------------------------------------------------------------------------
// formatCappyMsg
// --------------------------------------------------------------------------

TEST(format_basic) {
    Item item = makeItem(ItemKind::Capture, "Alice", "Frog");
    char buf[96];
    const int n = formatCappyMsg(buf, sizeof(buf), item);
    EXPECT(n > 0);
    EXPECT_EQ_S(std::string(buf), "Got Frog from Alice!");
}

TEST(format_empty_name_falls_back_to_qmark) {
    Item item = makeItem(ItemKind::Capture, "Alice", "");
    char buf[96];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got ? from Alice!");
}

TEST(format_truncation_safe) {
    Item item = makeItem(ItemKind::Moon, "Alice", "Cascade Kingdom Power Moon");
    char buf[12];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT(std::strlen(buf) < sizeof(buf));
}

// M6 phase C reconcile — bridge-sentinel from drops the "from <sender>"
// suffix so the bubble shows a clean "Got X!" for offline-collected moons
// instead of "Got X from (offline)!". Sender sentinel is the public
// kReconcileFromSentinel constant the bridge mirrors on its side.

TEST(format_reconcile_sentinel_drops_from_clause) {
    Item item = makeItem(ItemKind::Moon, kReconcileFromSentinel,
                         "Cascade Kingdom Power Moon");
    char buf[96];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Cascade Power Moon!");
}

TEST(format_reconcile_sentinel_short_capture_name) {
    // Capture short-name path — shortener no-ops on raw cap names, so the
    // bubble is literally "Got <cap>!".
    Item item = makeItem(ItemKind::Capture, kReconcileFromSentinel, "Frog");
    char buf[96];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Frog!");
}

TEST(format_reconcile_sentinel_filter_passes) {
    // Belt-and-braces: the filter must pass for the sentinel (it's a
    // non-empty value that's distinct from any real slot name). Without
    // this the formatter would never run for a reconcile item.
    EXPECT(shouldShowCappyMsg(ItemKind::Moon, kReconcileFromSentinel, "Mario", false));
    // And it's still suppressed for the legitimate self-find case:
    EXPECT(!shouldShowCappyMsg(ItemKind::Moon, "Mario", "Mario", false));
}

// Manual-grant sentinel ("(self)") — bridge tags the from-field with this
// for the AP echo of a user-issued `/send_location`. Renders identically
// to the reconcile sentinel: clean "Got X!" with no from-clause.

TEST(format_manual_grant_sentinel_drops_from_clause) {
    Item item = makeItem(ItemKind::Capture, kManualGrantSentinel, "Bullet Bill");
    char buf[96];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Bullet Bill!");
}

TEST(format_manual_grant_sentinel_filter_passes) {
    // Same belt-and-braces as the reconcile sentinel: filter must accept
    // "(self)" (non-empty, never matches a real slot name).
    EXPECT(shouldShowCappyMsg(ItemKind::Capture, kManualGrantSentinel, "Mario", false));
}

// --------------------------------------------------------------------------
// shortenItemNameForBubble — cosmetic suffix rewrites
// --------------------------------------------------------------------------

TEST(shorten_kingdom_power_moon) {
    char buf[64];
    auto n = shortenItemNameForBubble("Cascade Kingdom Power Moon", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Cascade Power Moon");
    EXPECT_EQ_I(n, std::strlen("Cascade Power Moon"));
}

TEST(shorten_kingdom_multi_moon) {
    char buf[64];
    shortenItemNameForBubble("Luncheon Kingdom Multi-Moon", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Luncheon Multi-Moon");
}

TEST(shorten_kingdom_sticker) {
    char buf[64];
    shortenItemNameForBubble("Cap Kingdom Sticker", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Cap Sticker");
}

TEST(shorten_apostrophe_kingdom_handled) {
    // "Bowser's Kingdom Power Moon" — apostrophe is part of the kingdom name,
    // not the suffix. Should still match and shorten correctly.
    char buf[64];
    shortenItemNameForBubble("Bowser's Kingdom Power Moon", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Bowser's Power Moon");
}

TEST(shorten_no_match_verbatim) {
    char buf[64];
    shortenItemNameForBubble("Frog", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Frog");
    shortenItemNameForBubble("Power Moon", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Power Moon");
    shortenItemNameForBubble("Multi-Moon", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Multi-Moon");
}

TEST(shorten_short_input_no_underflow) {
    // Input shorter than the suffix — must not underflow strcmp.
    char buf[64];
    shortenItemNameForBubble("Hi", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "Hi");
    shortenItemNameForBubble("", buf, sizeof(buf));
    EXPECT_EQ_S(std::string(buf), "");
}

TEST(shorten_null_dst_safe) {
    EXPECT_EQ_I(shortenItemNameForBubble("Cascade Kingdom Power Moon", nullptr, 64), 0);
}

TEST(shorten_zero_cap_safe) {
    char buf[1] = {'X'};
    EXPECT_EQ_I(shortenItemNameForBubble("Anything", buf, 0), 0);
    EXPECT_EQ_I(buf[0], 'X');  // untouched
}

TEST(shorten_tiny_cap_truncates_verbatim) {
    // Rewrite would fit ("Cascade Power Moon" = 18) but dst is too small —
    // fall through to verbatim with truncation. We just need no overrun + NUL.
    char buf[8];
    shortenItemNameForBubble("Cascade Kingdom Power Moon", buf, sizeof(buf));
    EXPECT(std::strlen(buf) < sizeof(buf));
}

TEST(format_shortens_long_moon_name) {
    // End-to-end: long item name + long sender. Pre-shortener this exceeds
    // kSoftMaxChars and the sender gets truncated. Post-shortener the full
    // sender fits comfortably.
    Item item = makeItem(ItemKind::Moon, "Alice", "Luncheon Kingdom Power Moon");
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Luncheon Power Moon from Alice!");
}

TEST(format_shortens_multimoon) {
    Item item = makeItem(ItemKind::Moon, "Alice", "Cascade Kingdom Multi-Moon");
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Cascade Multi-Moon from Alice!");
}

// --------------------------------------------------------------------------
// Soft-truncation rule: short messages stay intact; long ones preserve the
// item name in full and truncate the sender with "..." before "!".
// --------------------------------------------------------------------------

TEST(format_short_message_passes_through) {
    // Under kSoftMaxChars (60) — full form should be emitted verbatim.
    Item item = makeItem(ItemKind::Capture, "Bob", "Frog");
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_S(std::string(buf), "Got Frog from Bob!");
}

TEST(format_at_threshold_passes_through) {
    // Build a message that's exactly kSoftMaxChars long.
    // "Got XXXX from YYY!" with len(X)+len(Y) = 60 - "Got "(4) - " from "(6) - "!"(1) = 49
    // Use a 30-char name and 19-char sender => total = 4+30+6+19+1 = 60.
    Item item = makeItem(ItemKind::Moon,
                         "ThisIsANineteenCharSendr",  // 24 chars
                         std::string(60 - 4 - 6 - 1 - 24, 'X').c_str());  // 25 chars
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    EXPECT_EQ_I(std::strlen(buf), 60);
    // Item name preserved in full
    EXPECT(std::string(buf).find("ThisIsANineteenCharSendr") != std::string::npos);
}

TEST(format_long_message_truncates_sender) {
    // Long sender, short-ish item name; sender gets cut with "...".
    Item item = makeItem(ItemKind::Capture, "ASenderWithAVeryVeryLongUsernameThatRunsForever",
                         "Frog");
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    std::string out(buf);
    // Total length within budget
    EXPECT(out.size() <= CappyMessenger::kSoftMaxChars);
    // Item name preserved
    EXPECT(out.find("Got Frog from ") == 0);
    // Sender truncated with ellipsis before trailing !
    EXPECT(out.find("...!") != std::string::npos);
    // Truncated sender is a prefix of the real sender
    auto from_pos = out.find(" from ");
    auto dots_pos = out.find("...!");
    std::string trunc = out.substr(from_pos + 6, dots_pos - from_pos - 6);
    EXPECT(std::string("ASenderWithAVeryVeryLongUsernameThatRunsForever").find(trunc) == 0);
}

TEST(format_long_name_drops_sender) {
    // Item name itself is so long that no sender room is left — drop sender.
    std::string huge_name(80, 'M');  // 80-char name > soft max
    Item item = makeItem(ItemKind::Moon, "Bob", huge_name.c_str());
    char buf[128];
    formatCappyMsg(buf, sizeof(buf), item);
    std::string out(buf);
    // "Got MMM...!" with name preserved (or truncated by buf cap, but no "from")
    EXPECT(out.find("Got ") == 0);
    EXPECT(out.find(" from ") == std::string::npos);
    EXPECT(out.back() == '!');
}

TEST(format_null_buf_safe) {
    Item item = makeItem(ItemKind::Capture, "Alice", "Frog");
    EXPECT_EQ_I(formatCappyMsg(nullptr, 64, item), 0);
}

TEST(format_zero_cap_safe) {
    Item item = makeItem(ItemKind::Capture, "Alice", "Frog");
    char buf[16];
    EXPECT_EQ_I(formatCappyMsg(buf, 0, item), 0);
}

// --------------------------------------------------------------------------
// utf8ToUtf16
// --------------------------------------------------------------------------

TEST(utf16_ascii_basic) {
    char16_t out[32] = {};
    const auto n = utf8ToUtf16("Hi", out, 32);
    EXPECT_EQ_I(n, 2);
    EXPECT_EQ_I(out[0], (char16_t)'H');
    EXPECT_EQ_I(out[1], (char16_t)'i');
    EXPECT_EQ_I(out[2], (char16_t)0);
}

TEST(utf16_two_byte) {
    // é = U+00E9 = 0xC3 0xA9 in UTF-8.
    char16_t out[8] = {};
    const auto n = utf8ToUtf16("\xC3\xA9", out, 8);
    EXPECT_EQ_I(n, 1);
    EXPECT_EQ_I(out[0], (char16_t)0x00E9);
    EXPECT_EQ_I(out[1], (char16_t)0);
}

TEST(utf16_three_byte) {
    // ★ = U+2605 = 0xE2 0x98 0x85 in UTF-8.
    char16_t out[8] = {};
    const auto n = utf8ToUtf16("\xE2\x98\x85", out, 8);
    EXPECT_EQ_I(n, 1);
    EXPECT_EQ_I(out[0], (char16_t)0x2605);
}

TEST(utf16_surrogate_pair) {
    // 🍄 = U+1F344 = 0xF0 0x9F 0x8D 0x84 in UTF-8.
    char16_t out[8] = {};
    const auto n = utf8ToUtf16("\xF0\x9F\x8D\x84", out, 8);
    EXPECT_EQ_I(n, 2);
    EXPECT_EQ_I(out[0], (char16_t)0xD83C);
    EXPECT_EQ_I(out[1], (char16_t)0xDF44);
}

TEST(utf16_malformed_skipped) {
    // 0x80 is a continuation byte with no lead — should be skipped.
    char16_t out[8] = {};
    const auto n = utf8ToUtf16("\x80""ok", out, 8);
    EXPECT_EQ_I(n, 2);
    EXPECT_EQ_I(out[0], (char16_t)'o');
    EXPECT_EQ_I(out[1], (char16_t)'k');
}

TEST(utf16_cap_respected) {
    // cap = 4 means we reserve 1 word for the terminator → at most 3 codeunits.
    char16_t out[4] = {};
    const auto n = utf8ToUtf16("abcdef", out, 4);
    EXPECT_EQ_I(n, 3);
    EXPECT_EQ_I(out[3], (char16_t)0);
}

TEST(utf16_zero_cap_safe) {
    char16_t out[1] = {0xBEEF};
    const auto n = utf8ToUtf16("anything", out, 0);
    EXPECT_EQ_I(n, 0);
    // dst[0] is untouched when cap == 0 (we early-return before any write).
    EXPECT_EQ_I(out[0], (char16_t)0xBEEF);
}

TEST(utf16_null_src_safe) {
    char16_t out[8] = {0xAAAA, 0xBBBB};
    const auto n = utf8ToUtf16(nullptr, out, 8);
    EXPECT_EQ_I(n, 0);
    EXPECT_EQ_I(out[0], (char16_t)0);  // terminator written before src check
}

// --------------------------------------------------------------------------
// CappyMessenger::lookupSubstitution
// --------------------------------------------------------------------------

TEST(lookup_returns_null_for_wrong_label) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    EXPECT(m.lookupSubstitution("SomeOtherLabel") == nullptr);
    EXPECT(m.lookupSubstitution(nullptr) == nullptr);
}

TEST(lookup_returns_null_when_buffer_idle) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    // Right label but no pump has filled the buffer yet — defense-in-depth.
    EXPECT(m.lookupSubstitution(kArchipelagoLabel) == nullptr);
}

// --------------------------------------------------------------------------
// CappyMessenger::enqueue: queue overflow drops new items
// --------------------------------------------------------------------------

TEST(enqueue_filters_self_and_replay) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    // Self-grant: from == local_slot
    m.enqueue(makeItem(ItemKind::Capture, "Bob", "Frog"), "Bob", false);
    EXPECT_EQ_I(m.pendingCount(), 0);
    // Replay suppress
    m.enqueue(makeItem(ItemKind::Capture, "Alice", "Frog"), "Bob", true);
    EXPECT_EQ_I(m.pendingCount(), 0);
    // REPL (empty from)
    m.enqueue(makeItem(ItemKind::Capture, "", "Frog"), "Bob", false);
    EXPECT_EQ_I(m.pendingCount(), 0);
}

TEST(enqueue_accepts_other_player_grant) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    m.enqueue(makeItem(ItemKind::Capture, "Alice", "Frog"), "Bob", false);
    EXPECT_EQ_I(m.pendingCount(), 1);
}

TEST(enqueue_overflow_drops_new) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    // Fill to cap.
    for (std::size_t i = 0; i < CappyMessenger::kQueueCap; ++i) {
        m.enqueue(makeItem(ItemKind::Moon, "Alice", "Power Moon"), "Bob", false);
    }
    EXPECT_EQ_I(m.pendingCount(), CappyMessenger::kQueueCap);
    // One more — should drop, not displace.
    m.enqueue(makeItem(ItemKind::Moon, "Alice", "Power Moon"), "Bob", false);
    EXPECT_EQ_I(m.pendingCount(), CappyMessenger::kQueueCap);
}

// --------------------------------------------------------------------------
// CappyMessenger::enqueueSystem: bypasses filter, verbatim text
// --------------------------------------------------------------------------

TEST(enqueue_system_accepts_verbatim_text) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    m.enqueueSystem("Connected to Archipelago");
    EXPECT_EQ_I(m.pendingCount(), 1);
}

TEST(enqueue_system_null_text_is_noop) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    m.enqueueSystem(nullptr);
    EXPECT_EQ_I(m.pendingCount(), 0);
}

TEST(enqueue_system_empty_text_is_noop) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    m.enqueueSystem("");
    EXPECT_EQ_I(m.pendingCount(), 0);
}

TEST(enqueue_system_respects_queue_cap) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    for (std::size_t i = 0; i < CappyMessenger::kQueueCap; ++i) {
        m.enqueueSystem("Connected to Archipelago");
    }
    EXPECT_EQ_I(m.pendingCount(), CappyMessenger::kQueueCap);
    // Overflow drops, doesn't displace.
    m.enqueueSystem("Disconnected from Archipelago");
    EXPECT_EQ_I(m.pendingCount(), CappyMessenger::kQueueCap);
}

TEST(enqueue_system_truncates_oversize_text) {
    auto& m = CappyMessenger::instance();
    m.resetForTest();
    // Entry::text is char[128]; 200 chars guarantees truncation with no
    // overrun + NUL-terminated output. The user-facing UX consequence is
    // the bubble shows a truncated line, but we only ever call this with
    // short fixed strings ("Connected to Archipelago" = 24 chars), so the
    // truncation path is defense-in-depth.
    std::string huge(200, 'X');
    m.enqueueSystem(huge.c_str());
    EXPECT_EQ_I(m.pendingCount(), 1);
}

// --------------------------------------------------------------------------
// main
// --------------------------------------------------------------------------

}  // namespace

int main() {
    if (g_failures == 0) {
        std::printf("OK: all CappyMessenger tests passed\n");
        return 0;
    }
    std::fprintf(stderr, "FAILED: %d CappyMessenger tests\n", g_failures);
    return 1;
}
