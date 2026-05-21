// Spicy Meatball Overdrive — Hakkun edition entry point.
//
// hkMain installs all hooks at module load. GameSystemInit + DrawMain are the
// two load-bearing trampolines: the former kicks off ApClient + ApState +
// HUD on the game's frame thread; the latter drives applyOnFrame + reconciler
// + CappyMessenger every frame.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "ap/ApClient.hpp"
#include "ap/ApConfig.hpp"
#include "ap/ApState.hpp"
#include "ui/ApHudOverlay.hpp"
#include "ui/CappyMessenger.hpp"
#include "util/Log.hpp"

#include <cstdint>

class GameSystem;
class HakoniwaSequence;

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
void reconcileCaptureDictionary();
}  // namespace smoap::game

namespace {

// Hook 1: GameSystem::init runs during SMO startup. After Orig, kick off
// the AP socket worker + HUD. Equivalent to production switch-mod's
// GameSystemInitHook except via HkTrampoline.
HkTrampoline<void, GameSystem*> gameSystemInitHook =
    hk::hook::trampoline([](GameSystem* self) -> void {
        SMOAP_LOG_INFO(">>> GameSystem::init hook FIRED (calling orig)");
        gameSystemInitHook.orig(self);
        SMOAP_LOG_INFO(">>> GameSystem::init orig returned");

        const auto cfg = smoap::ap::loadApConfig();

        // nifm + socket bring-up MUST happen on this (frame) thread.
        smoap::ap::ApClient::instance().initNetworking();

        // Force ApState singleton construction now (same nn-singleton
        // hardening reason as production — the worker doesn't have a
        // safe-to-construct context).
        (void)smoap::ap::ApState::instance();

        SMOAP_LOG_INFO("starting ApClient worker");
        smoap::ap::ApClient::instance().start(smoap::ap::BridgeTarget{
            .host = cfg.bridge_host,
            .port = cfg.bridge_port,
            .retry_ms = cfg.retry_ms,
            .recv_timeout_ms = cfg.recv_timeout_ms,
        });

        smoap::ui::initHud();

        SMOAP_LOG_INFO("<<< GameSystem::init hook complete");
    });

// Hook 2: HakoniwaSequence::drawMain runs every rendered frame. After Orig
// caches scene + GameDataHolder pointers, then drains the inbound queue.
HkTrampoline<void, const HakoniwaSequence*> drawMainHook =
    hk::hook::trampoline([](const HakoniwaSequence* self) -> void {
        static bool s_first = true;
        if (s_first) {
            s_first = false;
            SMOAP_LOG_INFO(">>> drawMain hook FIRED (first frame)");
        }
        smoap::util::drainPendingToFile();  // no-op stub post-Hakkun migration
        drawMainHook.orig(self);

        if (self) {
            // M6 phase B + Cappy Messenger: cache curScene + GameDataHolder
            // pointers from HakoniwaSequence's known field offsets. The cast
            // through al::Scene* with the IUseSceneObjHolder static_cast
            // adjustment was load-bearing for the production exlaunch build —
            // here we read the raw curScene pointer and store as void*; the
            // multiple-inheritance adjustment offset doesn't matter for our
            // rs:: callers since we hand the pointer through unchanged
            // (CappyMessenger's tryPump passes it to rs::isActiveCapMessage
            // which expects an IUseSceneObjHolder*; SMO's CapMessage director
            // is found by the rs:: function via the holder's vtable, not by
            // pointer arithmetic).
            //
            // TODO(phase-3b): once OdysseyHeaders is fully wired, restore the
            // static_cast<IUseSceneObjHolder*>(al::Scene*) adjustment for
            // type safety + correctness if any rs:: call site relies on it.
            constexpr std::size_t kCurSceneOffset       = 0xB0;
            constexpr std::size_t kGameDataHolderOffset = 0xB8;
            const auto* base = reinterpret_cast<const std::uint8_t*>(self);
            void* scene_obj = *reinterpret_cast<void* const*>(
                base + kCurSceneOffset);
            void* gdh = *reinterpret_cast<void* const*>(
                base + kGameDataHolderOffset);
            auto& st = smoap::ap::ApState::instance();
            st.scene_cache.store(scene_obj, std::memory_order_relaxed);
            st.game_data_holder_cache.store(gdh, std::memory_order_relaxed);
        }

        smoap::ap::ApState::instance().applyOnFrame();
        smoap::game::reconcileCaptureDictionary();
        smoap::ap::ApState::instance().flushPendingCaptureGrants();
        smoap::hooks::tickPendingUncapture();
        smoap::ui::drawHudFrame();
        smoap::ui::CappyMessenger::instance().tryPump(
            smoap::ap::ApState::instance().scene_cache.load(
                std::memory_order_relaxed));
    });

}  // namespace

