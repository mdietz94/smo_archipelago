// M7 Path A — fork-cinematic kingdom-order gate.
//
// Forces linear progression at SMO's two world-map bifurcations: the
// post-Sand fork substitutes Wooded->Lake; the post-Metro fork substitutes
// Seaside->Snow. The substitution applies anywhere calcNextLocked* fires
// (cinematic AND the leave-kingdom regular map — both share that
// function), released once Mario has actually been to the prereq kingdom.
//
// The release condition is OR'd in the gate (see KingdomOrderGate.cpp):
//   (a) ApState::visited_kingdoms bit set — Mario flew here via either
//       transition hook below. STICKY for the session.
//   (b) Mario is currently in the prereq kingdom — on-demand query via
//       getCurrentWorldIdNoDevelop. Handles the save-reload-into-Lake
//       case without polluting (a) on every load.
//
// Hooks installed:
//
//   calcNextLockedWorldIdForWorldMap (LayoutActor + Scene overloads)
//     Fires when the world map (cinematic OR regular leave-kingdom UI)
//     populates a "next-unlocked" slot. SUBSTITUTES per the gate decision.
//
//   tryChangeNextStageWithDemoWorldWarp (cinematic stage commit)
//     BACKSTOP for the cinematic flight in case calcNextLocked misses.
//     Also marks visited[destination] so post-cinematic the gate releases.
//
//   tryChangeNextStageWithWorldWarpHole (regular-map portal-hole commit)
//     VISITED-ONLY (no substitution). Marks visited[destination] when
//     Mario actively boards the Odyssey from the regular map. NOT a
//     substitution chokepoint — the substitution happens earlier at
//     calcNextLocked when the UI populates the slot.
//
// History — prior iterations also hooked getUnlockWorldId (4 overloads)
// and used tryChangeNextStageWithWorldWarpHole for substitution. That
// blocked the leave-kingdom map after the cinematic (Seaside-from-Snow
// loop). The earlier threshold gate (e505c5c, "lifetime AP-receipts >=
// N") soft-locked when other players' own-slot completions pushed the
// counter past the gate before Mario had ever visited. Switching the
// signal to "actually traveled here, OR currently here" plus narrowing
// substitution to the cinematic-shared calcNextLocked path is what
// makes both bugs not-a-problem.
//
// See CLAUDE.md M7 section's "prior-iteration failure log" for the
// failed attempts (skip Orig at ChangeStage / DemoWorldWarp produces UI
// soft-lock; substitute at DemoWorldWarp produces broken cutscene
// visuals; isUnlockedWorld doesn't gate the cursor; refusing tryChange
// soft-locks the menu).

#include "lib.hpp"  // HOOK_DEFINE_TRAMPOLINE

#include "lib/nx/nx.h"  // Result, R_FAILED — via extern "C" wrapper
#include "nn/ro.h"

#include <cstdint>

#include "../ap/ApState.hpp"
#include "../game/KingdomOrderGate.hpp"
#include "../game/KingdomUnlock.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

