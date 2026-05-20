// Spicy Meatball Overdrive — Hakkun edition entry point.
//
// Phase 3b in progress: installing trampoline hooks incrementally. Each
// installXxx call below pulls a HkTrampoline + lambda definition from a
// hooks/*.cpp into the link (gc-sections drops uninstalled trampolines, so
// an installAtSym call here is what keeps the hook live).

#include "util/Log.hpp"

namespace smoap::hooks {
void installScenarioFlagHook();
void installMoonGetHook();
void installDeathHook();
void installShineNumGetHook();
void installShineNumByWorldGetHook();
}  // namespace smoap::hooks

namespace smoap::game {
void installDepositKingdomLookupSymbol();
void installPayShineSnapshotSymbol();
}  // namespace smoap::game

extern "C" void hkMain() {
    SMOAP_LOG_INFO("=== hkMain START ===");

    SMOAP_LOG_INFO("resolving M6-phase-D current-kingdom lookup");
    smoap::game::installDepositKingdomLookupSymbol();
    SMOAP_LOG_INFO("resolving M6-phase-D getPayShineNum lookup");
    smoap::game::installPayShineSnapshotSymbol();

    smoap::hooks::installScenarioFlagHook();
    smoap::hooks::installMoonGetHook();
    smoap::hooks::installDeathHook();
    smoap::hooks::installShineNumGetHook();
    smoap::hooks::installShineNumByWorldGetHook();

    SMOAP_LOG_INFO("=== hkMain END ===");
}
