// Host-compiler tests for smoap::ap::ApProtocol (encoders + decoder).
//
// Build (any host compiler):
//   g++ -std=c++20 -Wall -Wextra -O0 -g
//       switch-mod/tests/test_protocol.cpp
//       switch-mod/src/ap/ApProtocol.cpp
//       switch-mod/src/util/Json.cpp
//       -Iswitch-mod/src -o test_protocol
//   ./test_protocol
//
// Mirrors bridge/tests/test_protocol.py — the same wire-format messages
// must round-trip on both sides.

#include "ap/ApProtocol.hpp"
#include "util/Json.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <string_view>

using namespace smoap::ap;

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
    auto _a = (actual);                                                         \
    auto _e = (expected);                                                       \
    if (_a != _e) {                                                             \
        std::fprintf(stderr, "[%s] FAIL %s:%d: %lld != %lld\n",                 \
                     g_current_test, __FILE__, __LINE__,                        \
                     (long long)_a, (long long)_e);                             \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define TEST(name) static void name();                                          \
    struct name##_runner { name##_runner() { g_current_test = #name; name(); } } name##_instance; \
    static void name()

// Decode a wire string (must end in '\n'). Returns true on success.
// Note: decoder needs a writable buffer (Reader decodes escapes in place).
bool decodeFrom(std::string s, DecodedMsg& out) {
    // Strip trailing newline since decoder doesn't need it.
    if (!s.empty() && s.back() == '\n') s.pop_back();
    return decode(s.data(), s.size(), out);
}

// Encoders write into a caller-owned LineBuffer (no heap touch) and return
// void. Tests want the bytes back as a std::string so they can compare. This
// helper bridges the two — `f` calls one of the encoders against `buf`.
template <typename F>
std::string wire(F&& f) {
    smoap::util::json::LineBuffer buf;
    f(buf);
    return std::string(buf.data(), buf.size());
}

// --------------------------------------------------------------------------
// ItemKind <-> wire
// --------------------------------------------------------------------------

TEST(itemkind_to_wire) {
    EXPECT_EQ_S(toWire(ItemKind::Moon),    "moon");
    EXPECT_EQ_S(toWire(ItemKind::Capture), "capture");
    EXPECT_EQ_S(toWire(ItemKind::Other),   "other");
}

TEST(itemkind_from_wire) {
    EXPECT(fromWire("moon")    == ItemKind::Moon);
    EXPECT(fromWire("capture") == ItemKind::Capture);
    EXPECT(fromWire("other")   == ItemKind::Other);
    EXPECT(fromWire("garbage") == ItemKind::Other);
    EXPECT(fromWire("")        == ItemKind::Other);
    EXPECT(fromWire("shop")    == ItemKind::Other);   // retired kind
    EXPECT(fromWire("kingdom") == ItemKind::Other);   // retired kind
}

// --------------------------------------------------------------------------
// Encoders (Switch -> Bridge): produce expected wire format
// --------------------------------------------------------------------------

TEST(encode_hello) {
    Hello h{.mod_ver="0.1.0+abc1234", .smo_ver="1.3.0", .cap_table_hash="sha1:deadbeef"};
    EXPECT_EQ_S(wire([&](auto& b){ encodeHello(b, h); }),
        R"({"t":"hello","mod_ver":"0.1.0+abc1234","smo_ver":"1.3.0","cap_table_hash":"sha1:deadbeef"})" "\n");
}

TEST(encode_check_moon) {
    Check c{.kind=ItemKind::Moon, .kingdom="Cascade", .shine_id="Our First Power Moon"};
    EXPECT_EQ_S(wire([&](auto& b){ encodeCheck(b, c); }),
        R"({"t":"check","kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"})" "\n");
}

TEST(encode_check_capture) {
    Check c{.kind=ItemKind::Capture, .cap="Goomba"};
    EXPECT_EQ_S(wire([&](auto& b){ encodeCheck(b, c); }),
        R"({"t":"check","kind":"capture","cap":"Goomba"})" "\n");
}


TEST(encode_check_skips_empty_fields) {
    Check c{.kind=ItemKind::Other};
    EXPECT_EQ_S(wire([&](auto& b){ encodeCheck(b, c); }), R"({"t":"check","kind":"other"})" "\n");
}

TEST(encode_status_full) {
    Status s{.kingdom="Metro", .scenario=2, .moons_collected=47};
    EXPECT_EQ_S(wire([&](auto& b){ encodeStatus(b, s); }),
        R"({"t":"status","kingdom":"Metro","scenario":2,"moons_collected":47})" "\n");
}

TEST(encode_status_empty_skips_fields) {
    Status s{};
    EXPECT_EQ_S(wire([&](auto& b){ encodeStatus(b, s); }), R"({"t":"status"})" "\n");
}

TEST(encode_goal) {
    EXPECT_EQ_S(wire([&](auto& b){ encodeGoal(b); }), R"({"t":"goal"})" "\n");
}

TEST(encode_ping) {
    Ping p{.ts_ms=1731536400000LL};
    EXPECT_EQ_S(wire([&](auto& b){ encodePing(b, p); }),
        R"({"t":"ping","ts_ms":1731536400000})" "\n");
}

TEST(encode_log) {
    Log lg{};
    copyFixedField(lg.level, "info");
    copyFixedField(lg.msg,   "hook installed for ShineGet at 0x...");
    EXPECT_EQ_S(wire([&](auto& b){ encodeLog(b, lg); }),
        R"({"t":"log","level":"info","msg":"hook installed for ShineGet at 0x..."})" "\n");
}

TEST(encode_log_all_levels) {
    const char* levels[] = {"debug", "info", "warn", "error"};
    for (const char* lvl : levels) {
        Log lg{};
        copyFixedField(lg.level, lvl);
        copyFixedField(lg.msg,   "x");
        const std::string out = wire([&](auto& b){ encodeLog(b, lg); });
        const std::string expected = std::string(R"({"t":"log","level":")") + lvl +
                                     R"(","msg":"x"})" "\n";
        EXPECT_EQ_S(out, expected);
    }
}

