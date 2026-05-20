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
void installAddHackDictionaryHook();
void installAddPayShineHook();
void installAddPayShineAllHook();
void installCaptureStartHook();
void tickPendingUncapture();
void installWorldMapSelectHook();
void installMoonLabelHook();
void installShineAppearanceHook();
void installCreditsStartHook();
void installCappyMessageTextHooks();
void installCappyMessengerSymbols();
void installSaveLoadHook();
}  // namespace smoap::hooks

namespace smoap::game {
void installSnapshotSymbols();
void installDepositKingdomLookupSymbol();
void installPayShineSnapshotSymbol();
void installCaptureGrantSymbols();
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

    SMOAP_LOG_INFO("resolving M6-phase-B capture-grant symbols");
    smoap::game::installCaptureGrantSymbols();
    SMOAP_LOG_INFO("installing AddHackDictionaryHook (Capture List AP gate)");
    smoap::hooks::installAddHackDictionaryHook();

    SMOAP_LOG_INFO("installing M6-phase-D deposit hooks (addPayShine + addPayShineCurrentAll)");
    smoap::hooks::installAddPayShineHook();
    smoap::hooks::installAddPayShineAllHook();

    SMOAP_LOG_INFO("installing CaptureStartHook (capture lock + AP check)");
    smoap::hooks::installCaptureStartHook();

    SMOAP_LOG_INFO("installing WorldMapSelectHook (M7 Path A kingdom-order gate)");
    smoap::hooks::installWorldMapSelectHook();

    SMOAP_LOG_INFO("installing M6-phase-A.5 cutscene label hooks");
    smoap::hooks::installMoonLabelHook();

    SMOAP_LOG_INFO("installing ShineAppearanceHook (AP-classification moon color)");
    smoap::hooks::installShineAppearanceHook();

    SMOAP_LOG_INFO("resolving M6-phase-C snapshot enumeration symbols");
    smoap::game::installSnapshotSymbols();

    SMOAP_LOG_INFO("installing CreditsStartHook (Strategy B: StaffRollScene::init)");
    smoap::hooks::installCreditsStartHook();

    SMOAP_LOG_INFO("installing CappyMessenger text-lookup trampolines (4)");
    smoap::hooks::installCappyMessageTextHooks();
    SMOAP_LOG_INFO("resolving CappyMessenger rs:: function pointers");
    smoap::hooks::installCappyMessengerSymbols();

    SMOAP_LOG_INFO("installing SaveLoadHook (session-state reset + re-HELLO)");
    smoap::hooks::installSaveLoadHook();

    SMOAP_LOG_INFO("=== hkMain END ===");
}
