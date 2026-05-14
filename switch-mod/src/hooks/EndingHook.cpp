// Hook on DemoPeachWedding::makeActorAlive().
//
// M3: empty trampoline. M7 reports goal via reportGoal (idempotent — sends
// at most once per save via ApState::goal_sent).

#include "lib.hpp"
#include "../ap/ApFrameBridge.hpp"
#include "../util/Log.hpp"
#include "HookSymbols.hpp"
#include "SoftInstall.hpp"

class DemoPeachWedding;

namespace smoap::hooks {

namespace {
HOOK_DEFINE_TRAMPOLINE(EndingHook) {
    static void Callback(DemoPeachWedding* self) {
        Orig(self);
        // M7 fills in: smoap::ap::reportGoal();
    }
};
}  // namespace

void installEndingHook() {
    SMOAP_LOG_INFO("installing EndingHook -> %s", smoap::sym::kDemoPeachWeddingMakeActorAlive);
    softInstallAtSymbol<EndingHook>(smoap::sym::kDemoPeachWeddingMakeActorAlive);
}

}  // namespace smoap::hooks