extern "C" void hkMain() {
    SMOAP_LOG_INFO("=== hkMain START ===");

    SMOAP_LOG_INFO("installing GameSystemInit + DrawMain (load-bearing)");
    gameSystemInitHook.installAtSym<"_ZN10GameSystem4initEv">();
    drawMainHook.installAtSym<"_ZNK16HakoniwaSequence8drawMainEv">();

    SMOAP_LOG_INFO("resolving M6-phase-D current-kingdom lookup");
    smoap::game::installDepositKingdomLookupSymbol();
    SMOAP_LOG_INFO("resolving M6-phase-D getPayShineNum lookup");
    smoap::game::installPayShineSnapshotSymbol();

    // BISECT phase 3: phase 2 (all 19 disabled) was STABLE for 2+ min in a
    // kingdom. So one of these 19 is the culprit. Re-enabling only the two
    // that fire routinely during idle exploration: ShineNumGet (HUD shine
    // count) and ShineNumByWorldGet (per-world HUD). If still stable, the
    // culprit is in one of the user-action-driven hooks (capture, moon, world
    // warp). If it crashes again, the culprit is one of these two.
    SMOAP_LOG_INFO("BISECT phase 3: enabling ShineNumGet + ShineNumByWorldGet only");
    // smoap::hooks::installScenarioFlagHook();
    // smoap::hooks::installMoonGetHook();
    // smoap::hooks::installDeathHook();
    smoap::hooks::installShineNumGetHook();
    smoap::hooks::installShineNumByWorldGetHook();
    // smoap::game::installCaptureGrantSymbols();
    // smoap::hooks::installAddHackDictionaryHook();
    // smoap::hooks::installAddPayShineHook();
    // smoap::hooks::installAddPayShineAllHook();
    // smoap::hooks::installCaptureStartHook();
    // smoap::hooks::installWorldMapSelectHook();
    // smoap::hooks::installMoonLabelHook();

    // BISECT: ShineAppearanceHook disabled. Highest-frequency hook (fires per
    // shine actor spawn at stage load); if the JIT crash is hook-related,
    // disabling this removes a major source of .orig() invocations.
    SMOAP_LOG_INFO("ShineAppearanceHook DISABLED (bisect)");
    // smoap::hooks::installShineAppearanceHook();

    SMOAP_LOG_INFO("resolving M6-phase-C snapshot enumeration symbols");
    smoap::game::installSnapshotSymbols();

    // CreditsStartHook (Strategy B / StaffRollScene::init trampoline) is
    // disabled — boot validation 2026-05-20 showed installAtSym hanging the
    // guest thread at this exact point and SMO never reaching title. Two
    // independent failure modes share this surface:
    //   (a) The candidate symbol `_ZN14StaffRollScene4initERKN2al13ActorInitInfoE`
    //       may not exist in SMO 1.0.0 main.nso under that mangling — sail's
    //       SymbolDynamic::apply aborts via HK_ABORT_UNLESS when the name
    //       isn't in the loaded module's dynsym, but the abort message never
    //       surfaces in the Ryujinx log.
    //   (b) Even if the symbol resolves, Strategy B trampolines the FUNCTION
    //       ENTRY of StaffRollScene::init. Production uses Strategy A: an
    //       INLINE patch at +0x4C54A4 (a BL inside the function body). The
    //       two are not behaviorally equivalent — entry trampolines would
    //       fire on EVERY StaffRollScene::init even if execution never
    //       reaches the BL (e.g. early-return paths).
    // Goal detection only matters at end-of-main-story; everything else
    // boots without it. Restoring this needs either a Hakkun inline-hook
    // primitive equivalent to HOOK_DEFINE_INLINE (port `writeBranchLinkAt
    // MainOffset` from spike Gate 3 if it exists), or verified symbol
    // mangling against an extracted main.nso.
    SMOAP_LOG_INFO("CreditsStartHook DISABLED (see main.cpp for rationale)");
    // smoap::hooks::installCreditsStartHook();

    // BISECT: CappyMessage text-lookup hooks (4) disabled. These intercept
    // every al::isExistLabelIn{System,Stage}Message + getSystem/Stage
    // MessageString call — SMO does many per frame for any UI text. Highest-
    // frequency trampoline-with-orig surface in the build. If the JIT crash
    // is hook-related, disabling this removes the dominant source.
    SMOAP_LOG_INFO("CappyMessageTextHooks DISABLED (bisect)");
    // smoap::hooks::installCappyMessageTextHooks();
    SMOAP_LOG_INFO("resolving CappyMessenger rs:: function pointers");
    smoap::hooks::installCappyMessengerSymbols();

    // BISECT phase 2: SaveLoad disabled too
    SMOAP_LOG_INFO("SaveLoadHook DISABLED (bisect phase 2)");
    // smoap::hooks::installSaveLoadHook();

    SMOAP_LOG_INFO("=== hkMain END (waiting for GameSystem::init to fire) ===");
}
