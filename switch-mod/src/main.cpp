// Spicy Meatball Overdrive — Switch module entry point.
//
// Loaded by exlaunch as `subsdk9` from
// sd:/atmosphere/contents/0100000000010000/exefs/subsdk9.
//
// Lifecycle on the game's main thread:
//   1. exl_main runs once, before SMO's main(). We install hooks; they don't
//      fire yet because their target functions haven't been called.
//   2. GameSystem::init runs during SMO's startup. Our hook there reads
//      ap_config.json, spawns the AP socket worker thread, and inits the HUD.
//   3. HakoniwaSequence::drawMain fires every rendered frame. Our hook there
//      drains the inbound item ring (applyOnFrame) and ticks the HUD heartbeat.
//
// Both critical hooks resolve fail-loud via R_ABORT_UNLESS inside
// InstallAtSymbol — if a symbol is wrong, SMO refuses to launch rather than
// running with a half-installed AP layer.

#include "lib.hpp"  // exl_main, NORETURN, EXL_ABORT, exl::hook::*

#include "ap/ApClient.hpp"
#include "ap/ApConfig.hpp"
#include "ap/ApState.hpp"
#include "hooks/HookSymbols.hpp"
#include "hooks/SoftInstall.hpp"
#include "ui/ApHudOverlay.hpp"
#include "ui/CappyMessenger.hpp"
#include "util/Log.hpp"

// al::Scene's layout: it inherits from FOUR bases (NerveExecutor primary,
// then IUseAudioKeeper, IUseCamera, IUseSceneObjHolder). The
// IUseSceneObjHolder sub-object lives at a NON-ZERO offset inside Scene.
// C++ generates the static_cast pointer adjustment at compile time. Passing
// a raw void* to rs::isActiveCapMessage (which expects an
// IUseSceneObjHolder*) without the adjustment yields garbage on the
// vtable lookup -> silent halt. We pull in the Scene header here so the
// cast in DrawMainHook actually applies the right offset.
#include "al/scene/Scene.h"

// Hook target classes. Forward-declared rather than including LunaKit's full
// game headers, since our hooks treat the receivers as opaque.
class GameSystem;
class HakoniwaSequence;

namespace smoap::hooks {
void installMoonGetHook();
void installCaptureStartHook();
void installScenarioFlagHook();
void installSaveLoadHook();
void installEndingHook();
void installDeathHook();
// M6 phase A: shine-counter HUD substitution for AP credit display.
void installShineNumGetHook();
void installShineNumByWorldGetHook();
// M6 phase A.5: moon-get cutscene label substitution (Channel A).
void installMoonLabelHook();
// M6 phase D: addPayShine deposit detection + AP-credit debit.
void installAddPayShineHook();
void installAddPayShineAllHook();
// Cappy Messenger: 4 trampolines on the al:: top-level MSBT lookup pair
// (system + stage existence/get) + LookupSymbol for the rs:: CapMessage
// entry points used by CappyMessenger.
void installCappyMessageTextHooks();
void installCappyMessengerSymbols();
// M-color: per-shine palette override (AP classification -> moon color).
void installShineAppearanceHook();
// M7 Path A: world-map kingdom-select intercept + AP-moon-count gate.
void installWorldMapSelectHook();
// M7 phase A (capture lock): drain ApState::pending_kill_keeper if its
// deadline elapsed. Called once per frame from DrawMainHook.
void tickPendingUncapture();
}  // namespace smoap::hooks

namespace smoap::hooks {
// M7-A2 (Capture List parity): filter addHackDictionary writes so the
// in-game Capture List only shows AP-unlocked captures.
void installAddHackDictionaryHook();
}  // namespace smoap::hooks

namespace smoap::game {
// M6 phase B: resolve addHackDictionary + isExistInHackDictionary once.
void installCaptureGrantSymbols();
// M6 phase D: resolve getCurrentWorldIdNoDevelop once (stored on ApState).
void installDepositKingdomLookupSymbol();
}  // namespace smoap::game

