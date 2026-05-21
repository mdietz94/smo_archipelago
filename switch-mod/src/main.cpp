// Spicy Meatball Overdrive — Hakkun edition entry point.
//
// hkMain installs all hooks at module load. GameSystemInit + DrawMain are the
// two load-bearing trampolines: the former kicks off ApClient + ApState +
// HUD on the game's frame thread; the latter drives applyOnFrame + reconciler
// + CappyMessenger every frame.

#include "hk/hook/Trampoline.h"
#include "hk/types.h"

#include "al/Library/Scene/IUseSceneObjHolder.h"
#include "al/Library/Scene/Scene.h"

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
// Talkatoo% mode: speech-bubble substitution via tryFindShineMessage
// trampoline + Poetter vtable filter. See hooks/TalkatooSpeechHook.cpp.
// Phase 4 (block non-named moon collection) lives inside the existing
// MoonGetHook (universal setGotShine chokepoint) — see MoonGetHook.cpp.
void installTalkatooSpeechHook();
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
            // pointers from HakoniwaSequence's known field offsets.
            //
            // CRITICAL: al::Scene multiply-inherits from NerveExecutor,
            // IUseAudioKeeper, IUseCamera, IUseSceneObjHolder. The
            // IUseSceneObjHolder subobject sits at a non-zero offset within
            // Scene (after the other three bases' subobjects). rs::
            // isActiveCapMessage and rs::tryShowCapMessagePriorityLow take
            // const al::IUseSceneObjHolder*, and they do vtable dispatch on
            // that pointer. Passing a raw al::Scene* without the static_cast
            // adjustment makes them read the wrong vtable -> NULL-deref,
            // surfacing as an ARMeilleure JIT translator fault under Ryujinx.
            // Production exlaunch always did this adjustment; the phase-3b
            // port skipped it on the assumption that "the pointer passes
            // through unchanged" — which is wrong for multiple inheritance.
            constexpr std::size_t kCurSceneOffset       = 0xB0;
            constexpr std::size_t kGameDataHolderOffset = 0xB8;
            const auto* base = reinterpret_cast<const std::uint8_t*>(self);
            auto* scene_obj = *reinterpret_cast<al::Scene* const*>(
                base + kCurSceneOffset);
            void* gdh = *reinterpret_cast<void* const*>(
                base + kGameDataHolderOffset);
            void* scene_holder = nullptr;
            if (scene_obj) {
                auto* holder = static_cast<al::IUseSceneObjHolder*>(scene_obj);
                scene_holder = static_cast<void*>(holder);
            }
            auto& st = smoap::ap::ApState::instance();
            st.scene_cache.store(scene_holder, std::memory_order_relaxed);
            st.game_data_holder_cache.store(gdh, std::memory_order_relaxed);
        }

        smoap::ap::ApState::instance().applyOnFrame();
        smoap::game::reconcileCaptureDictionary();
        smoap::ap::ApState::instance().flushPendingCaptureGrants();
        smoap::hooks::tickPendingUncapture();
        smoap::ui::drawHudFrame();

        // Drain worker-thread system-bubble pushes before tryPump so a freshly
        // arrived "Connected/Disconnected/Not connected to Archipelago" lands
        // in CappyMessenger's queue in time for this frame's dispatch attempt.
        // Direct worker-thread CappyMessenger access would race with frame-
        // thread tryPump reads; the worker pushes onto the SPSC ring and we
        // drain here from frame-thread context.
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

    SMOAP_LOG_INFO("installing WorldMapSelectHook (M7 Path A)");
    smoap::hooks::installWorldMapSelectHook();

    SMOAP_LOG_INFO("installing M6-phase-A.5 cutscene label hooks");
    smoap::hooks::installMoonLabelHook();

    SMOAP_LOG_INFO("installing ShineAppearanceHook (AP-classification moon color)");
    smoap::hooks::installShineAppearanceHook();

    SMOAP_LOG_INFO("resolving M6-phase-C snapshot enumeration symbols");
    smoap::game::installSnapshotSymbols();

    // CreditsStartHook is now Strategy A — inline BL patch at +0x4C54A4 via
    // hk::hook::writeBranchLinkAtMainOffset. Matches Kgamer77/SMOO-Plus-Hakkun
    // (the other public Hakkun-based SMO Archipelago project) verbatim.
    SMOAP_LOG_INFO("installing CreditsStartHook (Strategy A: +0x4C54A4 BL inline)");
    smoap::hooks::installCreditsStartHook();

    SMOAP_LOG_INFO("installing CappyMessenger text-lookup trampolines (4)");
    smoap::hooks::installCappyMessageTextHooks();
    SMOAP_LOG_INFO("resolving CappyMessenger rs:: function pointers");
    smoap::hooks::installCappyMessengerSymbols();

    SMOAP_LOG_INFO("installing SaveLoadHook (session-state reset + re-HELLO)");
    smoap::hooks::installSaveLoadHook();

    SMOAP_LOG_INFO("installing TalkatooSpeechHook (Phase 3 — tryFindShineMessage tramp + Poetter vtable filter)");
    smoap::hooks::installTalkatooSpeechHook();

    SMOAP_LOG_INFO("=== hkMain END (waiting for GameSystem::init to fire) ===");
}