TEST(encode_log_msg_truncates_at_cap) {
    // Source longer than kLogMsgCap should be truncated to kLogMsgCap - 1
    // chars (leaving room for the null terminator) by copyFixedField.
    std::string src(kLogMsgCap + 50, 'A');  // 306 'A's
    Log lg{};
    copyFixedField(lg.level, "warn");
    copyFixedField(lg.msg, src.c_str());
    EXPECT_EQ_I(std::strlen(lg.msg), kLogMsgCap - 1);
    const std::string out = wire([&](auto& b){ encodeLog(b, lg); });
    const std::string expected_msg(kLogMsgCap - 1, 'A');
    const std::string expected = std::string(R"({"t":"log","level":"warn","msg":")") +
                                 expected_msg + R"("})" "\n";
    EXPECT_EQ_S(out, expected);
}

TEST(encode_log_level_truncates_at_cap) {
    // Levels are short ("debug" is 5 chars + null = 6, fits in
    // kLogLevelCap=8). Validate truncation contract for the buffer all the
    // same — a forwarder bug elsewhere shouldn't crash the encoder.
    Log lg{};
    copyFixedField(lg.level, "criticalalert");  // 13 chars, exceeds 8
    copyFixedField(lg.msg,   "x");
    EXPECT_EQ_I(std::strlen(lg.level), kLogLevelCap - 1);  // truncated to 7
    EXPECT_EQ_S(std::string(lg.level), "critica");
}

// --------------------------------------------------------------------------
// State snapshot encoders (M4.5)
// --------------------------------------------------------------------------

TEST(encode_state_begin_with_save_slot) {
    StateBegin b{.mod_ver = "0.1.0", .save_slot = 0};
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateBegin(buf, b); }),
        R"({"t":"state_begin","mod_ver":"0.1.0","save_slot":0})" "\n");
}

