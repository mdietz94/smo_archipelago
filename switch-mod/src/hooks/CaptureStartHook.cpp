// Hook on PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*).
//
// M3: empty trampoline. M4 reads the LiveActor's class to identify the cap
// type, calls reportCaptureChecked. M7 flips to early-out if cap not unlocked.

#include "lib.hpp"
#include "../ap/ApFrameBridge.hpp"
#include "../ap/ApState.hpp"
#include "../game/CaptureGate.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

class PlayerHackKeeper;
namespace al { class HitSensor; class LiveActor; }

namespace smoap::hooks {

namespace {
HOOK_DEFINE_TRAMPOLINE(CaptureStartHook) {
    static void Callback(PlayerHackKeeper* self,
                         al::HitSensor* a, al::HitSensor* b, al::LiveActor* target) {
        Orig(self, a, b, target);
        // M4 fills in: identify cap type from target, reportCaptureChecked.
        // M7 flips to: if (captureBlocked(name)) { playSE_NG(); return; }
    }
};
}  // namespace

void installCaptureStartHook() {
    SMOAP_LOG_INFO("installing CaptureStartHook -> %s", smoap::sym::kPlayerHackKeeperStartHack);
    softInstallAtSymbol<CaptureStartHook>(smoap::sym::kPlayerHackKeeperStartHack);
}

}  // namespace smoap::hooks
