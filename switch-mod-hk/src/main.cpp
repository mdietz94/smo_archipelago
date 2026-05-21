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

        // Drain worker-thread system-bubble pushes BEFORE tryPump so a freshly
        // arrived "Connected/Disconnected/Not connected to Archipelago" lands
        // in CappyMessenger's queue in time for this frame's dispatch attempt.
        // Cross-thread access to CappyMessenger from the worker crashes
        // Ryujinx ARMeilleure's JIT; the worker pushes onto the SPSC ring and
        // we drain here from frame-thread context.
        {
            smoap::ap::ApState::SystemBubble bubble;
            while (smoap::ap::ApState::instance()
                       .inbound_system_bubbles.pop(bubble)) {
                smoap::ui::CappyMessenger::instance().enqueueSystem(bubble.text);
            }
        }

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

    // All hooks re-enabled now that the worker->Cappy cross-thread crash is
    // fixed via the inbound_system_bubbles SPSC ring drained by drawMain.
    SMOAP_LOG_INFO("installing 5 game-event hooks");
    smoap::hooks::installScenarioFlagHook();
    smoap::hooks::installMoonGetHook();
    smoap::hooks::installDeathHook();
    smoap::hooks::installShineNumGetHook();
    smoap::hooks::installShineNumByWorldGetHook();

    SMOAP_LOG_INFO("resolving M6-phase-B capture-grant symbols");
    smoap::game::installCaptureGrantSymbols();
    SMOAP_LOG_INFO("installing AddHackDictionaryHook (Capture List AP gate)");
    smoap::hooks::installAddHackDictionaryHook();

    SMOAP_LOG_INFO("installing M6-phase-D deposit hooks");
    smoap::hooks::installAddPayShineHook();
    smoap::hooks::installAddPayShineAllHook();

    SMOAP_LOG_INFO("installing CaptureStartHook (capture lock + AP check)");
    smoap::hooks::installCaptureStartHook();

    // BISECT phase 14: confirm the worker->ring fix actually solves the
    // SaveLoad crash. Disable WorldMap (5), MoonLabel (3), and ShineAppearance
    // which were OFF during the phase-4 bisect set. If this build is stable,
    // the fix worked and one of those was the additional crash in the
    // "everything on" run. If it crashes, the fix didn't actually help.
    SMOAP_LOG_INFO("WorldMap/MoonLabel/ShineAppearance disabled (phase 14 isolation)");
    // smoap::hooks::installWorldMapSelectHook();
    // smoap::hooks::installMoonLabelHook();
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

    SMOAP_LOG_INFO("CappyMessageTextHooks disabled (phase 14 isolation)");
    // smoap::hooks::installCappyMessageTextHooks();
    SMOAP_LOG_INFO("resolving CappyMessenger rs:: function pointers");
    smoap::hooks::installCappyMessengerSymbols();

    SMOAP_LOG_INFO("installing SaveLoadHook (session-state reset + re-HELLO)");
    smoap::hooks::installSaveLoadHook();

    SMOAP_LOG_INFO("=== hkMain END (waiting for GameSystem::init to fire) ===");
}