TEST(encode_state_begin_omits_save_slot_when_negative) {
    StateBegin b{.mod_ver = "0.1.0", .save_slot = -1};
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateBegin(buf, b); }),
        R"({"t":"state_begin","mod_ver":"0.1.0"})" "\n");
}

// Small helper: copy a literal into a StateChunk's stage_name without
// requiring the test to remember the M6-phase-C buffer cap.
inline void setStage(StateChunk& c, const char* s) {
    copyFixedField(c.stage_name, s);
}

// Append a shine entry into a StateChunk using the fixed-buffer API.
// Mirrors the runtime SnapshotBuilder::addShine path (sans the per-stage flush
// + Send), keeping test setup symmetrical with production code.
inline void addShine(StateChunk& c, const char* obj, int uid) {
    if (c.shine_count >= static_cast<int>(kSnapshotMaxShinesPerStage)) return;
    ShineEntry& s = c.shines[c.shine_count++];
    copyFixedField(s.object_id, obj);
    s.shine_uid = uid;
}

inline void addCapture(StateChunk& c, const char* hack) {
    if (c.capture_count >= static_cast<int>(kSnapshotMaxCaptures)) return;
    copyFixedField(c.captures[c.capture_count++], hack);
}

TEST(encode_state_chunk_per_stage) {
    StateChunk c;
    setStage(c, "CapWorldHomeStage");
    addShine(c, "MoonOurFirst", 100);
    addShine(c, "MoonHatTrampoline", 101);
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateChunk(buf, c); }),
        R"({"t":"state_chunk","stage_name":"CapWorldHomeStage",)"
        R"("shines":[{"object_id":"MoonOurFirst","shine_uid":100},)"
        R"({"object_id":"MoonHatTrampoline","shine_uid":101}]})" "\n");
}

TEST(encode_state_chunk_meta_carries_captures_and_goal) {
    StateChunk c;
    setStage(c, "_meta");
    addCapture(c, "Kuribo");
    addCapture(c, "Frog");
    c.include_goal_reached = true;
    c.goal_reached = false;
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateChunk(buf, c); }),
        R"({"t":"state_chunk","stage_name":"_meta",)"
        R"("captures":["Kuribo","Frog"],"goal_reached":false})" "\n");
}

TEST(encode_state_chunk_skips_empty_arrays) {
    StateChunk c;
    setStage(c, "_meta");
    // No captures, no goal_reached_included.
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateChunk(buf, c); }),
        R"({"t":"state_chunk","stage_name":"_meta"})" "\n");
}

TEST(encode_state_end) {
    EXPECT_EQ_S(wire([&](auto& b){ encodeStateEnd(b); }), R"({"t":"state_end"})" "\n");
}

// Regression: Encoder must emit commas between successive nested objects in
// an array. Pre-fix, "[{...}{...}" would slip through.
TEST(encode_state_chunk_multi_shine_has_comma_between_objects) {
    StateChunk c;
    setStage(c, "X");
    addShine(c, "A", 1);
    addShine(c, "B", 2);
    addShine(c, "C", 3);
    const std::string w = wire([&](auto& buf){ encodeStateChunk(buf, c); });
    // Spot-check: must contain "},{" as the separator between adjacent objects.
    EXPECT(w.find("},{") != std::string::npos);
    // And the array must close properly.
    EXPECT(w.find("}]}") != std::string::npos);
}

// M6 phase C: long stage_name (exceeds SSO=15) must encode losslessly under
// the fixed-buffer regime. WaterfallWorldHomeStage = 23 chars is the exact
// scenario that would have crashed the worker thread pre-hardening.
TEST(encode_state_chunk_long_stage_name) {
    StateChunk c;
    setStage(c, "WaterfallWorldHomeStage");
    addShine(c, "obj214", 1);
    EXPECT_EQ_S(wire([&](auto& buf){ encodeStateChunk(buf, c); }),
        R"({"t":"state_chunk","stage_name":"WaterfallWorldHomeStage",)"
        R"("shines":[{"object_id":"obj214","shine_uid":1}]})" "\n");
}

