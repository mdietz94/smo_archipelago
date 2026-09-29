#include "KingdomOrderGate.hpp"

#include <cstring>

#include "../ap/ApState.hpp"
#include "KingdomUnlock.hpp"

namespace smoap::game {

namespace {

struct Rule {
    const char* picked;          // kingdom short name the player picked
    const char* prereq;          // kingdom short name to redirect to
    const char* prereq_stage;    // SMO HomeStage for the redirect
};

// Gates only the two post-bifurcation siblings. Lake and Snow themselves are
// not gated — they're always available immediately post-Sand / post-Metro.
//
// The HomeStage names below are the canonical SMO 1.0.0 strings, matching the
// KINGDOM_FOR_HOMESTAGE table in scripts/extract_shine_map.py.
constexpr Rule kRules[] = {
    {"Wooded",  "Lake", "LakeWorldHomeStage"},
    {"Seaside", "Snow", "SnowWorldHomeStage"},
};

// Mario's currently-occupied kingdom bit comes from
// KingdomUnlock::currentKingdomBit() (shared with the kingdom_exit emitter
// in WorldMapSelectHook). Returns 0xff when the holder or symbol isn't
// ready (caller treats as "not current").

}  // namespace

OrderGateDecision evaluateOrderGateForKingdom(const char* kingdom_short) {
    OrderGateDecision d{};
    if (!kingdom_short || !*kingdom_short) return d;

    for (const auto& r : kRules) {
        if (std::strcmp(kingdom_short, r.picked) != 0) continue;

        // Release condition: the prereq kingdom must be either
        //   (a) sticky-visited in this session (Mario flew there via a
        //       cinematic or regular-map commit — see ApState::visited_kingdoms
        //       and the TryChange* hooks in WorldMapSelectHook), or
        //   (b) Mario's currently-occupied kingdom (handles the save-reload-
        //       into-prereq case where visited bits would otherwise be cold).
        // (b) is a same-frame query of getCurrentWorldIdNoDevelop, distinct
        // from the per-frame poll the earlier design used: this fires only
        // when the gate is consulted (calcNextLocked / tryChange), not every
        // tick, so a save-reload doesn't pollute visited_kingdoms.
        //
        // Unknown prereq → fail open (better than soft-lock).
        const std::uint8_t prereq_bit = kingdomBitFor(r.prereq);
        if (prereq_bit >= 17) return d;

        auto& st = smoap::ap::ApState::instance();
        if (st.isKingdomBitVisited(static_cast<int>(prereq_bit))) {
            return d;  // (a) visited — released
        }
        if (currentKingdomBit() == prereq_bit) {
            return d;  // (b) currently in prereq — released
        }

        d.blocked                = true;
        d.required_kingdom_short = r.prereq;
        d.required_stage         = r.prereq_stage;
        return d;
    }
    return d;  // not a gated kingdom
}

}  // namespace smoap::game
