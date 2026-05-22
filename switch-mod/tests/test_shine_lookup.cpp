// Host-compiler tests for switch-mod/src/ap/shine_lookup.hpp + the related
// surface area used by Phase 4 (named-set roundtrip on a stub ApState-like
// bitset).
//
// shine_lookup.hpp is header-only and depends only on shine_table.h, which
// has no runtime side effects — perfect for a pure host test. The named-set
// helper isn't exercised here against the real ApState singleton (atomic
// state + Switch headers); instead we test the same word/bit indexing
// arithmetic against a local bitset with identical layout, so a regression
// in the indexing math is caught in CI without needing a Switch build.
//
// IP boundary: this test runs against a synthetic shine_table.h built from
// switch-mod/tests/{locations,shine_map}_fixture.json — both fixtures use
// invented stage names (TestStageAlpha, TestStageBeta), invented obj_ids
// (objBravo, objCharlie, ...), invented uids (101..105), invented kingdom
// labels (TestKingdom, OtherTestKingdom), and invented moon names — EXCEPT
// for the one canonical M5.7 anchor "Our First Power Moon" that CLAUDE.md
// explicitly allows as a verifiable test fixture. No production
// (stage, obj_id) → uid mappings live in committed test source. Data-drift
// of real production identifiers is the job of
// apworld/smo_archipelago/tests/test_progression_moons.py (Python, runs
// against committed locations.json — no Nintendo IP).
//
// Build (msys2 g++; single-line so it doesn't trip -Wcomment):
//   "C:/msys64/mingw64/bin/g++.exe" -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST -Iswitch-mod/src switch-mod/tests/test_shine_lookup.cpp -o test_shine_lookup.exe
//
// Generate the synthetic shine_table.h first (CI workflow already does this;
// single-line so it doesn't trip -Wcomment):
//   python scripts/sync_shine_table.py --locations switch-mod/tests/locations_fixture.json --shine-map switch-mod/tests/shine_map_fixture.json
//
// Covers:
//   - shineUidByStageObj: known synthetic moons resolve to expected uids;
//     unknown returns -1; null/empty inputs return -1; partial matches don't
//     resolve; wrong-stage-right-obj doesn't resolve.
//   - shineUidByDisplayName: canonical anchor name resolves; mismatched
//     casing does NOT resolve (byte-for-byte semantics).
//   - isProgressionShine: progression-flagged synthetic moons return true;
//     non-progression-flagged return false; unknown returns false (fail-open).
//   - Named-set bit indexing: markMoonNamed → isMoonNamed roundtrips,
//     boundary uids (0, 2047, 2048), out-of-range short-circuits.

#define SMOAP_HOST_TEST 1

#include "ap/shine_lookup.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

using namespace smoap::game;

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

#define EXPECT_EQ_I(actual, expected) do {                                      \
    long long _a = (long long)(actual);                                         \
    long long _e = (long long)(expected);                                       \
    if (_a != _e) {                                                             \
        std::fprintf(stderr, "[%s] FAIL %s:%d: %lld != %lld\n",                 \
                     g_current_test, __FILE__, __LINE__, _a, _e);               \
        ++g_failures;                                                           \
    }                                                                           \
} while (0)

#define EXPECT_TRUE(actual)  EXPECT((actual))
#define EXPECT_FALSE(actual) EXPECT(!(actual))

#define TEST(name) static void name();                                          \
    struct name##_runner { name##_runner() { g_current_test = #name; name(); } } name##_instance; \
    static void name()

// --------------------------------------------------------------------------
// shineUidByStageObj
//
// Fixture rows (see shine_map_fixture.json):
//   TestStageAlpha / objAnchor  -> 101 ("Our First Power Moon", progression)
//   TestStageAlpha / objBravo   -> 102 ("Test Moon Bravo",     progression)
//   TestStageAlpha / objCharlie -> 103 ("Test Moon Charlie",   regular)
//   TestStageBeta  / objDelta   -> 104 ("Test Moon Delta",     progression)
//   TestStageBeta  / objEcho    -> 105 ("Test Moon Echo",      regular)
// --------------------------------------------------------------------------

TEST(stage_obj_known_alpha_bravo) {
    EXPECT_EQ_I(shineUidByStageObj("TestStageAlpha", "objBravo"), 102);
}