// M6 phase C: at-capacity shines array must encode every entry — no buffer
// overrun, no off-by-one truncation.
TEST(encode_state_chunk_shines_at_capacity) {
    StateChunk c;
    setStage(c, "Bulk");
    for (int i = 0; i < static_cast<int>(kSnapshotMaxShinesPerStage); ++i) {
        char obj[16];
        std::snprintf(obj, sizeof(obj), "o%d", i);
        addShine(c, obj, i);
    }
    EXPECT_EQ_I(c.shine_count, static_cast<int>(kSnapshotMaxShinesPerStage));
    const std::string w = wire([&](auto& buf){ encodeStateChunk(buf, c); });
    // First and last entries both present in the output.
    EXPECT(w.find(R"({"object_id":"o0","shine_uid":0})") != std::string::npos);
    char last[64];
    std::snprintf(last, sizeof(last), R"({"object_id":"o%zu","shine_uid":%zu})",
                  kSnapshotMaxShinesPerStage - 1, kSnapshotMaxShinesPerStage - 1);
    EXPECT(w.find(last) != std::string::npos);
}

// M6 phase C: SnapshotBuilder's overflow guard maps to the helper's bounds
// check — extra addShine calls beyond capacity must silently no-op so the
// runtime path can log+drop without corrupting the buffer.
TEST(state_chunk_addshine_helper_clamps_at_capacity) {
    StateChunk c;
    setStage(c, "Bulk");
    for (int i = 0; i < static_cast<int>(kSnapshotMaxShinesPerStage) + 5; ++i) {
        addShine(c, "x", i);
    }
    EXPECT_EQ_I(c.shine_count, static_cast<int>(kSnapshotMaxShinesPerStage));
}

// M6 phase C: stage_name overrun truncates rather than overflowing. Bridge
// would log a "no shine_map entry" warning on a truncated key; the local
// contract here is just "encode + NUL-terminate within the buffer".
TEST(encode_state_chunk_stage_name_truncates_at_cap) {
    StateChunk c;
    std::string huge(kCheckFieldCap + 20, 'S');  // 84 'S's
    copyFixedField(c.stage_name, huge.c_str());
    EXPECT_EQ_I(std::strlen(c.stage_name), kCheckFieldCap - 1);
    // Encoder should still produce well-formed JSON — no array sections.
    const std::string w = wire([&](auto& buf){ encodeStateChunk(buf, c); });
    EXPECT(w.find(R"("t":"state_chunk")") != std::string::npos);
    EXPECT(w.find(R"("stage_name":")") != std::string::npos);
    EXPECT(w.back() == '\n');
}

// --------------------------------------------------------------------------
// Decoder (Bridge -> Switch)
// --------------------------------------------------------------------------

TEST(decode_hello_ack) {
    DecodedMsg m;
    EXPECT(decodeFrom(
        R"({"t":"hello_ack","ok":true,"seed":"X4F2","slot":"Mario","cap_table_hash":"sha1:abc"})",
        m));
    EXPECT_EQ_S(m.t, "hello_ack");
    EXPECT(m.hello_ack.ok == true);
    EXPECT_EQ_S(m.hello_ack.seed, "X4F2");
    EXPECT_EQ_S(m.hello_ack.slot, "Mario");
    EXPECT_EQ_S(m.hello_ack.cap_table_hash, "sha1:abc");
    EXPECT_EQ_S(m.hello_ack.err, "");
}

TEST(decode_hello_ack_with_err) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"hello_ack","ok":false,"err":"bad slot"})", m));
    EXPECT(m.hello_ack.ok == false);
    EXPECT_EQ_S(m.hello_ack.err, "bad slot");
}

