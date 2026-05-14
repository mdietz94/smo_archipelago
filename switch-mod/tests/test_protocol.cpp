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

// --------------------------------------------------------------------------
// ItemKind <-> wire
// --------------------------------------------------------------------------

TEST(itemkind_to_wire) {
    EXPECT_EQ_S(toWire(ItemKind::Moon),    "moon");
    EXPECT_EQ_S(toWire(ItemKind::Capture), "capture");
    EXPECT_EQ_S(toWire(ItemKind::Kingdom), "kingdom");
    EXPECT_EQ_S(toWire(ItemKind::Shop),    "shop");
    EXPECT_EQ_S(toWire(ItemKind::Other),   "other");
}

TEST(itemkind_from_wire) {
    EXPECT(fromWire("moon")    == ItemKind::Moon);
    EXPECT(fromWire("capture") == ItemKind::Capture);
    EXPECT(fromWire("kingdom") == ItemKind::Kingdom);
    EXPECT(fromWire("shop")    == ItemKind::Shop);
    EXPECT(fromWire("other")   == ItemKind::Other);
    EXPECT(fromWire("garbage") == ItemKind::Other);
    EXPECT(fromWire("")        == ItemKind::Other);
}

// --------------------------------------------------------------------------
// Encoders (Switch -> Bridge): produce expected wire format
// --------------------------------------------------------------------------

TEST(encode_hello) {
    Hello h{.mod_ver="0.1.0+abc1234", .smo_ver="1.3.0", .cap_table_hash="sha1:deadbeef"};
    EXPECT_EQ_S(encodeHello(h),
        R"({"t":"hello","mod_ver":"0.1.0+abc1234","smo_ver":"1.3.0","cap_table_hash":"sha1:deadbeef"})" "\n");
}

TEST(encode_check_moon) {
    Check c{.kind=ItemKind::Moon, .kingdom="Cascade", .shine_id="Our First Power Moon"};
    EXPECT_EQ_S(encodeCheck(c),
        R"({"t":"check","kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"})" "\n");
}

TEST(encode_check_capture) {
    Check c{.kind=ItemKind::Capture, .cap="Goomba"};
    EXPECT_EQ_S(encodeCheck(c),
        R"({"t":"check","kind":"capture","cap":"Goomba"})" "\n");
}

TEST(encode_check_shop_with_slot) {
    Check c{.kind=ItemKind::Shop, .kingdom="Cap", .slot=3};
    EXPECT_EQ_S(encodeCheck(c),
        R"({"t":"check","kind":"shop","kingdom":"Cap","slot":3})" "\n");
}

TEST(encode_check_skips_empty_fields) {
    Check c{.kind=ItemKind::Other};
    EXPECT_EQ_S(encodeCheck(c), R"({"t":"check","kind":"other"})" "\n");
}

TEST(encode_status_full) {
    Status s{.kingdom="Metro", .scenario=2, .moons_collected=47};
    EXPECT_EQ_S(encodeStatus(s),
        R"({"t":"status","kingdom":"Metro","scenario":2,"moons_collected":47})" "\n");
}

TEST(encode_status_empty_skips_fields) {
    Status s{};
    EXPECT_EQ_S(encodeStatus(s), R"({"t":"status"})" "\n");
}

TEST(encode_goal) {
    EXPECT_EQ_S(encodeGoal(), R"({"t":"goal"})" "\n");
}

TEST(encode_ping) {
    Ping p{.ts_ms=1731536400000LL};
    EXPECT_EQ_S(encodePing(p),
        R"({"t":"ping","ts_ms":1731536400000})" "\n");
}

TEST(encode_log) {
    Log lg{.level="info", .msg="hook installed for ShineGet at 0x..."};
    EXPECT_EQ_S(encodeLog(lg),
        R"({"t":"log","level":"info","msg":"hook installed for ShineGet at 0x..."})" "\n");
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

TEST(decode_checked_replay_two_entries) {
    DecodedMsg m;
    EXPECT(decodeFrom(
        R"({"t":"checked_replay","ids":[)"
        R"({"kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"},)"
        R"({"kind":"capture","cap":"Frog"}]})",
        m));
    EXPECT_EQ_S(m.t, "checked_replay");
    EXPECT_EQ_I(m.checked_replay.ids.size(), 2u);
    EXPECT(m.checked_replay.ids[0].kind == ItemKind::Moon);
    EXPECT_EQ_S(m.checked_replay.ids[0].kingdom, "Cascade");
    EXPECT_EQ_S(m.checked_replay.ids[0].shine_id, "Our First Power Moon");
    EXPECT(m.checked_replay.ids[1].kind == ItemKind::Capture);
    EXPECT_EQ_S(m.checked_replay.ids[1].cap, "Frog");
}

TEST(decode_checked_replay_empty) {
    DecodedMsg m;
    EXPECT(decodeFrom(R"({"t":"checked_replay","ids":[]})", m));
    EXPECT_EQ_I(m.checked_replay.ids.size(), 0u);
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
    EXPECT_EQ_I(m.item.slot, -1);  // absent -> sentinel
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

TEST(roundtrip_check_via_reader) {
    Check c{.kind=ItemKind::Moon, .kingdom="Cap", .shine_id="Spinning-Hat Stack"};
    std::string wire = encodeCheck(c);
    // Strip newline.
    if (!wire.empty() && wire.back() == '\n') wire.pop_back();
    smoap::util::json::Reader r(wire.data(), wire.size());
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