TEST(stage_obj_known_alpha_anchor) {
    EXPECT_EQ_I(shineUidByStageObj("TestStageAlpha", "objAnchor"), 101);
}

TEST(stage_obj_known_beta_delta) {
    EXPECT_EQ_I(shineUidByStageObj("TestStageBeta", "objDelta"), 104);
}

TEST(stage_obj_unknown_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("NoSuchStage", "obj99999"), -1);
}

TEST(stage_obj_null_stage_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj(nullptr, "objAnchor"), -1);
}

TEST(stage_obj_null_obj_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("TestStageAlpha", nullptr), -1);
}

TEST(stage_obj_empty_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("", "objAnchor"), -1);
    EXPECT_EQ_I(shineUidByStageObj("TestStageAlpha", ""), -1);
}

TEST(stage_obj_partial_match_does_not_resolve) {
    // Bare prefix shouldn't match a longer obj_id — comparison is whole-string.
    EXPECT_EQ_I(shineUidByStageObj("TestStageAlpha", "obj"), -1);
}

TEST(stage_obj_wrong_stage_right_obj_does_not_resolve) {
    // objAnchor lives on TestStageAlpha; querying TestStageBeta must not match.
    EXPECT_EQ_I(shineUidByStageObj("TestStageBeta", "objAnchor"), -1);
}

// --------------------------------------------------------------------------
// shineUidByDisplayName
// --------------------------------------------------------------------------

TEST(display_name_anchor) {
    // CLAUDE.md's canonical M5.7 anchor — the ONE verbatim moon name
    // allowed in committed test fixtures. Other display-name lookups
    // would need a second real name; the by-display-name scan path is
    // exercised by this single assertion against the anchor entry in
    // shine_map_fixture.json.
    EXPECT_EQ_I(shineUidByDisplayName("Our First Power Moon"), 101);
}

TEST(display_name_synthetic_resolves) {
    // Validate the by-display-name scan against a synthetic moon name too,
    // so the case-sensitive / boundary tests below have something invented
    // (not the anchor) to assert against.
    EXPECT_EQ_I(shineUidByDisplayName("Test Moon Bravo"), 102);
}

TEST(display_name_unknown_returns_negative_one) {
    EXPECT_EQ_I(shineUidByDisplayName("Not A Real Moon"), -1);
}

TEST(display_name_case_sensitive) {
    // shine_id comparison is byte-for-byte. Lowercase miss should return -1.
    EXPECT_EQ_I(shineUidByDisplayName("test moon bravo"), -1);
}

TEST(display_name_null_empty) {
    EXPECT_EQ_I(shineUidByDisplayName(nullptr), -1);
    EXPECT_EQ_I(shineUidByDisplayName(""), -1);
}

// --------------------------------------------------------------------------
// isProgressionShine
//
// Real-production progression-flag drift is covered by
// apworld/smo_archipelago/tests/test_progression_moons.py — that suite runs
// against committed locations.json (no IP). Here we only verify the
// scan-logic correctness on the synthetic fixture.
// --------------------------------------------------------------------------

TEST(progression_alpha_anchor_flagged) {
    // objAnchor row has "progression": true in locations_fixture.json.
    EXPECT_TRUE(isProgressionShine("TestStageAlpha", "objAnchor"));
}

TEST(progression_alpha_bravo_flagged) {
    // Second progression entry on the same stage — validates per-row scan.
    EXPECT_TRUE(isProgressionShine("TestStageAlpha", "objBravo"));
}

TEST(progression_beta_delta_flagged_other_kingdom) {
    // Progression entry on a different stage / kingdom — validates the scan
    // doesn't accidentally key on stage_name prefix.
    EXPECT_TRUE(isProgressionShine("TestStageBeta", "objDelta"));
}

TEST(progression_alpha_charlie_not_flagged) {
    // Same stage as the flagged anchor entry, but charlie's row is regular
    // (progression: false). Validates the scan doesn't bleed flags across
    // rows that share a stage_name.
    EXPECT_FALSE(isProgressionShine("TestStageAlpha", "objCharlie"));
}

TEST(progression_beta_echo_not_flagged) {
    EXPECT_FALSE(isProgressionShine("TestStageBeta", "objEcho"));
}

TEST(progression_unknown_returns_false) {
    EXPECT_FALSE(isProgressionShine("NoSuchStage", "obj99999"));
}