TEST(decode_checked_replay_truncates_past_cap) {
    // Synthesize a checked_replay with kMaxIds + 4 entries; the decoder must
    // fill the buffer to capacity, set truncated=true, and still successfully
    // close out the rest of the JSON without overrunning the fixed array.
    std::string body = R"({"t":"checked_replay","ids":[)";
    constexpr std::size_t kOver = CheckedReplay::kMaxIds + 4;
    for (std::size_t i = 0; i < kOver; ++i) {
        if (i > 0) body += ',';
        body += R"({"kind":"moon","kingdom":"Cascade","shine_id":"Moon )";
        body += std::to_string(i);
        body += R"("})";
    }
    body += "]}";
    DecodedMsg m;
    EXPECT(decodeFrom(body, m));
    EXPECT_EQ_I(m.checked_replay.id_count, CheckedReplay::kMaxIds);
    EXPECT(m.checked_replay.truncated);
    // First-and-last sanity: first slot is "Moon 0", last filled is
    // "Moon {kMaxIds-1}". The 4 overflow entries are consumed and dropped.
    EXPECT_EQ_S(m.checked_replay.ids[0].shine_id, "Moon 0");
    char last[32];
    std::snprintf(last, sizeof(last), "Moon %zu", CheckedReplay::kMaxIds - 1);
    EXPECT_EQ_S(m.checked_replay.ids[CheckedReplay::kMaxIds - 1].shine_id, last);
}

TEST(decode_field_overlong_string_truncates) {
    // ItemRef.shine_id is char[kMediumFieldCap=128]. Feed a 200-char value;
    // copyFixedFieldN must truncate to 127 chars + NUL, no overrun.
    std::string huge(200, 'x');
    std::string body = R"({"t":"item","kind":"moon","shine_id":")" + huge + R"("})";
    DecodedMsg m;
    EXPECT(decodeFrom(body, m));
    // shine_id is char[128], so the visible length is 127 chars.
    EXPECT_EQ_I(std::strlen(m.item.shine_id), 127u);
    // Buffer must be NUL-terminated at exactly position 127.
    EXPECT(m.item.shine_id[127] == '\0');
}

TEST(decode_checked_replay_two_entries) {
    DecodedMsg m;
    EXPECT(decodeFrom(
        R"({"t":"checked_replay","ids":[)"
        R"({"kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"},)"
        R"({"kind":"capture","cap":"Frog"}]})",
        m));
    EXPECT_EQ_S(m.t, "checked_replay");
    EXPECT_EQ_I(m.checked_replay.id_count, 2u);
    EXPECT(!m.checked_replay.truncated);
    EXPECT(m.checked_replay.ids[0].kind == ItemKind::Moon);
    EXPECT_EQ_S(m.checked_replay.ids[0].kingdom, "Cascade");
    EXPECT_EQ_S(m.checked_replay.ids[0].shine_id, "Our First Power Moon");
    EXPECT(m.checked_replay.ids[1].kind == ItemKind::Capture);
    EXPECT_EQ_S(m.checked_replay.ids[1].cap, "Frog");
}

TEST(decode_checked_replay_empty) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"checked_replay","ids":[]})", m));
    EXPECT_EQ_I(m.checked_replay.id_count, 0u);
}

TEST(decode_item_moon) {
    DecodedMsg m;
    EXPECT(decodeFrom(
        R"({"t":"item","kind":"moon","kingdom":"Sand","shine_id":"PoolUnderwater","from":"Bob"})",
        m));
    EXPECT_EQ_S(m.t, "item");
    EXPECT(m.item.kind == ItemKind::Moon);
    EXPECT_EQ_S(m.item.kingdom, "Sand");
    EXPECT_EQ_S(m.item.shine_id, "PoolUnderwater");
    EXPECT_EQ_S(m.item.from, "Bob");
}

TEST(decode_item_capture_self) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"item","kind":"capture","cap":"Yoshi","from":"self"})", m));
    EXPECT(m.item.kind == ItemKind::Capture);
    EXPECT_EQ_S(m.item.cap, "Yoshi");
    EXPECT_EQ_S(m.item.from, "self");
}

TEST(decode_item_other_with_name) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"json({"t":"item","kind":"other","name":"Power Moon (Generic)","from":"Bob"})json", m));
    EXPECT(m.item.kind == ItemKind::Other);
    EXPECT_EQ_S(m.item.name, "Power Moon (Generic)");
}

