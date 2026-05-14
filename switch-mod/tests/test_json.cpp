// Host-compiler tests for smoap::util::json::Reader.
//
// Build (any host compiler — no devkitPro):
//   g++ -std=c++20 -Wall -Wextra -O0 -g
//       switch-mod/tests/test_json.cpp switch-mod/src/util/Json.cpp
//       -Iswitch-mod/src -o test_json
//   ./test_json
//
// Exercises the AP wire-protocol message shapes from docs/wire-protocol.md.

#include "util/Json.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <string_view>

using smoap::util::json::Reader;

namespace {

int g_failures = 0;
const char* g_current_test = "";

#define EXPECT(cond) do {                                                       \
    if (!(cond)) {                                                              \
        std::fprintf(stderr, "[%s] FAIL %s:%d: %s\n",                            \
                     g_current_test, __FILE__, __LINE__, #cond);                 \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define EXPECT_EQ_SV(actual, expected) do {                                     \
    std::string_view _a = (actual);                                             \
    std::string_view _e = (expected);                                           \
    if (_a != _e) {                                                             \
        std::fprintf(stderr, "[%s] FAIL %s:%d: \"%.*s\" != \"%.*s\"\n",         \
                     g_current_test, __FILE__, __LINE__,                        \
                     (int)_a.size(), _a.data(), (int)_e.size(), _e.data());     \
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

// Helper: make a writable buffer from a literal (Reader decodes escapes in
// place, so the buffer must be mutable).
struct Buf {
    std::string s;
    explicit Buf(std::string_view lit) : s(lit) {}
    char* data() { return s.data(); }
    std::size_t size() const { return s.size(); }
};

#define TEST(name) static void name(); \
    struct name##_runner { name##_runner() { g_current_test = #name; name(); } } name##_instance; \
    static void name()

// --------------------------------------------------------------------------
// Switch -> Bridge messages
// --------------------------------------------------------------------------

TEST(hello) {
    Buf b(R"({"t":"hello","mod_ver":"0.1.0+abc1234","smo_ver":"1.3.0","cap_table_hash":"sha1:deadbeef"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");              EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "hello");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "mod_ver");        EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "0.1.0+abc1234");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "smo_ver");        EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "1.3.0");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "cap_table_hash"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "sha1:deadbeef");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(check_moon) {
    Buf b(R"({"t":"check","kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");        EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "check");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind");     EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "moon");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kingdom");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Cascade");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "shine_id"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Our First Power Moon");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(check_capture) {
    Buf b(R"({"t":"check","kind":"capture","cap":"Goomba"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");    EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "check");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "capture");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "cap");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Goomba");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(check_shop_slot) {
    Buf b(R"({"t":"check","kind":"shop","kingdom":"Cap","slot":3})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");       EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "check");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind");    EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "shop");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kingdom"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Cap");
    std::int64_t slot = 0;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "slot");    EXPECT(r.nextInt(slot)); EXPECT_EQ_I(slot, 3);
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(status) {
    Buf b(R"({"t":"status","kingdom":"Metro","scenario":2,"moons_collected":47})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");       EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "status");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kingdom"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Metro");
    std::int64_t n = 0;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "scenario");        EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 2);
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "moons_collected"); EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 47);
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(goal) {
    Buf b(R"({"t":"goal"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "goal");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(ping_large_ts) {
    Buf b(R"({"t":"ping","ts_ms":1731536400000})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "ping");
    std::int64_t ts = 0;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "ts_ms"); EXPECT(r.nextInt(ts)); EXPECT_EQ_I(ts, 1731536400000LL);
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(log_msg) {
    Buf b(R"({"t":"log","level":"info","msg":"hook installed for ShineGet at 0x..."})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");     EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "log");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "level"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "info");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "msg");   EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "hook installed for ShineGet at 0x...");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

// --------------------------------------------------------------------------
// Bridge -> Switch messages
// --------------------------------------------------------------------------