namespace smoap::hooks {

namespace {

// 1-pointer Itanium-ABI wrappers, matching game/CaptureGate.cpp.
struct GameDataHolderWriter   { void* mData; };
struct GameDataHolderAccessor { void* mData; };

// Compile-time kill switch. When false, the hooks log substitutions they
// WOULD have applied but pass Orig's value through unchanged. Useful for
// disabling the gate without rebuilding observation-only.
constexpr bool kGateEnabled = true;

// Shared substitution helper for the cinematic UI's per-slot world-id
// callback. Given the worldId Orig returned, decide whether to substitute.
// `origin` is a log prefix that identifies which hook fired.
int substituteSlotWorldId(const char* origin, int index, int orig_world_id) {
    if (!kGateEnabled) return orig_world_id;

    const char* kingdom = smoap::game::kingdomShortFromWorldId(orig_world_id);
    if (!kingdom) return orig_world_id;

    auto decision = smoap::game::evaluateOrderGateForKingdom(kingdom);
    if (!decision.blocked) return orig_world_id;

    const int prereq_id = smoap::game::worldIdFromKingdomShort(
        decision.required_kingdom_short);
    if (prereq_id < 0) {
        SMOAP_LOG_WARN("[wmap.%s] gate misconfigured: prereq='%s' not in "
                       "kKingdoms; passing original worldId=%d through",
                       origin,
                       decision.required_kingdom_short
                           ? decision.required_kingdom_short
                           : "(null)",
                       orig_world_id);
        return orig_world_id;
    }

    // Throttle the log so per-frame re-queries don't flood. Key the throttle
    // on (origin, index, orig_id) — re-log when any of these changes so we
    // capture state transitions.
    static const char* s_last_origin   = nullptr;
    static int         s_last_index    = -1;
    static int         s_last_orig_id  = -1;
    const bool changed =
        s_last_origin  != origin  ||
        s_last_index   != index   ||
        s_last_orig_id != orig_world_id;
    if (changed) {
        SMOAP_LOG_INFO("[wmap.%s] SUB slot=%d origId=%d (%s) -> prereqId=%d (%s)",
                       origin, index, orig_world_id, kingdom,
                       prereq_id, decision.required_kingdom_short);
        s_last_origin  = origin;
        s_last_index   = index;
        s_last_orig_id = orig_world_id;
    }
    return prereq_id;
}

// ----------------------------------------------------------------------------
// Layer 1: calcNextLockedWorldIdForWorldMap — the post-Multi-Moon FORK
// cinematic uses this to populate the "newly unlocked" destinations. Two
// overloads (LayoutActor*, Scene*). Verified firing in 2026-05-17 fresh-save
// playtest as Scene overload on slot 0.
// ----------------------------------------------------------------------------

HOOK_DEFINE_TRAMPOLINE(CalcNextLockedWorldIdLayoutActorHook) {
    static int Callback(const void* p, int index) {
        return substituteSlotWorldId("menu.NextLocked.Layout", index, Orig(p, index));
    }
};
HOOK_DEFINE_TRAMPOLINE(CalcNextLockedWorldIdSceneHook) {
    static int Callback(const void* p, int index) {
        return substituteSlotWorldId("menu.NextLocked.Scene", index, Orig(p, index));
    }
};

// ----------------------------------------------------------------------------
// Layer 2: tryChangeNextStageWithDemoWorldWarp — BACKSTOP for the cinematic
// stage commit ("Demo" = cutscene in SMO parlance; this is the cinematic
// flight path). If Layer 1 misses, this rewrites the stage arg. WARN-level
// log makes any backstop fire loud — it's a signal that a new upstream catch
// is needed. Substitution at this layer may produce broken cutscene visuals
// (per failed-iteration #3 in CLAUDE.md M7 section) because the world-map
// state machine may have already pre-loaded the gated kingdom's cutscene
// assets by the time tryChange runs. Refusing (returning false) was tried
// and soft-locks the menu, so substitution is the safer choice despite the
// visual cost.
//
// The regular-map portal-hole equivalent (tryChangeNextStageWithWorldWarpHole)
// is intentionally NOT hooked — the regular map should permit free travel
// between any unlocked kingdom.
// ----------------------------------------------------------------------------

// Mark Mario as having "visited" the destination kingdom. Called from both
// tryChange* hooks below — these are the actual stage-commit chokepoints
// (cinematic Odyssey-flight + regular-map portal-hole), so they're the right
// time to flip the sticky bit. Save-data load doesn't go through either, so
// reloading into Lake won't pollute visited[Lake]. Goal-fire moved to
// CreditsStartHook (StaffRollScene::init inline patch) — Mushroom Kingdom
// arrival false-positives on the Luncheon portrait warp.
void markVisitedFromStage(const char* origin, const char* stage) {
    if (!stage) return;
    const char* kingdom = smoap::game::kingdomShortFromHomeStage(stage);
    if (!kingdom) return;
    const std::uint8_t bit = smoap::game::kingdomBitFor(kingdom);
    if (bit >= 17) return;
    auto& st = smoap::ap::ApState::instance();
    const bool was_visited = st.isKingdomBitVisited(static_cast<int>(bit));
    if (!was_visited) {
        SMOAP_LOG_INFO("[wmap.%s] visited[%s] = true (stage='%s')",
                       origin, kingdom, stage);
    }
    st.markKingdomBitVisited(static_cast<int>(bit));
}

HOOK_DEFINE_TRAMPOLINE(TryChangeDemoWorldWarpHook) {
    static bool Callback(GameDataHolderWriter writer, const char* stage) {
        const char* final_stage = stage;
        const char* kingdom = stage ? smoap::game::kingdomShortFromHomeStage(stage)
                                     : nullptr;
        if (kGateEnabled && kingdom) {
            const auto decision = smoap::game::evaluateOrderGateForKingdom(kingdom);
            if (decision.blocked && decision.required_stage) {
                SMOAP_LOG_WARN("[wmap.tryChange.Demo] BACKSTOP substituting "
                               "stage='%s' -> '%s' (upstream cinematic catch missed)",
                               stage, decision.required_stage);
                final_stage = decision.required_stage;
            }
        }
        // Visited tracking: record the kingdom Mario actually flies to
        // (post-substitution). The cinematic-fork case substitutes
        // Wooded->Lake here; we want visited[Lake] to set so the next gate
        // consult releases. The flag is sticky; no harm in setting it on
        // every cinematic flight.
        markVisitedFromStage("tryChange.Demo", final_stage);
        return Orig(writer, final_stage);
    }
};

// Regular-map portal-hole commit. NOT used for substitution (the regular
// map should allow free travel between unlocked kingdoms — see the header
// comment). Re-installed solely to mark visited on the destination so the
// gate's sticky bit reflects "Mario actually flew here at some point this
// session," not "Mario is sitting here right now" (the latter is the gate's
// own current-kingdom OR-check, see KingdomOrderGate.cpp).
HOOK_DEFINE_TRAMPOLINE(TryChangeWorldWarpHoleHook) {
    static bool Callback(GameDataHolderWriter writer, const char* stage) {
        markVisitedFromStage("tryChange.Hole", stage);
        return Orig(writer, stage);
    }
};

}  // namespace

void installWorldMapSelectHook() {
    SMOAP_LOG_INFO("installing M7 Path A Layer 1 (calcNextLocked, 2 overloads)");
    softInstallAtSymbol<CalcNextLockedWorldIdLayoutActorHook>(
        smoap::sym::kGameDataFunctionCalcNextLockedWorldIdForWorldMap_LayoutActor);
    softInstallAtSymbol<CalcNextLockedWorldIdSceneHook>(
        smoap::sym::kGameDataFunctionCalcNextLockedWorldIdForWorldMap_Scene);

    SMOAP_LOG_INFO("installing M7 Path A Layer 2 (DemoWorldWarp backstop + visited)");
    softInstallAtSymbol<TryChangeDemoWorldWarpHook>(
        smoap::sym::kGameDataFunctionTryChangeNextStageWithDemoWorldWarp);

    SMOAP_LOG_INFO("installing M7 Path A WorldWarpHole (visited-only, no gate)");
    softInstallAtSymbol<TryChangeWorldWarpHoleHook>(
        smoap::sym::kGameDataFunctionTryChangeNextStageWithWorldWarpHole);
}

}  // namespace smoap::hooks