TEST(decode_print) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"json({"t":"print","text":"Bob found Mario's Power Moon (Lake)"})json", m));
    EXPECT_EQ_S(m.t, "print");
    EXPECT_EQ_S(m.print.text, "Bob found Mario's Power Moon (Lake)");
}

TEST(decode_ap_state) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"ap_state","conn":"ready"})", m));
    EXPECT_EQ_S(m.t, "ap_state");
    EXPECT_EQ_S(m.ap_state.conn, "ready");
}

TEST(decode_pong) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"pong","ts_ms":1731536400000})", m));
    EXPECT_EQ_S(m.t, "pong");
    EXPECT_EQ_I(m.pong.ts_ms, 1731536400000LL);
}

TEST(decode_err) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"err","code":"unknown_kind","ctx":"check"})", m));
    EXPECT_EQ_S(m.err.code, "unknown_kind");
    EXPECT_EQ_S(m.err.ctx, "check");
}

TEST(decode_unknown_type_returns_true_with_t) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"future_type","x":1,"y":"z"})", m));
    EXPECT_EQ_S(m.t, "future_type");
    // Body fields were not parsed, but decoder returned true so caller can warn.
}

// --------------------------------------------------------------------------
// Decoder error cases
// --------------------------------------------------------------------------

TEST(decode_rejects_empty) {
    DecodedMsg m;
    EXPECT(!decodeFrom("", m));
}

TEST(decode_rejects_missing_t) {
    DecodedMsg m;
    EXPECT(!decodeFrom(R"({"x":1})", m));
}

TEST(decode_rejects_t_not_first) {
    DecodedMsg m;
    EXPECT(!decodeFrom(R"({"x":1,"t":"hello_ack"})", m));
}

TEST(decode_rejects_unknown_field_in_known_type) {
    DecodedMsg m;
    EXPECT(!decodeFrom(R"({"t":"hello_ack","ok":true,"bogus":"x"})", m));
}

TEST(decode_rejects_truncated) {
    DecodedMsg m;
    EXPECT(!decodeFrom(R"({"t":"hello_ack","ok":tr)", m));
}

// --------------------------------------------------------------------------
// Round-trip: encoded by us, decoded by us (sanity check the wire format
// is internally consistent).
//
// We don't have a Switch-side decoder for the Switch->Bridge messages
// (those go to the bridge, which has its own decoder). But we can at least
// verify our encoders produce JSON the Reader can re-parse.
// --------------------------------------------------------------------------

// --------------------------------------------------------------------------
// M6 phase D — deposit + outstanding wire messages
// --------------------------------------------------------------------------

TEST(encode_deposit_basic) {
    Deposit d{};
    d.seq = 7;
    copyCheckField(d.kingdom, "Wooded");
    d.amount = 1;
    EXPECT_EQ_S(wire([&](auto& b){ encodeDeposit(b, d); }),
        R"({"t":"deposit","seq":7,"kingdom":"Wooded","amount":1})" "\n");
}

TEST(encode_deposit_multi_moon_amount_three) {
    Deposit d{};
    d.seq = 42;
    copyCheckField(d.kingdom, "Cap");
    d.amount = 3;
    EXPECT_EQ_S(wire([&](auto& b){ encodeDeposit(b, d); }),
        R"({"t":"deposit","seq":42,"kingdom":"Cap","amount":3})" "\n");
}

TEST(decode_deposit_ack) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"deposit_ack","seq":7})", m));
    EXPECT_EQ_S(m.t, "deposit_ack");
    EXPECT_EQ_I(m.deposit_ack.seq, 7u);
}

TEST(decode_deposit_ack_zero) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"deposit_ack","seq":0})", m));
    EXPECT_EQ_I(m.deposit_ack.seq, 0u);
}

TEST(decode_outstanding_empty) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"outstanding","entries":[]})", m));
    EXPECT_EQ_S(m.t, "outstanding");
    EXPECT_EQ_I(m.outstanding.entry_count, 0u);
}

