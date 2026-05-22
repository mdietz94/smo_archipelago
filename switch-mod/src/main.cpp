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
#include "ui/ApDebugConsole.hpp"
#include "ui/ApHudOverlay.hpp"
#include "ui/CappyMessenger.hpp"
#include "util/Log.hpp"

#include "game/System/GameSystem.h"

#ifdef SMOAP_HAS_DEBUG_RENDERER
#  include "hk/gfx/ImGuiBackendNvn.h"
#endif

#include <cstdint>

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
// Talkatoo% mode: pause-menu mark fix (isOpenShineName getter +
// tryUnlockShineName setter trampolines). See hooks/TalkatooMenuMarkHook.cpp.
void installTalkatooMenuMarkHook();
// Instant seed growth: trampolines rs::getGrowFlowerTime to return 1 for
// planted pots (orig==0 passes through), collapsing the 20–60 min real-time
// wait on seed-pot moons to a single area re-entry. See
// hooks/GrowSeedInstantHook.cpp.
void installGrowSeedInstantHook();
}  // namespace smoap::hooks

namespace smoap::game {
void installSnapshotSymbols();
void installDepositKingdomLookupSymbol();
void installPayShineSnapshotSymbol();
void installCaptureGrantSymbols();
void reconcileCaptureDictionary();
// Lost + Ruined Kingdom softlock fix — see game/OdysseyRescue.hpp.
void installOdysseyRescueSymbols();
void runOdysseySoftlockSweep();
}  // namespace smoap::game

// Forward-declare nn::socket::Initialize so the GameSystem::init hook can
// open our own bsd:u session before SMO's gets a chance. sail resolves
// it against main.nso via syms/nn/socket.sym.
namespace nn::socket {
    unsigned int Initialize(void* pool, unsigned long poolSize,
                            unsigned long allocPoolSize, int concurLimit);
}

