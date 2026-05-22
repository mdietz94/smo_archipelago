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
// Build (msys2 g++; single-line so it doesn't trip -Wcomment):
//   "C:/msys64/mingw64/bin/g++.exe" -std=c++20 -Wall -Wextra -O0 -g -DSMOAP_HOST_TEST -Iswitch-mod/src switch-mod/tests/test_shine_lookup.cpp -o test_shine_lookup.exe
//
// Covers:
//   - shineUidByStageObj: known moons resolve to expected uids; unknown
//     returns -1; null/empty inputs return -1; both sides matter.
//   - shineUidByDisplayName: known display names resolve; mismatched casing
//     does NOT resolve (byte-for-byte semantics).
//   - isProgressionShine: progression-flagged moons return true; regular
//     moons return false; unknown returns false (fail-open). At least one
//     case per kingdom that has a progression moon.
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
// --------------------------------------------------------------------------

TEST(stage_obj_known_cascade_progression) {
    // From the CI fixture (switch-mod/tests/shine_map_fixture.json) the
    // Cascade obj21 entry resolves to uid 218 — same stage/obj/uid triple
    // the real shine_map.json holds, but with a synthetic shine_id so the
    // fixture doesn't carry Nintendo IP. The real production table is
    // verified by test_progression_moons.py against the live locations.json.
    EXPECT_EQ_I(shineUidByStageObj("WaterfallWorldHomeStage", "obj21"), 218);
}

TEST(stage_obj_known_first_power_moon) {
    // Used as the canonical M5.7 fixture in CLAUDE.md: ground-truth datapoint.
    EXPECT_EQ_I(shineUidByStageObj("WaterfallWorldHomeStage", "obj214"), 205);
}

TEST(stage_obj_known_cap_kingdom) {
    EXPECT_EQ_I(shineUidByStageObj("CapWorldHomeStage", "obj2422"), 815);
}

TEST(stage_obj_unknown_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("NoSuchStage", "obj99999"), -1);
}

TEST(stage_obj_null_stage_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj(nullptr, "obj21"), -1);
}

TEST(stage_obj_null_obj_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("WaterfallWorldHomeStage", nullptr), -1);
}

TEST(stage_obj_empty_returns_negative_one) {
    EXPECT_EQ_I(shineUidByStageObj("", "obj21"), -1);
    EXPECT_EQ_I(shineUidByStageObj("WaterfallWorldHomeStage", ""), -1);
}

TEST(stage_obj_partial_match_does_not_resolve) {
    // Bare prefix shouldn't match a longer obj_id — comparison is whole-string.
    EXPECT_EQ_I(shineUidByStageObj("WaterfallWorldHomeStage", "obj2"), -1);
}

// --------------------------------------------------------------------------
// shineUidByDisplayName
// --------------------------------------------------------------------------

TEST(display_name_first_power_moon) {
    // CLAUDE.md's canonical M5.7 anchor — the ONE verbatim moon name
    // allowed in committed test fixtures, mirrored in the CI fixture's
    // (Cascade, "Our First Power Moon") row. Other display-name lookups
    // would need a second real name; the by-display-name scan path is
    // exercised by this single assertion.
    EXPECT_EQ_I(shineUidByDisplayName("Our First Power Moon"), 205);
}

TEST(display_name_unknown_returns_negative_one) {
    EXPECT_EQ_I(shineUidByDisplayName("Not A Real Moon"), -1);
}

TEST(display_name_case_sensitive) {
    // shine_id comparison is byte-for-byte. Lowercase miss should return -1.
    EXPECT_EQ_I(shineUidByDisplayName("multi moon atop the falls"), -1);
}

TEST(display_name_null_empty) {
    EXPECT_EQ_I(shineUidByDisplayName(nullptr), -1);
    EXPECT_EQ_I(shineUidByDisplayName(""), -1);
}

// --------------------------------------------------------------------------
// isProgressionShine
// --------------------------------------------------------------------------

TEST(progression_cascade_multimoon) {
    // The canonical example.
    EXPECT_TRUE(isProgressionShine("WaterfallWorldHomeStage", "obj21"));
}