TEST(decode_outstanding_multiple_kingdoms) {
    DecodedMsg m;
    EXPECT(decodeFrom(
        R"({"t":"outstanding","entries":[)"
        R"({"kingdom":"Cap","count":2},)"
        R"({"kingdom":"Cascade","count":5},)"
        R"({"kingdom":"Wooded","count":0}]})",
        m));
    EXPECT_EQ_I(m.outstanding.entry_count, 3u);
    EXPECT_EQ_S(m.outstanding.entries[0].kingdom, "Cap");
    EXPECT_EQ_I(m.outstanding.entries[0].count, 2);
    EXPECT_EQ_S(m.outstanding.entries[1].kingdom, "Cascade");
    EXPECT_EQ_I(m.outstanding.entries[1].count, 5);
    EXPECT_EQ_S(m.outstanding.entries[2].kingdom, "Wooded");
    EXPECT_EQ_I(m.outstanding.entries[2].count, 0);
}

TEST(decode_outstanding_caps_at_max_entries) {
    // Build a synthetic message with kMaxEntries + 2 entries; decoder must
    // accept up to the cap and silently drop the rest (no error, partial OK).
    std::string body = R"({"t":"outstanding","entries":[)";
    constexpr std::size_t kOver = Outstanding::kMaxEntries + 2;
    for (std::size_t i = 0; i < kOver; ++i) {
        if (i > 0) body += ',';
        body += R"({"kingdom":"K)";
        body += std::to_string(i);
        body += R"(","count":)";
        body += std::to_string(static_cast<int>(i) + 1);
        body += '}';
    }
    body += "]}";
    DecodedMsg m;
    EXPECT(decodeFrom(body, m));
    EXPECT_EQ_I(m.outstanding.entry_count, Outstanding::kMaxEntries);
    EXPECT_EQ_S(m.outstanding.entries[0].kingdom, "K0");
    EXPECT_EQ_I(m.outstanding.entries[0].count, 1);
}

TEST(roundtrip_deposit_via_reader) {
    Deposit d{};
    d.seq = 99;
    copyCheckField(d.kingdom, "Snow");
    d.amount = 2;
    std::string w = wire([&](auto& b){ encodeDeposit(b, d); });
    if (!w.empty() && w.back() == '\n') w.pop_back();
    smoap::util::json::Reader r(w.data(), w.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    std::int64_t iv;
    EXPECT(r.nextField(k)); EXPECT(k == "t");       EXPECT(r.nextString(v)); EXPECT(v == "deposit");
    EXPECT(r.nextField(k)); EXPECT(k == "seq");     EXPECT(r.nextInt(iv));   EXPECT(iv == 99);
    EXPECT(r.nextField(k)); EXPECT(k == "kingdom"); EXPECT(r.nextString(v)); EXPECT(v == "Snow");
    EXPECT(r.nextField(k)); EXPECT(k == "amount");  EXPECT(r.nextInt(iv));   EXPECT(iv == 2);
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(roundtrip_check_via_reader) {
    Check c{.kind=ItemKind::Moon, .kingdom="Cap", .shine_id="Spinning-Hat Stack"};
    std::string w = wire([&](auto& b){ encodeCheck(b, c); });
    // Strip newline.
    if (!w.empty() && w.back() == '\n') w.pop_back();
    smoap::util::json::Reader r(w.data(), w.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT(k == "t");        EXPECT(r.nextString(v)); EXPECT(v == "check");
    EXPECT(r.nextField(k)); EXPECT(k == "kind");     EXPECT(r.nextString(v)); EXPECT(v == "moon");
    EXPECT(r.nextField(k)); EXPECT(k == "kingdom");  EXPECT(r.nextString(v)); EXPECT(v == "Cap");
    EXPECT(r.nextField(k)); EXPECT(k == "shine_id"); EXPECT(r.nextString(v)); EXPECT(v == "Spinning-Hat Stack");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

}  // namespace

int main() {
    if (g_failures != 0) {
        std::fprintf(stderr, "\n%d failure(s)\n", g_failures);
        return 1;
    }
    std::fprintf(stdout, "All tests passed.\n");
    return 0;
}