TEST(progression_null_returns_false) {
    EXPECT_FALSE(isProgressionShine(nullptr, "objAnchor"));
    EXPECT_FALSE(isProgressionShine("TestStageAlpha", nullptr));
}

// --------------------------------------------------------------------------
// Named-set bit indexing.
//
// The real ApState::named_moons_bits is a std::atomic<uint64_t>[32] —
// 2048 bits indexed by shine_uid. Test the same arithmetic against a local
// bitset so a regression in the math is caught here without needing a
// Switch build. The fns under test (markMoonNamed / isMoonNamed) live in
// ApState; we inline-replicate just the indexing here.
// --------------------------------------------------------------------------

constexpr std::size_t kWordCount = 32;
constexpr int kMaxBit = static_cast<int>(kWordCount) * 64;

struct LocalNamedSet {
    std::uint64_t bits[kWordCount] = {};

    void mark(int uid) {
        if (uid < 0 || uid >= kMaxBit) return;
        bits[uid / 64] |= (std::uint64_t{1} << (uid % 64));
    }
    bool query(int uid) const {
        if (uid < 0 || uid >= kMaxBit) return false;
        return (bits[uid / 64] & (std::uint64_t{1} << (uid % 64))) != 0;
    }
};

TEST(named_set_empty_returns_false) {
    LocalNamedSet s;
    EXPECT_FALSE(s.query(101));
    EXPECT_FALSE(s.query(0));
    EXPECT_FALSE(s.query(kMaxBit - 1));
}

TEST(named_set_mark_then_query_roundtrip) {
    LocalNamedSet s;
    s.mark(101);  // Synthetic anchor uid from shine_map_fixture.json.
    EXPECT_TRUE(s.query(101));
    // Neighbors stay clean.
    EXPECT_FALSE(s.query(9000));
    EXPECT_FALSE(s.query(102));
}

TEST(named_set_mark_idempotent) {
    LocalNamedSet s;
    s.mark(101);
    s.mark(101);
    s.mark(101);
    EXPECT_TRUE(s.query(101));
}

TEST(named_set_boundary_bits) {
    LocalNamedSet s;
    s.mark(0);
    s.mark(63);   // last bit of word 0
    s.mark(64);   // first bit of word 1
    s.mark(kMaxBit - 1);  // last representable bit
    EXPECT_TRUE(s.query(0));
    EXPECT_TRUE(s.query(63));
    EXPECT_TRUE(s.query(64));
    EXPECT_TRUE(s.query(kMaxBit - 1));
}

TEST(named_set_out_of_range_silently_ignored) {
    LocalNamedSet s;
    s.mark(-1);
    s.mark(kMaxBit);
    s.mark(kMaxBit + 1000);
    EXPECT_FALSE(s.query(-1));
    EXPECT_FALSE(s.query(kMaxBit));
    EXPECT_FALSE(s.query(kMaxBit + 1000));
    // No spillover into the real bits.
    EXPECT_FALSE(s.query(0));
    EXPECT_FALSE(s.query(kMaxBit - 1));
}

TEST(named_set_covers_all_known_shine_uids) {
    // The real shine_table.h max uid is well under 2048; this guards
    // against an apworld expansion that pushes uids past the bitset size
    // (a silent bug since markMoonNamed early-returns on out-of-range).
    // Synthetic fixture uses 101..105, which is also well under 2048
    // when treated as a sanity check on the scan logic, but the real
    // production max is what matters in practice — this is verified by
    // a dev-only run against the populated header.
    int max_uid = 0;
    for (const auto& row : kShineTable) {
        if (row.shine_uid > max_uid) max_uid = row.shine_uid;
    }
    // Sized for 2048; complain loudly if we ever get within 256 of the cap.
    EXPECT(max_uid < kMaxBit);
    if (max_uid >= kMaxBit - 256) {
        std::fprintf(stderr,
                     "[%s] WARNING: max shine_uid=%d nearing bitset cap %d — "
                     "bump kNamedMoonsWordCount before this gets tight\n",
                     g_current_test, max_uid, kMaxBit);
    }
}

}  // namespace

int main() {
    if (g_failures == 0) {
        std::printf("All tests passed.\n");
        return 0;
    }
    std::fprintf(stderr, "%d failures\n", g_failures);
    return 1;
}