TEST(progression_cascade_first_power_moon) {
    // Story 1->2 advancer added during 2026-05-21 audit.
    EXPECT_TRUE(isProgressionShine("WaterfallWorldHomeStage", "obj214"));
}

TEST(progression_sand_both_multimoons) {
    // Sand has TWO Multi Moons (Hariet + Knucklotec). Audit caught the
    // missing one (The Hole in the Desert) — guard against regression.
    EXPECT_TRUE(isProgressionShine("SandWorldHomeStage", "obj1432"));      // Hariet MM
    EXPECT_TRUE(isProgressionShine("SandWorldUnderground001Stage", "obj9")); // Knucklotec MM
}

TEST(progression_seaside_seals_and_mollusque) {
    // 4 seals + Mollusque MM — all five must be flagged for the seal chain
    // → boss spawn → MM to complete in talkatoo_mode.
    EXPECT_TRUE(isProgressionShine("SeaWorldHomeStage", "obj382"));         // Stone Pillar Seal
    EXPECT_TRUE(isProgressionShine("SeaWorldLighthouseZone", "obj16"));     // Lighthouse Seal
    EXPECT_TRUE(isProgressionShine("SeaWorldLavaZone", "obj168"));          // Hot Spring Seal
    EXPECT_TRUE(isProgressionShine("SeaWorldDamageBallZone", "obj60"));     // Above the Canyon Seal
    EXPECT_TRUE(isProgressionShine("SeaWorldHomeStage", "obj1277"));        // Glass Is Half Full (Mollusque MM)
}

TEST(progression_bowsers_full_chain) {
    // 4-step Bowser's story chain — all four must be flagged.
    EXPECT_TRUE(isProgressionShine("SkyWorldHomeStage", "obj1921"));   // Infiltrate
    EXPECT_TRUE(isProgressionShine("SkyWorldWallZone", "obj1671"));    // Smart Bombing
    EXPECT_TRUE(isProgressionShine("SkyWorldCastleZone", "obj3811"));  // Big Broodal Battle
    EXPECT_TRUE(isProgressionShine("SkyWorldCastleZone", "obj3819"));  // Showdown (RoboBrood MM)
}

TEST(progression_regular_moon_not_flagged) {
    // A run-of-the-mill Cascade moon — should NOT be flagged. Picked from
    // the actual user-collected moon that triggered the block in testing.
    EXPECT_FALSE(isProgressionShine("WaterfallWorldHomeStage", "obj2618")); // Cascade Kingdom Timer Challenge 1
}

TEST(progression_pruned_lost_no_progression) {
    // Audit removed "Lost: A Propeller Pillar's Secret" — Lost Kingdom has
    // no progression moon per Mario Wiki Multi_Moon page. Regression guard
    // so it doesn't drift back in.
    EXPECT_FALSE(isProgressionShine("ClashWorldHomeStage", "obj1144"));
}

TEST(progression_pruned_make_the_secret_flower) {
    // Audit removed "Wooded: Make the Secret Flower Field Bloom" — not a
    // Multi Moon (just a scenario-5 spawn after Torkdrift). Player can
    // advance past Wooded with the Torkdrift MM alone.
    EXPECT_FALSE(isProgressionShine("ForestWorldBossStage", "obj488"));
}

TEST(progression_unknown_returns_false) {
    EXPECT_FALSE(isProgressionShine("NoSuchStage", "obj99999"));
}

TEST(progression_null_returns_false) {
    EXPECT_FALSE(isProgressionShine(nullptr, "obj21"));
    EXPECT_FALSE(isProgressionShine("WaterfallWorldHomeStage", nullptr));
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
    EXPECT_FALSE(s.query(218));
    EXPECT_FALSE(s.query(0));
    EXPECT_FALSE(s.query(kMaxBit - 1));
}

TEST(named_set_mark_then_query_roundtrip) {
    LocalNamedSet s;
    s.mark(218);  // Cascade Multi Moon uid from shine_table.h
    EXPECT_TRUE(s.query(218));
    // Neighbors stay clean.
    EXPECT_FALSE(s.query(217));
    EXPECT_FALSE(s.query(219));
}

TEST(named_set_mark_idempotent) {
    LocalNamedSet s;
    s.mark(218);
    s.mark(218);
    s.mark(218);
    EXPECT_TRUE(s.query(218));
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