namespace {

// Hook 1 — game-system init. Run after Orig so SMO's heap and other
// subsystems are up before we open a socket.
HOOK_DEFINE_TRAMPOLINE(GameSystemInitHook) {
    static void Callback(GameSystem* self) {
        SMOAP_LOG_INFO(">>> GameSystem::init hook FIRED (calling Orig)");
        Orig(self);
        SMOAP_LOG_INFO(">>> GameSystem::init Orig returned");

        const auto cfg = smoap::ap::loadApConfig();

        // nifm + socket bring-up MUST happen on this (frame) thread because
        // they're nn-service IPCs and our raw-svcCreateThread worker can't
        // make those.
        smoap::ap::ApClient::instance().initNetworking();

        // Force Meyer's-singleton construction on this nn-aware frame thread.
        // The worker runs on a raw svcCreateThread and lacks the nn TLS that
        // __cxa_guard_acquire's mutex needs — first call from there NULL-derefs
        // inside InternalCriticalSectionImplByHorizon::Enter. Touching every
        // singleton here flips its guard byte to "initialized" so the worker's
        // later calls take the fast path and skip the lock.
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
    }
};

// Hook 2 — frame pump. Apply queued inbound items and tick the HUD log.
HOOK_DEFINE_TRAMPOLINE(DrawMainHook) {
    static void Callback(const HakoniwaSequence* self) {
        static bool s_first = true;
        if (s_first) {
            s_first = false;
            SMOAP_LOG_INFO(">>> drawMain hook FIRED (first frame)");
        }
        Orig(self);
        // M6 phase B: cache GameDataHolder* for grantCapture / future M6
        // GameDataFunction calls. HakoniwaSequence::mGameDataHolder lives at
        // offset 0xB8 (see lunakit-vendor HakoniwaSequence.h) and is a
        // GameDataHolderAccessor — first field is the GameDataHolder*. Refresh
        // every frame in case the holder swaps (it doesn't in practice, but
        // cheap insurance against scene transitions).
        //
        // Cappy Messenger: HakoniwaSequence::curScene at offset 0xB0 is a
        // StageScene*. StageScene IS-A al::Scene IS-A al::IUseSceneObjHolder
        // — exactly what rs::tryShowCapMessagePriorityLow wants. Null during
        // boot / scene transitions; CappyMessenger::tryPump null-guards.
        if (self) {
            constexpr std::size_t kCurSceneOffset       = 0xB0;
            constexpr std::size_t kGameDataHolderOffset = 0xB8;
            const auto* base = reinterpret_cast<const std::uint8_t*>(self);
            // curScene is a StageScene* (lunakit-vendor HakoniwaSequence.h:67).
            // Read it as al::Scene* and let the compiler insert the
            // IUseSceneObjHolder pointer adjustment via static_cast. The
            // adjusted pointer is what we pass to rs:: functions.
            auto* scene_obj = *reinterpret_cast<al::Scene* const*>(
                base + kCurSceneOffset);
            void* gdh = *reinterpret_cast<void* const*>(base + kGameDataHolderOffset);
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
        smoap::hooks::tickPendingUncapture();
        smoap::ui::drawHudFrame();
        // Pump the Cappy-speech queue. Reads the freshly-cached scene; no-op
        // when null (boot scene) or when SMO is already showing a CapMessage.
        smoap::ui::CappyMessenger::instance().tryPump(
            smoap::ap::ApState::instance().scene_cache.load(
                std::memory_order_relaxed));
    }
};

}  // namespace

extern "C" void exl_main(void* /*x0*/, void* /*x1*/) {
    // Pre-FS logs go to svcOutputDebugString only (visible via Atmosphere's
    // lm log if enabled in system_settings.ini). File logging activates
    // after markFsReady() is called from inside the first hook callback.

    SMOAP_LOG_INFO("=== exl_main START ===");
    SMOAP_LOG_INFO("SMO AP module " SMO_AP_MOD_VERSION_STRING
                   " target=SMO " SMO_VERSION_STRING);

    SMOAP_LOG_INFO("calling exl::hook::Initialize");
    exl::hook::Initialize();
    SMOAP_LOG_INFO("exl::hook::Initialize returned");

    // Soft install: probes nn::ro::LookupSymbol and logs per-symbol result.
    // Lets the module keep loading even if some symbols miss.
    SMOAP_LOG_INFO("installing GameSystemInitHook -> %s", smoap::sym::kGameSystemInit);
    smoap::hooks::softInstallAtSymbol<GameSystemInitHook>(smoap::sym::kGameSystemInit);

    SMOAP_LOG_INFO("installing DrawMainHook -> %s", smoap::sym::kHakoniwaSequenceDrawMain);
    smoap::hooks::softInstallAtSymbol<DrawMainHook>(smoap::sym::kHakoniwaSequenceDrawMain);

    SMOAP_LOG_INFO("installing 6 game-event hooks");
    smoap::hooks::installMoonGetHook();
    smoap::hooks::installCaptureStartHook();
    smoap::hooks::installScenarioFlagHook();
    smoap::hooks::installSaveLoadHook();
    smoap::hooks::installEndingHook();
    smoap::hooks::installDeathHook();

    SMOAP_LOG_INFO("installing 2 M6-phase-A shine-counter hooks");
    smoap::hooks::installShineNumGetHook();
    smoap::hooks::installShineNumByWorldGetHook();

    SMOAP_LOG_INFO("installing M6-phase-A.5 cutscene label hooks");
    smoap::hooks::installMoonLabelHook();

    SMOAP_LOG_INFO("resolving M6-phase-B capture-grant symbols");
    smoap::game::installCaptureGrantSymbols();

    SMOAP_LOG_INFO("installing AddHackDictionaryHook (Capture List AP gate)");
    smoap::hooks::installAddHackDictionaryHook();

    SMOAP_LOG_INFO("resolving M6-phase-D current-kingdom lookup");
    smoap::game::installDepositKingdomLookupSymbol();
    SMOAP_LOG_INFO("installing M6-phase-D deposit hooks (addPayShine + addPayShineCurrentAll)");
    smoap::hooks::installAddPayShineHook();
    smoap::hooks::installAddPayShineAllHook();

    SMOAP_LOG_INFO("installing CappyMessenger text-lookup trampolines (4)");
    smoap::hooks::installCappyMessageTextHooks();
    SMOAP_LOG_INFO("resolving CappyMessenger rs:: function pointers");
    smoap::hooks::installCappyMessengerSymbols();

    SMOAP_LOG_INFO("installing ShineAppearanceHook (AP-classification moon color)");
    smoap::hooks::installShineAppearanceHook();

    SMOAP_LOG_INFO("installing WorldMapSelectHook (M7 Path A kingdom-order gate)");
    smoap::hooks::installWorldMapSelectHook();

    SMOAP_LOG_INFO("=== exl_main END (waiting for GameSystem::init to fire) ===");
}

extern "C" NORETURN void exl_exception_entry() {
    EXL_ABORT(0x420);
}