TEST(hello_ack) {
    Buf b(R"({"t":"hello_ack","ok":true,"seed":"X4F2","slot":"Mario","cap_table_hash":"sha1:abc"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "hello_ack");
    bool ok = false;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "ok");   EXPECT(r.nextBool(ok)); EXPECT(ok == true);
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "seed"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "X4F2");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "slot"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Mario");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "cap_table_hash"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "sha1:abc");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(checked_replay) {
    Buf b(R"({"t":"checked_replay","ids":[{"kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"},{"kind":"capture","cap":"Frog"}]})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "checked_replay");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "ids");
    EXPECT(r.enterArray());
    // First entry
    EXPECT(r.hasMoreInArray());
    EXPECT(r.enterObject());
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind");     EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "moon");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kingdom");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Cascade");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "shine_id"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Our First Power Moon");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
    // Second entry
    EXPECT(r.hasMoreInArray());
    EXPECT(r.enterObject());
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "capture");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "cap");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Frog");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
    EXPECT(!r.hasMoreInArray());
    EXPECT(r.exitArray());
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(checked_replay_empty) {
    Buf b(R"({"t":"checked_replay","ids":[]})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "checked_replay");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "ids"); EXPECT(r.enterArray());
    EXPECT(!r.hasMoreInArray());
    EXPECT(r.exitArray());
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(item_moon) {
    Buf b(R"({"t":"item","kind":"moon","kingdom":"Sand","shine_id":"PoolUnderwater","from":"Bob"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");        EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "item");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kind");     EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "moon");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "kingdom");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Sand");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "shine_id"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "PoolUnderwater");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "from");     EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Bob");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(print_msg) {
    Buf b(R"json({"t":"print","text":"Bob found Mario's Power Moon (Lake)"})json");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");    EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "print");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "text"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "Bob found Mario's Power Moon (Lake)");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(ap_state_msg) {
    Buf b(R"({"t":"ap_state","conn":"ready"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");    EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "ap_state");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "conn"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "ready");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(err_msg) {
    Buf b(R"({"t":"err","code":"unknown_kind","ctx":"check"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "t");    EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "err");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "code"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "unknown_kind");
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "ctx");  EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "check");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

// --------------------------------------------------------------------------
// Edge cases
// --------------------------------------------------------------------------

TEST(escape_sequences) {
    // Contains: quote, backslash, newline, tab, BMP unicode (é = U+00E9).
    Buf b(R"({"text":"a\"b\\c\nd\teéf"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "text");
    EXPECT(r.nextString(v));
    static const char kExpected[] = "a\"b\\c\nd\te\xC3\xA9" "f";
    EXPECT_EQ_SV(v, std::string_view(kExpected, sizeof(kExpected) - 1));
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(negative_int) {
    Buf b(R"({"v":-42})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k;
    EXPECT(r.nextField(k));
    std::int64_t n = 0;
    EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, -42);
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(bool_and_null) {
    Buf b(R"({"a":true,"b":false,"c":null,"d":"x"})");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k, v;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "a"); bool a = false; EXPECT(r.nextBool(a)); EXPECT(a);
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "b"); bool bv = true; EXPECT(r.nextBool(bv)); EXPECT(!bv);
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "c"); EXPECT(r.isNull());
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "d"); EXPECT(r.nextString(v)); EXPECT_EQ_SV(v, "x");
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(whitespace_tolerant) {
    Buf b(" {  \"a\" : 1 ,\n\t\"b\" : [ 1 , 2 , 3 ] } ");
    Reader r(b.data(), b.size());
    EXPECT(r.enterObject());
    std::string_view k;
    std::int64_t n = 0;
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "a"); EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 1);
    EXPECT(r.nextField(k)); EXPECT_EQ_SV(k, "b"); EXPECT(r.enterArray());
    EXPECT(r.hasMoreInArray()); EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 1);
    EXPECT(r.hasMoreInArray()); EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 2);
    EXPECT(r.hasMoreInArray()); EXPECT(r.nextInt(n)); EXPECT_EQ_I(n, 3);
    EXPECT(!r.hasMoreInArray());
    EXPECT(r.exitArray());
    EXPECT(!r.nextField(k));
    EXPECT(r.exitObject());
}

TEST(reject_unterminated_string) {
    Buf b(R"({"t":"hel)");
    Reader r(b.data(), b.size());
    std::string_view k, v;
    EXPECT(r.enterObject());
    EXPECT(r.nextField(k));
    EXPECT(!r.nextString(v));
}

TEST(reject_truncated_object) {
    Buf b(R"({"t":"check","kind":)");
    Reader r(b.data(), b.size());
    std::string_view k, v;
    EXPECT(r.enterObject());
    EXPECT(r.nextField(k));
    EXPECT(r.nextString(v));
    EXPECT(r.nextField(k));
    // The value is missing; nextString should fail (and subsequent ops too).
    EXPECT(!r.nextString(v));
}

TEST(reject_float_value) {
    Buf b(R"({"x":1.5})");
    Reader r(b.data(), b.size());
    std::string_view k;
    EXPECT(r.enterObject());
    EXPECT(r.nextField(k));
    std::int64_t n = 0;
    EXPECT(!r.nextInt(n));
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
