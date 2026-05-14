// SMO Archipelago module entry point.
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
#include "util/Log.hpp"

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
}  // namespace smoap::hooks

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
        smoap::ap::ApState::instance().applyOnFrame();
        smoap::ui::drawHudFrame();
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

    SMOAP_LOG_INFO("installing 5 game-event hooks");
    smoap::hooks::installMoonGetHook();
    smoap::hooks::installCaptureStartHook();
    smoap::hooks::installScenarioFlagHook();
    smoap::hooks::installSaveLoadHook();
    smoap::hooks::installEndingHook();

    SMOAP_LOG_INFO("=== exl_main END (waiting for GameSystem::init to fire) ===");
}

extern "C" NORETURN void exl_exception_entry() {
    EXL_ABORT(0x420);
}