namespace {

// Larger socket pool than SMO's stock setup — borrowed from Kgamer77/
// SMOO-Plus-Hakkun's main.cpp pattern. 6 MB transfer pool + 128 KB
// allocator pool, page-aligned. We need this because:
//   1. nn::socket::Initialize can only be called ONCE per process; the
//      first call wins. If SMO calls it first with its (smaller) pool,
//      we either get a re-init assert (if we re-call) or are stuck with
//      whatever SMO chose. Either way we lose pool control.
//   2. Opening a parallel hk::socket client against bsd:u — our prior
//      approach — fails on retail with `KernelResult_OutOfSessions`
//      because svcConnectToNamedPort("sm:") exceeds the per-process
//      session quota. See project_sail_missing_symbol_crashes_init's
//      sibling memory about Kgamer's init pattern.
//
// Wired in below: pre-orig, we call Initialize ourselves with this pool,
// then install a no-op HkTrampoline at nn::socket::Initialize so SMO's
// later call is neutered.
constexpr unsigned long kSocketPoolSize      = 0x600000;   // 6 MB
constexpr unsigned long kSocketAllocPoolSize = 0x20000;    // 128 KB
alignas(0x1000) char g_socket_pool[kSocketPoolSize + kSocketAllocPoolSize];

// No-op trampoline used to disarm nn::socket::Initialize after our own
// call lands. Has to be installed BEFORE orig fires so SMO's invocation
// during GameSystem::init lands on the no-op.
HkTrampoline<unsigned int, void*, unsigned long, unsigned long, int>
    disableSocketInit = hk::hook::trampoline(
        [](void*, unsigned long, unsigned long, int) -> unsigned int {
            return 0;  // nn::Result success
        });

// Hook 1: GameSystem::init runs during SMO startup. We do the socket
// bring-up BEFORE orig (so our pool wins the one-shot Initialize race),
// then call orig, then start the AP worker. Mirrors Kgamer77/SMOO-Plus-
// Hakkun (which has the same nn::socket pre-orig hijack — confirmed in
// their src/main.cpp).
HkTrampoline<void, GameSystem*> gameSystemInitHook =
    hk::hook::trampoline([](GameSystem* self) -> void {
        SMOAP_LOG_INFO(">>> GameSystem::init hook FIRED");

        // ImGui setup MUST land before orig. Kgamer77/SMOO-Plus-Hakkun
        // calls imgui::setup() as the FIRST thing in its gameSystemInit
        // pre-orig, before nn::socket::Initialize. Their imgui::setup
        // creates the ExpHeap + wires the allocator + calls
        // ImGuiBackendNvn::tryInitialize(). By initializing ImGui BEFORE
        // SMO touches NVN, the addon's nvnDeviceInitialize override
        // gets to call setDevice on an already-prepared backend. Our
        // prior pattern (defer to first-draw lazy-init) hung at first
        // drawMain.orig — putting init here matches their proven setup.
        smoap::ui::initDebugConsole();

        // Socket init MUST land before orig so our 6 MB pool wins the
        // one-shot nn::socket::Initialize race. Pattern from Kgamer's
        // gameSystemInit hook body. Without our larger pool the bridge's
        // many-concurrent-connection scenarios run out of session slots.
        SMOAP_LOG_INFO("[init] nn::socket::Initialize (our pool, %lu+%lu bytes)",
                       kSocketPoolSize, kSocketAllocPoolSize);
        const unsigned int sock_rc = nn::socket::Initialize(
            g_socket_pool, kSocketPoolSize, kSocketAllocPoolSize,
            /*concurLimit=*/0xE);
        if (sock_rc != 0) {
            SMOAP_LOG_ERROR("[init] nn::socket::Initialize FAILED rc=0x%x",
                            sock_rc);
        } else {
            SMOAP_LOG_INFO("[init] nn::socket::Initialize OK");
        }
        // Disarm SMO's eventual call so it can't double-init / clobber
        // our pool.
        disableSocketInit.installAtSym<"_ZN2nn6socket10InitializeEPvmmi">();

        SMOAP_LOG_INFO(">>> calling GameSystem::init orig");
        gameSystemInitHook.orig(self);
        SMOAP_LOG_INFO(">>> GameSystem::init orig returned");

        const auto cfg = smoap::ap::loadApConfig();

        // nifm bring-up MUST happen on this (frame) thread. Socket session
        // is already up from our pre-orig Initialize above.
        smoap::ap::ApClient::instance().initNetworking();

        // Force ApState singleton construction now (same nn-singleton
        // hardening reason as production — the worker doesn't have a
        // safe-to-construct context).
        (void)smoap::ap::ApState::instance();

        SMOAP_LOG_INFO("starting ApClient worker");
        smoap::ap::ApClient::instance().start(smoap::ap::BridgeTarget{
            .host = cfg.bridge_host,  // seed; ApDiscovery overwrites with the actual reply sender
            .port = cfg.bridge_port,
            .retry_ms = cfg.retry_ms,
            .recv_timeout_ms = cfg.recv_timeout_ms,
        });

        smoap::ui::initHud();
        // initDebugConsole moved to pre-orig above (Kgamer pattern).

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
        smoap::util::drainPendingToFile();
        drawMainHook.orig(self);

        if (self) {
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

        // Lost + Ruined softlock sweep — see game/OdysseyRescue.hpp. Throttled
        // to once per 60 frames (~1s @ 60fps). Pattern + cadence mirror
        // Kgamer77/SuperMarioOdysseyArchipelago v1.2.
        {
            static int s_softlockTick = 0;
            if (++s_softlockTick >= 60) {
                s_softlockTick = 0;
                smoap::game::runOdysseySoftlockSweep();
            }
        }
        smoap::ui::drawHudFrame();
        smoap::ui::drawDebugConsole();

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

    SMOAP_LOG_INFO("resolving OdysseyRescue symbols (Lost + Ruined softlock fix)");
    smoap::game::installOdysseyRescueSymbols();

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

    // TalkatooMenuMarkHook disabled 2026-05-22: the GameDataFile::tryUnlockShineName
    // suppression breaks Talkatoo's speech path. User log SMOClient_2026_05_22_18_26_16
    // shows one `[talkatoo-menu] suppressing` line and zero `[talkatoo] substituting`
    // lines — Talkatoo says "no hints". Returning bool true from the suppress (per
    // 7b0fc6a) didn't unblock it. The actual Talkatoo-facing API is
    // `rs::tryUnlockShineName(LiveActor*, s32)` (GameDataUtil.h:92, namespace `rs`),
    // not the GameDataFile class method we hook here. Re-enable only after the menu
    // mark redesign targets the correct layer.
    // SMOAP_LOG_INFO("installing TalkatooMenuMarkHook (pause-menu mark fix)");
    // smoap::hooks::installTalkatooMenuMarkHook();

    SMOAP_LOG_INFO("installing GrowSeedInstantHook (rs::getGrowFlowerTime -> 1 for planted)");
    smoap::hooks::installGrowSeedInstantHook();

#ifdef SMOAP_HAS_DEBUG_RENDERER
    // Install the Nvn bootstrap trampoline so ImGuiBackendNvn auto-wires
    // its device/cmdbuf the moment NVN comes up. `installHooks(false)` =
    // don't auto-call tryInitialize (we do that lazily on first overlay
    // draw, after NVN device is ready). Matches Kgamer77/SMOO-Plus-Hakkun.
    //
    // Re-enabled after the LibHakkun bump to 9892726b. Our prior patched
    // relocator (page-aligned 4 KiB slots) was the prime suspect for the
    // first-NVN-init hang. Upstream's compact packed slots should let
    // ARMeilleure translate the cross-module trampoline cleanly.
    SMOAP_LOG_INFO("installing ImGuiBackendNvn bootstrap hook (manual init)");
    hk::gfx::ImGuiBackendNvn::instance()->installHooks(false);
#endif

    SMOAP_LOG_INFO("=== hkMain END (waiting for GameSystem::init to fire) ===");
}
