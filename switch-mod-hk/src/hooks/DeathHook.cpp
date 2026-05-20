// Hook on PlayerHitPointData::kill().
//
// Three responsibilities:
//   1. Outbound: emit a debounced death event for the bridge.
//   2. Inbound apply support: cache `self` so synthKillMario can re-invoke
//      kill() later via the trampoline's orig (skipping our callback).
//   3. Loopback guard: synthetic kills set ApState::synthetic_death_this_frame
//      before calling orig.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "DeathHook.hpp"

#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../util/Log.hpp"

class PlayerHitPointData;

namespace smoap::hooks {

namespace {

HkTrampoline<void, PlayerHitPointData*> deathHook =
    hk::hook::trampoline([](PlayerHitPointData* self) -> void {
        auto& st = smoap::ap::ApState::instance();
        // Cache for the inbound apply path. Updated every fire so we always
        // hold a live pointer (PlayerHitPointData is rebuilt per stage).
        st.player_hp_cache.store(self, std::memory_order_relaxed);
        deathHook.orig(self);
        // Stamp AFTER orig so the timestamp reflects "Mario is now dead".
        st.last_observed_death_ms.store(smoap::ap::ApState::nowMs(),
                                        std::memory_order_relaxed);
        if (st.synthetic_death_this_frame) return;
        smoap::ap::reportDeath();  // debounced inside reportDeath
    });

}  // namespace

void installDeathHook() {
    SMOAP_LOG_INFO("installing DeathHook -> PlayerHitPointData::kill");
    deathHook.installAtSym<"_ZN18PlayerHitPointData4killEv">();
}

void synthKillMario(PlayerHitPointData* hp) {
    // Direct call to the trampoline's stored original. Bypasses our callback,
    // so the only thing the game sees is a normal PlayerHitPointData::kill
    // invocation. Caller is responsible for setting synthetic_death_this_frame
    // (already done by maybeApplyInboundKill) and for non-null hp.
    deathHook.orig(hp);
}

}  // namespace smoap::hooks
